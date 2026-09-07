from datetime import UTC, datetime

import pytest

from backend.app.config import WebResearchPolicy
from backend.app.models import Company, CompanyResearchJob
from backend.app.research_outcome import research_result
from backend.app.web_research_service import _content_quality_decision, _event_classification

NAME = "示例星河科技一号有限公司"
NOW = datetime(2026, 9, 6, tzinfo=UTC)


@pytest.mark.parametrize(
    "wording", ["成功上市", "成功赴港上市", "成功在 港交所主板 上市", "成功于香港联合交易所上市"]
)
def test_completed_listing_wording(wording):
    assert _event_classification("", f"{NAME}{wording}。") == "exit_liquidity"


@pytest.mark.parametrize("prefix", ["拟", "计划", "预计", "有望", "尚未", "未能", "没有", "争取"])
def test_listing_plans_or_failures_are_not_completion(prefix):
    assert _event_classification("", f"{NAME}{prefix}成功在港交所主板上市。") is None


@pytest.mark.parametrize(
    ("prefix", "expected", "date_status"),
    [
        ("2026年4月16日，", "2026-04-16", "explicit_body_date"),
        ("4月16日，", None, "body_date_year_unknown"),
        ("2026年2月30日，", None, "invalid_body_date"),
        ("", None, "unknown"),
    ],
)
def test_event_date_is_not_borrowed_from_repost_or_founding_date(prefix, expected, date_status):
    result = _content_quality_decision(
        Company(legal_name=NAME),
        title=f"{NAME}上市",
        excerpt=f"{prefix}{NAME}成功在 港交所主板 上市，从2022年5月成立至今。",
        published_at=datetime(2026, 9, 3, tzinfo=UTC),
        observed_at=NOW,
        policy=WebResearchPolicy(),
    )
    assert result.eligible
    assert (result.occurred_at.date().isoformat() if result.occurred_at else None) == expected
    assert result.event_date_status == date_status
    assert result.to_dict()["change_recognition_version"] == "explicit-change-v2"


def test_future_dated_completion_is_not_eligible():
    result = _content_quality_decision(
        Company(legal_name=NAME),
        title="公告",
        excerpt=f"2027年4月16日，{NAME}成功上市。",
        published_at=NOW,
        observed_at=NOW,
        policy=WebResearchPolicy(),
    )
    assert not result.eligible
    assert "event_date_in_future" in result.reasons


@pytest.mark.parametrize(
    "body",
    [
        f"2026年9月3日，记者回顾{NAME}2020年4月16日成功上市的历程。",
        f"2026年9月3日，{NAME}回顾2020年4月16日成功上市的历程。",
    ],
)
def test_report_date_or_multiple_dates_are_not_guessed(body):
    result = _content_quality_decision(
        Company(legal_name=NAME),
        title="上市历史回顾",
        excerpt=body,
        published_at=NOW,
        observed_at=NOW,
        policy=WebResearchPolicy(),
    )
    assert result.eligible
    assert result.occurred_at is None
    assert result.event_date_status == "body_date_ambiguous"


def test_title_and_other_company_cannot_supply_listing_change():
    result = _content_quality_decision(
        Company(legal_name=NAME),
        title=f"{NAME}成功上市",
        excerpt=f"{NAME}是一家科技企业。\n其他公司成功上市。",
        published_at=NOW,
        observed_at=NOW,
        policy=WebResearchPolicy(),
    )
    assert not result.eligible
    assert "subject_not_in_change_passage" in result.reasons


def test_outcome_allowlists_gaps_without_urls_or_private_metadata():
    job = CompanyResearchJob(
        status="completed",
        policy_version="bounded-web-v3",
        coverage={
            "completed_at": NOW.isoformat(),
            "stats": {"quality_gate_passed": 0},
            "secret": "PRIVATE-NOTE",
            "documents": [
                {
                    "status": "failed",
                    "error_code": "robots_disallowed",
                    "url": "https://private.invalid",
                },
                {"status": "failed", "error_code": "request_limit_exceeded"},
                {"quality_gate": {"status": "internal_only"}},
            ],
        },
    )
    result = research_result(job)
    assert result.outcome == "no_usable_evidence"
    assert result.finished_at == NOW
    assert len(result.limitations) == 3
    assert "不代表公司没有" in result.message
    assert "private" not in result.model_dump_json().lower()
    job.status = "cancelled"
    assert research_result(job) is None
    job.status = "completed"
    job.policy_version = "legacy"
    assert research_result(job) is None
