import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from backend.app.demo import ALPHA_TENANT_ID, MOCK_SOURCE_ID, demo_uuid
from backend.app.models import OfficialIdentityVerification, RawDocument, utc_now
from backend.app.providers import MockResearchProvider
from backend.app.services import seed_demo_entities


@pytest.fixture(params=["sqlite", "postgresql"])
def migration_database(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Engine]:
    admin = None
    database_name = f"exchange_migration_{uuid4().hex}"
    if request.param == "postgresql":
        admin_url = os.getenv("DATABASE_ADMIN_URL")
        if not admin_url:
            pytest.skip("set DATABASE_ADMIN_URL to test isolated PostgreSQL migrations")
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        url = make_url(admin_url).set(database=database_name).render_as_string(hide_password=False)
    else:
        url = f"sqlite:///{tmp_path / 'exchange-migration.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()
        if admin is not None:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{database_name}"'))
            admin.dispose()


@pytest.mark.parametrize("blocking_table", ["companies", "official_identity_verifications"])
def test_exchange_migration_preserves_rows_and_refuses_lossy_downgrade(
    migration_database: Engine, blocking_table: str
) -> None:
    config = Config("alembic.ini")
    command.upgrade(config, "0025")
    engine = migration_database
    company_id = demo_uuid("company-示例星河科技一号有限公司")
    with Session(engine) as session:
        seed_demo_entities(session, MockResearchProvider().load())
        document = RawDocument(
            source_id=MOCK_SOURCE_ID,
            visibility_scope="organization_private",
            owner_tenant_id=ALPHA_TENANT_ID,
            external_record_id="identity-migration-test",
            canonical_url="https://www.example.gov.cn/identity",
            title="虚构核验资料",
            content_hash="a" * 64,
            document_dedupe_key="b" * 64,
            license_status="public",
            payload={"original": "must-preserve"},
        )
        session.add(document)
        session.flush()
        session.add(
            OfficialIdentityVerification(
                tenant_id=ALPHA_TENANT_ID,
                company_id=company_id,
                raw_document_id=document.id,
                query_text="虚构核验",
                legal_name="示例星河科技一号有限公司",
                credit_code="91310000MA1K000006",
                registered_region="虚构省甲市",
                registration_status="存续",
                verification_status="verified",
                verification_basis="official_government",
                match_rule="official_credit_code_exact",
                checked_at=utc_now(),
            )
        )
        session.commit()
    tables = ["companies", "official_identity_verifications", "raw_documents", "investments"]

    def snapshot() -> dict:
        with engine.connect() as connection:
            return {
                table: connection.execute(text(f"SELECT * FROM {table} ORDER BY id")).all()
                for table in tables
            }

    before = snapshot()
    command.upgrade(config, "head")
    command.check(config)
    assert snapshot() == before
    command.downgrade(config, "0025")
    assert snapshot() == before
    command.upgrade(config, "head")
    with engine.begin() as connection:
        column = (
            "identity_verification_basis" if blocking_table == "companies" else "verification_basis"
        )
        connection.execute(text(f"UPDATE {blocking_table} SET {column} = 'exchange_disclosure'"))
    with pytest.raises(RuntimeError, match="Cannot downgrade 0026"):
        command.downgrade(config, "0025")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0026"
        assert (
            connection.scalar(
                text(f"SELECT count(*) FROM {blocking_table} WHERE {column}='exchange_disclosure'")
            )
            > 0
        )
    for table in tables[:2]:
        assert any(
            "exchange_disclosure" in constraint["sqltext"]
            for constraint in inspect(engine).get_check_constraints(table)
        )
