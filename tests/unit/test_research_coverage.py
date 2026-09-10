from copy import deepcopy

import pytest

from backend.app.research_coverage import (
    COVERAGE_VERSION,
    RESEARCH_MODULES,
    category_coverage,
    source_routes,
)

NOW = "2026-09-10T04:05:06+00:00"
OLD = "2026-09-01T01:02:03+00:00"
CATEGORY = "contract_commercial"


def recorded_coverage():
    return {
        "category_coverage_version": COVERAGE_VERSION,
        "source_routes": source_routes("baidu", "bocha"),
        "search_groups": {
            "business_capital": {
                "status": "completed",
                "subject_results": 0,
                "providers": {"baidu": {"status": "completed"}},
                "attempted_at": NOW,
                "checked_at": OLD,
            },
            "technology_risk_exit": {"status": "pending", "providers": {}},
        },
        "documents": [],
        "candidates": [],
    }


def row(coverage, category=CATEGORY):
    return next(item for item in category_coverage(coverage) if item.category == category)


@pytest.mark.parametrize("module_status", ["completed", "no_data", "failed", "pending"])
def test_legacy_module_states_never_fabricate_coverage(module_status):
    original = {"modules": {category: module_status for category in RESEARCH_MODULES}}
    before = deepcopy(original)
    rows = category_coverage(original)
    assert len(rows) == 9
    assert all(item.status == "unknown" and item.evidence_count is None for item in rows)
    assert all(item.search_checked_at is None for item in rows)
    assert original == before


def test_unconfigured_and_unchecked_are_different_from_a_successful_empty_search():
    coverage = recorded_coverage()
    assert row(coverage).status == "no_records"
    assert row(coverage, "exit_liquidity").status == "not_checked"
    assert row(coverage, "information_quality").status == "not_configured"
    coverage["source_routes"][CATEGORY] = None
    assert row(coverage).status == "not_configured"
    coverage["source_routes"].pop(CATEGORY)
    assert row(coverage).status == "not_configured"


@pytest.mark.parametrize(
    "error", ["robots_disallowed", "blocked_address", "request_limit_exceeded"]
)
def test_blocked_is_not_failure_or_empty(error):
    coverage = recorded_coverage()
    coverage["documents"] = [
        {"coverage_category": CATEGORY, "status": "failed", "error_code": error}
    ]
    result = row(coverage)
    assert result.status == "blocked"
    assert result.blocked_count == 1 and result.failed_count == 0
    assert result.evidence_count == 0 and result.evidence_checked_at is None


def test_read_failure_and_partial_search_failure_are_not_no_records():
    coverage = recorded_coverage()
    coverage["documents"] = [
        {
            "coverage_category": CATEGORY,
            "status": "failed",
            "error_code": "request_failed",
        }
    ]
    assert row(coverage).status == "failed"
    coverage["documents"] = []
    group = coverage["search_groups"]["business_capital"]
    group["providers"]["bocha"] = {"status": "failed", "error_code": "authentication_failed"}
    assert row(coverage).status == "failed"
    group["providers"].pop("baidu")
    assert row(coverage).search_checked_at is None


@pytest.mark.parametrize("http_status", [401, 403, 429])
def test_explicit_access_and_rate_limits_are_blocked(http_status):
    coverage = recorded_coverage()
    coverage["documents"] = [
        {
            "coverage_category": CATEGORY,
            "status": "failed",
            "error_code": "http_error",
            "http_status": http_status,
        }
    ]
    assert row(coverage).status == "blocked"
    assert row(coverage).failed_count == 0


def test_shared_search_or_metadata_alone_cannot_supply_category_evidence():
    coverage = recorded_coverage()
    coverage["search_groups"]["business_capital"]["subject_results"] = 1
    assert row(coverage).status == "candidates_only"
    coverage["candidates"] = [{"url": "https://private.invalid", "coverage_category": CATEGORY}]
    result = row(coverage)
    assert result.evidence_count == 0
    assert any("未读取" in gap for gap in result.gaps)
    coverage["documents"] = [{"status": "created", "coverage_category": "financing_cap_table"}]
    assert row(coverage).status == "candidates_only"
    assert row(coverage, "financing_cap_table").status == "evidence_obtained"
    assert "private.invalid" not in result.model_dump_json()


def test_body_reading_does_not_require_an_event_and_keeps_mixed_gaps():
    coverage = recorded_coverage()
    coverage["documents"] = [
        {
            "coverage_category": CATEGORY,
            "status": "reused",
            "event_created": False,
            "quality_gate": {"status": "internal_only"},
            "checked_at": OLD,
            "attempted_at": NOW,
            "cache_reused": True,
            "url": "https://private.invalid",
        },
        {"coverage_category": CATEGORY, "status": "failed", "error_code": "robots_disallowed"},
        {"coverage_category": CATEGORY, "status": "failed", "error_code": "request_failed"},
    ]
    before = deepcopy(coverage)
    result = row(coverage)
    assert result.status == "evidence_obtained" and result.evidence_count == 1
    assert result.blocked_count == result.failed_count == 1
    assert result.evidence_checked_at.isoformat() == OLD
    assert result.last_attempt_at.isoformat() == NOW and result.cache_reused
    assert len(result.gaps) == 4
    assert "private.invalid" not in result.model_dump_json()
    coverage["modules"] = {CATEGORY: "no_data"}
    coverage["stats"] = {"events_created": 999}
    assert row(coverage) == result
    assert before["documents"] == coverage["documents"]


@pytest.mark.parametrize("time", [None, "invalid", "2026-09-01", "2026-09-01T01:02:03"])
def test_cache_without_precise_time_does_not_borrow_job_completion(time):
    coverage = recorded_coverage()
    coverage["completed_at"] = NOW
    group = coverage["search_groups"]["business_capital"]
    group.update({"checked_at": time, "cache_reused": True})
    coverage["documents"] = [
        {
            "coverage_category": CATEGORY,
            "status": "reused",
            "checked_at": time,
            "cache_reused": True,
        }
    ]
    result = row(coverage)
    assert result.search_checked_at is result.evidence_checked_at is None
    assert result.cache_reused
