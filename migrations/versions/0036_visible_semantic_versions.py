"""Preserve legacy receipts and establish an explicit semantic baseline at migration."""

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "personal_report_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "report_id",
            sa.Uuid(),
            sa.ForeignKey("personal_company_reports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.UniqueConstraint("owner_user_id", "idempotency_key", name="uq_report_request_owner_key"),
    )
    op.add_column("personal_event_view_receipts", sa.Column("semantic_version", sa.String(64)))
    op.add_column("personal_company_reports", sa.Column("input_fingerprint", sa.String(64)))
    op.create_index(
        "ix_personal_company_reports_input_fingerprint",
        "personal_company_reports",
        ["input_fingerprint"],
    )
    if op.get_bind().dialect.name == "postgresql":
        import importlib

        clause = importlib.import_module(
            "migrations.versions.0017_add_personal_changes_and_reports"
        )._active_owner_clause()
        op.execute("ALTER TABLE personal_report_requests ENABLE ROW LEVEL SECURITY")
        op.execute(
            "CREATE POLICY report_request_read ON personal_report_requests FOR SELECT USING ("
            + clause
            + ")"
        )
        op.execute(
            "CREATE POLICY report_request_insert ON personal_report_requests "
            "FOR INSERT WITH CHECK ("
            + clause
            + " AND EXISTS (SELECT 1 FROM personal_company_reports r WHERE r.id=report_id "
            "AND r.owner_user_id=personal_report_requests.owner_user_id))"
        )
        op.execute(
            "CREATE POLICY personal_event_view_receipts_owner_update ON "
            "personal_event_view_receipts FOR UPDATE USING ("
            + clause
            + ") WITH CHECK ("
            + clause
            + ")"
        )
    # 旧回执的历史版本未知。迁移时的授权投影作为新基线，不声称是当时所见。
    if op.get_context().as_sql:
        # 允许生成空库 DDL；已有旧回执时 SQL 部署必须中止，在线迁移才能建立基线。
        op.execute(
            "DO $$ BEGIN IF EXISTS (SELECT 1 FROM personal_event_view_receipts) "
            "THEN RAISE EXCEPTION 'Existing receipts require online semantic migration'; "
            "END IF; END $$"
        )
        return
    from sqlalchemy.orm import Session

    from backend.app.models import Event, PersonalEventViewReceipt, User
    from backend.app.semantic_content import event_version
    from backend.app.services import platform_shared_event_out

    with Session(bind=op.get_bind()) as session:
        after = None
        while True:
            query = (
                sa.select(PersonalEventViewReceipt).order_by(PersonalEventViewReceipt.id).limit(100)
            )
            if after is not None:
                query = query.where(PersonalEventViewReceipt.id > after)
            rows = list(session.scalars(query))
            if not rows:
                break
            for receipt in rows:
                event = session.get(Event, receipt.event_id)
                user = session.get(User, receipt.owner_user_id)
                if (
                    event
                    and user
                    and event.status == "published"
                    and event.visibility_scope == "platform_shared"
                ):
                    receipt.semantic_version = event_version(
                        platform_shared_event_out(session, event, user)
                    )
            after = rows[-1].id
            session.flush()


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("SET LOCAL lock_timeout = '3s'")
    if op.get_context().as_sql or op.get_bind().scalar(
        sa.text(
            "SELECT (SELECT count(*) FROM personal_event_view_receipts) + "
            "(SELECT count(*) FROM personal_company_reports) + "
            "(SELECT count(*) FROM event_observations)"
        )
    ):
        raise RuntimeError(
            "Event observations exist; Retain matter observations or semantic history "
            "and migration 0036 on rollback"
        )
    op.drop_table("personal_report_requests")
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP POLICY personal_event_view_receipts_owner_update ON personal_event_view_receipts"
        )
    op.drop_index("ix_personal_company_reports_input_fingerprint", "personal_company_reports")
    op.drop_column("personal_company_reports", "input_fingerprint")
    op.drop_column("personal_event_view_receipts", "semantic_version")
