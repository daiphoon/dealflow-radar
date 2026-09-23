"""Separate observation identity and processing version from the schema contract."""

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("event_observations") as batch:
        batch.add_column(
            sa.Column("observation_key", sa.String(64), nullable=False, server_default="")
        )
        batch.add_column(
            sa.Column("processing_version", sa.String(64), nullable=False, server_default="")
        )
        batch.drop_constraint("uq_event_observation_document", type_="unique")
        batch.create_unique_constraint(
            "uq_event_observation_document",
            [
                "event_id",
                "raw_document_id",
                "schema_version",
                "observation_key",
                "processing_version",
            ],
        )


def downgrade():
    # 不删除多版本观测凑出旧唯一键；有新数据时使用应用回滚并保留迁移。
    if op.get_context().as_sql or op.get_bind().scalar(
        sa.text("SELECT count(*) FROM event_observations")
    ):
        raise RuntimeError(
            "Event observations exist; Retain matter observations and migration 0035 on rollback"
        )
    with op.batch_alter_table("event_observations") as batch:
        batch.drop_constraint("uq_event_observation_document", type_="unique")
        batch.drop_column("processing_version")
        batch.drop_column("observation_key")
        batch.create_unique_constraint(
            "uq_event_observation_document", ["event_id", "raw_document_id", "schema_version"]
        )
