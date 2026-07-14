from __future__ import annotations

import json
import os
from uuid import UUID

from backend.app.config import Settings
from backend.app.database import build_engine, build_session_factory
from backend.app.worker import run_mock_worker_once


def _worker_tenant_id() -> UUID:
    value = os.getenv("WORKER_TENANT_ID", "").strip()
    if not value:
        raise RuntimeError("WORKER_TENANT_ID is required")
    try:
        return UUID(value)
    except ValueError as error:
        raise RuntimeError("WORKER_TENANT_ID must be a UUID") from error


def main() -> None:
    settings = Settings.from_env()
    if settings.app_mode != "demo":
        raise RuntimeError("Mock Worker requires APP_MODE=demo")
    engine = build_engine(settings.database_url)
    try:
        with build_session_factory(engine)() as session:
            result = run_mock_worker_once(
                session,
                _worker_tenant_id(),
                settings.refresh_policy,
            )
        print(json.dumps(result.to_dict(), ensure_ascii=False, separators=(",", ":")))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
