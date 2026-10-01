"""版本化隔离验证入口；只替换 Provider 边界，不复制 Worker 业务循环。"""

import argparse
import hashlib
import json
import os
import time
import traceback
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from backend.app.business_dates import reference_fields
from backend.app.config import Settings
from backend.app.database import build_engine, build_session_factory, request_session
from backend.app.evidence_integrity import hash_canonical_object
from backend.app.models import (
    Company,
    CompanyResearchJob,
    PersonalCompanyRequest,
    UsageLedger,
    User,
)
from backend.app.research_completion import stored_completion
from backend.app.web_research_service import ACTIVE_JOB_STATUSES, run_web_research_worker_once
from scripts.research_validation_contract import assert_binding, prepare_bound_jobs, runtime_binding
from scripts.run_web_research_worker import _validate_worker_safety

ISOLATION_MARKER = "isolated-research-validation-v1"


@dataclass
class ProviderBoundaries:
    providers: dict
    fetcher_factory: object = None
    matter_provider: object = None
    document_provider: object = None

    def close(self):
        for provider in [*self.providers.values(), self.matter_provider, self.document_provider]:
            if provider is not None and hasattr(provider, "close"):
                provider.close()


def readonly_settings(settings: Settings) -> Settings:
    """报告/页面进程只读查询配置；保留正式事项功能组合。"""
    return replace(
        settings,
        external_calls_enabled=False,
        paid_api_calls_enabled=False,
        auto_refresh_enabled=False,
        web_research_enabled=False,
        web_research_calls_enabled=False,
        trusted_source_calls_enabled=False,
        source_monitor_scheduler_enabled=False,
        investor_analysis_enabled=False,
        web_research_policy=replace(
            settings.web_research_policy,
            watchlist=replace(settings.web_research_policy.watchlist, enabled=False),
        ),
        publication_policy=replace(settings.publication_policy, enabled=False),
    )


def execution_binding(settings, module_root, identity_path, reference_at):
    binding = runtime_binding(
        module_root, identity_path, settings.web_research_policy, reference_at
    )
    url = make_url(settings.database_url)
    binding["database_target"] = {
        "host": url.host,
        "port": url.port,
        "database": url.database,
        "role": url.username,
    }
    binding["switches"] = {
        key: value
        for key, value in asdict(settings).items()
        if key.endswith("_enabled") or key == "app_mode"
    }
    binding["runner_modules"] = {
        name: hashlib.sha256((Path(module_root) / name).read_bytes()).hexdigest()
        for name in (
            "scripts/research_validation_runner.py",
            "scripts/research_validation_contract.py",
            "scripts/run_web_research_worker.py",
        )
    }
    return binding


def _save(path, payload, *, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("x" if exclusive else "w") as output:
        path.chmod(0o600)
        json.dump(payload, output, ensure_ascii=False, indent=2, default=str)
        output.write("\n")


def _error_record(error, stage, settings):
    stack = traceback.format_exc()
    for secret in [
        make_url(settings.database_url).password,
        *[
            value
            for key, value in os.environ.items()
            if key.endswith(("API_KEY", "TOKEN", "PASSWORD", "SECRET"))
        ],
    ]:
        if secret:
            stack = stack.replace(secret, "<redacted>")
    return {"class": type(error).__name__, "stage": stage, "traceback": stack}


def _validate_input(contract, settings):
    reference = datetime.fromisoformat(contract["reference_at"])
    fields = reference_fields(reference, settings.web_research_policy.recent_change_window_days)
    assert_binding(
        contract["binding"],
        execution_binding(settings, contract["module_root"], contract["identity_path"], reference),
    )
    companies = contract["binding"]["company_order"]
    if (
        len(companies) != 1
        or not companies[0].get("reference_at")
        or not companies[0].get("event_window")
    ):
        raise ValueError("single_frozen_identity_and_window_required")
    if contract["mode"] not in {"identity_search", "given_sources"}:
        raise ValueError("invalid_validation_mode")
    sources = contract.get("source_urls", [])
    if (contract["mode"] == "identity_search" and sources) or (
        contract["mode"] == "given_sources" and not sources
    ):
        raise ValueError("source_mode_mismatch")
    from backend.app.web_research_service import _canonical_candidate_url

    if len(sources) > settings.web_research_policy.max_documents_per_job or any(
        _canonical_candidate_url(url) != url for url in sources
    ):
        raise ValueError("unapproved_given_source")
    if not 1 <= contract["max_steps"] <= 256 or not 1 <= contract["outer_seconds"] <= 1215:
        raise ValueError("invalid_outer_limits")
    policy = settings.web_research_policy
    if (
        policy.watchlist.enabled
        or settings.auto_refresh_enabled
        or settings.publication_policy.enabled
    ):
        raise ValueError("unrelated_execution_switch_enabled")
    if contract["boundary_mode"] == "metered":
        _validate_worker_safety(settings)
    elif (
        contract["boundary_mode"] != "mock"
        or settings.external_calls_enabled
        or settings.paid_api_calls_enabled
    ):
        raise ValueError("invalid_provider_boundary_mode")
    return reference, fields


def _validate_database(session, contract):
    if session.get_bind().dialect.name != "postgresql":
        raise ValueError("isolated_postgresql_required")
    row = session.execute(
        text(
            "SELECT current_database(), current_user, rolsuper, rolbypassrls, "
            "rolcreatedb, rolcreaterole, "
            "EXISTS(SELECT 1 FROM pg_class WHERE relowner=pg_roles.oid AND relnamespace="
            "'public'::regnamespace), shobj_description((SELECT oid FROM pg_database WHERE datname="
            "current_database()), 'pg_database') FROM pg_roles WHERE rolname=current_user"
        )
    ).one()
    target = contract["binding"]["database_target"]
    if (
        row[:2] != (target["database"], target["role"])
        or any(row[2:7])
        or row[7] != ISOLATION_MARKER
    ):
        raise ValueError("isolated_non_owner_database_required")
    if session.scalar(text("SELECT version_num FROM alembic_version")) != "0036":
        raise ValueError("validation_schema_mismatch")


def _queue(session, request_id, company_id, job_id=None):
    request = session.get(PersonalCompanyRequest, request_id)
    company = session.get(Company, company_id)
    if (
        request is None
        or request.company_id != company_id
        or company is None
        or company.identity_status != "verified"
    ):
        raise ValueError("verified_business_request_required")
    requests = session.scalars(
        select(PersonalCompanyRequest).where(
            PersonalCompanyRequest.status.in_(
                (
                    "pending",
                    "in_review",
                    "research_queued",
                    "researching",
                    "identity_queued",
                    "identity_checking",
                    "partial",
                )
            )
        )
    ).all()
    jobs = session.scalars(
        select(CompanyResearchJob).where(CompanyResearchJob.status.in_(ACTIVE_JOB_STATUSES))
    ).all()
    if any(row.id != request_id for row in requests) or any(row.id != job_id for row in jobs):
        raise ValueError("exclusive_business_queue_required")
    if request.status in {"identity_queued", "identity_checking"}:
        raise ValueError("online_identity_research_not_authorized")
    return request, company


def _snapshot(session, job_id):
    job = session.get(CompanyResearchJob, job_id)
    if job is None:
        raise ValueError("bound_job_not_visible")
    return {
        "native_job_status": job.status,
        "native_stage": job.current_stage,
        "native_error_code": job.last_error_code,
        "coverage": dict(job.coverage),
        "research_completion": stored_completion(job.coverage),
        "usage_ledger": [
            {
                "id": str(row.id),
                "state": row.usage_state,
                "calls": row.external_calls,
                "metrics": row.metrics,
            }
            for row in session.scalars(
                select(UsageLedger).where(UsageLedger.task_key == f"web-research:{job_id}")
            )
        ],
    }


def run_validation(contract, settings, boundary_factory, *, approved_contract_sha256):
    """唯一编排入口：预检→正式 prepare→正式 Worker steps→独立重读/封存。"""
    if hash_canonical_object(contract) != approved_contract_sha256:
        raise ValueError("approved_contract_hash_mismatch")
    output = Path(contract["output_dir"])
    if (output / "attempt.json").exists():
        raise ValueError("existing_attempt_must_not_restart")
    reference, fields = _validate_input(contract, settings)
    request_id, company_id = UUID(contract["request_id"]), UUID(contract["company_id"])
    user_id, tenant_id = UUID(contract["worker_user_id"]), UUID(contract["worker_tenant_id"])
    engine = build_engine(settings.database_url)
    factory = build_session_factory(engine)
    receipt = {
        "attempt_id": contract["attempt_id"],
        "job_id": None,
        "steps": [],
        "driver_exit_reason": "preflight",
        "attempt_finished": False,
        "evidence_sealed": False,
        "first_error": None,
        "secondary_errors": [],
        "reference": fields,
    }
    boundaries = None
    job_id = None
    started = time.monotonic()
    _save(output / "attempt.json", {"contract": contract, "receipt": receipt}, exclusive=True)

    def session():
        return request_session(factory, user_id, tenant_id)

    try:
        with session() as current:
            _validate_database(current, contract)
            user = current.get(User, user_id)
            if user is None or user.status != "active" or user.tenant_id != tenant_id:
                raise ValueError("invalid_worker_identity")
            request, company = _queue(current, request_id, company_id)
            identity = contract["binding"]["company_order"][0]
            if (
                company.credit_code != identity.get("ucc", identity.get("credit_code"))
                or company.legal_name != identity["legal_name"]
            ):
                raise ValueError("database_identity_mismatch")
            if request.research_job_id is not None:
                raise ValueError("existing_attempt_must_not_restart")
            from backend.app.research_subject import load_subject

            aliases = identity.get("reviewed_aliases", identity.get("safe_aliases", []))
            if sorted(load_subject(current, company).aliases) != sorted(aliases):
                raise ValueError("database_aliases_differ_from_frozen_identity")
            prepare_bound_jobs(
                current,
                user,
                settings.web_research_policy,
                contract["binding_without_runner"],
                contract["module_root"],
                contract["identity_path"],
                reference,
            )
            current.refresh(request)
            job_id = request.research_job_id
            receipt["job_id"] = str(job_id)
            _queue(current, request_id, company_id, job_id)
            job = current.get(CompanyResearchJob, job_id)
            if contract["mode"] == "given_sources":
                coverage = dict(job.coverage)
                coverage.update(
                    research_scope="given_source_processing",
                    search_groups={
                        key: {**row, "status": "not_run"}
                        for key, row in coverage["search_groups"].items()
                    },
                    candidates=[
                        {
                            "url": url,
                            "title": "",
                            "snippet": "",
                            "source_name": "给定来源",
                            "discovered_by": [],
                            "query_kind": "given_source",
                            "source_tier": "official",
                            "coverage_category": None,
                        }
                        for url in contract["source_urls"]
                    ],
                )
                job.coverage, job.current_stage = coverage, "fetch"
                current.commit()
        # 预检失败不会构建 Provider；离线与真实仅在这里替换边界。
        boundaries = boundary_factory(settings)
        for _ in range(contract["max_steps"]):
            if hash_canonical_object(contract) != approved_contract_sha256:
                raise ValueError("approved_contract_hash_mismatch")
            _validate_input(contract, settings)
            if time.monotonic() - started >= contract["outer_seconds"]:
                receipt["driver_exit_reason"] = "outer_time_limit"
                break
            with session() as current:
                _queue(current, request_id, company_id, job_id)
                user = current.get(User, user_id)
                result = run_web_research_worker_once(
                    current,
                    user,
                    boundaries.providers,
                    settings.web_research_policy,
                    fetcher_factory=boundaries.fetcher_factory,
                    matter_provider=boundaries.matter_provider,
                    document_provider=boundaries.document_provider,
                    watchlist_gate=lambda: False,
                )
                if result.job_id != job_id:
                    raise ValueError("worker_selected_unbound_job")
                receipt["steps"].append(result.to_dict())
                current.expire_all()
                native = _snapshot(current, job_id)
                if native["native_job_status"] in {
                    "completed",
                    "failed",
                    "budget_deferred",
                    "cancelled",
                }:
                    receipt["driver_exit_reason"] = "native_terminal"
                    break
        else:
            receipt["driver_exit_reason"] = "outer_step_limit"
    except Exception as error:
        receipt["first_error"] = {
            "class": type(error).__name__,
            "classification": "database_transaction_error"
            if isinstance(error, SQLAlchemyError)
            else "driver_contract_error"
            if isinstance(error, ValueError)
            else "worker_execution_error",
        }
        receipt["driver_exit_reason"] = "first_error"
        _save(
            output / "first-error.json",
            {**receipt["first_error"], **_error_record(error, "execution", settings)},
        )
    finally:
        receipt["attempt_finished"] = True
        if job_id:
            try:
                with session() as current:
                    receipt.update(_snapshot(current, job_id))
            except Exception as error:
                receipt["secondary_errors"].append(
                    {"class": type(error).__name__, "stage": "snapshot"}
                )
                _save(output / "snapshot-error.json", _error_record(error, "snapshot", settings))
        if boundaries:
            try:
                boundaries.close()
            except Exception as error:
                receipt["secondary_errors"].append(
                    {"class": type(error).__name__, "stage": "provider_close"}
                )
                _save(output / "close-error.json", _error_record(error, "provider_close", settings))
        engine.dispose()
        receipt["evidence_sealed"] = True
        _save(output / "receipt.json", receipt)
        if json.loads((output / "receipt.json").read_text()) != json.loads(
            json.dumps(receipt, default=str)
        ):
            raise ValueError("receipt_readback_failed")
    return receipt


def main():
    parser = argparse.ArgumentParser(
        description="Run one separately authorized isolated research attempt"
    )
    parser.add_argument("--contract", required=True)
    parser.add_argument("--approved-contract-sha256", required=True)
    args = parser.parse_args()
    settings = Settings.from_env()
    contract = json.loads(Path(args.contract).read_text())
    if contract["boundary_mode"] != "metered":
        raise ValueError(
            "CLI_requires_explicit_metered_contract; offline calls inject mock boundaries"
        )

    def providers(current):
        from backend.app.research_extraction import DeepSeekMatterProvider
        from backend.app.web_search import BaiduSearchProvider, BochaSearchProvider
        from scripts.run_web_research_worker import _required

        policy = current.web_research_policy
        if policy.tavily_enabled or {policy.primary_provider, policy.fallback_provider} != {
            "baidu",
            "bocha",
        }:
            raise ValueError("unapproved_validation_provider")
        search = {
            code: cls(
                _required(code.upper() + "_SEARCH_API_KEY"),
                timeout_seconds=policy.timeout_seconds,
                max_response_bytes=policy.max_response_bytes,
                user_agent=policy.user_agent,
            )
            for code, cls in (("baidu", BaiduSearchProvider), ("bocha", BochaSearchProvider))
        }
        model = (
            DeepSeekMatterProvider(_required("DEEPSEEK_API_KEY"), policy)
            if policy.matter_model_enabled
            else None
        )
        return ProviderBoundaries(search, matter_provider=model)

    result = run_validation(
        contract, settings, providers, approved_contract_sha256=args.approved_contract_sha256
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "driver_exit_reason",
                    "attempt_finished",
                    "evidence_sealed",
                    "first_error",
                )
            }
        )
    )


if __name__ == "__main__":
    main()
