"""Allow typed matter observations while keeping raw documents restricted."""

import importlib

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    ledger = importlib.import_module("migrations.versions.0024_add_evidence_fact_support_ledger")
    allowed = f"""
        event_observations.schema_version='matter-v1'
        AND ({ledger._active_role_clause(("platform_admin",))})
        AND EXISTS (
          SELECT 1 FROM events e
          JOIN raw_documents d ON d.id=event_observations.raw_document_id
          JOIN sources s ON s.id=d.source_id
          JOIN event_evidence v ON v.event_id=e.id AND v.raw_document_id=d.id
          JOIN entity_mentions m ON m.raw_document_id=d.id AND m.candidate_company_id=e.company_id
          WHERE e.id=event_observations.event_id
            AND e.visibility_scope='platform_shared'
            AND e.event_type IN ('financing_cap_table','exit_liquidity', 'contract_commercial',
              'product_technology','financial_operation','governance_people',
              'legal_compliance','capacity_assets')
            AND e.owner_user_id IS NULL AND e.owner_tenant_id IS NULL
            AND d.visibility_scope='system_restricted' AND d.license_status='public'
            AND d.owner_user_id IS NULL AND d.owner_tenant_id IS NULL
            AND s.code='bounded_public_web' AND s.license_status='public'
            AND m.resolution_status='verified'
            AND v.visibility_scope='platform_shared'
        )
    """
    # 撤证后管理员仍须读取历史观测防止重试重建；新增观测要求可展示的健康证据。
    op.execute(
        "CREATE POLICY event_observations_matter_read ON event_observations "
        f"FOR SELECT USING ({allowed})"
    )
    op.execute(
        "CREATE POLICY event_observations_matter_insert ON event_observations "
        f"FOR INSERT WITH CHECK (({allowed}) AND created_by={ledger._current_user()} "
        "AND EXISTS (SELECT 1 FROM event_evidence v "
        "WHERE v.event_id=event_observations.event_id "
        "AND v.raw_document_id=event_observations.raw_document_id "
        "AND v.visibility_scope='platform_shared' AND v.display_allowed "
        "AND v.display_license_status='public' AND v.display_url_health_status='healthy'))"
    )


def downgrade():
    if op.get_context().as_sql or op.get_bind().scalar(
        sa.text("SELECT count(*) FROM event_observations WHERE schema_version='matter-v1'")
    ):
        raise RuntimeError("Retain matter observations and their access policy")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP POLICY event_observations_matter_insert ON event_observations")
        op.execute("DROP POLICY event_observations_matter_read ON event_observations")
