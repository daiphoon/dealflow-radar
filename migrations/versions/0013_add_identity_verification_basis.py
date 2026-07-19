"""distinguish government and licensed business identity verification

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("companies") as batch_op:
        batch_op.add_column(sa.Column("identity_verification_basis", sa.String(32), nullable=True))
        batch_op.create_check_constraint(
            "ck_company_identity_verification_basis",
            "identity_verification_basis IS NULL OR identity_verification_basis IN "
            "('official_government', 'licensed_business_data')",
        )

    with op.batch_alter_table("official_identity_verifications") as batch_op:
        batch_op.add_column(
            sa.Column(
                "verification_basis",
                sa.String(32),
                nullable=False,
                server_default="official_government",
            )
        )
        batch_op.create_check_constraint(
            "ck_official_identity_verification_basis",
            "verification_basis IN ('official_government', 'licensed_business_data')",
        )
        batch_op.alter_column(
            "verification_basis",
            existing_type=sa.String(32),
            nullable=False,
            server_default=None,
        )


def downgrade() -> None:
    with op.batch_alter_table("official_identity_verifications") as batch_op:
        batch_op.drop_constraint(
            "ck_official_identity_verification_basis",
            type_="check",
        )
        batch_op.drop_column("verification_basis")

    with op.batch_alter_table("companies") as batch_op:
        batch_op.drop_constraint(
            "ck_company_identity_verification_basis",
            type_="check",
        )
        batch_op.drop_column("identity_verification_basis")
