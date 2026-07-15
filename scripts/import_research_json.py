from __future__ import annotations

import json
import os
from uuid import UUID

from backend.app.config import Settings
from backend.app.database import build_engine, build_session_factory, set_request_context
from backend.app.models import User
from backend.app.providers import ManualResearchImportProvider
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


def main() -> None:
    user_id = _import_user_id()
    provider = ManualResearchImportProvider(_required_env("RESEARCH_IMPORT_FILE"))
    settings = Settings.from_env()
    engine = build_engine(settings.database_url)
    try:
        with build_session_factory(engine)() as session:
            user = session.get(User, user_id)
            if user is None or user.status != "active":
                raise RuntimeError("IMPORT_USER_ID must identify an active user")
            set_request_context(session, user.id, user.tenant_id)
            result = import_manual_research(session, user, provider)
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
