"""add explicit list-page path scope

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("trusted_sources") as batch_op:
        batch_op.add_column(sa.Column("list_path_prefix", sa.String(500), nullable=True))
        batch_op.create_check_constraint(
            "ck_trusted_source_list_path_prefix",
            "list_path_prefix IS NULL OR ("
            "source_type = 'list_page' AND list_path_prefix LIKE '/%' "
            "AND list_path_prefix NOT LIKE '//%' "
            "AND list_path_prefix NOT LIKE '%?%' "
            "AND list_path_prefix NOT LIKE '%#%' "
            "AND list_path_prefix NOT LIKE '%..%')",
        )


def downgrade() -> None:
    with op.batch_alter_table("trusted_sources") as batch_op:
        batch_op.drop_constraint("ck_trusted_source_list_path_prefix", type_="check")
        batch_op.drop_column("list_path_prefix")
