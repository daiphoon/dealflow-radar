"""add manually reviewed exchange disclosure identity basis

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINTS = (
    ("companies", "identity_verification_basis", "ck_company_identity_verification_basis"),
    (
        "official_identity_verifications",
        "verification_basis",
        "ck_official_identity_verification_basis",
    ),
)


def _replace_constraints(*, include_exchange: bool) -> None:
    choices = "'official_government', 'licensed_business_data'"
    if include_exchange:
        choices += ", 'exchange_disclosure'"
    for table, column, constraint in _CONSTRAINTS:
        clause = f"{column} IN ({choices})"
        if table == "companies":
            clause = f"{column} IS NULL OR {clause}"
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(constraint, type_="check")
            batch.create_check_constraint(constraint, clause)


def upgrade() -> None:
    _replace_constraints(include_exchange=True)


def downgrade() -> None:
    connection = op.get_bind()
    # 保留真实核验血缘；不能通过重标来源或删除记录来迎合旧约束。
    for table, column, _constraint in _CONSTRAINTS:
        if connection.scalar(
            sa.text(f"SELECT count(*) FROM {table} WHERE {column} = 'exchange_disclosure'")
        ):
            raise RuntimeError(
                "Cannot downgrade 0026 while exchange disclosure identity records exist; "
                "keep the forward-compatible schema and assess a backed-up rollback."
            )
    _replace_constraints(include_exchange=False)
