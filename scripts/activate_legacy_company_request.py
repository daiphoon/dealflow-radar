from __future__ import annotations

import argparse
import json
import os
from uuid import UUID

from backend.app.config import Settings
from backend.app.database import build_engine, build_session_factory, set_request_context
from backend.app.models import User
from backend.app.personal_features import activate_platform_company_request


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
        description="Move one audited legacy request into the on-demand research queue"
    )
    parser.add_argument("--request-id", required=True, type=UUID)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    if len(args.reason.strip()) < 3:
        raise RuntimeError("--reason must contain at least 3 characters")

    settings = Settings.from_env()
    operator_user_id = _required_uuid("ON_DEMAND_WORKER_USER_ID")
    operator_tenant_id = _required_uuid("WORKER_TENANT_ID")
    engine = build_engine(settings.database_url)
    try:
        with build_session_factory(engine)() as session:
            set_request_context(session, operator_user_id, operator_tenant_id)
            operator = session.get(User, operator_user_id)
            if operator is None or operator.tenant_id != operator_tenant_id:
                raise RuntimeError("operator user must belong to WORKER_TENANT_ID")
            output = activate_platform_company_request(
                session,
                operator,
                args.request_id,
                reason=args.reason,
            )
        print(
            json.dumps(
                {
                    "request_id": str(output.id),
                    "status": output.status,
                    "reused": output.reused,
                    "external_calls": 0,
                    "model_tokens": 0,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
