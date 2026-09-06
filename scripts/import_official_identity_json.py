from __future__ import annotations

import json
import os
from uuid import UUID

from backend.app.config import Settings
from backend.app.database import build_engine, build_session_factory, set_request_context
from backend.app.models import User
from backend.app.providers import ManualOfficialIdentityImportProvider
from backend.app.services import import_official_identities


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _import_user_id() -> UUID:
    value = _required_env("IMPORT_USER_ID")
    try:
        return UUID(value)
    except ValueError as error:
        raise RuntimeError("IMPORT_USER_ID must be a UUID") from error


def _dry_run_enabled() -> bool:
    return os.getenv("IDENTITY_IMPORT_DRY_RUN", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _dry_run_result(provider: ManualOfficialIdentityImportProvider) -> dict[str, object]:
    loaded = provider.load()
    return {
        "status": "dry_run",
        "provider": provider.code,
        "records_seen": len(loaded.batch.records),
        "queries": sorted({record.query_text for record in loaded.batch.records}),
        "official_source": loaded.batch.source.name,
        "verification_basis": loaded.batch.verification_basis,
        "external_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost": "0",
        "database_writes": 0,
    }


def main() -> None:
    provider = ManualOfficialIdentityImportProvider(_required_env("IDENTITY_IMPORT_FILE"))
    if _dry_run_enabled():
        print(
            json.dumps(
                _dry_run_result(provider),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return

    settings = Settings.from_env()
    user_id = _import_user_id()
    engine = build_engine(settings.database_url)
    try:
        with build_session_factory(engine)() as session:
            user = session.get(User, user_id)
            if user is None or user.status != "active":
                raise RuntimeError("IMPORT_USER_ID must identify an active user")
            set_request_context(session, user.id, user.tenant_id)
            result = import_official_identities(session, user, provider)
        print(
            json.dumps(
                result.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
