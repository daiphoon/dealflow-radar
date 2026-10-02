from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from backend.app.database import build_engine, set_request_context
from backend.app.demo import (
    ALPHA_TENANT_ID,
    ALPHA_USER_ID,
    BETA_TENANT_ID,
    BETA_USER_ID,
    NO_ACCESS_USER_ID,
    demo_uuid,
)
from backend.app.models import (
    Company,
    EntityMention,
    Event,
    EventEvidence,
    EventFact,
    EventFactSupport,
    EventObservation,
    RawDocument,
    Role,
    Source,
    User,
    UserRoleAssignment,
)
from backend.app.tender_events import (
    TenderDocument,
    TenderSubject,
    extract_tender_candidate,
)
from backend.app.tender_storage import persist_tender_candidate
from scripts.bootstrap_local_database import bootstrap_application_role

CASES = {
    item["id"]: item
    for item in json.loads(Path("data/sample/tender_notice_cases.json").read_text())["cases"]
}


COMPANY_ID = demo_uuid("company-示例星河科技一号有限公司")


@dataclass
class StorageDatabase:
    owner: Engine
    app: Engine
    postgres: bool


@pytest.fixture(params=["sqlite", "postgresql"])
def database(request, tmp_path, monkeypatch, database_templates):
    admin = None
    app_role = f"e12_app_{uuid4().hex[:16]}"
    postgres = request.param == "postgresql"
    if postgres:
        admin_url = os.getenv("DATABASE_ADMIN_URL")
        if not admin_url:
            pytest.skip("set DATABASE_ADMIN_URL for isolated PostgreSQL validation")
        if make_url(admin_url).host not in {"localhost", "127.0.0.1"}:
            pytest.skip("isolated tender tests require a local PostgreSQL test server")
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    owner_url = database_templates.copy(postgres, tmp_path / "tender.db")
    database_name = make_url(owner_url).database
    monkeypatch.setenv("DATABASE_URL", owner_url)
    monkeypatch.setenv("EXTERNAL_CALLS_ENABLED", "false")
    monkeypatch.setenv("PAID_API_CALLS_ENABLED", "false")
    monkeypatch.setenv("AUTO_REFRESH_ENABLED", "false")
    owner = build_engine(owner_url)
    app = None
    try:
        if postgres:
            bootstrap_application_role(owner_url, app_role, "ephemeral_fixture_app_only")
            app_url = (
                make_url(owner_url)
                .set(username=app_role, password="ephemeral_fixture_app_only")
                .render_as_string(hide_password=False)
            )
            app = build_engine(app_url)
            with app.connect() as connection:
                assert connection.execute(
                    text(
                        "SELECT rolsuper, rolbypassrls, "
                        "EXISTS(SELECT 1 FROM pg_class WHERE relowner=pg_roles.oid "
                        "AND relname='event_observations') FROM pg_roles WHERE rolname=current_user"
                    )
                ).one() == (False, False, False)
        else:
            app = build_engine(owner_url)
        yield StorageDatabase(owner, app, postgres)
    finally:
        if app is not None:
            app.dispose()
        owner.dispose()
        if admin is not None:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{database_name}"'))
                connection.execute(text(f'DROP ROLE IF EXISTS "{app_role}"'))
            admin.dispose()


def _source_document(
    database, case_id="award", scope="personal_private", owner_id=NO_ACCESS_USER_ID
):
    raw = CASES[case_id]["document"]
    doc_id = uuid4()
    with Session(database.owner) as session:
        source = session.get(Source, UUID(raw["source_id"]))
        if source is None:
            source = Source(
                id=UUID(raw["source_id"]),
                code=f"fixture_{raw['source_id']}",
                name="虚构中标来源（隔离测试权限模拟）",
                source_quality="B",
                license_status="permission_confirmed",
                base_url="https://example.invalid",
            )
            session.add(source)
            session.flush()
        if scope == "platform_shared":
            role = session.scalar(select(Role).where(Role.code == "platform_admin"))
            if not session.scalar(
                select(UserRoleAssignment).where(
                    UserRoleAssignment.user_id == ALPHA_USER_ID,
                    UserRoleAssignment.role_id == role.id,
                )
            ):
                session.add(UserRoleAssignment(user_id=ALPHA_USER_ID, role_id=role.id))
        document = RawDocument(
            id=doc_id,
            source_id=source.id,
            visibility_scope=scope,
            owner_user_id=owner_id if scope == "personal_private" else None,
            owner_tenant_id=owner_id if scope == "organization_private" else None,
            external_record_id=f"fixture-{doc_id}",
            canonical_url=raw["canonical_url"],
            title=raw["title"],
            content_hash=raw["content_hash"],
            published_at=datetime.fromisoformat(raw["published_at"]).astimezone(UTC),
            observed_at=datetime.fromisoformat(raw["observed_at"]).astimezone(UTC),
            document_dedupe_key=hashlib.sha256(str(doc_id).encode()).hexdigest(),
            license_status="permission_confirmed",
            payload={"excerpt": raw["body"], "fixture_only": True},
        )
        session.add(document)
        session.flush()
        session.add(
            EntityMention(
                raw_document_id=doc_id,
                candidate_company_id=COMPANY_ID,
                visibility_scope=scope,
                owner_user_id=document.owner_user_id,
                owner_tenant_id=document.owner_tenant_id,
                mention_text=CASES[case_id]["subject"]["legal_name"],
                match_rule="fixture_verified_code",
                match_confidence=Decimal("1"),
                resolution_status="verified",
            )
        )
        session.commit()
    return doc_id


def _candidate(database, document_id):
    with Session(database.owner) as session:
        document = session.get(RawDocument, document_id)
        company = session.get(Company, COMPANY_ID)

        def aware(value):
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value

        result = extract_tender_candidate(
            TenderDocument(
                document_id=document.id,
                source_id=document.source_id,
                canonical_url=document.canonical_url,
                title=document.title,
                content_hash=document.content_hash,
                body=document.payload["excerpt"],
                license_status=document.license_status,
                published_at=aware(document.published_at),
                observed_at=aware(document.observed_at),
            ),
            TenderSubject(
                company_id=company.id,
                legal_name=company.legal_name,
                credit_code=company.credit_code,
                identity_status="verified",
            ),
        )
        assert result.candidate is not None
        return result.candidate


def _actor(session, user_id=NO_ACCESS_USER_ID):
    tenant_id = BETA_TENANT_ID if user_id == BETA_USER_ID else ALPHA_TENANT_ID
    set_request_context(session, user_id, tenant_id)
    return session.get(User, user_id)


def _persist(database, candidate, user_id=NO_ACCESS_USER_ID):
    with Session(database.app) as session:
        result = persist_tender_candidate(
            session, _actor(session, user_id), candidate, enabled=True
        )
        session.commit()
        return result


def _counts(database):
    with Session(database.owner) as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (Event, EventObservation, EventEvidence, EventFact, EventFactSupport)
        )
