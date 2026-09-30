"""隔离实验的只读契约及状态投影；没有 Provider 调用或生产写入口。"""

import hashlib
import importlib
import json
import re
import subprocess
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from backend.app.evidence_integrity import hash_canonical_object
from backend.app.research_completion import stored_completion
from backend.app.research_plan import TOPICS

IDENTITY_KEYS = {
    "stable_company_key",
    "ucc",
    "credit_code",
    "legal_name",
    "reviewed_aliases",
    "safe_aliases",
    "region",
    "reviewed_safe_aliases",
    "reference_at",
    "event_window",
    "research_categories",
}


def runtime_binding(module_root, identity_path, policy, reference_at):
    root, path = Path(module_root).resolve(), Path(identity_path).resolve()
    identity = json.loads(path.read_text())
    order = identity["company_order"]
    if any(set(company) - IDENTITY_KEYS for company in order):
        raise ValueError("identity_contains_unapproved_fields")
    if reference_at.tzinfo is None:
        raise ValueError("reference_timezone_required")
    window = [
        (reference_at - timedelta(days=policy.recent_change_window_days)).date().isoformat(),
        reference_at.date().isoformat(),
    ]
    for company in order:
        if company.get("reference_at") not in {
            None,
            reference_at.date().isoformat(),
            reference_at.isoformat(),
        }:
            raise ValueError("identity_reference_differs_from_execution")
        if company.get("event_window", window) != window:
            raise ValueError("identity_window_differs_from_execution")
        if set(company.get("research_categories", TOPICS)) != set(TOPICS):
            raise ValueError("identity_category_scope_differs_from_execution")

    def git(ref):
        return subprocess.check_output(["git", "rev-parse", ref], cwd=root, text=True).strip()

    modules = tuple(
        "backend/app/" + name + ".py"
        for name in (
            "config",
            "web_research_service",
            "research_extraction",
            "research_matters",
            "matter_validation",
            "matter_comparison",
            "matter_dates",
            "matter_contract",
            "research_matter_storage",
            "research_completion",
            "research_plan",
            "source_fetcher",
            "research_network",
            "web_search",
            "research_subject",
        )
    )
    for name in modules:
        module = importlib.import_module(name.removesuffix(".py").replace("/", "."))
        if Path(module.__file__).resolve() != (root / name).resolve():
            raise ValueError("loaded_module_root_differs_from_bound_checkout")
    return {
        "main": git("HEAD"),
        "tree": git("HEAD^{tree}"),
        "module_root": str(root),
        "modules": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in modules
        },
        "identity_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "company_order": order,
        "config_sha256": hashlib.sha256(
            json.dumps(asdict(policy), sort_keys=True, default=str).encode()
        ).hexdigest(),
        "reference_at": reference_at.isoformat(),
        "event_window_days": policy.recent_change_window_days,
    }


def assert_binding(frozen, actual):
    if set(frozen) != set(actual) or any(actual[key] != value for key, value in frozen.items()):
        raise ValueError("execution_contract_drift_before_metered_call")


def prepare_bound_jobs(session, user, policy, frozen, module_root, identity_path, reference_at):
    from backend.app.web_research_service import prepare_pending_research_requests

    assert_binding(frozen, runtime_binding(module_root, identity_path, policy, reference_at))
    return prepare_pending_research_requests(session, user, policy, reference_at=reference_at)


def _url_key(url):
    if re.fullmatch(r"[a-f0-9]{64}", str(url)):
        return str(url)
    return hashlib.sha256(str(url).encode()).hexdigest()


def export_state(coverage, *, context, source, attempt_at):
    """不导出正文、查询、答案或真实用户信息；仅保留 completion 所需执行状态。"""
    if set(context) != {"company_key", "scope", "synthetic_user", "synthetic_tenant"}:
        raise ValueError("invalid_projection_context")
    if set(source) != {"job_id", "main", "input_sha256", "config_sha256"}:
        raise ValueError("invalid_projection_source")
    if coverage.get("research_scope") != context["scope"]:
        raise ValueError("projection_scope_mismatch")
    state = {
        key: coverage[key]
        for key in ("research_scope", "reference_at", "event_window_days", "stop_reason")
        if key in coverage
    }
    state["previous_successful_checks"] = {
        category: when
        for category, when in coverage.get("previous_successful_checks", {}).items()
        if category in TOPICS
    }
    state["source_routes"] = {
        category: {"search_group": row.get("search_group")}
        for category, row in coverage.get("source_routes", {}).items()
        if isinstance(row, dict)
    }
    state["network_preflight"] = {"status": coverage.get("network_preflight", {}).get("status")}
    state["search_groups"] = {}
    for code, row in coverage.get("search_groups", {}).items():
        group = {
            key: row[key]
            for key in (
                "topic_category",
                "status",
                "attempted_at",
                "checked_at",
                "subject_results",
                "coverage_scope",
            )
            if key in row
        }
        group["providers"] = {
            provider: {key: data[key] for key in ("status", "reason") if key in data}
            for provider, data in row.get("providers", {}).items()
        }
        state["search_groups"][code] = group
    for name in ("candidates", "deferred_candidates", "documents"):
        state[name] = [
            {
                **{
                    key: row[key]
                    for key in (
                        "coverage_category",
                        "query_kind",
                        "query_kinds",
                        "status",
                        "error_code",
                        "checked_at",
                        "attempted_at",
                        "matter_categories",
                        "searched_topics",
                    )
                    if key in row
                },
                "url": _url_key(row.get("url")),
            }
            for row in coverage.get(name, [])
        ]
    body = {
        "version": "validation-completion-projection-v1",
        "context": context,
        "source": source,
        "attempt_at": attempt_at,
        "exported_at": datetime.now().astimezone().isoformat(),
        "coverage": state,
        "completion": stored_completion(state),
    }
    return {**body, "projection_sha256": hash_canonical_object(body)}


def accept_state(envelope, *, context, source, current=None):
    body = {key: value for key, value in envelope.items() if key != "projection_sha256"}
    if envelope.get("projection_sha256") != hash_canonical_object(body):
        raise ValueError("projection_hash_mismatch")
    if (
        envelope.get("version") != "validation-completion-projection-v1"
        or envelope["context"] != context
    ):
        raise ValueError("projection_context_mismatch")
    if envelope["source"] != source:
        raise ValueError("projection_source_mismatch")
    sanitized = export_state(
        envelope["coverage"], context=context, source=source, attempt_at=envelope["attempt_at"]
    )["coverage"]
    if sanitized != envelope["coverage"]:
        raise ValueError("projection_contains_unapproved_fields")
    if stored_completion(envelope["coverage"]) != envelope["completion"]:
        raise ValueError("projection_completion_mismatch")
    when = datetime.fromisoformat(envelope["attempt_at"])
    if when.tzinfo is None:
        raise ValueError("projection_timezone_required")
    if current and when < datetime.fromisoformat(current["attempt_at"]):
        raise ValueError("stale_projection")
    if (
        current
        and when == datetime.fromisoformat(current["attempt_at"])
        and envelope["projection_sha256"] != current["projection_sha256"]
    ):
        raise ValueError("conflicting_projection_same_attempt")
    return envelope["coverage"], bool(
        current and envelope["projection_sha256"] == current["projection_sha256"]
    )
