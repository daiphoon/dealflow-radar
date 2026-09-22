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
    ForeignKeyConstraint,
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


PLATFORM_SHARED_SCOPE = "platform_shared"
PERSONAL_PRIVATE_SCOPE = "personal_private"
ORGANIZATION_PRIVATE_SCOPE = "organization_private"
SYSTEM_RESTRICTED_SCOPE = "system_restricted"

SCOPED_OWNER_CHECK = (
    "(visibility_scope = 'platform_shared' "
    "AND owner_user_id IS NULL AND owner_tenant_id IS NULL) OR "
    "(visibility_scope = 'personal_private' "
    "AND owner_user_id IS NOT NULL AND owner_tenant_id IS NULL) OR "
    "(visibility_scope = 'organization_private' "
    "AND owner_user_id IS NULL AND owner_tenant_id IS NOT NULL) OR "
    "(visibility_scope = 'system_restricted' "
    "AND owner_user_id IS NULL AND owner_tenant_id IS NULL)"
)


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
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        Index(
            "uq_users_auth_identity",
            "auth_provider",
            "auth_subject",
            unique=True,
            postgresql_where=text("auth_provider IS NOT NULL AND auth_subject IS NOT NULL"),
            sqlite_where=text("auth_provider IS NOT NULL AND auth_subject IS NOT NULL"),
        ),
        CheckConstraint(
            "(auth_provider IS NULL AND auth_subject IS NULL) OR "
            "(auth_provider = 'cloudbase' AND auth_subject IS NOT NULL)",
            name="ck_user_auth_identity_pair",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(32), default="active")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auth_provider: Mapped[str | None] = mapped_column(String(32))
    auth_subject: Mapped[str | None] = mapped_column(String(255))


class AuthenticationAuditLog(Base):
    __tablename__ = "authentication_audit_logs"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('identity_linked', 'session_started', "
            "'session_refreshed', 'session_ended')",
            name="ck_authentication_audit_event_type",
        ),
        CheckConstraint(
            "outcome IN ('succeeded', 'failed')",
            name="ck_authentication_audit_outcome",
        ),
        Index(
            "ix_authentication_audit_tenant_created",
            "tenant_id",
            "created_at",
        ),
        Index(
            "ix_authentication_audit_user_created",
            "user_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(32))
    subject_hash: Mapped[str] = mapped_column(String(64))
    event_type: Mapped[str] = mapped_column(String(32))
    outcome: Mapped[str] = mapped_column(String(16))
    reason_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


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
    __table_args__ = (
        CheckConstraint(
            "identity_verification_basis IS NULL OR identity_verification_basis IN "
            "('official_government', 'licensed_business_data', 'exchange_disclosure', "
            "'public_crosscheck', 'curator_confirmed')",
            name="ck_company_identity_verification_basis",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID | None] = mapped_column(ForeignKey("tenants.id"))
    credit_code: Mapped[str | None] = mapped_column(String(32), unique=True)
    legal_name: Mapped[str] = mapped_column(String(240), index=True)
    registered_region: Mapped[str | None] = mapped_column(String(120))
    official_website: Mapped[str | None] = mapped_column(String(500))
    identity_status: Mapped[str] = mapped_column(String(32), default="unresolved")
    identity_verification_basis: Mapped[str | None] = mapped_column(String(32))
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
        Index(
            "uq_company_alias_platform_shared",
            "company_id",
            "normalized_alias",
            "alias_type",
            unique=True,
            postgresql_where=text("visibility_scope = 'platform_shared'"),
            sqlite_where=text("visibility_scope = 'platform_shared'"),
        ),
        Index(
            "uq_company_alias_personal_private",
            "company_id",
            "owner_user_id",
            "normalized_alias",
            "alias_type",
            unique=True,
            postgresql_where=text("visibility_scope = 'personal_private'"),
            sqlite_where=text("visibility_scope = 'personal_private'"),
        ),
        Index(
            "uq_company_alias_organization_private",
            "company_id",
            "owner_tenant_id",
            "normalized_alias",
            "alias_type",
            unique=True,
            postgresql_where=text("visibility_scope = 'organization_private'"),
            sqlite_where=text("visibility_scope = 'organization_private'"),
        ),
        Index(
            "uq_company_alias_system_restricted",
            "company_id",
            "normalized_alias",
            "alias_type",
            unique=True,
            postgresql_where=text("visibility_scope = 'system_restricted'"),
            sqlite_where=text("visibility_scope = 'system_restricted'"),
        ),
        CheckConstraint(SCOPED_OWNER_CHECK, name="ck_company_alias_scope_owner"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    source_id: Mapped[UUID | None] = mapped_column(ForeignKey("sources.id"))
    owner_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    owner_tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    visibility_scope: Mapped[str] = mapped_column(String(32), default=SYSTEM_RESTRICTED_SCOPE)
    alias: Mapped[str] = mapped_column(String(240))
    normalized_alias: Mapped[str] = mapped_column(String(240), index=True)
    alias_type: Mapped[str] = mapped_column(String(32))
    verification_status: Mapped[str] = mapped_column(String(32), default="verified")


class PersonalWatchlistItem(TimestampMixin, Base):
    __tablename__ = "personal_watchlist_items"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "company_id",
            name="uq_personal_watchlist_owner_company",
        ),
        Index("ix_personal_watchlist_owner_created", "owner_user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )


class PersonalCompanyRequest(TimestampMixin, Base):
    __tablename__ = "personal_company_requests"
    __table_args__ = (
        CheckConstraint(
            "request_type IN ('inclusion', 'refresh')",
            name="ck_personal_company_request_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'in_review', 'identity_queued', "
            "'identity_checking', 'awaiting_confirmation', 'needs_input', "
            "'research_queued', 'researching', 'partial', 'budget_deferred', "
            "'cancel_requested', 'cancelled', 'completed', 'rejected', 'failed')",
            name="ck_personal_company_request_status",
        ),
        CheckConstraint(
            "(request_type = 'inclusion' "
            "AND (requested_name IS NOT NULL OR requested_credit_code IS NOT NULL)) OR "
            "(request_type = 'refresh' AND "
            "(company_id IS NOT NULL OR requested_name IS NOT NULL "
            "OR requested_credit_code IS NOT NULL))",
            name="ck_personal_company_request_target",
        ),
        Index(
            "uq_personal_company_request_active_target",
            "owner_user_id",
            "target_key",
            unique=True,
            postgresql_where=text(
                "status IN ('pending', 'in_review', 'identity_queued', "
                "'identity_checking', 'awaiting_confirmation', 'research_queued', "
                "'researching', 'partial', 'budget_deferred', 'cancel_requested')"
            ),
            sqlite_where=text(
                "status IN ('pending', 'in_review', 'identity_queued', "
                "'identity_checking', 'awaiting_confirmation', 'research_queued', "
                "'researching', 'partial', 'budget_deferred', 'cancel_requested')"
            ),
        ),
        Index(
            "ix_personal_company_request_owner_created",
            "owner_user_id",
            "created_at",
        ),
        Index("ix_personal_company_request_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    request_type: Mapped[str] = mapped_column(String(16))
    company_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"), index=True
    )
    requested_name: Mapped[str | None] = mapped_column(String(240))
    requested_credit_code: Mapped[str | None] = mapped_column(String(32))
    target_key: Mapped[str] = mapped_column(String(280))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    research_job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("company_research_jobs.id", ondelete="SET NULL"), index=True
    )
    resolved_legal_name: Mapped[str | None] = mapped_column(String(240))
    resolved_credit_code: Mapped[str | None] = mapped_column(String(18))
    resolved_registered_region: Mapped[str | None] = mapped_column(String(120))
    resolved_registration_status: Mapped[str | None] = mapped_column(String(64))
    resolved_registration_authority: Mapped[str | None] = mapped_column(String(240))
    provider_company_id: Mapped[str | None] = mapped_column(String(80))
    identity_response_hash: Mapped[str | None] = mapped_column(String(64))
    identity_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmation_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_calls: Mapped[int] = mapped_column(Integer, default=0)
    cache_hits: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_stage: Mapped[str | None] = mapped_column(String(64))
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    reviewed_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)


class IdentityResearchState(TimestampMixin, Base):
    """Platform-only evidence and resumable progress; never serialized to request owners."""

    __tablename__ = "identity_research_states"

    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("personal_company_requests.id", ondelete="CASCADE"), primary_key=True
    )
    progress: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class PersonalUsageRecord(Base):
    __tablename__ = "personal_usage_records"
    __table_args__ = (
        CheckConstraint(
            "operation IN ('company_search', 'company_request', 'company_report')",
            name="ck_personal_usage_operation",
        ),
        Index(
            "ix_personal_usage_owner_period_operation",
            "owner_user_id",
            "period_key",
            "operation",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    operation: Mapped[str] = mapped_column(String(32))
    period_key: Mapped[str] = mapped_column(String(7))
    resource_id: Mapped[UUID | None] = mapped_column(Uuid)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PersonalQuotaIncreaseRequest(TimestampMixin, Base):
    __tablename__ = "personal_quota_increase_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired')",
            name="ck_personal_quota_increase_status",
        ),
        CheckConstraint(
            "requested_daily_extra > 0 OR requested_monthly_extra > 0",
            name="ck_personal_quota_increase_requested_positive",
        ),
        CheckConstraint(
            "approved_daily_extra >= 0 AND approved_monthly_extra >= 0",
            name="ck_personal_quota_increase_approved_non_negative",
        ),
        Index(
            "uq_personal_quota_increase_owner_pending",
            "owner_user_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
        Index(
            "ix_personal_quota_increase_status_created",
            "status",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    requested_daily_extra: Mapped[int] = mapped_column(Integer, default=0)
    requested_monthly_extra: Mapped[int] = mapped_column(Integer, default=0)
    request_reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    approved_daily_extra: Mapped[int] = mapped_column(Integer, default=0)
    approved_monthly_extra: Mapped[int] = mapped_column(Integer, default=0)
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)


class CompanyResearchJob(TimestampMixin, Base):
    __tablename__ = "company_research_jobs"
    __table_args__ = (
        CheckConstraint("trigger_type IN ('manual', 'watchlist')", name="ck_research_job_trigger"),
        CheckConstraint(
            "status IN ('queued', 'running', 'partial', 'budget_deferred', "
            "'completed', 'cancelled', 'failed')",
            name="ck_company_research_job_status",
        ),
        Index(
            "uq_company_research_job_active",
            "company_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running', 'partial', 'budget_deferred')"),
            sqlite_where=text("status IN ('queued', 'running', 'partial', 'budget_deferred')"),
        ),
        Index("ix_company_research_job_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    created_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    current_stage: Mapped[str] = mapped_column(String(64), default="queued")
    policy_version: Mapped[str] = mapped_column(String(64))
    trigger_type: Mapped[str] = mapped_column(String(16), default="manual", server_default="manual")
    coverage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    external_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(80))


class CompanyWatchSchedule(TimestampMixin, Base):
    __tablename__ = "company_watch_schedules"
    __table_args__ = (
        CheckConstraint(
            "consecutive_failures >= 0 AND consecutive_no_change_runs >= 0",
            name="ck_watch_schedule_counts",
        ),
        Index("ix_watch_schedule_due", "next_check_at"),
    )

    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id"), primary_key=True)
    policy_version: Mapped[str] = mapped_column(String(64))
    next_check_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_job_id: Mapped[UUID | None] = mapped_column(ForeignKey("company_research_jobs.id"))
    last_outcome_key: Mapped[str | None] = mapped_column(String(128))
    last_outcome: Mapped[str] = mapped_column(String(32), default="never_checked")
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_no_change_runs: Mapped[int] = mapped_column(Integer, default=0)


class WebSearchCacheEntry(TimestampMixin, Base):
    __tablename__ = "web_search_cache_entries"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "provider_code",
            "query_hash",
            "identity_fingerprint",
            name="uq_web_search_cache_identity_query",
        ),
        Index("ix_web_search_cache_company_expires", "company_id", "expires_at"),
        Index("ix_web_search_cache_provider_fetched", "provider_code", "fetched_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    provider_code: Mapped[str] = mapped_column(String(32))
    query_kind: Mapped[str] = mapped_column(String(64))
    query_text: Mapped[str] = mapped_column(Text)
    query_hash: Mapped[str] = mapped_column(String(64))
    identity_fingerprint: Mapped[str] = mapped_column(String(64))
    response_hash: Mapped[str] = mapped_column(String(64))
    results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PersonalCompanyViewState(TimestampMixin, Base):
    __tablename__ = "personal_company_view_states"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "company_id",
            name="uq_personal_company_view_owner_company",
        ),
        Index(
            "ix_personal_company_view_owner_updated",
            "owner_user_id",
            "updated_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    last_viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PersonalEventViewReceipt(Base):
    __tablename__ = "personal_event_view_receipts"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "event_id",
            name="uq_personal_event_view_owner_event",
        ),
        Index(
            "ix_personal_event_view_owner_seen",
            "owner_user_id",
            "first_seen_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PersonalCompanyReport(Base):
    __tablename__ = "personal_company_reports"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "idempotency_key",
            name="uq_personal_company_report_owner_idempotency",
        ),
        Index(
            "ix_personal_company_report_owner_created",
            "owner_user_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    company_id: Mapped[UUID] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    company_legal_name: Mapped[str] = mapped_column(String(240))
    report_version: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(280))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    markdown: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    source_event_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


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
            "selection_key",
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
    selection_key: Mapped[str] = mapped_column(String(64), default="", server_default="")
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
        Index(
            "uq_raw_doc_source_record_platform_shared",
            "source_id",
            "external_record_id",
            unique=True,
            postgresql_where=text("visibility_scope = 'platform_shared'"),
            sqlite_where=text("visibility_scope = 'platform_shared'"),
        ),
        Index(
            "uq_raw_doc_source_record_personal_private",
            "source_id",
            "owner_user_id",
            "external_record_id",
            unique=True,
            postgresql_where=text("visibility_scope = 'personal_private'"),
            sqlite_where=text("visibility_scope = 'personal_private'"),
        ),
        Index(
            "uq_raw_doc_source_record_organization_private",
            "source_id",
            "owner_tenant_id",
            "external_record_id",
            unique=True,
            postgresql_where=text("visibility_scope = 'organization_private'"),
            sqlite_where=text("visibility_scope = 'organization_private'"),
        ),
        Index(
            "uq_raw_doc_source_record_system_restricted",
            "source_id",
            "external_record_id",
            unique=True,
            postgresql_where=text("visibility_scope = 'system_restricted'"),
            sqlite_where=text("visibility_scope = 'system_restricted'"),
        ),
        Index(
            "uq_raw_doc_dedupe_platform_shared",
            "document_dedupe_key",
            unique=True,
            postgresql_where=text("visibility_scope = 'platform_shared'"),
            sqlite_where=text("visibility_scope = 'platform_shared'"),
        ),
        Index(
            "uq_raw_doc_dedupe_personal_private",
            "owner_user_id",
            "document_dedupe_key",
            unique=True,
            postgresql_where=text("visibility_scope = 'personal_private'"),
            sqlite_where=text("visibility_scope = 'personal_private'"),
        ),
        Index(
            "uq_raw_doc_dedupe_organization_private",
            "owner_tenant_id",
            "document_dedupe_key",
            unique=True,
            postgresql_where=text("visibility_scope = 'organization_private'"),
            sqlite_where=text("visibility_scope = 'organization_private'"),
        ),
        Index(
            "uq_raw_doc_dedupe_system_restricted",
            "document_dedupe_key",
            unique=True,
            postgresql_where=text("visibility_scope = 'system_restricted'"),
            sqlite_where=text("visibility_scope = 'system_restricted'"),
        ),
        Index(
            "uq_raw_document_candidate_handoff",
            "candidate_document_id",
            unique=True,
        ),
        CheckConstraint(SCOPED_OWNER_CHECK, name="ck_raw_document_scope_owner"),
        CheckConstraint(
            "candidate_document_id IS NULL OR ("
            "visibility_scope = 'organization_private' "
            "AND owner_user_id IS NULL AND owner_tenant_id IS NOT NULL)",
            name="ck_raw_document_candidate_scope",
        ),
        ForeignKeyConstraint(
            ["candidate_document_id", "owner_tenant_id"],
            ["candidate_documents.id", "candidate_documents.tenant_id"],
            name="fk_raw_document_candidate_owner",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("sources.id"))
    research_import_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("research_imports.id", ondelete="SET NULL"), index=True
    )
    candidate_document_id: Mapped[UUID | None] = mapped_column(Uuid)
    owner_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    owner_tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    visibility_scope: Mapped[str] = mapped_column(String(32), default=SYSTEM_RESTRICTED_SCOPE)
    external_record_id: Mapped[str] = mapped_column(String(160))
    canonical_url: Mapped[str] = mapped_column(String(1000))
    title: Mapped[str] = mapped_column(String(500))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_on: Mapped[date | None] = mapped_column(Date)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    document_dedupe_key: Mapped[str] = mapped_column(String(64))
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
        CheckConstraint(SCOPED_OWNER_CHECK, name="ck_entity_mention_scope_owner"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    raw_document_id: Mapped[UUID] = mapped_column(
        ForeignKey("raw_documents.id", ondelete="CASCADE")
    )
    owner_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    owner_tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    visibility_scope: Mapped[str] = mapped_column(String(32), default=SYSTEM_RESTRICTED_SCOPE)
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
        CheckConstraint(
            "verification_basis IN "
            "('official_government', 'licensed_business_data', 'exchange_disclosure')",
            name="ck_official_identity_verification_basis",
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
    verification_basis: Mapped[str] = mapped_column(String(32))
    match_rule: Mapped[str] = mapped_column(String(80))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TrustedSource(TimestampMixin, Base):
    __tablename__ = "trusted_sources"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "start_url", name="uq_trusted_source_company_url"
        ),
        UniqueConstraint("id", "tenant_id", "company_id", name="uq_trusted_source_scope_lineage"),
        CheckConstraint(
            "source_type IN ('single_page', 'list_page', 'rss', 'sitemap')",
            name="ck_trusted_source_type",
        ),
        CheckConstraint(
            "content_retention_policy IN ('metadata_only', 'minimal_excerpt')",
            name="ck_trusted_source_retention",
        ),
        CheckConstraint(
            "license_status IN ('public_access', 'permission_confirmed', 'unclear', 'restricted')",
            name="ck_trusted_source_license_status",
        ),
        CheckConstraint(
            "visibility_scope = 'organization_private'",
            name="ck_trusted_source_scope",
        ),
        CheckConstraint(
            "check_frequency_minutes > 0 AND consecutive_failures >= 0",
            name="ck_trusted_source_counters",
        ),
        Index("ix_trusted_source_tenant_company", "tenant_id", "company_id"),
        Index("ix_trusted_source_tenant_enabled", "tenant_id", "enabled"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    source_type: Mapped[str] = mapped_column(String(32))
    root_domain: Mapped[str] = mapped_column(String(253))
    start_url: Mapped[str] = mapped_column(String(1000))
    list_path_prefix: Mapped[str | None] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    access_basis: Mapped[str] = mapped_column(Text)
    license_status: Mapped[str] = mapped_column(String(32))
    check_frequency_minutes: Mapped[int] = mapped_column(Integer, default=10_080)
    content_retention_policy: Mapped[str] = mapped_column(String(32), default="metadata_only")
    visibility_scope: Mapped[str] = mapped_column(String(32), default=ORGANIZATION_PRIVATE_SCOPE)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_code: Mapped[str | None] = mapped_column(String(64))
    last_http_status: Mapped[int | None] = mapped_column(Integer)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_etag: Mapped[str | None] = mapped_column(String(500))
    last_modified: Mapped[str | None] = mapped_column(String(200))
    last_content_hash: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))


class SourceCheckRun(TimestampMixin, Base):
    __tablename__ = "source_check_runs"
    __table_args__ = (
        Index(
            "uq_source_check_run_active",
            "trusted_source_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'partial', 'failed', "
            "'dry_run_completed')",
            name="ck_source_check_run_status",
        ),
        CheckConstraint(
            "trigger_type IN ('manual', 'scheduled')",
            name="ck_source_check_run_trigger_type",
        ),
        CheckConstraint(
            "visibility_scope = 'organization_private'",
            name="ck_source_check_run_scope",
        ),
        CheckConstraint(
            "max_requests > 0 AND max_download_bytes > 0 AND max_response_bytes > 0 "
            "AND timeout_seconds > 0 AND retry_limit >= 0 AND max_redirects > 0",
            name="ck_source_check_run_limits",
        ),
        CheckConstraint(
            "request_count >= 0 AND downloaded_bytes >= 0 AND new_count >= 0 "
            "AND changed_count >= 0 AND unchanged_count >= 0 "
            "AND duplicate_count >= 0 AND failure_count >= 0",
            name="ck_source_check_run_counts",
        ),
        ForeignKeyConstraint(
            ["trusted_source_id", "tenant_id", "company_id"],
            ["trusted_sources.id", "trusted_sources.tenant_id", "trusted_sources.company_id"],
            name="fk_source_check_run_source_lineage",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            "company_id",
            "trusted_source_id",
            name="uq_source_check_run_scope_lineage",
        ),
        Index("ix_source_check_run_tenant_status", "tenant_id", "status"),
        Index("ix_source_check_run_company_created", "company_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    trusted_source_id: Mapped[UUID] = mapped_column(Uuid)
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    trigger_type: Mapped[str] = mapped_column(String(32), default="manual", server_default="manual")
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    visibility_scope: Mapped[str] = mapped_column(String(32), default=ORGANIZATION_PRIVATE_SCOPE)
    policy_version: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    max_requests: Mapped[int] = mapped_column(Integer)
    max_download_bytes: Mapped[int] = mapped_column(Integer)
    max_response_bytes: Mapped[int] = mapped_column(Integer)
    timeout_seconds: Mapped[int] = mapped_column(Integer)
    retry_limit: Mapped[int] = mapped_column(Integer)
    max_redirects: Mapped[int] = mapped_column(Integer)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    downloaded_bytes: Mapped[int] = mapped_column(Integer, default=0)
    new_count: Mapped[int] = mapped_column(Integer, default=0)
    changed_count: Mapped[int] = mapped_column(Integer, default=0)
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    external_calls: Mapped[int] = mapped_column(Integer, default=0)
    paid_api_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("0"))
    robots_status: Mapped[str | None] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    request_log: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CandidateDocument(TimestampMixin, Base):
    __tablename__ = "candidate_documents"
    __table_args__ = (
        UniqueConstraint(
            "trusted_source_id",
            "canonical_url",
            "content_hash",
            name="uq_candidate_document_source_url_hash",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            name="uq_candidate_document_owner_lineage",
        ),
        CheckConstraint(
            "change_type IN ('new', 'changed')",
            name="ck_candidate_document_change_type",
        ),
        CheckConstraint(
            "processing_status IN ('pending', 'worth_research', 'irrelevant', "
            "'duplicate', 'source_unavailable')",
            name="ck_candidate_document_processing_status",
        ),
        CheckConstraint(
            "link_health_status IN ('healthy', 'unchecked', 'broken')",
            name="ck_candidate_document_link_health",
        ),
        CheckConstraint(
            "license_status IN ('public_access', 'permission_confirmed', 'unclear', 'restricted')",
            name="ck_candidate_document_license_status",
        ),
        CheckConstraint(
            "visibility_scope = 'organization_private'",
            name="ck_candidate_document_scope",
        ),
        ForeignKeyConstraint(
            ["trusted_source_id", "tenant_id", "company_id"],
            ["trusted_sources.id", "trusted_sources.tenant_id", "trusted_sources.company_id"],
            name="fk_candidate_document_source_lineage",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["discovery_run_id", "tenant_id", "company_id", "trusted_source_id"],
            [
                "source_check_runs.id",
                "source_check_runs.tenant_id",
                "source_check_runs.company_id",
                "source_check_runs.trusted_source_id",
            ],
            name="fk_candidate_document_run_lineage",
            ondelete="CASCADE",
        ),
        Index("ix_candidate_document_tenant_status", "tenant_id", "processing_status"),
        Index("ix_candidate_document_company_seen", "company_id", "first_discovered_at"),
        Index("ix_candidate_document_source_hash", "trusted_source_id", "content_hash"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    trusted_source_id: Mapped[UUID] = mapped_column(Uuid)
    discovery_run_id: Mapped[UUID] = mapped_column(Uuid)
    previous_candidate_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("candidate_documents.id", ondelete="SET NULL")
    )
    canonical_url: Mapped[str] = mapped_column(String(1000))
    title: Mapped[str] = mapped_column(String(500))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64))
    change_type: Mapped[str] = mapped_column(String(16))
    link_health_status: Mapped[str] = mapped_column(String(32), default="unchecked")
    http_status: Mapped[int | None] = mapped_column(Integer)
    etag: Mapped[str | None] = mapped_column(String(500))
    last_modified: Mapped[str | None] = mapped_column(String(200))
    excerpt: Mapped[str | None] = mapped_column(Text)
    license_status: Mapped[str] = mapped_column(String(32))
    processing_status: Mapped[str] = mapped_column(String(32), default="pending")
    identity_status_at_discovery: Mapped[str] = mapped_column(String(32))
    visibility_scope: Mapped[str] = mapped_column(String(32), default=ORGANIZATION_PRIVATE_SCOPE)
    document_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    handoff_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    processed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)


class Event(TimestampMixin, Base):
    __tablename__ = "events"
    __table_args__ = (
        Index(
            "uq_event_fingerprint_platform_shared",
            "company_id",
            "fingerprint_version",
            "event_fingerprint",
            unique=True,
            postgresql_where=text("visibility_scope = 'platform_shared'"),
            sqlite_where=text("visibility_scope = 'platform_shared'"),
        ),
        Index(
            "uq_event_fingerprint_personal_private",
            "company_id",
            "owner_user_id",
            "fingerprint_version",
            "event_fingerprint",
            unique=True,
            postgresql_where=text("visibility_scope = 'personal_private'"),
            sqlite_where=text("visibility_scope = 'personal_private'"),
        ),
        Index(
            "uq_event_fingerprint_organization_private",
            "company_id",
            "owner_tenant_id",
            "fingerprint_version",
            "event_fingerprint",
            unique=True,
            postgresql_where=text("visibility_scope = 'organization_private'"),
            sqlite_where=text("visibility_scope = 'organization_private'"),
        ),
        Index(
            "uq_event_fingerprint_system_restricted",
            "company_id",
            "fingerprint_version",
            "event_fingerprint",
            unique=True,
            postgresql_where=text("visibility_scope = 'system_restricted'"),
            sqlite_where=text("visibility_scope = 'system_restricted'"),
        ),
        CheckConstraint(
            "materiality_score >= 0 AND materiality_score <= 100",
            name="ck_event_materiality",
        ),
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="ck_event_confidence",
        ),
        CheckConstraint(SCOPED_OWNER_CHECK, name="ck_event_scope_owner"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    owner_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    owner_tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    visibility_scope: Mapped[str] = mapped_column(String(32), default=SYSTEM_RESTRICTED_SCOPE)
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
        UniqueConstraint("id", "event_id", name="uq_event_evidence_id_event"),
        UniqueConstraint("event_id", "raw_document_id", "span_hash", name="uq_event_evidence"),
        UniqueConstraint(
            "event_id",
            "source_event_evidence_id",
            name="uq_event_evidence_source_reference",
        ),
        CheckConstraint(
            "(raw_document_id IS NOT NULL AND source_event_evidence_id IS NULL) OR "
            "(raw_document_id IS NULL AND source_event_evidence_id IS NOT NULL)",
            name="ck_event_evidence_single_origin",
        ),
        CheckConstraint(SCOPED_OWNER_CHECK, name="ck_event_evidence_scope_owner"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    raw_document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("raw_documents.id", ondelete="CASCADE")
    )
    source_event_evidence_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("event_evidence.id"), index=True
    )
    owner_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    owner_tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    visibility_scope: Mapped[str] = mapped_column(String(32), default=SYSTEM_RESTRICTED_SCOPE)
    evidence_excerpt: Mapped[str] = mapped_column(Text)
    span_hash: Mapped[str] = mapped_column(String(64))
    support_type: Mapped[str] = mapped_column(String(32), default="supports")
    display_source_name: Mapped[str | None] = mapped_column(String(200))
    display_source_quality: Mapped[str | None] = mapped_column(String(1))
    display_title: Mapped[str | None] = mapped_column(String(500))
    display_canonical_url: Mapped[str | None] = mapped_column(String(1000))
    display_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    display_published_on: Mapped[date | None] = mapped_column(Date)
    display_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    display_url_health_status: Mapped[str | None] = mapped_column(String(32))
    display_url_http_status: Mapped[int | None] = mapped_column(Integer)
    display_url_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    display_final_url: Mapped[str | None] = mapped_column(String(1000))
    display_license_status: Mapped[str | None] = mapped_column(String(32))
    display_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    display_detail_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class EventFact(TimestampMixin, Base):
    __tablename__ = "event_facts"
    __table_args__ = (
        UniqueConstraint("event_id", "fact_key", name="uq_event_fact_key"),
        UniqueConstraint("id", "event_id", name="uq_event_fact_id_event"),
        CheckConstraint("position >= 0", name="ck_event_fact_position"),
        CheckConstraint("occurrence_count >= 1", name="ck_event_fact_occurrence_count"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    fact_key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    value: Mapped[str] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(64))
    position: Mapped[int] = mapped_column(Integer)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)


class EventFactSupport(TimestampMixin, Base):
    __tablename__ = "event_fact_supports"
    __table_args__ = (
        UniqueConstraint(
            "event_fact_id",
            "event_evidence_id",
            name="uq_event_fact_support_pair",
        ),
        ForeignKeyConstraint(
            ["event_fact_id", "event_id"],
            ["event_facts.id", "event_facts.event_id"],
            ondelete="CASCADE",
            name="fk_event_fact_support_fact_event",
        ),
        ForeignKeyConstraint(
            ["event_evidence_id", "event_id"],
            ["event_evidence.id", "event_evidence.event_id"],
            ondelete="CASCADE",
            name="fk_event_fact_support_evidence_event",
        ),
        CheckConstraint(
            "support_status IN ('supported', 'partial', 'conflicting', "
            "'pending_review', 'unsupported')",
            name="ck_event_fact_support_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    event_fact_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    event_evidence_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    support_status: Mapped[str] = mapped_column(String(32))
    evidence_locator: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    deterministic_checks: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    support_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    policy_version: Mapped[str] = mapped_column(String(64))
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EventObservation(Base):
    __tablename__ = "event_observations"
    __table_args__ = (
        UniqueConstraint("id", "event_id", name="uq_event_observation_id_event"),
        UniqueConstraint(
            "event_id",
            "raw_document_id",
            "schema_version",
            "observation_key",
            "processing_version",
            name="uq_event_observation_document",
        ),
        CheckConstraint(
            "observation_kind IN ('initial', 'same_facts', 'correction_candidate', "
            "'conflicting', 'incomplete')",
            name="ck_event_observation_kind",
        ),
        CheckConstraint(
            "(occurred_on IS NULL AND date_precision = 'unknown') OR "
            "(occurred_on IS NOT NULL AND date_precision = 'day')",
            name="ck_event_observation_date_precision",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), index=True)
    raw_document_id: Mapped[UUID] = mapped_column(ForeignKey("raw_documents.id"), index=True)
    schema_version: Mapped[str] = mapped_column(String(64))
    observation_key: Mapped[str] = mapped_column(String(64), default="", server_default="")
    processing_version: Mapped[str] = mapped_column(String(64), default="", server_default="")
    fact_version: Mapped[str] = mapped_column(String(64))
    observation_kind: Mapped[str] = mapped_column(String(32))
    occurred_on: Mapped[date | None] = mapped_column(Date)
    date_precision: Mapped[str] = mapped_column(String(16))
    candidate_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class InvestorChangeAnalysis(TimestampMixin, Base):
    __tablename__ = "investor_change_analyses"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "prompt_version",
            "input_hash",
            name="uq_investor_change_analysis_input",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'budget_deferred')",
            name="ck_investor_change_analysis_status",
        ),
        CheckConstraint(
            "visibility_scope = 'platform_shared'",
            name="ck_investor_change_analysis_platform_shared",
        ),
        Index(
            "ix_investor_change_analysis_status_created",
            "status",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    visibility_scope: Mapped[str] = mapped_column(
        String(32), default=PLATFORM_SHARED_SCOPE, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    provider: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    analysis_output: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    response_id: Mapped[str | None] = mapped_column(String(200))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventSharingDecision(Base):
    __tablename__ = "event_sharing_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_observation_id", "source_event_id"],
            ["event_observations.id", "event_observations.event_id"],
            name="fk_sharing_decision_observation_event",
        ),
        CheckConstraint(
            "action IN ('promote', 'reject', 'retract')",
            name="ck_event_sharing_decision_action",
        ),
        CheckConstraint(
            "(action = 'promote' AND source_event_id IS NOT NULL "
            "AND shared_event_id IS NOT NULL) OR "
            "(action = 'reject' AND source_event_id IS NOT NULL "
            "AND shared_event_id IS NULL) OR "
            "(action = 'retract' AND shared_event_id IS NOT NULL)",
            name="ck_event_sharing_decision_subject",
        ),
        Index(
            "uq_event_sharing_source_outcome",
            "source_event_id",
            unique=True,
            postgresql_where=text(
                "action IN ('promote', 'reject') AND source_observation_id IS NULL"
            ),
            sqlite_where=text("action IN ('promote', 'reject') AND source_observation_id IS NULL"),
        ),
        Index("uq_event_sharing_observation", "source_observation_id", unique=True),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    source_event_id: Mapped[UUID | None] = mapped_column(ForeignKey("events.id"), index=True)
    source_observation_id: Mapped[UUID | None] = mapped_column(Uuid)
    shared_event_id: Mapped[UUID | None] = mapped_column(ForeignKey("events.id"), index=True)
    actor_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), index=True)
    actor_tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    action: Mapped[str] = mapped_column(String(16), index=True)
    reason: Mapped[str] = mapped_column(Text)
    shared_title: Mapped[str | None] = mapped_column(String(200))
    shared_summary: Mapped[str | None] = mapped_column(Text)
    policy_version: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EventSharingDecisionEvidence(Base):
    __tablename__ = "event_sharing_decision_evidence"
    __table_args__ = (
        UniqueConstraint(
            "decision_id",
            "source_event_evidence_id",
            name="uq_event_sharing_decision_evidence_source",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    decision_id: Mapped[UUID] = mapped_column(ForeignKey("event_sharing_decisions.id"))
    source_event_evidence_id: Mapped[UUID] = mapped_column(ForeignKey("event_evidence.id"))
    shared_event_evidence_id: Mapped[UUID | None] = mapped_column(ForeignKey("event_evidence.id"))
    evidence_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CompanySnapshot(TimestampMixin, Base):
    __tablename__ = "company_snapshots"
    __table_args__ = (
        UniqueConstraint("company_id", "snapshot_version", name="uq_company_snapshot_version"),
        Index(
            "uq_company_snapshot_current_platform_shared",
            "company_id",
            unique=True,
            postgresql_where=text("is_current AND visibility_scope = 'platform_shared'"),
            sqlite_where=text("is_current = 1 AND visibility_scope = 'platform_shared'"),
        ),
        Index(
            "uq_company_snapshot_current_personal_private",
            "company_id",
            "owner_user_id",
            unique=True,
            postgresql_where=text("is_current AND visibility_scope = 'personal_private'"),
            sqlite_where=text("is_current = 1 AND visibility_scope = 'personal_private'"),
        ),
        Index(
            "uq_company_snapshot_current_organization_private",
            "company_id",
            "owner_tenant_id",
            unique=True,
            postgresql_where=text("is_current AND visibility_scope = 'organization_private'"),
            sqlite_where=text("is_current = 1 AND visibility_scope = 'organization_private'"),
        ),
        Index(
            "uq_company_snapshot_current_system_restricted",
            "company_id",
            unique=True,
            postgresql_where=text("is_current AND visibility_scope = 'system_restricted'"),
            sqlite_where=text("is_current = 1 AND visibility_scope = 'system_restricted'"),
        ),
        CheckConstraint(SCOPED_OWNER_CHECK, name="ck_company_snapshot_scope_owner"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    owner_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    owner_tenant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    visibility_scope: Mapped[str] = mapped_column(String(32), default=SYSTEM_RESTRICTED_SCOPE)
    snapshot_version: Mapped[int] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    data_as_of: Mapped[date | None] = mapped_column(Date)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    __table_args__ = (
        CheckConstraint(
            "usage_state IN ('legacy','reserved','in_flight','settled','uncertain','released')",
            name="ck_usage_state",
        ),
        CheckConstraint(
            "cost_status IN ('unknown','estimated','actual','confirmed_free')",
            name="ck_usage_cost_status",
        ),
        CheckConstraint("reserved_calls >= 0", name="ck_usage_reserved_calls"),
        CheckConstraint(
            "quota_scope IS NULL OR (quota_scope = 'platform_web' AND task_key IS NOT NULL "
            "AND subject_key IS NOT NULL)",
            name="ck_usage_quota_scope",
        ),
        Index("ix_usage_quota_task", "quota_scope", "task_key"),
        Index("ix_usage_quota_subject", "quota_scope", "subject_key", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    company_id: Mapped[UUID | None] = mapped_column(ForeignKey("companies.id"))
    provider: Mapped[str] = mapped_column(String(80))
    operation: Mapped[str] = mapped_column(String(80))
    external_calls: Mapped[int | None] = mapped_column(
        Integer().evaluates_none(), default=0, nullable=True
    )
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 6).evaluates_none(), default=Decimal("0"), nullable=True
    )
    cost_status: Mapped[str] = mapped_column(
        String(24), default="unknown", server_default="unknown"
    )
    usage_state: Mapped[str] = mapped_column(String(24), default="legacy", server_default="legacy")
    quota_scope: Mapped[str | None] = mapped_column(String(32))
    task_key: Mapped[str | None] = mapped_column(String(96))
    subject_key: Mapped[str | None] = mapped_column(String(64))
    reserved_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reserved_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    quoted_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    pricing_version: Mapped[str | None] = mapped_column(String(64))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
