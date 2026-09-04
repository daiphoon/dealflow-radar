"""add evidence fact support ledger

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-04
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUPPORT_POLICY_VERSION = "evidence-fact-support-v1"


def _current_user() -> str:
    return "NULLIF(current_setting('app.current_user_id', true), '')::uuid"


def _current_tenant() -> str:
    return "NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"


def _active_user_clause() -> str:
    return f"""
        EXISTS (
            SELECT 1 FROM users AS current_scope_user
            WHERE current_scope_user.id = {_current_user()}
              AND current_scope_user.tenant_id = {_current_tenant()}
              AND current_scope_user.status = 'active'
        )
    """


def _active_role_clause(role_codes: tuple[str, ...]) -> str:
    roles = ", ".join(f"'{role}'" for role in role_codes)
    return f"""
        EXISTS (
            SELECT 1
            FROM user_role_assignments AS assignment
            JOIN roles ON roles.id = assignment.role_id
            JOIN users ON users.id = assignment.user_id
            WHERE assignment.user_id = {_current_user()}
              AND users.tenant_id = {_current_tenant()}
              AND users.status = 'active'
              AND roles.code IN ({roles})
              AND (assignment.valid_until IS NULL OR assignment.valid_until > CURRENT_TIMESTAMP)
        )
    """


def _authorized_company_clause(company_reference: str) -> str:
    return f"""
        EXISTS (
            SELECT 1
            FROM investments AS scoped_investment
            JOIN fund_access_grants AS scoped_grant
              ON scoped_grant.fund_id = scoped_investment.fund_id
            WHERE scoped_investment.company_id = {company_reference}
              AND scoped_investment.tenant_id = {_current_tenant()}
              AND scoped_grant.user_id = {_current_user()}
              AND (
                  scoped_grant.valid_until IS NULL
                  OR scoped_grant.valid_until > CURRENT_TIMESTAMP
              )
        )
    """


def _ledger_read_clause(table_name: str, *, require_evidence: bool) -> str:
    evidence_clause = ""
    if require_evidence:
        evidence_clause = f"""
            AND EXISTS (
                SELECT 1 FROM event_evidence AS visible_evidence
                WHERE visible_evidence.id = {table_name}.event_evidence_id
                  AND visible_evidence.event_id = {table_name}.event_id
            )
        """
    return f"""
        EXISTS (
            SELECT 1 FROM events AS visible_event
            WHERE visible_event.id = {table_name}.event_id
        )
        {evidence_clause}
    """


def _ledger_write_clause(table_name: str, *, require_evidence: bool) -> str:
    platform_admin = _active_role_clause(("platform_admin",))
    tenant_roles = _active_role_clause(("institution_admin", "reviewer"))
    organization_write = (
        f"{tenant_roles} OR {_authorized_company_clause('ledger_parent_event.company_id')}"
    )
    evidence_clause = ""
    if require_evidence:
        evidence_clause = f"""
            AND EXISTS (
                SELECT 1 FROM event_evidence AS writable_evidence
                WHERE writable_evidence.id = {table_name}.event_evidence_id
                  AND writable_evidence.event_id = {table_name}.event_id
            )
        """
    return f"""
        ({_active_user_clause()})
        AND EXISTS (
            SELECT 1 FROM events AS ledger_parent_event
            WHERE ledger_parent_event.id = {table_name}.event_id
              AND (
                  (ledger_parent_event.visibility_scope = 'platform_shared'
                   AND ledger_parent_event.owner_user_id IS NULL
                   AND ledger_parent_event.owner_tenant_id IS NULL
                   AND ({platform_admin}))
                  OR (ledger_parent_event.visibility_scope = 'personal_private'
                      AND ledger_parent_event.owner_user_id = {_current_user()}
                      AND ledger_parent_event.owner_tenant_id IS NULL)
                  OR (ledger_parent_event.visibility_scope = 'organization_private'
                      AND ledger_parent_event.owner_user_id IS NULL
                      AND ledger_parent_event.owner_tenant_id = {_current_tenant()}
                      AND ({organization_write}))
              )
        )
        {evidence_clause}
    """


def _create_tables() -> None:
    with op.batch_alter_table("event_evidence") as batch_op:
        batch_op.create_unique_constraint(
            "uq_event_evidence_id_event",
            ["id", "event_id"],
        )

    op.create_table(
        "event_facts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("fact_key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("unit", sa.String(64), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_event_fact_position"),
        sa.CheckConstraint(
            "occurrence_count >= 1",
            name="ck_event_fact_occurrence_count",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "fact_key", name="uq_event_fact_key"),
        sa.UniqueConstraint("id", "event_id", name="uq_event_fact_id_event"),
    )
    op.create_index("ix_event_facts_event_id", "event_facts", ["event_id"])

    op.create_table(
        "event_fact_supports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_fact_id", sa.Uuid(), nullable=False),
        sa.Column("event_evidence_id", sa.Uuid(), nullable=False),
        sa.Column("support_status", sa.String(32), nullable=False),
        sa.Column("evidence_locator", sa.JSON(), nullable=False),
        sa.Column("deterministic_checks", sa.JSON(), nullable=False),
        sa.Column("support_reasons", sa.JSON(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "support_status IN ('supported', 'partial', 'conflicting', "
            "'pending_review', 'unsupported')",
            name="ck_event_fact_support_status",
        ),
        sa.ForeignKeyConstraint(
            ["event_fact_id", "event_id"],
            ["event_facts.id", "event_facts.event_id"],
            ondelete="CASCADE",
            name="fk_event_fact_support_fact_event",
        ),
        sa.ForeignKeyConstraint(
            ["event_evidence_id", "event_id"],
            ["event_evidence.id", "event_evidence.event_id"],
            ondelete="CASCADE",
            name="fk_event_fact_support_evidence_event",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_fact_id",
            "event_evidence_id",
            name="uq_event_fact_support_pair",
        ),
    )
    op.create_index("ix_event_fact_supports_event_id", "event_fact_supports", ["event_id"])
    op.create_index(
        "ix_event_fact_supports_event_fact_id",
        "event_fact_supports",
        ["event_fact_id"],
    )
    op.create_index(
        "ix_event_fact_supports_event_evidence_id",
        "event_fact_supports",
        ["event_evidence_id"],
    )


def _uuid(value: object) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _fact_parts(value: object) -> tuple[str, str, str | None] | None:
    if not isinstance(value, dict):
        return None
    name = str(value.get("name") or "").strip()
    fact_value = str(value.get("value") or "").strip()
    unit_value = value.get("unit")
    unit = str(unit_value).strip() if unit_value is not None else None
    if not name or not fact_value:
        return None
    return name, fact_value, unit or None


def _fact_key(name: str, value: str, unit: str | None) -> str:
    def canonical_component(component: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", component).casefold().split())

    canonical = json.dumps(
        {
            "name": canonical_component(name),
            "unit": canonical_component(unit) if unit is not None else None,
            "value": canonical_component(value),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _backfill_existing_rows() -> None:
    if op.get_context().as_sql:
        return
    bind = op.get_bind()
    now = datetime.now(UTC)
    event_fact_table = sa.table(
        "event_facts",
        sa.column("id", sa.Uuid()),
        sa.column("event_id", sa.Uuid()),
        sa.column("fact_key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("value", sa.Text()),
        sa.column("unit", sa.String()),
        sa.column("position", sa.Integer()),
        sa.column("occurrence_count", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    support_table = sa.table(
        "event_fact_supports",
        sa.column("id", sa.Uuid()),
        sa.column("event_id", sa.Uuid()),
        sa.column("event_fact_id", sa.Uuid()),
        sa.column("event_evidence_id", sa.Uuid()),
        sa.column("support_status", sa.String()),
        sa.column("evidence_locator", sa.JSON()),
        sa.column("deterministic_checks", sa.JSON()),
        sa.column("support_reasons", sa.JSON()),
        sa.column("policy_version", sa.String()),
        sa.column("assessed_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    evidence_by_event: dict[UUID, list[dict[str, Any]]] = {}
    evidence_rows = bind.execute(
        sa.text(
            "SELECT id, event_id, raw_document_id, source_event_evidence_id, span_hash "
            "FROM event_evidence"
        )
    ).mappings()
    for row in evidence_rows:
        event_id = _uuid(row["event_id"])
        evidence_by_event.setdefault(event_id, []).append(dict(row))

    for event_row in bind.execute(sa.text("SELECT id, facts FROM events")).mappings():
        event_id = _uuid(event_row["id"])
        facts = event_row["facts"]
        if isinstance(facts, str):
            try:
                facts = json.loads(facts)
            except json.JSONDecodeError:
                facts = []
        if not isinstance(facts, list):
            continue
        parsed = [parts for item in facts if (parts := _fact_parts(item)) is not None]
        counts = Counter(_fact_key(*parts) for parts in parsed)
        first_position: dict[str, int] = {}
        unique_parts: dict[str, tuple[str, str, str | None]] = {}
        for position, parts in enumerate(parsed):
            key = _fact_key(*parts)
            first_position.setdefault(key, position)
            unique_parts.setdefault(key, parts)
        for key, parts in unique_parts.items():
            fact_id = uuid5(NAMESPACE_URL, f"dealflow-radar:event-fact:{event_id}:{key}")
            bind.execute(
                event_fact_table.insert().values(
                    id=fact_id,
                    event_id=event_id,
                    fact_key=key,
                    name=parts[0],
                    value=parts[1],
                    unit=parts[2],
                    position=first_position[key],
                    occurrence_count=counts[key],
                    created_at=now,
                    updated_at=now,
                )
            )
            for evidence in evidence_by_event.get(event_id, []):
                evidence_id = _uuid(evidence["id"])
                support_id = uuid5(
                    NAMESPACE_URL,
                    f"dealflow-radar:event-fact-support:{fact_id}:{evidence_id}",
                )
                origin_id = evidence["raw_document_id"] or evidence["source_event_evidence_id"]
                bind.execute(
                    support_table.insert().values(
                        id=support_id,
                        event_id=event_id,
                        event_fact_id=fact_id,
                        event_evidence_id=evidence_id,
                        support_status="pending_review",
                        evidence_locator={
                            "kind": "historical_excerpt",
                            "origin_id": str(origin_id) if origin_id is not None else None,
                            "span_hash": evidence["span_hash"],
                        },
                        deterministic_checks={"backfill": "not_reassessed"},
                        support_reasons=["historical_relationship_not_proven_per_fact"],
                        policy_version=SUPPORT_POLICY_VERSION,
                        assessed_at=now,
                        created_at=now,
                        updated_at=now,
                    )
                )


def _enable_rls() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table_name, require_evidence in (
        ("event_facts", False),
        ("event_fact_supports", True),
    ):
        read_clause = _ledger_read_clause(table_name, require_evidence=require_evidence)
        write_clause = _ledger_write_clause(table_name, require_evidence=require_evidence)
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table_name}_scope_read ON {table_name} "
            f"FOR SELECT USING ({read_clause})"
        )
        op.execute(
            f"CREATE POLICY {table_name}_scope_insert ON {table_name} "
            f"FOR INSERT WITH CHECK ({write_clause})"
        )
        op.execute(
            f"CREATE POLICY {table_name}_scope_update ON {table_name} "
            f"FOR UPDATE USING ({read_clause}) WITH CHECK ({write_clause})"
        )


def upgrade() -> None:
    _create_tables()
    _backfill_existing_rows()
    _enable_rls()


def downgrade() -> None:
    op.drop_index(
        "ix_event_fact_supports_event_evidence_id",
        table_name="event_fact_supports",
    )
    op.drop_index(
        "ix_event_fact_supports_event_fact_id",
        table_name="event_fact_supports",
    )
    op.drop_index("ix_event_fact_supports_event_id", table_name="event_fact_supports")
    op.drop_table("event_fact_supports")
    op.drop_index("ix_event_facts_event_id", table_name="event_facts")
    op.drop_table("event_facts")
    with op.batch_alter_table("event_evidence") as batch_op:
        batch_op.drop_constraint("uq_event_evidence_id_event", type_="unique")
