from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class Tenant(TimestampMixin, Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="active")


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32), default="active")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Role(TimestampMixin, Base):
    __tablename__ = "roles"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    scope_type: Mapped[str] = mapped_column(String(32), default="tenant")


class UserRoleAssignment(TimestampMixin, Base):
    __tablename__ = "user_role_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", "scope_id", name="uq_user_role_scope"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role_id: Mapped[UUID] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"))
    scope_id: Mapped[UUID | None] = mapped_column(Uuid)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Fund(TimestampMixin, Base):
    __tablename__ = "funds"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_funds_tenant_code"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="active")
    visibility_scope: Mapped[str] = mapped_column(String(16), default="fund")


class FundAccessGrant(TimestampMixin, Base):
    __tablename__ = "fund_access_grants"
    __table_args__ = (UniqueConstraint("user_id", "fund_id", name="uq_fund_access_user_fund"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    fund_id: Mapped[UUID] = mapped_column(ForeignKey("funds.id", ondelete="CASCADE"))
    permission: Mapped[str] = mapped_column(String(32), default="read")
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID | None] = mapped_column(ForeignKey("tenants.id"))
    credit_code: Mapped[str | None] = mapped_column(String(32), unique=True)
    legal_name: Mapped[str] = mapped_column(String(240), index=True)
    registered_region: Mapped[str | None] = mapped_column(String(120))
    official_website: Mapped[str | None] = mapped_column(String(500))
    identity_status: Mapped[str] = mapped_column(String(32), default="unresolved")
    last_identity_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    visibility_scope: Mapped[str] = mapped_column(String(16), default="public")


class Source(TimestampMixin, Base):
    __tablename__ = "sources"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    source_quality: Mapped[str] = mapped_column(String(1))
    license_status: Mapped[str] = mapped_column(String(32))
    base_url: Mapped[str | None] = mapped_column(String(500))


class CompanyAlias(TimestampMixin, Base):
    __tablename__ = "company_aliases"
    __table_args__ = (
        UniqueConstraint("company_id", "normalized_alias", "alias_type", name="uq_company_alias"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    source_id: Mapped[UUID | None] = mapped_column(ForeignKey("sources.id"))
    alias: Mapped[str] = mapped_column(String(240))
    normalized_alias: Mapped[str] = mapped_column(String(240), index=True)
    alias_type: Mapped[str] = mapped_column(String(32))
    verification_status: Mapped[str] = mapped_column(String(32), default="verified")


class Investment(TimestampMixin, Base):
    __tablename__ = "investments"
    __table_args__ = (
        UniqueConstraint("fund_id", "company_id", "round_name", name="uq_investment_round"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    fund_id: Mapped[UUID] = mapped_column(ForeignKey("funds.id", ondelete="CASCADE"))
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    round_name: Mapped[str] = mapped_column(String(80), default="demo_round")
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    ownership: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    internal_valuation: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    visibility_scope: Mapped[str] = mapped_column(String(16), default="fund")


class ResearchImport(TimestampMixin, Base):
    __tablename__ = "research_imports"
    __table_args__ = (
        UniqueConstraint("tenant_id", "batch_id", name="uq_research_import_batch"),
        UniqueConstraint(
            "tenant_id",
            "file_hash",
            "parser_version",
            name="uq_research_import_file_parser",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    imported_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    schema_version: Mapped[str] = mapped_column(String(16))
    batch_id: Mapped[str] = mapped_column(String(120))
    queried_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    research_tool: Mapped[str] = mapped_column(String(80))
    agent_name: Mapped[str | None] = mapped_column(String(120))
    original_query: Mapped[str] = mapped_column(Text)
    target_company_hint: Mapped[str] = mapped_column(String(240))
    source_filename: Mapped[str] = mapped_column(String(255))
    file_format: Mapped[str] = mapped_column(String(16), default="json")
    file_hash: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(16))
    license_status: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    record_count: Mapped[int] = mapped_column(Integer)
    resolved_count: Mapped[int] = mapped_column(Integer)
    unresolved_count: Mapped[int] = mapped_column(Integer)
    auto_published_count: Mapped[int] = mapped_column(Integer, default=0)
    unconfirmed_count: Mapped[int] = mapped_column(Integer, default=0)
    identity_review_count: Mapped[int] = mapped_column(Integer, default=0)


class RawDocument(TimestampMixin, Base):
    __tablename__ = "raw_documents"
    __table_args__ = (
        UniqueConstraint("source_id", "external_record_id", name="uq_document_source_record"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id"))
    research_import_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("research_imports.id", ondelete="SET NULL"), index=True
    )
    external_record_id: Mapped[str] = mapped_column(String(160))
    canonical_url: Mapped[str] = mapped_column(String(1000))
    title: Mapped[str] = mapped_column(String(500))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_on: Mapped[date | None] = mapped_column(Date)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    document_dedupe_key: Mapped[str] = mapped_column(String(64), unique=True)
    license_status: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class EntityMention(TimestampMixin, Base):
    __tablename__ = "entity_mentions"
    __table_args__ = (
        UniqueConstraint(
            "raw_document_id", "candidate_company_id", "mention_text", name="uq_entity_mention"
        ),
        CheckConstraint(
            "match_confidence >= 0 AND match_confidence <= 1", name="ck_match_confidence"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    raw_document_id: Mapped[UUID] = mapped_column(
        ForeignKey("raw_documents.id", ondelete="CASCADE")
    )
    candidate_company_id: Mapped[UUID | None] = mapped_column(ForeignKey("companies.id"))
    mention_text: Mapped[str] = mapped_column(String(240))
    match_rule: Mapped[str] = mapped_column(String(80))
    match_confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    resolution_status: Mapped[str] = mapped_column(String(32))


class OfficialIdentityVerification(TimestampMixin, Base):
    __tablename__ = "official_identity_verifications"
    __table_args__ = (
        UniqueConstraint("raw_document_id", name="uq_official_identity_document"),
        CheckConstraint(
            "verification_status IN ('verified', 'conflict', 'unmatched')",
            name="ck_official_identity_status",
        ),
        Index(
            "ix_official_identity_tenant_status_checked",
            "tenant_id",
            "verification_status",
            "checked_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    company_id: Mapped[UUID | None] = mapped_column(ForeignKey("companies.id"), index=True)
    raw_document_id: Mapped[UUID] = mapped_column(
        ForeignKey("raw_documents.id", ondelete="CASCADE")
    )
    query_text: Mapped[str] = mapped_column(String(240), index=True)
    legal_name: Mapped[str] = mapped_column(String(240))
    credit_code: Mapped[str] = mapped_column(String(18), index=True)
    registered_region: Mapped[str | None] = mapped_column(String(120))
    registration_status: Mapped[str] = mapped_column(String(64))
    verification_status: Mapped[str] = mapped_column(String(32))
    match_rule: Mapped[str] = mapped_column(String(80))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Event(TimestampMixin, Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "fingerprint_version", "event_fingerprint", name="uq_event_fingerprint"
        ),
        CheckConstraint(
            "materiality_score >= 0 AND materiality_score <= 100",
            name="ck_event_materiality",
        ),
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="ck_event_confidence",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    event_subtype: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="candidate", index=True)
    direction: Mapped[str] = mapped_column(String(16))
    materiality_score: Mapped[int] = mapped_column(Integer)
    risk_severity: Mapped[str] = mapped_column(String(16))
    confidence_score: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    source_quality: Mapped[str] = mapped_column(String(1))
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text)
    facts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    uncertainties: Mapped[list[str]] = mapped_column(JSON, default=list)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_on: Mapped[date | None] = mapped_column(Date)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    fingerprint_version: Mapped[str] = mapped_column(String(16), default="1")
    event_fingerprint: Mapped[str] = mapped_column(String(64))
    publication_route: Mapped[str] = mapped_column(String(32), default="unconfirmed_lead")
    publication_policy_version: Mapped[str] = mapped_column(String(32), default="legacy-v1")
    publication_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)


class EventEvidence(TimestampMixin, Base):
    __tablename__ = "event_evidence"
    __table_args__ = (
        UniqueConstraint("event_id", "raw_document_id", "span_hash", name="uq_event_evidence"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    raw_document_id: Mapped[UUID] = mapped_column(
        ForeignKey("raw_documents.id", ondelete="CASCADE")
    )
    evidence_excerpt: Mapped[str] = mapped_column(Text)
    span_hash: Mapped[str] = mapped_column(String(64))
    support_type: Mapped[str] = mapped_column(String(32), default="supports")


class CompanySnapshot(TimestampMixin, Base):
    __tablename__ = "company_snapshots"
    __table_args__ = (
        UniqueConstraint("company_id", "snapshot_version", name="uq_company_snapshot_version"),
        Index(
            "uq_company_snapshot_current",
            "company_id",
            unique=True,
            postgresql_where=text("is_current"),
            sqlite_where=text("is_current = 1"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    snapshot_version: Mapped[int] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    data_as_of: Mapped[date | None] = mapped_column(Date)
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    freshness_status: Mapped[str] = mapped_column(String(32))
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    information_gaps: Mapped[list[str]] = mapped_column(JSON, default=list)


class RefreshJob(TimestampMixin, Base):
    __tablename__ = "refresh_jobs"
    __table_args__ = (
        Index(
            "uq_refresh_job_active",
            "tenant_id",
            "company_id",
            "job_type",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    job_type: Mapped[str] = mapped_column(String(32), default="mock_refresh")
    refresh_reason: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewQueue(TimestampMixin, Base):
    __tablename__ = "review_queue"
    __table_args__ = (
        UniqueConstraint(
            "entity_mention_id",
            name="uq_review_queue_entity_mention",
        ),
        CheckConstraint(
            "(event_id IS NOT NULL AND entity_mention_id IS NULL) OR "
            "(event_id IS NULL AND entity_mention_id IS NOT NULL)",
            name="ck_review_queue_subject",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), unique=True
    )
    entity_mention_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("entity_mentions.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    trigger_rules: Mapped[list[str]] = mapped_column(JSON, default=list)
    assigned_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str | None] = mapped_column(String(32))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageLedger(TimestampMixin, Base):
    __tablename__ = "usage_ledger"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    company_id: Mapped[UUID | None] = mapped_column(ForeignKey("companies.id"))
    provider: Mapped[str] = mapped_column(String(80))
    operation: Mapped[str] = mapped_column(String(80))
    external_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
