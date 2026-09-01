from __future__ import annotations

import argparse
import json
import os
import time
from uuid import UUID

from sqlalchemy import func, select

from backend.app.config import Settings
from backend.app.database import build_engine, build_session_factory, set_request_context
from backend.app.deepseek import DeepSeekInvestorAnalysisProvider
from backend.app.investor_analysis import (
    run_investor_analysis_worker_once,
    validate_investor_analysis_worker_user,
)
from backend.app.models import Event, InvestorChangeAnalysis, User


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
        value = int(os.getenv("INVESTOR_ANALYSIS_WORKER_POLL_SECONDS", "5"))
    except ValueError as error:
        raise RuntimeError("INVESTOR_ANALYSIS_WORKER_POLL_SECONDS must be an integer") from error
    if not 1 <= value <= 60:
        raise RuntimeError("INVESTOR_ANALYSIS_WORKER_POLL_SECONDS must be between 1 and 60")
    return value


def _validate_worker_safety(settings: Settings) -> None:
    if not settings.investor_analysis_enabled:
        raise RuntimeError("INVESTOR_ANALYSIS_ENABLED must be true")
    if not settings.external_calls_enabled:
        raise RuntimeError("EXTERNAL_CALLS_ENABLED must be true")
    if not settings.paid_api_calls_enabled:
        raise RuntimeError("PAID_API_CALLS_ENABLED must be true for DeepSeek")
    if settings.auto_refresh_enabled:
        raise RuntimeError("AUTO_REFRESH_ENABLED must remain false")
    if settings.publication_policy.enabled:
        raise RuntimeError("AUTO_PUBLISH_ENABLED must remain false")
    if settings.trusted_source_calls_enabled or settings.source_monitor_scheduler_enabled:
        raise RuntimeError("trusted source calls must remain disabled in the analysis worker")
    policy = settings.investor_analysis_policy
    if policy.input_cost_per_million_tokens == 0 and policy.output_cost_per_million_tokens == 0:
        raise RuntimeError("configured DeepSeek token prices are required before real calls")


def _dry_run(
    settings: Settings,
    *,
    worker_user_id: UUID,
    worker_tenant_id: UUID,
) -> dict[str, object]:
    engine = build_engine(settings.database_url)
    try:
        with build_session_factory(engine)() as session:
            set_request_context(session, worker_user_id, worker_tenant_id)
            user = session.get(User, worker_user_id)
            if user is None or user.tenant_id != worker_tenant_id:
                raise RuntimeError("worker user must belong to WORKER_TENANT_ID")
            validate_investor_analysis_worker_user(session, user)
            eligible_events = int(
                session.scalar(
                    select(func.count())
                    .select_from(Event)
                    .where(
                        Event.visibility_scope == "platform_shared",
                        Event.status == "published",
                        Event.publication_route == "deterministic_change",
                        Event.materiality_score
                        >= settings.investor_analysis_policy.min_materiality_score,
                    )
                )
                or 0
            )
            pending = int(
                session.scalar(
                    select(func.count())
                    .select_from(InvestorChangeAnalysis)
                    .where(InvestorChangeAnalysis.status.in_(["pending", "budget_deferred"]))
                )
                or 0
            )
        return {
            "status": "dry_run",
            "eligible_change_events": eligible_events,
            "pending_analyses": pending,
            "provider": settings.investor_analysis_policy.provider,
            "model": settings.investor_analysis_policy.model,
            "monthly_token_limit": settings.investor_analysis_policy.monthly_token_limit,
            "external_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost": "0",
        }
    finally:
        engine.dispose()


def _run_once(
    settings: Settings,
    provider: DeepSeekInvestorAnalysisProvider,
    *,
    worker_user_id: UUID,
    worker_tenant_id: UUID,
) -> dict[str, object]:
    engine = build_engine(settings.database_url)
    try:
        with build_session_factory(engine)() as session:
            set_request_context(session, worker_user_id, worker_tenant_id)
            user = session.get(User, worker_user_id)
            if user is None or user.tenant_id != worker_tenant_id:
                raise RuntimeError("worker user must belong to WORKER_TENANT_ID")
            result = run_investor_analysis_worker_once(
                session,
                user,
                provider,
                settings.investor_analysis_policy,
            )
        return result.to_dict()
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the investor change analysis worker")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="process at most one analysis")
    mode.add_argument(
        "--drain",
        action="store_true",
        help="process the eligible queue and exit when it is empty",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="inspect eligible changes without creating jobs or calling a model",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    worker_user_id = _required_uuid("ANALYSIS_WORKER_USER_ID")
    worker_tenant_id = _required_uuid("WORKER_TENANT_ID")
    if args.dry_run:
        print(
            json.dumps(
                _dry_run(
                    settings,
                    worker_user_id=worker_user_id,
                    worker_tenant_id=worker_tenant_id,
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return

    _validate_worker_safety(settings)
    provider = DeepSeekInvestorAnalysisProvider(
        api_key=_required("DEEPSEEK_API_KEY"),
        policy=settings.investor_analysis_policy,
    )
    try:
        while True:
            output = _run_once(
                settings,
                provider,
                worker_user_id=worker_user_id,
                worker_tenant_id=worker_tenant_id,
            )
            print(json.dumps(output, ensure_ascii=False, separators=(",", ":")), flush=True)
            if args.once:
                return
            if args.drain and output["status"] == "idle":
                return
            if output["status"] == "idle":
                time.sleep(_poll_seconds())
    finally:
        provider.close()


if __name__ == "__main__":
    main()
