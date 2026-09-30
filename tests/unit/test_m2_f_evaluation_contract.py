"""可追溯裁决与实验绑定；不复制产品抽取和比较算法。"""

import json
import os
import subprocess
import sys
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


def test_versioned_not_evaluated_is_not_false_or_business_recall():
    row = decision("candidate", "A", confirmed=None, delivered=None)
    row["not_evaluated_reasons"] = {"confirmed": "未执行确认评价", "delivered": "未实际读取"}
    row["system_status"] = {"confirmed": False, "delivered": "not_run"}
    judged = linked_judgment(["A"], ["candidate"], [row], schema_version="linked-four-layer-v2")
    assert judged["layer_assessments"]["delivered"]["not_evaluated"] == 1
    assert judged["layer_assessments"]["delivered"]["failed"] == 0
    scored = scorecard([judged], benchmark=[{"expected": 1, "allowed_date_precision": ["unknown"]}])
    assert scored["candidate_supported_recall"] == 1
    assert scored["business_delivery_recall"] is None
    assert not scored["formal_metrics_available"]
    assert scored["sample_count"] == scored["reviewed_count"] == 1


def test_v2_delivery_failure_and_absence_are_evaluated_without_changing_legacy_false():
    rows = [decision("a", "A", confirmed=True, delivered=False)]
    judgment = linked_judgment(["A", "B"], ["a"], rows, schema_version="linked-four-layer-v2")
    result = scorecard([judgment], benchmark=[{"expected": 2, "allowed_date_precision": ["day"]}])
    assert result["candidate_supported_recall"] == 0.5
    assert result["business_delivery_recall"] == 0
    legacy = linked_judgment(["A"], ["a"], rows)
    assert legacy["layers"]["delivered"] == 0
    with pytest.raises(ValueError):
        linked_judgment(["A"], ["a"], [{**rows[0], "delivered": None}])
    empty = linked_judgment(
        ["B"], [], [], absence_reason="no_system_output", schema_version="linked-four-layer-v2"
    )
    assert empty["layer_assessments"]["delivered"]["expected_evaluated"] == 1


def test_unknown_extraction_never_becomes_failed_precision_or_qualified_cost():
    row = decision("a", "A", correct=None, supported=None, confirmed=None, delivered=None)
    row["not_evaluated_reasons"] = dict.fromkeys(
        ("extracted_correct", "evidence_supported", "confirmed", "delivered"), "缺少必要评价证据"
    )
    judged = linked_judgment(["A"], ["a"], [row], schema_version="linked-four-layer-v2")
    judged["cost"] = 1
    result = scorecard([judged], benchmark=[{"expected": 1, "allowed_date_precision": ["unknown"]}])
    assert result["precision"] is result["reviewed_subset"]["precision"] is None
    assert result["candidate_supported_recall"] is result["business_delivery_recall"] is None
    assert result["cost_per_qualified"] is None
    assert result["recall_bounds"] == [0, 1]
    assert result["layer_assessments"]["extracted_correct"]["failed"] == 0


@pytest.mark.parametrize(
    "mode",
    ["not_evaluated", "missing_field", "tampered_count", "wrong_denominator", "invalid_json"],
)
def test_actual_offline_cli_layered_results_and_error_exit(tmp_path, mode):
    code_root = Path(__file__).resolve().parents[2]
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "isolated CLI fixture",
        ],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / "alembic.ini").write_text(
        "[alembic]\nscript_location = " + str(code_root / "migrations") + "\n"
    )
    row = decision("a", "A", confirmed=None, delivered=None)
    row["not_evaluated_reasons"] = {"confirmed": "尚未确认评价", "delivered": "未实际读取"}
    judged = linked_judgment(["A"], ["a"], [row], schema_version="linked-four-layer-v2")
    if mode == "missing_field":
        del judged["decisions"][0]["delivered"]
    elif mode == "tampered_count":
        judged["matched_expected"] = 0
    source = tmp_path / "input.json"
    source.write_text(
        "invalid"
        if mode == "invalid_json"
        else json.dumps(
            {
                "sample_origin": "synthetic",
                "reference_at": "2026-09-30",
                "cases": [
                    {
                        "id": "case",
                        "body": None,
                        "not_retained_reason": "not_needed",
                        "frozen_judgment": judged,
                    }
                ],
                "frozen_benchmark": [
                    {
                        "id": "case",
                        "expected": 2 if mode == "wrong_denominator" else 1,
                        "allowed_date_precision": ["unknown"],
                    }
                ],
            }
        )
    )
    out = tmp_path / "data/private/result"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.research_benchmark",
            str(source),
            "--offline-production",
            "--output",
            str(out),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(code_root), "EXTERNAL_CALLS_ENABLED": "false"},
        text=True,
        capture_output=True,
        timeout=30,
    )
    if mode == "not_evaluated":
        assert result.returncode == 0, result.stderr
        scores = json.loads((out / "manifest.json").read_text())["scorecard"]
        assert scores["candidate_supported_recall"] == 1
        assert scores["business_delivery_recall"] is None
        assert scores["layer_assessments"]["delivered"]["not_evaluated"] == 1
        assert not scores["formal_metrics_available"]
    else:
        assert result.returncode == 1 and not out.exists()
        assert "Error" in result.stderr


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
