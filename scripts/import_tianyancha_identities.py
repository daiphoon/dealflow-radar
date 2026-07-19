from __future__ import annotations

import json
import os
from uuid import UUID

from backend.app.config import Settings
from backend.app.database import build_engine, build_session_factory, set_request_context
from backend.app.models import User
from backend.app.providers import DEFAULT_PRIVATE_IDENTITY_IMPORT_ROOT
from backend.app.services import import_official_identities
from backend.app.tianyancha import (
    TianyanchaIdentityProvider,
    load_tianyancha_identity_manifest,
)


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _dry_run_enabled() -> bool:
    return _as_bool(os.getenv("TIANYANCHA_IDENTITY_DRY_RUN", "false"))


def _import_user_id() -> UUID:
    try:
        return UUID(_required_env("IMPORT_USER_ID"))
    except ValueError as error:
        raise RuntimeError("IMPORT_USER_ID must be a UUID") from error


def _validate_real_run_settings(settings: Settings) -> None:
    if settings.app_mode != "demo":
        raise RuntimeError("Tianyancha identity V1 is limited to APP_MODE=demo")
    if not settings.external_calls_enabled:
        raise RuntimeError("EXTERNAL_CALLS_ENABLED must be true for this controlled run")
    if not settings.tianyancha_identity_calls_enabled:
        raise RuntimeError("TIANYANCHA_IDENTITY_CALLS_ENABLED must be true for this controlled run")
    if settings.paid_api_calls_enabled:
        raise RuntimeError("PAID_API_CALLS_ENABLED must remain false")
    if settings.auto_refresh_enabled:
        raise RuntimeError("AUTO_REFRESH_ENABLED must remain false")
    if settings.publication_policy.enabled:
        raise RuntimeError("AUTO_PUBLISH_ENABLED must remain false")
    if settings.trusted_source_calls_enabled:
        raise RuntimeError("TRUSTED_SOURCE_CALLS_ENABLED must remain false during this run")


def _dry_run_result(manifest_path: str) -> dict[str, object]:
    manifest = load_tianyancha_identity_manifest(
        manifest_path,
        allowed_root=DEFAULT_PRIVATE_IDENTITY_IMPORT_ROOT,
    )
    return {
        "status": "dry_run",
        "provider": "tianyancha_licensed_identity",
        "companies": len(manifest.queries),
        "planned_requests": len(manifest.queries) * 2,
        "queries": [query.model_dump(mode="json") for query in manifest.queries],
        "external_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost": "0",
        "database_writes": 0,
    }


def main() -> None:
    manifest_path = _required_env("TIANYANCHA_IDENTITY_MANIFEST")
    if _dry_run_enabled():
        print(
            json.dumps(
                _dry_run_result(manifest_path),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return

    settings = Settings.from_env()
    _validate_real_run_settings(settings)
    provider = TianyanchaIdentityProvider(
        manifest_path,
        authorization=_required_env("TIANYANCHA_AUTHORIZATION"),
        policy=settings.tianyancha_identity_policy,
    )
    user_id = _import_user_id()
    engine = build_engine(settings.database_url)
    try:
        with build_session_factory(engine)() as session:
            user = session.get(User, user_id)
            if user is None or user.status != "active":
                raise RuntimeError("IMPORT_USER_ID must identify an active user")
            set_request_context(session, user.id, user.tenant_id)
            result = import_official_identities(session, user, provider)
        output = result.model_dump(mode="json")
        output["cache_hits"] = provider.cache_hits
        output["input_tokens"] = 0
        output["output_tokens"] = 0
        print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
