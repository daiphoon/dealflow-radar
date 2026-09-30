"""可追溯裁决与实验绑定；不复制产品抽取和比较算法。"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from backend.app.config import WebResearchPolicy
from backend.app.research_evaluation import linked_judgment, scorecard
from scripts.research_validation_contract import (
    accept_state,
    assert_binding,
    export_state,
    runtime_binding,
)


def decision(
    output, benchmark, *, correct=True, supported=True, confirmed=False, delivered=True, errors=()
):
    return {
        "output_id": output,
        "benchmark_id": benchmark,
        "evidence_id": "evidence-" + output,
        "quote": "示例公司公开事项的连续片段",
        "reason": "按主体、动作和允许日期精度独立复核",
        "extracted_correct": correct,
        "evidence_supported": supported,
        "confirmed": confirmed,
        "delivered": delivered,
        "errors": list(errors),
    }


def test_four_layers_are_not_published_row_counts():
    rows = [
        decision("candidate", "A"),
        decision(
            "wrong-subject",
            None,
            correct=False,
            supported=False,
            delivered=False,
            errors=("wrong_subject",),
        ),
        decision("unknown-date", "C", errors=("unknown_time",)),
        decision("unconfirmed-readable", "D"),
    ]
    judged = linked_judgment(["A", "B", "C", "D", "E"], [r["output_id"] for r in rows], rows)
    assert judged["layers"] == {
        "extracted_correct": 3,
        "evidence_supported": 3,
        "confirmed": 0,
        "delivered": 3,
    }
    assert judged["output"] == 4 and judged["matched_expected"] == 3
    scored = scorecard(
        [judged], benchmark=[{"expected": 5, "allowed_date_precision": ["unknown", "day"]}]
    )
    assert scored["known_set_recall"] == 3 / 5
    assert scored["precision"] == 3 / 4
    missing = linked_judgment(["E"], [], [], absence_reason="no_system_output")
    assert missing["output"] == missing["matched_expected"] == 0
    with pytest.raises(ValueError):
        linked_judgment(["A"], ["candidate"], [])


@pytest.mark.parametrize(
    "field",
    [
        "main",
        "tree",
        "modules",
        "identity_sha256",
        "reference_at",
        "event_window_days",
        "config_sha256",
        "company_order",
    ],
)
def test_runtime_drift_stops_before_first_metered_call(tmp_path, field):
    identity = tmp_path / "identity.json"
    identity.write_text(
        json.dumps(
            {
                "company_order": [
                    {
                        "stable_company_key": "fictive-1",
                        "ucc": "FIXTURE",
                        "legal_name": "示例公司",
                        "safe_aliases": [],
                        "region": "虚构市",
                    }
                ]
            }
        )
    )
    actual = runtime_binding(
        Path.cwd(),
        identity,
        WebResearchPolicy(),
        datetime.fromisoformat("2026-09-29T00:00:00+08:00"),
    )
    frozen = dict(actual)
    assert_binding(frozen, actual)
    actual[field] = "intentional drift"
    calls = []
    with pytest.raises(ValueError, match="before_metered"):
        assert_binding(frozen, actual)
        calls.append("provider")
    assert not calls


def test_projection_is_allowlisted_bound_idempotent_and_cannot_replace_new_attempt():
    context = {
        "company_key": "fictive-1",
        "scope": "bounded_full_scope_refresh",
        "synthetic_user": "u1",
        "synthetic_tenant": "t1",
    }
    source = {
        "job_id": "job1",
        "main": "fixture-code",
        "input_sha256": "fixture-input",
        "config_sha256": "fixture-config",
    }
    coverage = {
        "research_scope": context["scope"],
        "reference_at": "2026-09-29T00:00:00+08:00",
        "event_window_days": 365,
        "secret": "DO_NOT_EXPORT",
        "documents": [
            {
                "url": "https://example.invalid/news",
                "excerpt": "DO_NOT_EXPORT",
                "status": "failed",
                "error_code": "timeout",
            }
        ],
    }
    old = export_state(
        coverage, context=context, source=source, attempt_at="2026-09-29T01:00:00+08:00"
    )
    assert "DO_NOT_EXPORT" not in json.dumps(old)
    accepted, reused = accept_state(old, context=context, source=source)
    assert not reused and accepted == old["coverage"]
    assert accept_state(old, context=context, source=source, current=old)[1]
    new = export_state(
        coverage, context=context, source=source, attempt_at="2026-09-30T01:00:00+08:00"
    )
    with pytest.raises(ValueError, match="stale"):
        accept_state(old, context=context, source=source, current=new)
    for key in context:
        with pytest.raises(ValueError, match="context"):
            accept_state(old, context={**context, key: "forged"}, source=source)
    with pytest.raises(ValueError, match="source"):
        accept_state(old, context=context, source={**source, "main": "forged"})
