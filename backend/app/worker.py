from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, or_, select, text
from sqlalchemy.orm import Session

from backend.app.config import RefreshPolicy
from backend.app.demo import DEMO_TENANT_IDS
from backend.app.models import CompanySnapshot, RefreshJob, UsageLedger, utc_now


@dataclass(frozen=True)
class MockWorkerResult:
    status: str
    job_id: UUID | None = None
    outcome: str | None = None
    snapshot_updated: bool = False
    external_calls: int = 0
    estimated_cost: Decimal = Decimal("0")

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "job_id": str(self.job_id) if self.job_id else None,
            "outcome": self.outcome,
            "snapshot_updated": self.snapshot_updated,
            "external_calls": self.external_calls,
            "estimated_cost": str(self.estimated_cost),
        }


@dataclass(frozen=True)
class _JobLease:
    job_id: UUID
    tenant_id: UUID
    company_id: UUID
    leased_until: datetime


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _set_worker_tenant_context(session: Session, tenant_id: UUID) -> None:
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        text(
            "SELECT "
            "set_config('app.current_user_id', '', true), "
            "set_config('app.current_tenant_id', :tenant_id, true)"
        ),
        {"tenant_id": str(tenant_id)},
    )


def _lease_next_job(
    session: Session,
    tenant_id: UUID,
    policy: RefreshPolicy,
    now: datetime,
) -> _JobLease | None:
    _set_worker_tenant_context(session, tenant_id)
    job = session.scalar(
        select(RefreshJob)
        .where(
            RefreshJob.tenant_id == tenant_id,
            RefreshJob.job_type == "mock_refresh",
            or_(
                RefreshJob.status == "queued",
                and_(
                    RefreshJob.status == "running",
                    or_(
                        RefreshJob.leased_until.is_(None),
                        RefreshJob.leased_until <= now,
                    ),
                ),
            ),
        )
        .order_by(RefreshJob.priority.desc(), RefreshJob.created_at)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        session.rollback()
        return None
    leased_until = now + timedelta(seconds=policy.mock_worker_lease_seconds)
    job.status = "running"
    job.heartbeat_at = now
    job.leased_until = leased_until
    session.commit()
    return _JobLease(
        job_id=job.id,
        tenant_id=job.tenant_id,
        company_id=job.company_id,
        leased_until=leased_until,
    )


def _complete_job(
    session: Session,
    lease: _JobLease,
    now: datetime,
) -> MockWorkerResult:
    _set_worker_tenant_context(session, lease.tenant_id)
    job = session.scalar(
        select(RefreshJob)
        .where(
            RefreshJob.id == lease.job_id,
            RefreshJob.tenant_id == lease.tenant_id,
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if job is None or job.status != "running" or job.leased_until is None:
        session.rollback()
        return MockWorkerResult(status="lease_lost", job_id=lease.job_id)
    active_lease = _as_utc(job.leased_until)
    if active_lease != _as_utc(lease.leased_until) or active_lease <= now:
        session.rollback()
        return MockWorkerResult(status="lease_lost", job_id=lease.job_id)

    snapshot = session.scalar(
        select(CompanySnapshot).where(
            CompanySnapshot.company_id == lease.company_id,
            CompanySnapshot.is_current.is_(True),
        )
    )
    snapshot_updated = snapshot is not None
    outcome = "no_change" if snapshot_updated else "no_snapshot"
    if snapshot is not None:
        snapshot.last_checked_at = now
        snapshot.freshness_status = "fresh"

    job.status = "completed"
    job.heartbeat_at = now
    job.leased_until = None
    session.add(
        UsageLedger(
            tenant_id=lease.tenant_id,
            company_id=lease.company_id,
            provider="mock",
            operation="mock_refresh",
            external_calls=0,
            input_tokens=0,
            output_tokens=0,
            estimated_cost=Decimal("0"),
            metrics={
                "job_id": str(lease.job_id),
                "outcome": outcome,
                "snapshot_updated": snapshot_updated,
                "data_changed": False,
                "simulated": True,
            },
            idempotency_key=_sha256(f"mock-refresh:{lease.job_id}"),
        )
    )
    session.commit()
    return MockWorkerResult(
        status="completed",
        job_id=lease.job_id,
        outcome=outcome,
        snapshot_updated=snapshot_updated,
    )


def run_mock_worker_once(
    session: Session,
    tenant_id: UUID,
    policy: RefreshPolicy,
    *,
    now: datetime | None = None,
) -> MockWorkerResult:
    if tenant_id not in DEMO_TENANT_IDS:
        raise RuntimeError("Mock Worker requires a fixed fictional Demo tenant")
    checked_at = _as_utc(now or utc_now())
    lease = _lease_next_job(session, tenant_id, policy, checked_at)
    if lease is None:
        return MockWorkerResult(status="idle")
    return _complete_job(session, lease, checked_at)
