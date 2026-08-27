"""add licensed evidence display details

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "event_evidence",
        sa.Column("display_detail_payload", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("event_evidence", "display_detail_payload")
