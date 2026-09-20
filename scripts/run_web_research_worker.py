from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.config import Settings
from backend.app.database import build_engine, build_session_factory, set_request_context
from backend.app.models import User
from backend.app.web_research_service import (
    inspect_web_research_queue,
    run_web_research_worker_once,
)
from backend.app.web_search import BaiduSearchProvider, BochaSearchProvider


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _required_uuid(name: str) -> UUID:
    try:
        return UUID(_required(name))
    except ValueError as error:
        raise RuntimeError(f"{name} must be a UUID") from error


def _poll_seconds() -> int:
    try:
        value = int(os.getenv("WEB_RESEARCH_WORKER_POLL_SECONDS", "5"))
    except ValueError as error:
        raise RuntimeError("WEB_RESEARCH_WORKER_POLL_SECONDS must be an integer") from error
    if not 1 <= value <= 60:
        raise RuntimeError("WEB_RESEARCH_WORKER_POLL_SECONDS must be between 1 and 60")
    return value


def _validate_worker_safety(settings: Settings) -> None:
    if not settings.web_research_enabled:
        raise RuntimeError("WEB_RESEARCH_ENABLED must be true")
    if not settings.external_calls_enabled:
        raise RuntimeError("EXTERNAL_CALLS_ENABLED must be true")
    if not settings.web_research_calls_enabled:
        raise RuntimeError("WEB_RESEARCH_CALLS_ENABLED must be true")
    if not settings.paid_api_calls_enabled:
        raise RuntimeError("PAID_API_CALLS_ENABLED must be true for metered search APIs")
    if settings.auto_refresh_enabled and not (
        settings.web_research_policy.incremental_research_enabled
        and settings.web_research_policy.topic_planning_enabled
    ):
        raise RuntimeError("AUTO_REFRESH_ENABLED requires incremental topic planning")
    if settings.publication_policy.enabled:
        raise RuntimeError("AUTO_PUBLISH_ENABLED must remain false")
    if settings.trusted_source_calls_enabled or settings.source_monitor_scheduler_enabled:
        raise RuntimeError("trusted source monitoring switches must remain disabled")
    if settings.investor_analysis_enabled:
        raise RuntimeError("INVESTOR_ANALYSIS_ENABLED must remain false in this worker")


def _with_worker_session(
    settings: Settings,
    worker_user_id: UUID,
    worker_tenant_id: UUID,
    callback: Callable[[Session, User], dict[str, object]],
) -> dict[str, object]:
    engine = build_engine(settings.database_url)
    try:
        with build_session_factory(engine)() as session:
            set_request_context(session, worker_user_id, worker_tenant_id)
            user = session.get(User, worker_user_id)
            if user is None or user.status != "active" or user.tenant_id != worker_tenant_id:
                raise RuntimeError("web research worker user must be active in WORKER_TENANT_ID")
            return callback(session, user)
    finally:
        engine.dispose()


def _watchlist_gate() -> bool:
    current = Settings.from_env()
    if not current.web_research_policy.watchlist.enabled:
        return False
    try:
        _validate_worker_safety(current)
    except RuntimeError:
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the bounded public web research worker")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="process at most one bounded step")
    mode.add_argument("--drain", action="store_true", help="process until the queue is idle")
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="inspect work and limits without writing or calling providers",
    )
    args = parser.parse_args()
    settings = Settings.from_env()
    worker_user_id = _required_uuid("WEB_RESEARCH_WORKER_USER_ID")
    worker_tenant_id = _required_uuid("WORKER_TENANT_ID")
    if args.dry_run:
        result = _with_worker_session(
            settings,
            worker_user_id,
            worker_tenant_id,
            lambda session, user: inspect_web_research_queue(
                session,
                user,
                settings.web_research_policy,
            ),
        )
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return

    _validate_worker_safety(settings)
    policy = settings.web_research_policy
    providers = {
        "baidu": BaiduSearchProvider(
            _required("BAIDU_SEARCH_API_KEY"),
            timeout_seconds=policy.timeout_seconds,
            max_response_bytes=policy.max_response_bytes,
            user_agent=policy.user_agent,
        ),
        "bocha": BochaSearchProvider(
            _required("BOCHA_SEARCH_API_KEY"),
            timeout_seconds=policy.timeout_seconds,
            max_response_bytes=policy.max_response_bytes,
            user_agent=policy.user_agent,
        ),
    }

    def run_step() -> dict[str, object]:
        return _with_worker_session(
            settings,
            worker_user_id,
            worker_tenant_id,
            lambda session, user: run_web_research_worker_once(
                session,
                user,
                providers,
                policy,
                watchlist_gate=_watchlist_gate,
            ).to_dict(),
        )

    while True:
        output = run_step()
        print(json.dumps(output, ensure_ascii=False, separators=(",", ":")), flush=True)
        if args.once or output["status"] in {"budget_deferred", "failed"}:
            return
        if args.drain and output["status"] == "idle":
            return
        if not args.drain:
            time.sleep(_poll_seconds())


if __name__ == "__main__":
    main()
