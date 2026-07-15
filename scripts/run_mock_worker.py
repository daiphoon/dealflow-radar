from __future__ import annotations

import json
import os
from uuid import UUID

from backend.app.config import Settings
from backend.app.database import build_engine, build_session_factory
from backend.app.demo import DEMO_TENANT_IDS
from backend.app.worker import run_mock_worker_once


def _require_explicit_demo_mode() -> None:
    if os.getenv("APP_MODE") != "demo":
        raise RuntimeError("APP_MODE must be explicitly set to demo for Mock Worker")


def _worker_tenant_id() -> UUID:
    value = os.getenv("WORKER_TENANT_ID", "").strip()
    if not value:
        raise RuntimeError("WORKER_TENANT_ID is required")
    try:
        tenant_id = UUID(value)
    except ValueError as error:
        raise RuntimeError("WORKER_TENANT_ID must be a UUID") from error
    if tenant_id not in DEMO_TENANT_IDS:
        raise RuntimeError("WORKER_TENANT_ID must be a fixed fictional Demo tenant")
    return tenant_id


def main() -> None:
    _require_explicit_demo_mode()
    tenant_id = _worker_tenant_id()
    settings = Settings.from_env()
    engine = build_engine(settings.database_url)
    try:
        with build_session_factory(engine)() as session:
            result = run_mock_worker_once(
                session,
                tenant_id,
                settings.refresh_policy,
            )
        print(json.dumps(result.to_dict(), ensure_ascii=False, separators=(",", ":")))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
