"""四项有界业务纯读投影；复用正式事务身份、事项投影和报告许可。"""

import base64
import hashlib
import hmac
import ipaddress
import json
import re
import threading
import time
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import create_engine, event, or_, select, text
from sqlalchemy.orm import load_only

from backend.app.business_dates import reference_fields
from backend.app.database import build_session_factory, request_session
from backend.app.models import (
    Company,
    CompanyAlias,
    CompanyResearchJob,
    Event,
    EventEvidence,
    PersonalCompanyReport,
    PersonalCompanyRequest,
    RawDocument,
    Source,
    Tenant,
    User,
)
from backend.app.personal_features import _report_reference_ids, get_personal_company_report
from backend.app.report_permissions import report_evidence_permission, report_lead_permission
from backend.app.research_completion import stored_completion
from backend.app.research_outcome import research_result
from backend.app.services import _company_event_outputs, _readable_scope_clause

TOOLS = {
    "find_company": "dealflow.company.read",
    "get_company_matters": "dealflow.matter.read",
    "list_company_reports": "dealflow.report.read",
    "get_saved_report": "dealflow.report.read",
}


def safe_url(value):
    try:
        u = urlsplit(value)
        if u.scheme not in {"https", "http"} or not u.hostname or u.username or u.password:
            return ""
        host = u.hostname.lower()
        if host == "localhost" or "." not in host or host.endswith((".local", ".internal")):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            pass
        return value
    except (ValueError, TypeError):
        return ""


def safe_text(value):
    # 返回内容是数据；移除可执行 HTML 和危险 URL，不访问任何返回链接。
    value = re.sub(r"<[^>]*>", "", value)
    value = re.sub(r"!\[([^\]]*)\]\(([^)]*)\)", lambda m: m[1], value)
    return re.sub(
        r"\[([^\]]*)\]\(([^)]*)\)",
        lambda m: "[" + m[1] + "](" + safe_url(m[2]) + ")" if safe_url(m[2]) else m[1],
        value,
    )


def safe_projection(value):
    if isinstance(value, dict):
        return {
            key: safe_url(item)
            if key in {"url", "canonical_url", "final_url", "source_url"}
            else safe_projection(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [safe_projection(item) for item in value]
    return safe_text(value) if isinstance(value, str) else value


def build_business_engine(url):
    if not url.startswith("postgresql"):
        raise ValueError("dedicated_postgres_credentials_required")
    engine = create_engine(
        url,
        pool_size=2,
        max_overflow=0,
        pool_timeout=1,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": 2,
            "options": "-c default_transaction_read_only=on -c statement_timeout=2500 "
            "-c lock_timeout=500 -c idle_in_transaction_session_timeout=5000",
        },
    )
    try:
        with engine.connect() as c:
            if any(
                c.execute(
                    text(
                        "SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolinherit "
                        "FROM pg_roles WHERE rolname=current_user"
                    )
                ).one()
            ):
                raise ValueError("unsafe_business_reader")
            if (
                c.scalar(
                    text(
                        "SELECT count(*) FROM pg_auth_members WHERE member="
                        "(SELECT oid FROM pg_roles WHERE rolname=current_user)"
                    )
                )
                or c.scalar(
                    text(
                        "SELECT count(*) FROM pg_class WHERE relnamespace='public'::regnamespace "
                        "AND relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)"
                    )
                )
                or c.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.column_privileges "
                        "WHERE grantee=current_user AND privilege_type<>'SELECT'"
                    )
                )
            ):
                raise ValueError("unsafe_business_reader")
    except BaseException:
        engine.dispose()
        raise
    return engine


class BusinessReadService:
    def __init__(self, engine, store, cursor_key, *, max_bytes=131072, minute=60, day=1000):
        if len(cursor_key) < 32:
            raise ValueError("cursor_key_too_short")
        self.engine, self.store, self.cursor_key = engine, store, cursor_key
        self.factory = build_session_factory(engine)
        self.slots = threading.BoundedSemaphore(2)
        self.max_bytes, self.minute, self.day = max_bytes, minute, day
        self.context = threading.local()

        @event.listens_for(self.factory.class_, "before_flush")
        def deny_flush(session, context, instances):
            if session.bind is self.engine:
                raise PermissionError("business_write_rejected")

        @event.listens_for(engine, "begin")
        def readonly(c):
            if c.dialect.name == "postgresql":
                c.exec_driver_sql("SET TRANSACTION READ ONLY")

        @event.listens_for(engine, "before_cursor_execute")
        def count_sql(c, cursor, statement, parameters, context, many):
            self.context.sql_count = getattr(self.context, "sql_count", 0) + 1

    def active_user(self, grant):
        with request_session(
            self.factory, user_id=UUID(grant.user_id), tenant_id=UUID(grant.tenant_id)
        ) as s:
            return (
                s.scalar(
                    select(User.id)
                    .join(Tenant, Tenant.id == User.tenant_id)
                    .where(
                        User.id == UUID(grant.user_id),
                        User.tenant_id == UUID(grant.tenant_id),
                        User.status == "active",
                        Tenant.status == "active",
                    )
                )
                is not None
            )

    def cursor(self, value):
        data = base64.urlsafe_b64encode(json.dumps(value, sort_keys=True).encode()).decode()
        return data + "." + hmac.new(self.cursor_key, data.encode(), hashlib.sha256).hexdigest()

    def page(self, grant, tool, params):
        limit = params.get("limit", 20)
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("invalid_page_limit")
        binding = hashlib.sha256(
            json.dumps(
                {k: v for k, v in params.items() if k not in {"cursor", "limit"}}, sort_keys=True
            ).encode()
        ).hexdigest()
        state = {
            "grant": grant.version,
            "tool": tool,
            "binding": binding,
            "expires": time.time() + 900,
            "offset": 0,
        }
        if params.get("cursor"):
            try:
                data, signature = params["cursor"].split(".")
                expected = hmac.new(self.cursor_key, data.encode(), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(signature, expected) or len(data) > 4096:
                    raise ValueError()
                saved = json.loads(base64.urlsafe_b64decode(data))
                if (
                    any(saved[k] != state[k] for k in ("grant", "tool", "binding"))
                    or saved["expires"] <= time.time()
                    or type(saved["offset"]) is not int
                    or saved["offset"] < 0
                ):
                    raise ValueError()
                state = saved
            except (ValueError, KeyError, TypeError):
                raise ValueError("invalid_cursor") from None
        return limit, state

    def call(self, grant_id, scopes, tool, params, *, grant_version=None):
        start = time.monotonic()
        if not self.slots.acquire(blocking=False):
            raise PermissionError("concurrency_limit")
        audit_id, outcome, size, rows, primary_error = None, "denied", 0, 0, None
        self.context.sql_count = 0
        try:
            grant = self.store.grant(grant_id)
            if not grant or (grant_version is not None and grant.version != grant_version):
                raise PermissionError("scope_or_grant_rejected")
            audit_id = self.store.quota(
                grant.id,
                "business_read",
                minute=self.minute,
                day=self.day,
                reserve_bytes=self.max_bytes,
            )
            if TOOLS.get(tool) not in scopes or TOOLS.get(tool) not in grant.scopes:
                raise PermissionError("scope_or_grant_rejected")
            allowed = (
                {"cursor", "limit", "query"}
                if tool == "find_company"
                else {
                    "cursor",
                    "limit",
                    "report_id" if tool == "get_saved_report" else "company_id",
                }
            )
            if set(params) - allowed:
                raise ValueError("unknown_parameters")
            with request_session(
                self.factory, user_id=UUID(grant.user_id), tenant_id=UUID(grant.tenant_id)
            ) as s:
                user = s.scalar(
                    select(User)
                    .options(load_only(User.id, User.tenant_id, User.status))
                    .join(Tenant, Tenant.id == User.tenant_id)
                    .where(
                        User.id == UUID(grant.user_id),
                        User.tenant_id == UUID(grant.tenant_id),
                        User.status == "active",
                        Tenant.status == "active",
                    )
                )
                if not user:
                    raise PermissionError("user_inactive")
                result = self._read(s, user, grant, tool, params)
            result = safe_projection(json.loads(json.dumps(result, default=str)))
            current = self.store.grant(grant_id)
            if not current or current.version != grant.version or not self.active_user(current):
                raise PermissionError("grant_changed_during_read")
            size = len(json.dumps(result, ensure_ascii=False).encode())
            if size > self.max_bytes:
                raise ValueError("response_too_large")
            rows, outcome = len(result.get("items", [])), "ok"
            return result
        except BaseException as error:
            primary_error = error
            if not isinstance(error, (PermissionError, ValueError)):
                outcome = "read_error"
            raise
        finally:
            try:
                if audit_id is not None:
                    self.store.finish(
                        audit_id,
                        outcome,
                        time.monotonic() - start,
                        self.context.sql_count,
                        rows,
                        size,
                    )
            except Exception:
                if primary_error is None:
                    raise
            finally:
                self.slots.release()

    def source_allowed(self, s, evidence, grant):
        seen = set()
        for _ in range(5):
            if evidence.id in seen:
                return False
            seen.add(evidence.id)
            if evidence.raw_document_id:
                doc = s.get(RawDocument, evidence.raw_document_id)
                return doc is not None and str(doc.source_id) in grant.source_ids
            if not evidence.source_event_evidence_id:
                return False
            evidence = s.get(EventEvidence, evidence.source_event_evidence_id)
            if evidence is None:
                return False
        return False

    def report(self, s, user, grant, report):
        ids, _, valid = _report_reference_ids(report)
        evidence = list(s.scalars(select(EventEvidence).where(EventEvidence.event_id.in_(ids))))
        _permission_rows = self.permission_inputs(s, evidence)
        if not valid or any(not self.source_allowed(s, e, grant) for e in evidence):
            return {
                "id": str(report.id),
                "company_id": str(report.company_id),
                "history_status": "restricted",
                "markdown": None,
                "content_hash": report.content_hash,
                "as_of": report.as_of,
                "created_at": report.created_at,
                "report_version": report.report_version,
            }
        output = get_personal_company_report(s, user, report.id).model_dump(mode="json")
        if output["history_status"] == "restricted":
            output["markdown"] = None
        return output

    def permission_inputs(self, s, evidence):
        # 当前只读事务内批量保留 ORM 行，正式许可函数的 get() 不重复查同一来源。
        # 不缓存许可结论，不跨请求/用户复用，也不扩大 RLS 可见范围。
        rows, seen = list(evidence), {e.id for e in evidence}
        for _ in range(5):
            ids = {e.source_event_evidence_id for e in rows if e.source_event_evidence_id} - seen
            if not ids:
                break
            parents = list(s.scalars(select(EventEvidence).where(EventEvidence.id.in_(ids))))
            seen.update(ids)
            rows.extend(parents)
        documents = list(
            s.scalars(
                select(RawDocument).where(
                    RawDocument.id.in_({e.raw_document_id for e in rows if e.raw_document_id})
                )
            )
        )
        sources = list(
            s.scalars(select(Source).where(Source.id.in_({d.source_id for d in documents})))
        )
        return rows, documents, sources

    def _read(self, s, user, grant, tool, params):
        limit, state = self.page(grant, tool, params)
        if tool == "find_company":
            query = params.get("query", "")
            if not isinstance(query, str) or not 1 <= len(query.strip()) <= 120:
                raise ValueError("invalid_query")
            query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            aliases = select(CompanyAlias.company_id).where(
                CompanyAlias.company_id.in_([UUID(i) for i in grant.company_ids]),
                CompanyAlias.verification_status == "verified",
                _readable_scope_clause(CompanyAlias, user, allow_organization_private=False),
                CompanyAlias.alias.ilike("%" + query + "%", escape="\\"),
            )
            q = (
                select(Company)
                .where(
                    Company.id.in_([UUID(i) for i in grant.company_ids]),
                    Company.tenant_id.is_(None),
                    Company.visibility_scope == "public",
                    or_(
                        Company.legal_name.ilike("%" + query + "%", escape="\\"),
                        Company.credit_code == query,
                        Company.id.in_(aliases),
                    ),
                )
                .order_by(Company.id)
            )
            raw = list(s.scalars(q.offset(state["offset"]).limit(limit + 1)))
            items = [
                {
                    "company_id": r.id,
                    "legal_name": safe_text(r.legal_name),
                    "ucc": r.credit_code,
                    "identity_status": r.identity_status,
                    "region": r.registered_region,
                }
                for r in raw[:limit]
            ]
        elif tool == "get_saved_report":
            rid = str(UUID(params["report_id"]))
            if rid not in grant.report_ids:
                raise PermissionError("report_not_authorized")
            report = s.scalar(
                select(PersonalCompanyReport).where(
                    PersonalCompanyReport.id == UUID(rid),
                    PersonalCompanyReport.owner_user_id == user.id,
                    PersonalCompanyReport.company_id.in_([UUID(i) for i in grant.company_ids]),
                )
            )
            if report is None:
                raise PermissionError("report_not_authorized")
            result = self.report(s, user, grant, report)
            if result["markdown"] is None:
                return {**result, "truncated": False, "next_cursor": None}
            body = safe_text(result.pop("markdown"))
            content_hash = hashlib.sha256(body.encode()).hexdigest()
            if state.get("content_hash", content_hash) != content_hash:
                raise ValueError("report_changed")
            state["content_hash"] = content_hash
            offset, end = state["offset"], state["offset"] + 8192
            result.update(
                markdown=body[offset:end],
                delivered_content_hash=content_hash,
                total_characters=len(body),
                offset=offset,
                truncated=end < len(body),
            )
            state["offset"] = end
            result["next_cursor"] = self.cursor(state) if result["truncated"] else None
            return result
        else:
            cid = str(UUID(params["company_id"]))
            if cid not in grant.company_ids:
                raise PermissionError("company_not_authorized")
            if tool == "list_company_reports":
                q = (
                    select(PersonalCompanyReport)
                    .where(
                        PersonalCompanyReport.company_id == UUID(cid),
                        PersonalCompanyReport.owner_user_id == user.id,
                        PersonalCompanyReport.id.in_([UUID(i) for i in grant.report_ids]),
                    )
                    .order_by(PersonalCompanyReport.id)
                )
                raw = list(s.scalars(q.offset(state["offset"]).limit(limit + 1)))
                items = []
                for r in raw[:limit]:
                    out = self.report(s, user, grant, r)
                    items.append(
                        {
                            k: v
                            for k, v in out.items()
                            if k not in {"markdown", "source_event_ids", "reused"}
                        }
                    )
            else:
                q = (
                    select(Event)
                    .where(
                        Event.company_id == UUID(cid),
                        _readable_scope_clause(Event, user, allow_organization_private=False),
                        or_(
                            (Event.visibility_scope == "platform_shared")
                            & Event.status.in_(["published", "corrected", "retracted"]),
                            (Event.status == "candidate")
                            & (Event.publication_route == "unconfirmed_lead"),
                        ),
                    )
                    .order_by(Event.id)
                )
                raw = list(s.scalars(q.offset(state["offset"]).limit(limit + 1)))
                allowed = []
                evidence_rows = list(
                    s.scalars(
                        select(EventEvidence).where(
                            EventEvidence.event_id.in_([r.id for r in raw[:limit]]),
                            _readable_scope_clause(
                                EventEvidence, user, allow_organization_private=False
                            ),
                        )
                    )
                )
                _permission_rows = self.permission_inputs(s, evidence_rows)
                for r in raw[:limit]:
                    if r.status == "candidate" and r.fingerprint_version == "curated-v1":
                        from backend.app.curated_publication import has_pending_curated_record

                        if not has_pending_curated_record(s, r):
                            continue
                    evidence = [e for e in evidence_rows if e.event_id == r.id]
                    if evidence and all(
                        self.source_allowed(s, e, grant)
                        and (
                            report_lead_permission(s, user, r, e)
                            if r.status == "candidate"
                            else report_evidence_permission(
                                s, e, withdrawn_fact=r.status == "retracted"
                            )
                        )
                        for e in evidence
                    ):
                        allowed.append(r)
                outputs = _company_event_outputs(s, allowed, user, allow_organization_private=False)
                items = []
                for out in outputs:
                    item = out.model_dump(mode="json")
                    # 固定公共事项投影，不外发分析 trace、内部 payload、持仓或原文整包。
                    keep = {
                        "id",
                        "event_type",
                        "event_subtype",
                        "occurred_at",
                        "occurred_on",
                        "published_at",
                        "title",
                        "fact_summary",
                        "status",
                        "direction",
                        "materiality_score",
                        "risk_severity",
                        "confidence_score",
                        "display_kind",
                        "information_status",
                        "temporal_status",
                        "facts",
                        "uncertainties",
                        "evidence",
                        "matter_observations",
                        "curated_observations",
                        "financing_observations",
                    }
                    item = {k: v for k, v in item.items() if k in keep}
                    for e in item.get("evidence", []):
                        e["excerpt"] = safe_text(e.get("excerpt", ""))[:800]
                        if not e.get("link_display_allowed"):
                            e["canonical_url"], e["final_url"] = "", None
                    for k in ("title", "fact_summary"):
                        if k in item:
                            item[k] = safe_text(item[k])
                    items.append(item)
                # 被许可/数据源过滤的记录不冒充“没有事项”。
                omitted = len(raw[:limit]) - len(allowed)
                job = s.scalar(
                    select(CompanyResearchJob)
                    .options(
                        load_only(
                            CompanyResearchJob.coverage,
                            CompanyResearchJob.status,
                            CompanyResearchJob.policy_version,
                        )
                    )
                    .join(
                        PersonalCompanyRequest,
                        PersonalCompanyRequest.research_job_id == CompanyResearchJob.id,
                    )
                    .where(
                        PersonalCompanyRequest.owner_user_id == user.id,
                        PersonalCompanyRequest.company_id == UUID(cid),
                        CompanyResearchJob.company_id == UUID(cid),
                        PersonalCompanyRequest.status.in_(
                            [
                                "research_queued",
                                "researching",
                                "partial",
                                "completed",
                                "failed",
                                "budget_deferred",
                            ]
                        ),
                        CompanyResearchJob.status.in_(
                            [
                                "queued",
                                "running",
                                "partial",
                                "completed",
                                "failed",
                                "budget_deferred",
                            ]
                        ),
                    )
                    .order_by(
                        PersonalCompanyRequest.created_at.desc(), PersonalCompanyRequest.id.desc()
                    )
                    .limit(1)
                )
                outcome = research_result(job)
                completion = (
                    outcome.completion.model_dump(mode="json")
                    if outcome and outcome.completion
                    else stored_completion({})
                )
                window = None
                if job and outcome:
                    try:
                        fields = reference_fields(
                            job.coverage.get("reference_at"), job.coverage.get("event_window_days")
                        )
                        if all(job.coverage.get(k, v) == v for k, v in fields.items()):
                            window = {"reference_at": job.coverage["reference_at"], **fields}
                    except (ValueError, TypeError):
                        pass
                # 旧任务缺少或冲突的参考日保持未知，不以今天重造窗口。
        truncated = len(raw) > limit
        state["offset"] += limit
        result = {
            "items": items,
            "truncated": truncated,
            "next_cursor": self.cursor(state) if truncated else None,
        }
        if tool == "get_company_matters":
            result.update(
                research_completion=completion,
                research_window=window,
                research_result=outcome.model_dump(mode="json") if outcome else None,
                permission_omitted=omitted,
                information_gap="未检查范围不表示公司没有相关事项。",
            )
        return result
