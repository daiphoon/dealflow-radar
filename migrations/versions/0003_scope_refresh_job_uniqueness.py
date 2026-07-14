"""scope active refresh job uniqueness to tenant

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE_JOB_FILTER = sa.text("status IN ('queued', 'running')")


def upgrade() -> None:
    """Keep active job deduplication inside one tenant budget scope."""
    op.drop_index(
        "uq_refresh_job_active",
        table_name="refresh_jobs",
        postgresql_where=ACTIVE_JOB_FILTER,
        sqlite_where=ACTIVE_JOB_FILTER,
    )
    op.create_index(
        "uq_refresh_job_active",
        "refresh_jobs",
        ["tenant_id", "company_id", "job_type"],
        unique=True,
        postgresql_where=ACTIVE_JOB_FILTER,
        sqlite_where=ACTIVE_JOB_FILTER,
    )


def downgrade() -> None:
    """Restore system-wide active job deduplication."""
    op.drop_index(
        "uq_refresh_job_active",
        table_name="refresh_jobs",
        postgresql_where=ACTIVE_JOB_FILTER,
        sqlite_where=ACTIVE_JOB_FILTER,
    )
    op.create_index(
        "uq_refresh_job_active",
        "refresh_jobs",
        ["company_id", "job_type"],
        unique=True,
        postgresql_where=ACTIVE_JOB_FILTER,
        sqlite_where=ACTIVE_JOB_FILTER,
    )
