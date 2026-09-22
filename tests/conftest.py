from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.providers import MockResearchProvider
from backend.app.services import seed_demo_entities

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def manual_import_payload() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "batch_id": "manual-batch-001",
        "queried_at": "2026-07-15T08:00:00+08:00",
        "research_tool": "manual",
        "agent_name": "本地研究",
        "original_query": "示例公司公开信息",
        "target_company_hint": "示例星河科技一号有限公司",
        "license_status": "public",
        "records": [
            {
                "external_record_id": "manual-record-001",
                "company_identity_evidence": {
                    "legal_name": "示例星河科技一号有限公司",
                    "credit_code": None,
                    "registered_region": "虚构省甲市",
                    "official_website": None,
                },
                "source_code": "manual_example_official",
                "source_name": "示例官方来源",
                "canonical_url": "https://example.invalid/manual-001",
                "source_published_at": "2026-07-14T10:00:00+08:00",
                "occurred_at": None,
                "title": "示例公司签署公开测试合同",
                "evidence_excerpt": "公开材料显示公司签署了一份测试合同，未披露金额。",
                "event_type": "contract_commercial",
                "event_subtype": "major_contract",
                "direction": "positive",
                "materiality_score": 70,
                "risk_severity": "low",
                "confidence_score": 0.95,
                "source_quality": "A",
                "facts": [{"name": "contract", "value": "测试合同", "unit": None}],
                "uncertainties": ["未披露金额"],
                "requires_human_review": True,
            }
        ],
    }


@pytest.fixture
def migrated_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    database_url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head")
    app = create_app(
        Settings(
            database_url=database_url,
            app_mode="demo",
            external_calls_enabled=False,
            paid_api_calls_enabled=False,
            auto_refresh_enabled=False,
        )
    )
    with app.state.session_factory() as session:
        seed_demo_entities(session, MockResearchProvider().load())
        session.commit()
    yield app
    app.state.engine.dispose()


@pytest.fixture
def client(migrated_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(migrated_app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def block_real_http_by_default(monkeypatch, request):
    import os

    import httpx

    if (
        request.node.get_closest_marker("external")
        and os.environ.get("RUN_EXTERNAL_TESTS") == "true"
    ):
        return

    def blocked(*args, **kwargs):
        pytest.fail("Real HTTP disabled in offline tests; inject a mock provider")

    async def blocked_async(*args, **kwargs):
        pytest.fail("Real HTTP disabled in offline tests; inject a mock provider")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked_async)
