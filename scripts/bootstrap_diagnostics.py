"""安装专用诊断视图与账号；仅显式命令执行，绝不读取应用 DATABASE_URL。"""

import os

import psycopg
from psycopg import sql

from scripts.bootstrap_local_database import ROLE_NAME_PATTERN, _connection_kwargs

VIEWS = {
    "companies": "SELECT id, legal_name, identity_status, created_at FROM public.companies",
    "runs": """SELECT id, company_id, status, current_stage, policy_version,
        external_calls, input_tokens, output_tokens, last_error_code, created_at,
        coverage->>'completion_status' AS completion_status,
        coverage->>'stop_reason' AS stop_reason
        FROM public.company_research_jobs""",
    "events": """SELECT id, company_id, event_type, event_subtype, status,
        fingerprint_version, created_at FROM public.events""",
    "evidence": """SELECT id, event_id, raw_document_id, span_hash, support_type,
        display_allowed, display_license_status, display_url_health_status,
        created_at FROM public.event_evidence""",
    "observations": """SELECT id, event_id, raw_document_id, schema_version,
        fact_version, processing_version, observation_kind, date_precision, created_at
        FROM public.event_observations""",
    "supports": """SELECT id, event_id, event_fact_id, event_evidence_id,
        support_status, policy_version, assessed_at AS created_at
        FROM public.event_fact_supports""",
    "revision": "SELECT version_num FROM public.alembic_version",
    "trace": """SELECT md5(j.id::text || ':' || n.ordinality::text)::uuid AS id,
        j.id AS run_id, j.created_at, 'recorded_disposition'::text AS node_type,
        left(n.item->>'stage',64) AS stage, left(n.item->>'outcome',64) AS outcome,
        left(n.item->>'reason',100) AS reason, left(n.item->>'document_id',64) AS document_id,
        NULL::numeric AS cost, NULL::text AS cost_status, NULL::integer AS calls
        FROM public.company_research_jobs j,
        LATERAL json_array_elements(COALESCE(j.coverage->'matter_dispositions', '[]'::json))
        WITH ORDINALITY AS n(item, ordinality)
        UNION ALL SELECT md5(j.id::text || ':candidate:' || r.ordinality::text || ':' ||
        n.ordinality::text)::uuid, j.id, j.created_at, 'search_candidate',
        'search', left(n.item->>'qualified',8),
        CASE WHEN n.item->>'subject_match'='true' THEN 'subject_match' ELSE 'subject_rejected' END,
        NULL, NULL, NULL, NULL
        FROM public.company_research_jobs j,
        LATERAL json_array_elements(COALESCE(j.coverage->'normalized_search_responses','[]'::json))
        WITH ORDINALITY AS r(item, ordinality),
        LATERAL json_array_elements(COALESCE(r.item->'results','[]'::json))
        WITH ORDINALITY AS n(item, ordinality)
        UNION ALL SELECT u.id, j.id, u.created_at, 'usage', u.operation,
        u.usage_state, u.cost_status, NULL, u.estimated_cost, u.cost_status, u.external_calls
        FROM public.usage_ledger u JOIN public.company_research_jobs j
        ON u.task_key='web-research:' || j.id::text WHERE u.quota_scope='platform_web'""",
    "lineage": """SELECT id, event_id, created_at, 'historical_observation'::text AS record_kind,
        raw_document_id::text AS source_record_id, processing_version AS version,
        observation_kind AS state, false AS current_support
        FROM public.event_observations
        UNION ALL SELECT id, event_id, created_at, 'evidence'::text,
        raw_document_id::text, span_hash, display_url_health_status,
        display_allowed AND display_license_status='public' AND display_url_health_status='healthy'
        AND encode(sha256(convert_to(evidence_excerpt,'UTF8')),'hex')=span_hash
        AND source_event_evidence_id IS NULL
        AND event_id IN (SELECT id FROM public.events WHERE status NOT IN ('rejected','retracted'))
        FROM public.event_evidence
        UNION ALL SELECT s.id, s.event_id, s.assessed_at, 'field_support'::text,
        s.event_evidence_id::text, s.policy_version, s.support_status,
        e.display_allowed AND e.display_license_status='public'
        AND e.display_url_health_status='healthy' AND s.support_status='supported'
        AND encode(sha256(convert_to(e.evidence_excerpt,'UTF8')),'hex')=e.span_hash
        AND e.source_event_evidence_id IS NULL
        AND s.event_id IN (SELECT id FROM public.events
        WHERE status NOT IN ('rejected','retracted'))
        FROM public.event_fact_supports s JOIN public.event_evidence e ON
        e.id=s.event_evidence_id""",
}


def bootstrap(database_admin_url, reader, password):
    owner = reader + "_views"
    if not ROLE_NAME_PATTERN.fullmatch(reader) or len(owner) > 63 or reader.startswith("pg_"):
        raise ValueError("invalid dedicated role")
    with psycopg.connect(**_connection_kwargs(database_admin_url)) as connection:
        with connection.cursor() as c:
            c.execute("SELECT 1 FROM pg_roles WHERE rolname IN (%s,%s)", (reader, owner))
            if c.fetchone():
                raise ValueError(
                    "use fresh diagnostic roles; existing privileges are not overwritten"
                )
            c.execute(
                sql.SQL(
                    "CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOBYPASSRLS"
                ).format(sql.Identifier(owner))
            )
            c.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOINHERIT NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOBYPASSRLS CONNECTION LIMIT 2"
                ).format(sql.Identifier(reader), sql.Literal(password))
            )
            c.execute(
                sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(
                    sql.Identifier(reader)
                )
            )
            c.execute(
                sql.SQL("ALTER ROLE {} SET statement_timeout = '2500ms'").format(
                    sql.Identifier(reader)
                )
            )
            c.execute(
                sql.SQL("ALTER ROLE {} SET lock_timeout = '500ms'").format(sql.Identifier(reader))
            )
            c.execute(
                sql.SQL("ALTER ROLE {} SET idle_in_transaction_session_timeout = '5000ms'").format(
                    sql.Identifier(reader)
                )
            )
            c.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(owner)))
            # 非登录视图所有者需读取旧 RLS 中的关联表；客户端没有角色成员资格或基础表权限。
            c.execute(
                sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(
                    sql.Identifier(owner)
                )
            )
            c.execute(
                sql.SQL("CREATE SCHEMA diagnostic AUTHORIZATION {}").format(sql.Identifier(owner))
            )
            scope = (
                "id::text = ANY(string_to_array("
                "current_setting('diagnostic.company_ids', true), ','))"
            )
            filters = {
                "companies": f"tenant_id IS NULL AND visibility_scope='public' AND ({scope})",
                "events": (
                    "visibility_scope='platform_shared' AND owner_user_id IS NULL AND "
                    "owner_tenant_id IS NULL AND company_id IN (SELECT id FROM "
                    "public.companies)"
                ),
                "company_research_jobs": "company_id IN (SELECT id FROM public.companies)",
                "usage_ledger": (
                    "quota_scope='platform_web' AND task_key IN "
                    "(SELECT 'web-research:' || id::text FROM public.company_research_jobs)"
                ),
                "event_evidence": (
                    "visibility_scope='platform_shared' AND owner_user_id IS NULL AND "
                    "owner_tenant_id IS NULL AND display_license_status='public' AND event_id "
                    "IN (SELECT id FROM public.events)"
                ),
                "event_observations": "event_id IN (SELECT id FROM public.events)",
                "event_fact_supports": (
                    "event_id IN (SELECT id FROM public.events) AND event_evidence_id IN "
                    "(SELECT id FROM public.event_evidence)"
                ),
            }
            for table, clause in filters.items():
                for kind in ("PERMISSIVE", "RESTRICTIVE"):
                    c.execute(
                        sql.SQL(
                            "CREATE POLICY {} ON public.{} AS "
                            + kind
                            + " FOR SELECT TO {} USING ("
                            + clause
                            + ")"
                        ).format(
                            sql.Identifier("diagnostic_" + kind.lower()),
                            sql.Identifier(table),
                            sql.Identifier(owner),
                        )
                    )
            for name, query in VIEWS.items():
                c.execute(
                    sql.SQL(
                        "CREATE VIEW diagnostic.{} WITH (security_barrier=true, "
                        "security_invoker=false) AS " + query
                    ).format(sql.Identifier(name))
                )
                c.execute(
                    sql.SQL("ALTER VIEW diagnostic.{} OWNER TO {}").format(
                        sql.Identifier(name), sql.Identifier(owner)
                    )
                )
            c.execute(sql.SQL("REVOKE ALL ON SCHEMA diagnostic FROM PUBLIC"))
            c.execute(
                sql.SQL("GRANT USAGE ON SCHEMA diagnostic TO {}").format(sql.Identifier(reader))
            )
            c.execute(
                sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA diagnostic TO {}").format(
                    sql.Identifier(reader)
                )
            )
    return {"status": "installed", "schema": "diagnostic", "reader": reader}


def main():
    import json

    print(
        json.dumps(
            bootstrap(
                os.environ["DIAGNOSTIC_ADMIN_URL"],
                os.environ["MCP_DATABASE_USER"],
                os.environ["MCP_DATABASE_PASSWORD"],
            )
        )
    )


if __name__ == "__main__":
    main()
