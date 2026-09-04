from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from uuid import UUID

from sqlalchemy import select

from backend.app.config import Settings
from backend.app.database import build_engine, build_session_factory, set_request_context
from backend.app.fact_support import materialize_event_fact_ledger
from backend.app.models import (
    PLATFORM_SHARED_SCOPE,
    Event,
    EventFactSupport,
    User,
)
from backend.app.services import user_has_role


def _required_uuid(name: str) -> UUID:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    try:
        return UUID(value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a UUID") from error


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reassess one shared event's fact support without external calls"
    )
    parser.add_argument("--event-id", type=UUID, required=True)
    args = parser.parse_args()
    settings = Settings.from_env()
    worker_user_id = _required_uuid("WEB_RESEARCH_WORKER_USER_ID")
    worker_tenant_id = _required_uuid("WORKER_TENANT_ID")
    engine = build_engine(settings.database_url)
    try:
        with build_session_factory(engine)() as session:
            set_request_context(session, worker_user_id, worker_tenant_id)
            user = session.get(User, worker_user_id)
            if (
                user is None
                or user.status != "active"
                or user.tenant_id != worker_tenant_id
                or not user_has_role(session, user.id, "platform_admin")
            ):
                raise RuntimeError("fact support reassessment requires an active platform admin")
            event = session.get(Event, args.event_id)
            if event is None or event.visibility_scope != PLATFORM_SHARED_SCOPE:
                raise RuntimeError("event must be a readable platform-shared event")
            facts = materialize_event_fact_ledger(session, event)
            statuses = Counter(
                session.scalars(
                    select(EventFactSupport.support_status).where(
                        EventFactSupport.event_id == event.id
                    )
                )
            )
            session.commit()
            print(
                json.dumps(
                    {
                        "event_id": str(event.id),
                        "fact_count": len(facts),
                        "support_status_counts": dict(sorted(statuses.items())),
                        "external_calls": 0,
                        "model_tokens": 0,
                        "auto_published_events": 0,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
