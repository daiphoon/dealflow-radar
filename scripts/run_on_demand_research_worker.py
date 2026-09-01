from __future__ import annotations

import argparse
import fcntl
import json
import os
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select

from backend.app.config import Settings
from backend.app.database import build_engine, build_session_factory, set_request_context
from backend.app.models import PersonalCompanyRequest, User
from backend.app.on_demand_research import (
    run_on_demand_worker_once,
    validate_on_demand_worker_user,
)
from backend.app.tianyancha import TianyanchaIdentityProvider


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
    raw = os.getenv("ON_DEMAND_WORKER_POLL_SECONDS", "5").strip()
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError("ON_DEMAND_WORKER_POLL_SECONDS must be an integer") from error
    if not 1 <= value <= 60:
        raise RuntimeError("ON_DEMAND_WORKER_POLL_SECONDS must be between 1 and 60")
    return value


def _provider_authorization() -> str:
    direct = os.getenv("TIANYANCHA_AUTHORIZATION", "").strip()
    file_name = os.getenv("TIANYANCHA_AUTHORIZATION_FILE", "").strip()
    if direct and file_name:
        raise RuntimeError(
            "set only one of TIANYANCHA_AUTHORIZATION and TIANYANCHA_AUTHORIZATION_FILE"
        )
    if direct:
        return direct
    if not file_name:
        raise RuntimeError("TIANYANCHA_AUTHORIZATION or TIANYANCHA_AUTHORIZATION_FILE is required")

    path = Path(file_name)
    if not path.is_absolute():
        raise RuntimeError("TIANYANCHA_AUTHORIZATION_FILE must be an absolute path")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RuntimeError("TIANYANCHA_AUTHORIZATION_FILE is not readable") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("TIANYANCHA_AUTHORIZATION_FILE must be a regular file")
        if metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise RuntimeError("TIANYANCHA_AUTHORIZATION_FILE must be owner-only")
        if metadata.st_size > 8_192:
            raise RuntimeError("TIANYANCHA_AUTHORIZATION_FILE is too large")
        with os.fdopen(descriptor, encoding="utf-8") as handle:
            descriptor = -1
            authorization = handle.read().strip()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not authorization:
        raise RuntimeError("TIANYANCHA_AUTHORIZATION_FILE is empty")
    return authorization


@contextmanager
def _exclusive_worker_lock() -> Iterator[None]:
    path = Path(
        os.getenv(
            "ON_DEMAND_WORKER_LOCK_FILE",
            "/app/data/private/provider_cache/.on-demand-worker.lock",
        )
    )
    if not path.is_absolute():
        raise RuntimeError("ON_DEMAND_WORKER_LOCK_FILE must be an absolute path")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(
                "another on-demand research worker already holds the lock"
            ) from error
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()}\n".encode())
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _validate_worker_safety(settings: Settings) -> None:
    if not settings.on_demand_research_enabled:
        raise RuntimeError("ON_DEMAND_RESEARCH_ENABLED must be true")
    if not settings.external_calls_enabled:
        raise RuntimeError("EXTERNAL_CALLS_ENABLED must be true for the dedicated worker")
    if not settings.tianyancha_identity_calls_enabled:
        raise RuntimeError("TIANYANCHA_IDENTITY_CALLS_ENABLED must be true")
    if settings.paid_api_calls_enabled:
        raise RuntimeError("PAID_API_CALLS_ENABLED must remain false")
    if settings.auto_refresh_enabled:
        raise RuntimeError("AUTO_REFRESH_ENABLED must remain false")
    if settings.publication_policy.enabled:
        raise RuntimeError("AUTO_PUBLISH_ENABLED must remain false")
    if settings.trusted_source_calls_enabled or settings.source_monitor_scheduler_enabled:
        raise RuntimeError("trusted-source external workers must remain disabled")
    worst_case_identity_calls = 2 * (settings.tianyancha_identity_policy.retry_limit + 1)
    if (
        worst_case_identity_calls
        > settings.on_demand_research_policy.max_provider_calls_per_company
    ):
        raise RuntimeError("identity retry policy exceeds ON_DEMAND_MAX_PROVIDER_CALLS_PER_COMPANY")


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
            validate_on_demand_worker_user(session, user)
            queued = int(
                session.scalar(
                    select(func.count())
                    .select_from(PersonalCompanyRequest)
                    .where(
                        PersonalCompanyRequest.status.in_(
                            ["identity_queued", "research_queued", "budget_deferred"]
                        )
                    )
                )
                or 0
            )
        return {
            "status": "dry_run",
            "queued_requests": queued,
            "provider_daily_limit": settings.on_demand_research_policy.provider_daily_call_limit,
            "provider_monthly_limit": (
                settings.on_demand_research_policy.provider_monthly_call_limit
            ),
            "effective_daily_limit": (
                settings.on_demand_research_policy.effective_daily_call_limit
            ),
            "effective_monthly_limit": (
                settings.on_demand_research_policy.effective_monthly_call_limit
            ),
            "research_calls_enabled": settings.tianyancha_research_calls_enabled,
            "external_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost": "0",
        }
    finally:
        engine.dispose()


def _run_once(
    settings: Settings,
    provider: TianyanchaIdentityProvider,
    session_factory,
    *,
    worker_user_id: UUID,
    worker_tenant_id: UUID,
) -> dict[str, object]:
    with session_factory() as session:
        set_request_context(session, worker_user_id, worker_tenant_id)
        user = session.get(User, worker_user_id)
        if user is None or user.tenant_id != worker_tenant_id:
            raise RuntimeError("worker user must belong to WORKER_TENANT_ID")
        validate_on_demand_worker_user(session, user)
        result = run_on_demand_worker_once(
            session,
            user,
            provider,
            settings.on_demand_research_policy,
            provider_retry_limit=settings.tianyancha_identity_policy.retry_limit,
            research_calls_enabled=settings.tianyancha_research_calls_enabled,
        )
    return result.to_dict()


def _healthcheck(
    settings: Settings,
    *,
    worker_user_id: UUID,
    worker_tenant_id: UUID,
) -> dict[str, object]:
    _validate_worker_safety(settings)
    output = _dry_run(
        settings,
        worker_user_id=worker_user_id,
        worker_tenant_id=worker_tenant_id,
    )
    output["status"] = "healthy"
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the controlled on-demand research worker")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="process at most one queue item")
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="inspect queue without a provider call",
    )
    mode.add_argument(
        "--healthcheck",
        action="store_true",
        help="verify worker safety, database access, and worker identity without provider access",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    worker_user_id = _required_uuid("ON_DEMAND_WORKER_USER_ID")
    worker_tenant_id = _required_uuid("WORKER_TENANT_ID")
    if args.healthcheck:
        print(
            json.dumps(
                _healthcheck(
                    settings,
                    worker_user_id=worker_user_id,
                    worker_tenant_id=worker_tenant_id,
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return
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
    with _exclusive_worker_lock():
        authorization = _provider_authorization()
        provider = TianyanchaIdentityProvider(
            None,
            authorization=authorization,
            policy=settings.tianyancha_identity_policy,
        )
        engine = build_engine(settings.database_url)
        session_factory = build_session_factory(engine)
        try:
            while True:
                provider.begin_run()
                output = _run_once(
                    settings,
                    provider,
                    session_factory,
                    worker_user_id=worker_user_id,
                    worker_tenant_id=worker_tenant_id,
                )
                print(json.dumps(output, ensure_ascii=False, separators=(",", ":")), flush=True)
                if args.once:
                    return
                if output["status"] == "idle":
                    time.sleep(_poll_seconds())
        finally:
            engine.dispose()
            provider.close()


if __name__ == "__main__":
    main()
