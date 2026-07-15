from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from backend.app.demo import ALPHA_TENANT_ID, BETA_TENANT_ID
from scripts.run_mock_worker import _require_explicit_demo_mode, _worker_tenant_id


@pytest.mark.parametrize("app_mode", [None, "", "production", " Demo "])
def test_mock_worker_requires_explicit_exact_demo_mode(
    monkeypatch: pytest.MonkeyPatch,
    app_mode: str | None,
) -> None:
    if app_mode is None:
        monkeypatch.delenv("APP_MODE", raising=False)
    else:
        monkeypatch.setenv("APP_MODE", app_mode)

    with pytest.raises(RuntimeError, match="explicitly set to demo"):
        _require_explicit_demo_mode()


def test_mock_worker_accepts_explicit_demo_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_MODE", "demo")

    _require_explicit_demo_mode()


@pytest.mark.parametrize("tenant_id", [ALPHA_TENANT_ID, BETA_TENANT_ID])
def test_mock_worker_accepts_only_fixed_demo_tenants(
    monkeypatch: pytest.MonkeyPatch,
    tenant_id: UUID,
) -> None:
    monkeypatch.setenv("WORKER_TENANT_ID", str(tenant_id))

    assert _worker_tenant_id() == tenant_id


def test_mock_worker_rejects_non_demo_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKER_TENANT_ID", str(uuid4()))

    with pytest.raises(RuntimeError, match="fixed fictional Demo tenant"):
        _worker_tenant_id()
