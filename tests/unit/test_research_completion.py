from copy import deepcopy

import pytest

from backend.app.research_completion import completion_projection
from backend.app.research_plan import TOPICS

OLD = "2026-09-01T00:00:00+00:00"
NOW = "2026-09-29T00:00:00+00:00"


def completed():
    return {
        "query_strategy_version": "topic-research-v1",
        "research_scope": "bounded_full_scope_refresh",
        "search_groups": {
            category: {
                "topic_category": category,
                "status": "completed",
                "subject_results": 0,
                "attempted_at": NOW,
                "checked_at": OLD,
                "providers": {"baidu": {"status": "cache_hit"}},
            }
            for category in TOPICS
        },
    }


def test_all_eight_successful_empty_checks_are_bounded_complete_not_new_freshness():
    result = completion_projection(completed())
    assert result["status"] == "complete" and len(result["categories"]) == 8
    assert all(
        r["source_acquisition_status"] == "no_qualified_results" for r in result["categories"]
    )
    assert all(r["last_attempt_at"].isoformat() == NOW for r in result["categories"])
    assert all(r["last_successful_check_at"].isoformat() == OLD for r in result["categories"])


@pytest.mark.parametrize("state", ["pending", "budget_deferred", "failed"])
def test_two_checked_six_unfinished_never_complete(state):
    coverage = completed()
    for group in list(coverage["search_groups"].values())[2:]:
        group["status"] = state
        group["providers"] = {}
    result = completion_projection(coverage)
    assert result["status"] != "complete"
    assert sum(r["status"] == "completed" for r in result["categories"]) == 2
    assert all(r["last_successful_check_at"] is None for r in result["categories"][2:])


@pytest.mark.parametrize(
    "code,failure",
    [
        ("network_error", "source_unreachable"),
        ("blocked_network", "network_environment_blocked"),
        ("robots_disallowed", "robots_denied"),
        ("dynamic_rendering_required", "dynamic_content_unavailable"),
    ],
)
def test_read_failure_preserves_old_success_and_other_category_results(code, failure):
    coverage = completed()
    category = "product_technology"
    coverage["search_groups"][category]["subject_results"] = 1
    coverage["previous_successful_checks"] = {category: OLD}
    coverage["documents"] = [
        {
            "url": "https://example.invalid/p",
            "status": "failed",
            "error_code": code,
            "coverage_category": category,
            "attempted_at": NOW,
        }
    ]
    result = completion_projection(coverage)
    assert result["status"] == "partial"
    row = next(r for r in result["categories"] if r["category"] == category)
    assert row["status"] == "failed" and row["failure_class"] == failure
    assert row["last_successful_check_at"].isoformat() == OLD
    assert sum(r["status"] == "completed" for r in result["categories"]) == 7


def test_fallback_failure_after_primary_results_does_not_hide_missing_candidates():
    coverage = completed()
    coverage["search_groups"]["product_technology"]["subject_results"] = 1
    result = completion_projection(coverage)
    assert result["status"] == "partial"


@pytest.mark.parametrize("state", ["failed", "budget_deferred"])
def test_required_fallback_unfinished_is_not_a_successful_empty_check(state):
    coverage = completed()
    group = coverage["search_groups"]["legal_compliance"]
    group["providers"]["bocha"] = {
        "status": state,
        "reason": "insufficient_qualified_subject_results"
        if state == "failed"
        else "task_call_limit",
    }
    result = completion_projection(coverage)
    row = next(r for r in result["categories"] if r["category"] == "legal_compliance")
    assert result["status"] == row["status"] == "partial"
    assert row["last_successful_check_at"] is None
    assert row["failure_class"] == (
        "budget_deferred" if state == "budget_deferred" else "source_unreachable"
    )
    from backend.app.research_plan import successful_topic_checks

    assert "legal_compliance" not in successful_topic_checks([coverage])


def test_projection_does_not_mutate_or_emit_internal_metadata():
    coverage = completed()
    coverage["network_preflight"] = {
        "status": "execution_environment_non_public_dns",
        "addresses": ["198.18.0.1"],
        "stack": "/private/operator",
    }
    for group in coverage["search_groups"].values():
        group.update(status="pending", providers={}, attempted_at=None, checked_at=None)
    before = deepcopy(coverage)
    result = completion_projection(coverage)
    assert coverage == before and result["status"] == "not_run"
    assert "198.18.0.1" not in str(result) and "/private" not in str(result)


def test_network_failure_after_started_research_keeps_completed_range():
    coverage = completed()
    for group in list(coverage["search_groups"].values())[2:]:
        group.update(status="pending", providers={}, attempted_at=None, checked_at=None)
    coverage["network_preflight"] = {"status": "dns_resolution_failed"}
    result = completion_projection(coverage)
    assert result["status"] == "partial"
    assert sum(r["status"] == "completed" for r in result["categories"]) == 2
    assert all(
        r["failure_class"] == "network_environment_blocked" for r in result["categories"][2:]
    )


def test_limited_watchlist_only_marks_checked_categories_and_no_full_freshness():
    coverage = completed()
    coverage["research_scope"] = "limited_scope_check"
    coverage["search_groups"] = {
        "contract_commercial": coverage["search_groups"]["contract_commercial"]
    }
    result = completion_projection(coverage)
    assert result["scope"] == "limited_scope_check" and result["status"] == "partial"
    assert sum(r["planned"] for r in result["categories"]) == 1
    assert all(
        r["last_successful_check_at"] is None for r in result["categories"] if not r["planned"]
    )


def test_legacy_narrow_routes_do_not_claim_eight_planned_categories():
    coverage = completed()
    coverage["query_strategy_version"] = "legacy-narrow-v1"
    group = coverage["search_groups"]["financing_cap_table"]
    group.pop("topic_category")
    coverage["search_groups"] = {"business_capital": group}
    coverage["source_routes"] = {
        "financing_cap_table": {"search_group": "business_capital", "providers": ["baidu"]}
    }
    rows = completion_projection(coverage)["categories"]
    assert sum(row["planned"] for row in rows) == 1
    assert sum(row["status"] == "not_run" for row in rows) == 7


@pytest.mark.parametrize(
    "documents,expected",
    [
        ([{"status": "created"}], "partial"),
        ([{"status": "reused"}], "partial"),
        ([{"status": "failed", "error_code": "blocked_network"}], "failed"),
    ],
)
def test_legacy_documents_without_category_plan_are_not_completely_unstarted(documents, expected):
    result = completion_projection({"documents": documents})
    assert result["status"] == expected
    assert all(row["status"] == "not_run" for row in result["categories"])
    assert all(row["last_successful_check_at"] is None for row in result["categories"])
