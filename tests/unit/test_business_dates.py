from datetime import UTC, date, datetime

import pytest

from backend.app.business_dates import business_date, reference_fields, report_reference
from backend.app.config import WebResearchPolicy
from backend.app.web_research_service import _search_date_status
from backend.app.web_search import SearchResult


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-09-29T00:00:00+08:00", "2026-09-29"),
        ("2026-09-28T16:00:00Z", "2026-09-29"),
        ("2026-09-28T23:59:59+08:00", "2026-09-28"),
        ("2026-09-29T07:59:59+08:00", "2026-09-29"),
        ("2026-09-29T08:00:00+08:00", "2026-09-29"),
        ("2026-09-29T23:59:59+08:00", "2026-09-29"),
        ("2026-10-31T23:59:59Z", "2026-11-01"),
        ("2026-12-31T16:00:00Z", "2027-01-01"),
        ("2024-02-28T16:00:00Z", "2024-02-29"),
        ("2024-02-29T16:00:00Z", "2024-03-01"),
        ("2026-09-29", "2026-09-29"),
        (date(2026, 9, 29), "2026-09-29"),
    ],
)
def test_business_date_matrix(value, expected):
    assert business_date(value).isoformat() == expected


@pytest.mark.parametrize(
    "value", [None, "", "invalid", "2026-09-29T00:00:00", datetime(2026, 9, 29)]
)
def test_new_reference_rejects_missing_invalid_or_naive(value):
    with pytest.raises((ValueError, TypeError)):
        reference_fields(value, 365)


@pytest.mark.parametrize(
    "coverage",
    [
        {},
        {"reference_at": None},
        {"reference_at": "bad"},
        {"reference_at": "2026-09-29T00:00:00"},
        {"reference_at": "2026-09-29T00:00:00+08:00", "business_reference_date": "2026-09-28"},
    ],
)
def test_legacy_reference_is_explicit_unknown_and_does_not_mutate(coverage):
    original = dict(coverage)
    day, source, days = report_reference(coverage, datetime(2026, 10, 1, 16, tzinfo=UTC))
    assert day == date(2026, 10, 2) and "未记录或无效" in source and days == 365
    assert coverage == original


def test_date_only_legacy_remains_its_date():
    day, source, days = report_reference({"reference_at": "2026-09-29"}, datetime.now(UTC))
    assert day == date(2026, 9, 29) and source == "研究记录" and days == 365


@pytest.mark.parametrize(
    "published,expected",
    [
        ("2025-09-28T15:59:59Z", "old"),
        ("2025-09-28T16:00:00Z", "recent"),
        ("2025-09-29", "recent"),
        ("2026-09-29T15:59:59Z", "recent"),
        ("2026-09-29T16:00:00Z", "future"),
        (None, "unknown"),
    ],
)
def test_search_window_uses_same_inclusive_business_dates(published, expected):
    row = SearchResult("fixture", "虚构", "https://example.com/fixture", "虚构", "虚构", published)
    for reference in ("2026-09-29T00:00:00+08:00", "2026-09-28T16:00:00Z"):
        assert (
            _search_date_status(
                row,
                WebResearchPolicy(),
                reference_at=reference,
                observed_at=datetime(2026, 10, 1, tzinfo=UTC),
            )
            == expected
        )


def test_business_window_start_and_end_contract():
    fields = reference_fields("2026-09-28T16:00:00Z", 365)
    assert fields == {
        "business_reference_date": "2026-09-29",
        "business_timezone": "Asia/Shanghai",
        "event_window_start": "2025-09-29",
        "event_window_end": "2026-09-29",
        "event_window_days": 365,
    }
