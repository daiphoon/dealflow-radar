"""独立 OAuth/审计/配额存储；不连接业务库，也不保存明文 token。"""

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID

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
            c.executescript("""
                CREATE TABLE IF NOT EXISTS records(
                    kind TEXT, key TEXT, value TEXT NOT NULL,
                    expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(kind,key));
                CREATE TABLE IF NOT EXISTS audit(
                    at REAL, principal TEXT, operation TEXT, outcome TEXT,
                    seconds REAL, sql_count INTEGER, rows INTEGER, bytes INTEGER);
                CREATE INDEX IF NOT EXISTS audit_window ON audit(principal,operation,at);
            """)

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

    def quota(
        self, principal, operation, *, minute=60, day=1000, day_bytes=67108864, reserve_bytes=0
    ):
        now = time.time()
        with self.transaction() as c:
            row = c.execute(
                "SELECT count(*),COALESCE(sum(bytes),0),"
                "COALESCE(sum(CASE WHEN at>? THEN 1 ELSE 0 END),0) FROM audit "
                "WHERE principal=? AND operation=? AND at>?",
                (now - 60, principal, operation, now - 86400),
            ).fetchone()
            if row[0] >= day or row[1] + reserve_bytes > day_bytes or row[2] >= minute:
                raise PermissionError("quota_exceeded")
            c.execute(
                "INSERT INTO audit VALUES(?,?,?,?,?,?,?,?)",
                (now, principal, operation, "reserved", 0, 0, 0, reserve_bytes),
            )
            audit_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.execute("DELETE FROM audit WHERE at<?", (now - 31 * 86400,))
            return audit_id

    def finish(self, audit_id, outcome, seconds, sql_count=0, rows=0, size=0):
        with self.transaction() as c:
            c.execute(
                "UPDATE audit SET outcome=?,seconds=?,sql_count=?,rows=?,bytes=? WHERE rowid=?",
                (outcome, seconds, sql_count, rows, size, audit_id),
            )
