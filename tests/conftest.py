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
