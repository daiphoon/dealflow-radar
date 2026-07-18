from __future__ import annotations

import json
import os
from uuid import UUID

from backend.app.config import Settings
from backend.app.database import build_engine, build_session_factory, set_request_context
from backend.app.models import User
from backend.app.source_monitoring import run_trusted_source_worker_once


def _required_uuid(name: str) -> UUID:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    try:
        return UUID(value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a UUID") from error


def _require_explicit_demo_mode() -> None:
    if os.getenv("APP_MODE") != "demo":
        raise RuntimeError("APP_MODE must be explicitly set to demo for the V1 source worker")


def _validate_source_worker_safety(settings: Settings) -> None:
    if (
        settings.paid_api_calls_enabled
        or settings.auto_refresh_enabled
        or settings.publication_policy.enabled
    ):
        raise RuntimeError(
            "PAID_API_CALLS_ENABLED, AUTO_REFRESH_ENABLED and AUTO_PUBLISH_ENABLED must be false"
        )


def main() -> None:
    _require_explicit_demo_mode()
    tenant_id = _required_uuid("WORKER_TENANT_ID")
    user_id = _required_uuid("SOURCE_MONITOR_WORKER_USER_ID")
    settings = Settings.from_env()
    _validate_source_worker_safety(settings)
    engine = build_engine(settings.database_url)
    try:
        with build_session_factory(engine)() as session:
            set_request_context(session, user_id, tenant_id)
            user = session.get(User, user_id)
            if user is None or user.status != "active" or user.tenant_id != tenant_id:
                raise RuntimeError("source worker user must be active in WORKER_TENANT_ID")
            result = run_trusted_source_worker_once(session, user, settings)
        print(json.dumps(result.to_dict(), ensure_ascii=False, separators=(",", ":")))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
