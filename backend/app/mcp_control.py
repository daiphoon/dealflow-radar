"""独立 OAuth/审计/配额存储；不连接业务库，也不保存明文 token。"""

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID, uuid4

BUSINESS_SCOPES = (
    "dealflow.company.read",
    "dealflow.matter.read",
    "dealflow.report.read",
)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Grant:
    id: str
    user_id: str
    tenant_id: str
    company_ids: tuple[str, ...]
    report_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    scopes: tuple[str, ...]
    expires_at: float

    def __post_init__(self):
        for value in (
            self.user_id,
            self.tenant_id,
            *self.company_ids,
            *self.report_ids,
            *self.source_ids,
        ):
            UUID(value)
        if not self.id or not set(self.scopes) <= set(BUSINESS_SCOPES):
            raise ValueError("invalid_grant")

    @property
    def version(self):
        return digest(json.dumps(asdict(self), sort_keys=True))


class ControlStore:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.is_symlink():
            raise ValueError("control_symlink_rejected")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.exists() and self.path.stat().st_mode & 0o077:
            raise ValueError("control_permissions_rejected")
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with self.transaction() as c:
            # executescript 会隐式提交前面的事务，升级中断会留下已加列但未转换的配额。
            # 结构、旧 principal 转换及标记统一提交；失败后下一次启动仍可安全重试。
            for statement in (
                """CREATE TABLE IF NOT EXISTS records(
                    kind TEXT, key TEXT, value TEXT NOT NULL,
                    expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(kind,key))""",
                """CREATE TABLE IF NOT EXISTS audit(
                    at REAL, principal TEXT, operation TEXT, outcome TEXT,
                    seconds REAL, sql_count INTEGER, rows INTEGER, bytes INTEGER)""",
                "CREATE INDEX IF NOT EXISTS audit_window ON audit(principal,operation,at)",
                "CREATE TABLE IF NOT EXISTS audit_noise("
                "hour INTEGER PRIMARY KEY,count INTEGER NOT NULL)",
            ):
                c.execute(statement)
            # 独立控制文件原地升级；旧行/滚动配额保留，不涉及业务 Alembic。
            columns = {r[1] for r in c.execute("PRAGMA table_info(audit)")}
            for name, definition in {
                "request_id": "TEXT",
                "tool": "TEXT",
                "grant_version": "TEXT",
                "objects": "TEXT NOT NULL DEFAULT '{}'",
                "reason": "TEXT",
                "query_outcome": "TEXT",
                "delivery": "TEXT NOT NULL DEFAULT 'unknown'",
                "charged": "INTEGER NOT NULL DEFAULT 1",
            }.items():
                if name not in columns:
                    c.execute(f"ALTER TABLE audit ADD COLUMN {name} {definition}")
            if "charged" not in columns:
                # 原版使用 grant id；升级为安全摘要时保留原滚动计数，不重置额度。
                for row in c.execute(
                    "SELECT DISTINCT principal FROM audit WHERE operation='business_read'"
                ).fetchall():
                    c.execute(
                        (
                            "UPDATE audit SET principal=? WHERE principal=? AND "
                            "operation='business_read'"
                        ),
                        (digest(row[0]), row[0]),
                    )
            c.execute("CREATE INDEX IF NOT EXISTS audit_request ON audit(request_id)")

    @contextmanager
    def transaction(self):
        c = sqlite3.connect(self.path, timeout=1, isolation_level=None)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA busy_timeout=1000")
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def put(self, kind, key, value, expires, *, connection=None):
        if connection is None:
            with self.transaction() as c:
                return self.put(kind, key, value, expires, connection=c)
        connection.execute(
            "INSERT OR REPLACE INTO records(kind,key,value,expires,used) VALUES(?,?,?,?,0)",
            (kind, digest(key), json.dumps(value, sort_keys=True), expires),
        )
        connection.execute("DELETE FROM records WHERE expires<?", (time.time(),))

    def get(self, kind, key, *, consume=False, connection=None, include_used=False):
        if connection is None:
            with self.transaction() as c:
                return self.get(kind, key, consume=consume, connection=c, include_used=include_used)
        row = connection.execute(
            "SELECT value,expires,used FROM records WHERE kind=? AND key=?",
            (kind, digest(key)),
        ).fetchone()
        if row is None or row["expires"] <= time.time() or (row["used"] and not include_used):
            return None
        if consume:
            connection.execute(
                "UPDATE records SET used=1 WHERE kind=? AND key=?", (kind, digest(key))
            )
        return json.loads(row["value"])

    def revoke(self, kind, key, *, connection=None):
        if connection is None:
            with self.transaction() as c:
                return self.revoke(kind, key, connection=c)
        connection.execute("UPDATE records SET used=1 WHERE kind=? AND key=?", (kind, digest(key)))

    def approve_identity(self, app_id, tenant_key, open_id, grant):
        # 仅由私有配置/受控初始化调用；DCR 与飞书登录不能创建授权。
        with self.transaction() as c:
            self.put("grant", grant.id, asdict(grant), grant.expires_at, connection=c)
            self.put(
                "identity",
                json.dumps([app_id, tenant_key, open_id]),
                {"grant_id": grant.id},
                grant.expires_at,
                connection=c,
            )

    def grant(self, grant_id, *, connection=None):
        value = self.get("grant", grant_id, connection=connection)
        return Grant(**value) if value else None

    def mapped_grant(self, app_id, tenant_key, open_id):
        value = self.get("identity", json.dumps([app_id, tenant_key, open_id]))
        return self.grant(value["grant_id"]) if value else None

    def _capacity(self, c, now):
        c.execute("DELETE FROM audit WHERE at<?", (now - 31 * 86400,))
        c.execute("DELETE FROM audit_noise WHERE hour<?", (int(now // 3600) - 744,))
        return c.execute("SELECT count(*) FROM audit").fetchone()[0] < 50000

    def begin_mcp(self, request_id):
        now = time.time()
        with self.transaction() as c:
            capacity = self._capacity(c, now)
            day, minute = c.execute(
                "SELECT count(*),COALESCE(sum(at>?),0) FROM audit "
                "WHERE operation='mcp_http' AND at>?",
                (now - 60, now - 86400),
            ).fetchone()
            if not capacity or day >= 20000 or minute >= 600:
                # 噪声按小时聚合，固定 31 天；不会为任意 token/IP/对象产生无限行。
                c.execute(
                    (
                        "INSERT INTO audit_noise VALUES(?,1) ON CONFLICT(hour) DO UPDATE SET "
                        "count=count+1"
                    ),
                    (int(now // 3600),),
                )
                return None
            cur = c.execute(
                "INSERT INTO audit(at,principal,operation,outcome,seconds,"
                "sql_count,rows,bytes,request_id,charged) "
                "VALUES(?,NULL,'mcp_http','pending',0,0,0,0,?,0)",
                (now, request_id),
            )
            return cur.lastrowid

    def quota(
        self,
        principal,
        operation,
        *,
        minute=60,
        day=1000,
        day_bytes=67108864,
        reserve_bytes=0,
        request_id=None,
        tool=None,
        grant_version=None,
    ):
        now = time.time()
        denied = False
        with self.transaction() as c:
            if not self._capacity(c, now):
                raise PermissionError("audit_capacity")
            row = c.execute(
                "SELECT count(*),COALESCE(sum(bytes),0),"
                "COALESCE(sum(CASE WHEN at>? THEN 1 ELSE 0 END),0) FROM audit "
                "WHERE principal=? AND operation=? AND charged=1 AND at>?",
                (now - 60, principal, operation, now - 86400),
            ).fetchone()
            denied = row[0] >= day or row[1] + reserve_bytes > day_bytes or row[2] >= minute
            cur = c.execute(
                "INSERT INTO audit(at,principal,operation,outcome,seconds,sql_count,rows,bytes,"
                "request_id,tool,grant_version,reason,charged) VALUES(?,?,?,?,0,0,0,?,?,?,?,?,?)",
                (
                    now,
                    principal,
                    operation,
                    "denied" if denied else "reserved",
                    0 if denied else reserve_bytes,
                    request_id or uuid4().hex,
                    tool,
                    grant_version,
                    "quota_exceeded" if denied else None,
                    0 if denied else 1,
                ),
            )
            audit_id = cur.lastrowid
        if denied:
            raise PermissionError("quota_exceeded")
        return audit_id

    def annotate_http(self, audit_id, tool, principal=None, grant_version=None):
        with self.transaction() as c:
            c.execute(
                "UPDATE audit SET tool=?,principal=?,grant_version=? WHERE rowid=?",
                (tool, principal, grant_version, audit_id),
            )

    def query_finish(self, audit_id, outcome, seconds, sql_count, rows, size, objects):
        # 查询线程晚于外层超时/取消完成时，仅补查询指标，绝不改最终交付结果。
        with self.transaction() as c:
            c.execute(
                (
                    "UPDATE audit SET "
                    "query_outcome=?,seconds=?,sql_count=?,rows=?,bytes=?,objects=? WHERE "
                    "rowid=?"
                ),
                (
                    outcome,
                    seconds,
                    sql_count,
                    rows,
                    size,
                    json.dumps(objects, sort_keys=True),
                    audit_id,
                ),
            )

    def finish(
        self, audit_id, outcome, seconds, sql_count=None, rows=None, size=None, *, reason=None
    ):
        with self.transaction() as c:
            c.execute(
                "UPDATE audit SET outcome=?,reason=?,seconds=?,sql_count=COALESCE(?,sql_count),"
                "rows=COALESCE(?,rows),bytes=COALESCE(?,bytes) WHERE rowid=?",
                (outcome, reason, seconds, sql_count, rows, size, audit_id),
            )
