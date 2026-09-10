from __future__ import annotations

import json
import os
from uuid import UUID

from backend.app.config import Settings
from backend.app.database import build_engine, build_session_factory, set_request_context
from backend.app.models import User
from backend.app.providers import (
    DisabledDocumentVerifier,
    HttpDocumentVerifier,
    ManualResearchImportProvider,
)
from backend.app.services import import_manual_research


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
    return os.getenv("RESEARCH_IMPORT_DRY_RUN", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _dry_run_result(
    provider: ManualResearchImportProvider,
    settings: Settings,
) -> dict[str, object]:
    loaded = provider.load()
    url_checks_planned = 0
    if settings.external_calls_enabled and settings.publication_policy.enabled:
        url_checks_planned = min(
            len(loaded.batch.records),
            settings.publication_policy.max_source_url_checks_per_import,
        )
    return {
        "status": "dry_run",
        "provider": provider.code,
        "records_seen": len(loaded.batch.records),
        "target_companies": sorted(
            {record.company_identity_evidence.legal_name for record in loaded.batch.records}
        ),
        "publication_policy_version": settings.publication_policy.version,
        "auto_publish_enabled": settings.publication_policy.enabled,
        "external_calls_enabled": settings.external_calls_enabled,
        "tender_events_enabled": settings.tender_events_enabled,
        "tender_records_planned": sum(
            settings.tender_events_enabled and record.event_subtype == "tender_notice"
            for record in loaded.batch.records
        ),
        "source_url_checks_upper_bound": url_checks_planned,
        "verification_attempts_upper_bound": url_checks_planned * 2,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost": "0",
        "database_writes": 0,
    }


def main() -> None:
    provider = ManualResearchImportProvider(_required_env("RESEARCH_IMPORT_FILE"))
    settings = Settings.from_env()
    if _dry_run_enabled():
        print(
            json.dumps(
                _dry_run_result(provider, settings),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return

    user_id = _import_user_id()
    engine = build_engine(settings.database_url)
    try:
        with build_session_factory(engine)() as session:
            user = session.get(User, user_id)
            if user is None or user.status != "active":
                raise RuntimeError("IMPORT_USER_ID must identify an active user")
            set_request_context(session, user.id, user.tenant_id)
            verifier = (
                HttpDocumentVerifier(settings.publication_policy.source_url_timeout_seconds)
                if settings.external_calls_enabled
                else DisabledDocumentVerifier()
            )
            result = import_manual_research(
                session,
                user,
                provider,
                settings.publication_policy,
                verifier,
                tender_events_enabled=settings.tender_events_enabled,
            )
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
