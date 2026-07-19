from __future__ import annotations

import json
import os
from uuid import UUID

from backend.app.config import Settings
from backend.app.database import build_engine, build_session_factory, set_request_context
from backend.app.models import User
from backend.app.source_monitoring import queue_due_source_checks


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
        raise RuntimeError("APP_MODE must be explicitly set to demo for the V1 source scheduler")


def _dry_run_enabled() -> bool:
    return os.getenv("SOURCE_MONITOR_SCHEDULER_DRY_RUN", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _validate_scheduler_safety(settings: Settings, *, dry_run: bool) -> None:
    if settings.paid_api_calls_enabled or settings.publication_policy.enabled:
        raise RuntimeError("PAID_API_CALLS_ENABLED and AUTO_PUBLISH_ENABLED must be false")
    if dry_run:
        return
    if not all(
        (
            settings.auto_refresh_enabled,
            settings.source_monitor_scheduler_enabled,
            settings.external_calls_enabled,
            settings.trusted_source_calls_enabled,
        )
    ):
        raise RuntimeError(
            "actual scheduling requires AUTO_REFRESH_ENABLED, "
            "SOURCE_MONITOR_SCHEDULER_ENABLED, EXTERNAL_CALLS_ENABLED and "
            "TRUSTED_SOURCE_CALLS_ENABLED"
        )


def main() -> None:
    _require_explicit_demo_mode()
    tenant_id = _required_uuid("WORKER_TENANT_ID")
    user_id = _required_uuid("SOURCE_MONITOR_WORKER_USER_ID")
    dry_run = _dry_run_enabled()
    settings = Settings.from_env()
    _validate_scheduler_safety(settings, dry_run=dry_run)
    engine = build_engine(settings.database_url)
    try:
        with build_session_factory(engine)() as session:
            set_request_context(session, user_id, tenant_id)
            user = session.get(User, user_id)
            if user is None or user.status != "active" or user.tenant_id != tenant_id:
                raise RuntimeError("source scheduler user must be active in WORKER_TENANT_ID")
            result = queue_due_source_checks(
                session,
                user,
                settings,
                dry_run=dry_run,
            )
        print(json.dumps(result.to_dict(), ensure_ascii=False, separators=(",", ":")))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
