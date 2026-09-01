"""retire legacy business data provider

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_SOURCE_CODE = "tianyancha_licensed_business_data"
_RETIREMENT_REASON = "legacy_provider_retired"


def _current_user() -> str:
    return "NULLIF(current_setting('app.current_user_id', true), '')::uuid"


def _current_tenant() -> str:
    return "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def _active_user_clause() -> str:
    return f"""
        EXISTS (
            SELECT 1 FROM users AS current_scope_user
            WHERE current_scope_user.id = {_current_user()}
              AND current_scope_user.tenant_id = {_current_tenant()}
              AND current_scope_user.status = 'active'
        )
    """


def _platform_admin_clause() -> str:
    return f"""
        EXISTS (
            SELECT 1
            FROM user_role_assignments AS assignment
            JOIN roles ON roles.id = assignment.role_id
            JOIN users ON users.id = assignment.user_id
            WHERE assignment.user_id = {_current_user()}
              AND users.tenant_id = {_current_tenant()}
              AND users.status = 'active'
              AND roles.code = 'platform_admin'
              AND (assignment.valid_until IS NULL OR assignment.valid_until > CURRENT_TIMESTAMP)
        )
    """


def _legacy_support_conditions(
    metadata: sa.MetaData,
) -> tuple[sa.Table, sa.ColumnElement[bool], sa.ColumnElement[bool]]:
    events = sa.Table("events", metadata, autoload_with=op.get_bind())
    evidence = sa.Table("event_evidence", metadata, autoload_with=op.get_bind())
    parent_evidence = evidence.alias("source_event_evidence")
    documents = sa.Table("raw_documents", metadata, autoload_with=op.get_bind())
    sources = sa.Table("sources", metadata, autoload_with=op.get_bind())
    resolved_document_id = sa.func.coalesce(
        evidence.c.raw_document_id,
        parent_evidence.c.raw_document_id,
    )
    evidence_sources = (
        evidence.outerjoin(
            parent_evidence,
            parent_evidence.c.id == evidence.c.source_event_evidence_id,
        )
        .join(documents, documents.c.id == resolved_document_id)
        .join(sources, sources.c.id == documents.c.source_id)
    )
    has_legacy_support = sa.exists(
        sa.select(sa.literal(1))
        .select_from(evidence_sources)
        .where(
            evidence.c.event_id == events.c.id,
            sources.c.code == _LEGACY_SOURCE_CODE,
        )
    )
    has_independent_support = sa.exists(
        sa.select(sa.literal(1))
        .select_from(evidence_sources)
        .where(
            evidence.c.event_id == events.c.id,
            sources.c.code != _LEGACY_SOURCE_CODE,
        )
    )
    return events, has_legacy_support, has_independent_support


def _retract_legacy_only_events(metadata: sa.MetaData) -> None:
    bind = op.get_bind()
    events, has_legacy_support, has_independent_support = _legacy_support_conditions(metadata)
    rows = list(
        bind.execute(
            sa.select(events.c.id, events.c.publication_reasons).where(
                events.c.status != "retracted",
                has_legacy_support,
                ~has_independent_support,
            )
        ).mappings()
    )
    for row in rows:
        reasons = list(row["publication_reasons"] or [])
        if _RETIREMENT_REASON not in reasons:
            reasons.append(_RETIREMENT_REASON)
        bind.execute(
            sa.update(events)
            .where(events.c.id == row["id"])
            .values(
                status="retracted",
                publication_reasons=reasons,
                updated_at=datetime.now().astimezone(),
            )
        )


def _hide_legacy_evidence(metadata: sa.MetaData) -> None:
    bind = op.get_bind()
    evidence = sa.Table("event_evidence", metadata, autoload_with=bind)
    candidate_evidence = evidence.alias("candidate_event_evidence")
    parent_evidence = evidence.alias("candidate_source_event_evidence")
    documents = sa.Table("raw_documents", metadata, autoload_with=bind)
    sources = sa.Table("sources", metadata, autoload_with=bind)
    resolved_document_id = sa.func.coalesce(
        candidate_evidence.c.raw_document_id,
        parent_evidence.c.raw_document_id,
    )
    legacy_evidence_ids = (
        sa.select(candidate_evidence.c.id)
        .select_from(
            candidate_evidence.outerjoin(
                parent_evidence,
                parent_evidence.c.id == candidate_evidence.c.source_event_evidence_id,
            )
            .join(documents, documents.c.id == resolved_document_id)
            .join(sources, sources.c.id == documents.c.source_id)
        )
        .where(sources.c.code == _LEGACY_SOURCE_CODE)
    )
    bind.execute(
        sa.update(evidence)
        .where(evidence.c.id.in_(legacy_evidence_ids))
        .values(display_allowed=False)
    )


def _downgrade_legacy_only_identities(metadata: sa.MetaData) -> None:
    companies = sa.Table("companies", metadata, autoload_with=op.get_bind())
    verifications = sa.Table(
        "official_identity_verifications",
        metadata,
        autoload_with=op.get_bind(),
    )
    has_independent_verification = sa.exists(
        sa.select(sa.literal(1)).where(
            verifications.c.company_id == companies.c.id,
            verifications.c.verification_basis == "official_government",
            verifications.c.verification_status == "verified",
        )
    )
    op.get_bind().execute(
        sa.update(companies)
        .where(
            companies.c.identity_status == "verified",
            companies.c.identity_verification_basis == "licensed_business_data",
            has_independent_verification,
        )
        .values(
            identity_verification_basis="official_government",
            updated_at=datetime.now().astimezone(),
        )
    )
    op.get_bind().execute(
        sa.update(companies)
        .where(
            companies.c.identity_status == "verified",
            companies.c.identity_verification_basis == "licensed_business_data",
            ~has_independent_verification,
        )
        .values(identity_status="unresolved", updated_at=datetime.now().astimezone())
    )


def _park_legacy_requests(metadata: sa.MetaData) -> None:
    bind = op.get_bind()
    requests = sa.Table("personal_company_requests", metadata, autoload_with=bind)
    jobs = sa.Table("company_research_jobs", metadata, autoload_with=bind)
    legacy_job_ids = sa.select(jobs.c.id).where(jobs.c.policy_version.like("on-demand-research%"))
    bind.execute(
        sa.update(requests)
        .where(
            requests.c.status.in_(
                [
                    "pending",
                    "in_review",
                    "identity_queued",
                    "identity_checking",
                    "awaiting_confirmation",
                    "research_queued",
                    "researching",
                    "partial",
                    "budget_deferred",
                    "cancel_requested",
                ]
            ),
            sa.or_(
                requests.c.provider_company_id.is_not(None),
                requests.c.research_job_id.in_(legacy_job_ids),
            ),
        )
        .values(
            status="pending",
            research_job_id=None,
            confirmation_expires_at=None,
            leased_until=None,
            heartbeat_at=None,
            last_error_code=_RETIREMENT_REASON,
            updated_at=datetime.now().astimezone(),
        )
    )
    bind.execute(
        sa.update(requests)
        .where(
            requests.c.status == "completed",
            sa.or_(
                requests.c.provider_company_id.is_not(None),
                requests.c.research_job_id.in_(legacy_job_ids),
            ),
        )
        .values(
            status="failed",
            research_job_id=None,
            confirmation_expires_at=None,
            leased_until=None,
            heartbeat_at=None,
            last_error_code=_RETIREMENT_REASON,
            updated_at=datetime.now().astimezone(),
        )
    )
    bind.execute(
        sa.update(jobs)
        .where(
            jobs.c.policy_version.like("on-demand-research%"),
            jobs.c.status.in_(["queued", "running", "partial", "budget_deferred"]),
        )
        .values(
            status="cancelled",
            current_stage="provider_retired",
            last_error_code=_RETIREMENT_REASON,
            cancel_requested_at=datetime.now().astimezone(),
            cancelled_at=datetime.now().astimezone(),
            leased_until=None,
            heartbeat_at=None,
            updated_at=datetime.now().astimezone(),
        )
    )


def _drop_legacy_policies() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for policy, table in (
        ("usage_ledger_tianyancha_platform_admin_read", "usage_ledger"),
        ("raw_documents_tianyancha_admin_read", "raw_documents"),
        ("raw_documents_tianyancha_admin_insert", "raw_documents"),
        ("official_identity_verifications_platform_admin_read", "official_identity_verifications"),
        (
            "official_identity_verifications_platform_admin_insert",
            "official_identity_verifications",
        ),
        (
            "official_identity_verifications_platform_admin_update",
            "official_identity_verifications",
        ),
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")


def upgrade() -> None:
    if op.get_context().as_sql:
        _drop_legacy_policies()
        return
    metadata = sa.MetaData()
    _retract_legacy_only_events(metadata)
    _hide_legacy_evidence(metadata)
    _downgrade_legacy_only_identities(metadata)
    _park_legacy_requests(metadata)
    _drop_legacy_policies()


def _restore_legacy_policies() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    active_user = _active_user_clause()
    platform_admin = _platform_admin_clause()
    op.execute(
        "CREATE POLICY usage_ledger_tianyancha_platform_admin_read "
        "ON usage_ledger FOR SELECT "
        f"USING (({active_user}) AND ({platform_admin}) "
        "AND provider LIKE 'tianyancha%')"
    )
    legacy_document = (
        "raw_documents.visibility_scope = 'system_restricted' "
        "AND raw_documents.owner_user_id IS NULL "
        "AND raw_documents.owner_tenant_id IS NULL "
        "AND EXISTS (SELECT 1 FROM sources "
        "WHERE sources.id = raw_documents.source_id "
        f"AND sources.code = '{_LEGACY_SOURCE_CODE}')"
    )
    op.execute(
        "CREATE POLICY raw_documents_tianyancha_admin_read ON raw_documents FOR SELECT "
        f"USING (({active_user}) AND ({platform_admin}) AND ({legacy_document}))"
    )
    op.execute(
        "CREATE POLICY raw_documents_tianyancha_admin_insert ON raw_documents FOR INSERT "
        f"WITH CHECK (({active_user}) AND ({platform_admin}) AND ({legacy_document}))"
    )
    op.execute(
        "CREATE POLICY official_identity_verifications_platform_admin_read "
        "ON official_identity_verifications FOR SELECT "
        f"USING (({active_user}) AND ({platform_admin}) "
        "AND verification_basis = 'licensed_business_data')"
    )
    op.execute(
        "CREATE POLICY official_identity_verifications_platform_admin_insert "
        "ON official_identity_verifications FOR INSERT "
        f"WITH CHECK (({active_user}) AND ({platform_admin}) "
        f"AND tenant_id = {_current_tenant()} "
        "AND verification_basis = 'licensed_business_data')"
    )
    op.execute(
        "CREATE POLICY official_identity_verifications_platform_admin_update "
        "ON official_identity_verifications FOR UPDATE "
        f"USING (({active_user}) AND ({platform_admin}) "
        f"AND tenant_id = {_current_tenant()} "
        "AND verification_basis = 'licensed_business_data') "
        f"WITH CHECK (({active_user}) AND ({platform_admin}) "
        f"AND tenant_id = {_current_tenant()} "
        "AND verification_basis = 'licensed_business_data')"
    )


def downgrade() -> None:
    # 数据降级不会重新发布已撤下事实，也不会重新确认旧身份；只恢复旧版本所需的策略形状。
    _restore_legacy_policies()
