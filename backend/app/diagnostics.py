"""只读诊断服务：只读专用视图、元数据白名单，不读取网页正文或业务写入口。"""

import base64
import fcntl
import hashlib
import hmac
import json
import logging
import math
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine, text

VERSION = "diagnostic-v1"
TOOLS = (
    "find_company",
    "get_company_diagnostic",
    "get_run_trace",
    "get_event_lineage",
    "get_diagnostic_summary",
)
logger = logging.getLogger("diagnostic.audit")


def audit(principal, tool, reason, started, *, request_id=None, rows=0, size=0, **extra):
    logger.info(
        json.dumps(
            {
                "utc": datetime.now(UTC).isoformat(),
                "request_id": request_id or uuid4().hex,
                "principal": hashlib.sha256(principal.id.encode()).hexdigest()
                if principal
                else "anonymous",
                "tool": tool if tool in TOOLS else "unknown_tool",
                "reason_code": reason,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "row_count": rows,
                "byte_count": size,
                "version": VERSION,
                **extra,
            }
        )
    )


class Deadline:
    def __init__(self, seconds=4.5):
        self.until = time.monotonic() + seconds
        self.cancelled = threading.Event()

    def remaining(self):
        left = self.until - time.monotonic()
        if self.cancelled.is_set() or left < 0.05:
            raise TimeoutError("diagnostic_deadline")
        return left


class BoundedConnection:
    def __init__(self, connection, deadline):
        self.connection, self.deadline = connection, deadline

    def execute(self, statement, parameters=None):
        milliseconds = max(1, min(2500, int(self.deadline.remaining() * 1000)))
        self.connection.execute(
            text("SELECT set_config('statement_timeout', :timeout, true)"),
            {"timeout": str(milliseconds)},
        )
        self.deadline.remaining()
        result = self.connection.execute(statement, parameters or {})
        self.deadline.remaining()
        return result

    def scalar(self, statement, parameters=None):
        return self.execute(statement, parameters).scalar()


@dataclass(frozen=True)
class Principal:
    id: str
    company_ids: frozenset[UUID]
    expires_at: float
    scope: str = "public_metadata"

    def key(self):
        return hashlib.sha256(
            json.dumps(
                [self.id, self.scope, sorted(map(str, self.company_ids)), self.expires_at]
            ).encode()
        ).hexdigest()


def build_diagnostic_engine(url):
    if not url.startswith("postgresql"):
        raise ValueError("diagnostics require dedicated PostgreSQL credentials")
    engine = create_engine(
        url,
        pool_size=2,
        max_overflow=0,
        pool_timeout=1,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": 2,
            "options": (
                "-c default_transaction_read_only=on -c statement_timeout=2500 "
                "-c lock_timeout=500 -c idle_in_transaction_session_timeout=5000"
            ),
        },
    )
    with engine.connect() as c:
        row = c.execute(
            text(
                "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolinherit "
                "FROM pg_roles WHERE rolname=current_user"
            )
        ).one()
        if any(row) or c.scalar(
            text(
                "SELECT count(*) FROM pg_auth_members WHERE member=(SELECT oid FROM "
                "pg_roles WHERE rolname=current_user)"
            )
        ):
            raise ValueError("unsafe diagnostic database role")
        if c.scalar(
            text(
                "SELECT count(*) FROM information_schema.role_table_grants WHERE "
                "grantee=current_user AND table_schema='public'"
            )
        ):
            raise ValueError("diagnostic role must not have base-table grants")
    return engine


class DiagnosticService:
    def __init__(
        self,
        engine,
        cursor_key,
        *,
        commit="not_recorded",
        max_bytes=131072,
        export_bytes=1048576,
        max_calls=30,
        budget_path=None,
        require_existing_budget=False,
    ):
        if len(cursor_key) < 32:
            raise ValueError("cursor signing key too short")
        self.engine, self.cursor_key, self.commit = engine, cursor_key, commit
        self.max_bytes, self.export_bytes, self.max_calls = max_bytes, export_bytes, max_calls
        self.lock, self.slots = threading.Lock(), threading.BoundedSemaphore(2)
        self.usage = {}
        self.context = threading.local()
        self.budget_path = Path(budget_path) if budget_path else None
        self.require_existing_budget = require_existing_budget
        if require_existing_budget:
            if self.budget_path is None:
                raise ValueError("invalid_budget_state")
            with self.usage_lock():
                pass

    @contextmanager
    def usage_lock(self):
        if not self.lock.acquire(timeout=0.5):
            raise TimeoutError("diagnostic_deadline")
        try:
            if self.budget_path is None:
                yield
                return
            with self.budget_path.open("r+" if self.require_existing_budget else "a+") as stream:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError("budget_busy") from None
                os.fchmod(stream.fileno(), 0o600)
                stream.seek(0)
                data = stream.read()
                if not data and self.require_existing_budget:
                    raise ValueError("invalid_budget_state")
                self.usage = json.loads(data) if data else {}
                if not isinstance(self.usage, dict) or any(
                    not isinstance(v, (list, tuple))
                    or len(v) != 3
                    or any(type(n) not in (int, float) or not math.isfinite(n) or n < 0 for n in v)
                    for v in self.usage.values()
                ):
                    raise ValueError("invalid_budget_state")
                try:
                    yield
                finally:
                    stream.seek(0)
                    stream.truncate()
                    json.dump(self.usage, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.lock.release()

    @contextmanager
    def transaction(self, principal):
        deadline = getattr(self.context, "deadline", None) or Deadline()
        deadline.remaining()
        with self.engine.connect() as c, c.begin():
            bounded = BoundedConnection(c, deadline)
            # set_config issues SELECT, so transaction mode must be set first.
            c.execute(text("SET TRANSACTION READ ONLY"))
            bounded.execute(
                text("SELECT set_config('diagnostic.company_ids', :ids, true)"),
                {"ids": ",".join(sorted(map(str, principal.company_ids)))},
            )
            yield bounded

    def cursor(self, value):
        payload = base64.urlsafe_b64encode(json.dumps(value, sort_keys=True).encode()).decode()
        return (
            payload + "." + hmac.new(self.cursor_key, payload.encode(), hashlib.sha256).hexdigest()
        )

    def page(self, principal, tool, params):
        limit = params.get("limit", 20)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("limit_out_of_range")
        binding = hashlib.sha256(
            json.dumps(
                {k: v for k, v in params.items() if k not in {"cursor", "limit"}},
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        state = {
            "scope": principal.key(),
            "tool": tool,
            "binding": binding,
            "as_of": datetime.now(UTC).isoformat(),
            "after": None,
        }
        token = params.get("cursor")
        if token:
            if not isinstance(token, str) or len(token) > 2048:
                raise ValueError("invalid_cursor")
            try:
                payload, signature = token.split(".")
                expected = hmac.new(self.cursor_key, payload.encode(), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(signature, expected):
                    raise ValueError()
                saved = json.loads(base64.urlsafe_b64decode(payload))
                if any(
                    saved[k] != state[k] for k in ("scope", "tool", "binding")
                ) or datetime.fromisoformat(saved["as_of"]) < datetime.now(UTC) - timedelta(
                    minutes=15
                ):
                    raise ValueError()
                state = saved
            except (ValueError, KeyError, TypeError):
                raise ValueError("invalid_cursor") from None
        return limit, state

    def call(self, principal, tool, params, *, deadline=None, request_id=None):
        started, reason, result = time.monotonic(), "internal_error", None
        self.context.deadline = deadline or Deadline()
        try:
            self.context.deadline.remaining()
            result = self._call(principal, tool, params)
            reason = "ok"
            return result
        except TimeoutError:
            reason = "diagnostic_deadline"
            raise
        except (ValueError, PermissionError) as exc:
            known = {
                "invalid_principal",
                "tool_not_allowed",
                "unknown_parameters",
                "concurrency_limit",
                "export_budget_exhausted",
                "invalid_cursor",
                "limit_out_of_range",
                "object_not_authorized",
                "scope_not_authorized",
                "window_out_of_range",
                "query_length",
                "response_byte_limit",
                "budget_busy",
                "invalid_budget_state",
            }
            reason = str(exc) if str(exc) in known else "invalid_input"
            raise
        finally:
            self.context.deadline = None
            audit(
                principal,
                tool,
                reason,
                started,
                request_id=request_id,
                rows=len(result["items"]) if result else 0,
                size=len(json.dumps(result, default=str, ensure_ascii=False).encode())
                if result
                else 0,
            )

    def _call(self, principal, tool, params):
        reserved = False
        if (
            principal.expires_at <= time.time()
            or principal.scope != "public_metadata"
            or len(principal.company_ids) > 100
        ):
            raise PermissionError("invalid_principal")
        if tool not in TOOLS:
            raise PermissionError("tool_not_allowed")
        allowed = {
            "find_company": {"query", "cursor", "limit"},
            "get_company_diagnostic": {"company_id", "cursor", "limit"},
            "get_run_trace": {"run_id", "cursor", "limit"},
            "get_event_lineage": {"event_id", "cursor", "limit"},
            "get_diagnostic_summary": {"scope", "window", "cursor", "limit"},
        }[tool]
        if set(params) - allowed:
            raise ValueError("unknown_parameters")
        if not self.slots.acquire(blocking=False):
            raise ValueError("concurrency_limit")
        try:
            with self.usage_lock():
                key = principal.key()
                # 限额按授权有效期累计，换页和换工具不能重置。
                calls, used, _ = self.usage.get(key, (0, 0, principal.expires_at))
                self.usage = {k: v for k, v in self.usage.items() if v[2] > time.time()}
                if calls >= self.max_calls or used + self.max_bytes > self.export_bytes:
                    raise PermissionError("export_budget_exhausted")
                self.usage[key] = (calls + 1, used + self.max_bytes, principal.expires_at)
                reserved = True
            limit, page = self.page(principal, tool, params)
            with self.transaction(principal) as c:
                revision = c.scalar(text("SELECT version_num FROM diagnostic.revision"))
                items, extra = self.read(c, principal, tool, params, page, limit)
            truncated = len(items) > limit
            items = items[:limit]
            result = {
                "diagnostic_schema_version": VERSION,
                "application_commit": self.commit,
                "schema_revision": revision,
                "as_of": page["as_of"],
                "snapshot": "created_before_cursor_current_state_read_at_request",
                "scope": principal.scope,
                "source_provenance": "database_metadata_only",
                "stage_timestamps": "not_recorded",
                "reason_codes": [],
                "items": items,
                "truncated": truncated,
                "next_cursor": None,
                **extra,
            }
            if truncated:
                result["next_cursor"] = self.cursor({**page, "after": str(items[-1]["id"])})
            size = len(json.dumps(result, default=str, ensure_ascii=False).encode())
            if size > self.max_bytes:
                raise ValueError("response_byte_limit")
            with self.usage_lock():
                calls, used, expiry = self.usage[key]
                self.usage[key] = calls, used - self.max_bytes + size, expiry
                reserved = False
            return result
        finally:
            try:
                if reserved:
                    with self.usage_lock():
                        calls, used, expiry = self.usage[key]
                        self.usage[key] = calls, max(0, used - self.max_bytes), expiry
            finally:
                self.slots.release()

    def read(self, c, principal, tool, params, page, limit):
        bindings = {"as_of": page["as_of"], "after": page["after"], "limit": limit + 1}
        extra = {}
        if tool == "find_company":
            query = params.get("query", "")
            if not isinstance(query, str) or not 1 <= len(query) <= 100:
                raise ValueError("query_length")
            bindings["query"] = (
                "%" + query.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
            )
            table, where = "companies", "legal_name ILIKE :query ESCAPE '!'"
            extra["untrusted_source_text"] = ["legal_name"]
        elif tool == "get_company_diagnostic":
            company_id = UUID(str(params["company_id"]))
            if company_id not in principal.company_ids:
                raise PermissionError("object_not_authorized")
            bindings["company_id"] = company_id
            table, where = "runs", "company_id=:company_id"
        elif tool == "get_run_trace":
            bindings["run_id"] = UUID(str(params["run_id"]))
            rows = (
                c.execute(text("SELECT * FROM diagnostic.runs WHERE id=:run_id"), bindings)
                .mappings()
                .all()
            )
            if not rows:
                raise PermissionError("object_not_authorized")
            extra["run"] = dict(rows[0])
            extra["historical_record_label"] = "recorded_disposition_not_current_revalidation"
            table, where = "trace", "run_id=:run_id"
        elif tool == "get_event_lineage":
            bindings["event_id"] = UUID(str(params["event_id"]))
            event = (
                c.execute(text("SELECT * FROM diagnostic.events WHERE id=:event_id"), bindings)
                .mappings()
                .first()
            )
            if not event:
                raise PermissionError("object_not_authorized")
            extra["event"] = dict(event)
            extra["historical_observations"] = "metadata_only_not_current_facts"
            table, where = "lineage", "event_id=:event_id"
        else:
            if params.get("scope", "public_metadata") != principal.scope:
                raise PermissionError("scope_not_authorized")
            window = params.get("window", 7)
            if type(window) is not int or not 1 <= window <= 30:
                raise ValueError("window_out_of_range")
            bindings["since"] = datetime.fromisoformat(page["as_of"]) - timedelta(days=window)
            table, where = "runs", "created_at>=:since"
            extra["summary_basis"] = "bounded_run_page_not_global_totals"
        sql = (
            f"SELECT * FROM diagnostic.{table} WHERE ({where}) "
            "AND created_at<=CAST(:as_of AS timestamptz) "
            "AND (CAST(:after AS uuid) IS NULL OR id>CAST(:after AS uuid)) "
            "ORDER BY id LIMIT :limit"
        )
        rows = c.execute(text(sql), bindings).mappings().all()
        return [dict(r) for r in rows], extra
