"""MCP v1 查询列合同；仅显式列，不随 ORM/数据库新增字段扩权。"""

from sqlalchemy import JSON, column, event, select, table
from sqlalchemy.orm import load_only, with_expression

CONTRACT_VERSION = "business-read-columns-v1"
# 查询用途及 JSON 例外见 docs/28-business-read-mcp-feishu.md。SQL WHERE/ORDER/RLS 所需列也列出。
READ_COLUMNS = {
    "companies": (
        "id",
        "tenant_id",
        "credit_code",
        "legal_name",
        "registered_region",
        "identity_status",
        "visibility_scope",
    ),
    "company_aliases": (
        "id",
        "company_id",
        "alias",
        "verification_status",
        "visibility_scope",
        "owner_user_id",
        "owner_tenant_id",
    ),
    "sources": ("id", "code", "name", "source_quality", "license_status"),
    "raw_documents": (
        "id",
        "source_id",
        "title",
        "canonical_url",
        "published_at",
        "published_on",
        "observed_at",
        "license_status",
        "visibility_scope",
        "owner_user_id",
        "owner_tenant_id",
        "content_hash",
    ),
    "events": (
        "id",
        "company_id",
        "owner_user_id",
        "owner_tenant_id",
        "visibility_scope",
        "event_type",
        "event_subtype",
        "status",
        "direction",
        "materiality_score",
        "risk_severity",
        "confidence_score",
        "source_quality",
        "title",
        "summary",
        "facts",
        "uncertainties",
        "occurred_at",
        "published_at",
        "published_on",
        "observed_at",
        "fingerprint_version",
        "publication_route",
        "publication_policy_version",
        "publication_reasons",
    ),
    "event_evidence": (
        "id",
        "event_id",
        "raw_document_id",
        "source_event_evidence_id",
        "owner_user_id",
        "owner_tenant_id",
        "visibility_scope",
        "evidence_excerpt",
        "span_hash",
        "support_type",
        "display_source_name",
        "display_source_quality",
        "display_title",
        "display_canonical_url",
        "display_published_at",
        "display_published_on",
        "display_observed_at",
        "display_url_health_status",
        "display_url_http_status",
        "display_url_checked_at",
        "display_final_url",
        "display_license_status",
        "display_allowed",
        "display_detail_payload",
    ),
    "event_facts": (
        "id",
        "event_id",
        "fact_key",
        "name",
        "value",
        "unit",
        "position",
        "occurrence_count",
    ),
    "event_fact_supports": (
        "id",
        "event_id",
        "event_fact_id",
        "event_evidence_id",
        "support_status",
        "support_reasons",
        "policy_version",
        "assessed_at",
    ),
    "event_observations": (
        "id",
        "event_id",
        "schema_version",
        "fact_version",
        "observation_kind",
        "occurred_on",
        "date_precision",
        "created_at",
    ),
    "personal_company_reports": (
        "id",
        "owner_user_id",
        "company_id",
        "company_legal_name",
        "report_version",
        "title",
        "as_of",
        "content_hash",
        "source_event_ids",
        "markdown",
        "created_at",
    ),
    "users": ("id", "tenant_id", "status"),
    "tenants": ("id", "status"),
    "roles": ("id", "code"),
    "user_role_assignments": ("user_id", "role_id", "valid_until"),
    "investments": ("company_id", "fund_id", "tenant_id"),
    "fund_access_grants": ("fund_id", "user_id", "valid_until"),
    "entity_mentions": (
        "id",
        "raw_document_id",
        "candidate_company_id",
        "resolution_status",
        "mention_text",
        "visibility_scope",
        "owner_user_id",
        "owner_tenant_id",
    ),
    "company_research_jobs": (
        "id",
        "company_id",
        "created_by_user_id",
        "coverage",
        "created_at",
        "status",
        "policy_version",
    ),
    "personal_company_requests": (
        "id",
        "company_id",
        "owner_user_id",
        "research_job_id",
        "status",
        "created_at",
    ),
    "event_sharing_decisions": ("id", "source_observation_id", "source_event_id", "action"),
}


def _json_keys(expression, keys):
    # 保留缺键和显式 null 的区别，正式读取的默认值语义不变。输入仅为本文件固定 SQL。
    names = ",".join("'" + key + "'" for key in keys)
    return (
        "(SELECT COALESCE(jsonb_object_agg(key,value),'{}'::jsonb) FROM jsonb_each("
        f"CASE WHEN jsonb_typeof({expression})='object' THEN {expression} ELSE '{{}}'::jsonb END) "
        f"WHERE key IN ({names}))"
    )


# 受限视图只暴露正式许可/事实支持真正读取的键；不授予 reader 底层 JSON 整列。
PROJECTIONS = {
    "raw_documents": (
        "payload",
        _json_keys("payload::jsonb", ("excerpt", "source_windows"))
        + " || jsonb_build_object('_source_verification',"
        + _json_keys(
            "payload::jsonb->'_source_verification'",
            ("status", "http_status", "checked_at", "final_url"),
        )
        + ",'curated_record',COALESCE(payload::jsonb->'curated_record' NOT IN "
        + "('{}'::jsonb,'[]'::jsonb,'null'::jsonb,'false'::jsonb,'0'::jsonb,'\"\"'::jsonb),false))",
    ),
    "event_fact_supports": (
        "deterministic_checks",
        _json_keys("deterministic_checks::jsonb", ("evidence_signature",)),
    ),
    "event_observations": (
        "candidate_payload",
        _json_keys(
            "candidate_payload::jsonb",
            (
                "event_fields",
                "metadata",
                "reviewed_at",
                "sources",
                "evidence_ids",
                "candidate",
                "field_links",
            ),
        ),
    ),
}


# 0036 的既有 RLS 子查询依赖；投影所有者仅可读取这些许可判定列。
PROJECTION_RLS_COLUMNS = {
    "companies": ("id", "tenant_id", "visibility_scope", "identity_status"),
    "users": ("id", "tenant_id", "status"),
    "roles": ("id", "code"),
    "user_role_assignments": ("user_id", "role_id", "valid_until"),
    "investments": ("company_id", "fund_id", "tenant_id"),
    "fund_access_grants": ("fund_id", "user_id", "valid_until"),
    "events": (
        "id",
        "company_id",
        "event_type",
        "visibility_scope",
        "owner_user_id",
        "owner_tenant_id",
    ),
    "event_evidence": ("id", "event_id", "raw_document_id", "visibility_scope"),
    "raw_documents": (
        "id",
        "source_id",
        "visibility_scope",
        "license_status",
        "owner_user_id",
        "owner_tenant_id",
    ),
    "sources": ("id", "code", "license_status"),
    "entity_mentions": ("raw_document_id", "candidate_company_id", "resolution_status"),
}


def install_read_loaders(factory, projection_schema):
    """只作用于专用 MCP Session；未声明属性 raiseload，不偷偷退回全列 lazy load。"""

    @event.listens_for(factory, "do_orm_execute")
    def restrict_load(state):
        if not state.is_select or not state.is_orm_statement:
            return
        for desc in state.statement.column_descriptions:
            model = desc.get("entity")
            if model is None or desc.get("expr") is not model:
                continue
            name = model.__tablename__
            if name not in READ_COLUMNS:
                raise PermissionError("undeclared_read_model")
            options = [load_only(*(getattr(model, c) for c in READ_COLUMNS[name]), raiseload=True)]
            if name in PROJECTIONS:
                field = PROJECTIONS[name][0]
                view = table(
                    name + "_read",
                    column("id", model.id.type),
                    column(field, JSON),
                    schema=projection_schema,
                )
                expr = (
                    select(view.c[field])
                    .where(view.c.id == model.__table__.c.id)
                    .correlate(model.__table__)
                    .scalar_subquery()
                )
                options.append(with_expression(getattr(model, field), expr))
            state.statement = state.statement.options(*options)
