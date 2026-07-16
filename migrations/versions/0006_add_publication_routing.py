"""add publication routing

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("company_snapshots") as batch_op:
        batch_op.alter_column(
            "data_as_of",
            existing_type=sa.Date(),
            nullable=True,
        )
    op.add_column("companies", sa.Column("official_website", sa.String(length=500)))
    op.add_column("raw_documents", sa.Column("published_on", sa.Date()))
    op.add_column("events", sa.Column("published_on", sa.Date()))
    op.add_column(
        "events",
        sa.Column(
            "publication_route",
            sa.String(length=32),
            nullable=False,
            server_default="legacy_candidate",
        ),
    )
    op.add_column(
        "events",
        sa.Column(
            "publication_policy_version",
            sa.String(length=32),
            nullable=False,
            server_default="legacy-v1",
        ),
    )
    op.add_column(
        "events",
        sa.Column(
            "publication_reasons",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    for column_name in (
        "auto_published_count",
        "unconfirmed_count",
        "identity_review_count",
    ):
        op.add_column(
            "research_imports",
            sa.Column(column_name, sa.Integer(), nullable=False, server_default="0"),
        )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "UPDATE raw_documents SET published_on = CAST(published_at AS DATE) "
            "WHERE published_at IS NOT NULL"
        )
        op.execute(
            "UPDATE events SET published_on = CAST(published_at AS DATE) "
            "WHERE published_at IS NOT NULL"
        )
    else:
        op.execute(
            "UPDATE raw_documents SET published_on = date(published_at) "
            "WHERE published_at IS NOT NULL"
        )
        op.execute(
            "UPDATE events SET published_on = date(published_at) WHERE published_at IS NOT NULL"
        )
    op.execute(
        "UPDATE events SET publication_route = CASE "
        "WHEN status = 'published' THEN 'legacy_reviewed' "
        "WHEN status = 'in_review' THEN 'legacy_human_review' "
        "ELSE 'legacy_candidate' END"
    )
    op.execute("UPDATE research_imports SET identity_review_count = unresolved_count")


def downgrade() -> None:
    for column_name in (
        "identity_review_count",
        "unconfirmed_count",
        "auto_published_count",
    ):
        op.drop_column("research_imports", column_name)
    op.drop_column("events", "publication_reasons")
    op.drop_column("events", "publication_policy_version")
    op.drop_column("events", "publication_route")
    op.drop_column("events", "published_on")
    op.drop_column("raw_documents", "published_on")
    op.drop_column("companies", "official_website")
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "UPDATE company_snapshots SET data_as_of = CAST(last_checked_at AS DATE) "
            "WHERE data_as_of IS NULL"
        )
    else:
        op.execute(
            "UPDATE company_snapshots SET data_as_of = date(last_checked_at) "
            "WHERE data_as_of IS NULL"
        )
    with op.batch_alter_table("company_snapshots") as batch_op:
        batch_op.alter_column(
            "data_as_of",
            existing_type=sa.Date(),
            nullable=False,
        )
