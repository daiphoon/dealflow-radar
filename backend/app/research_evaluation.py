"""离线评估只调用生产抽取/验证；缺失阶段不推断成功。"""

import math

from backend.app.evidence_integrity import hash_canonical_object, hash_excerpt_bytes
from backend.app.matter_validation import VALIDATION_VERSION
from backend.app.research_extraction import merge_matters
from backend.app.research_matters import (
    EXTRACTION_VERSION,
    PROMPT_VERSION,
    TypedProposedMatters,
    extract_matters,
    validate_proposals,
)

STAGES = (
    "search_hit",
    "subject_filter",
    "read_allocated",
    "body_available",
    "retained_context",
    "extraction",
    "field_validation",
    "merge",
    "api_visible",
    "deliverable",
)


def funnel(stages):
    ordered = {name: stages.get(name, "not_recorded") for name in STAGES}
    failures = [name for name, value in ordered.items() if value is False]
    return {
        "stages": ordered,
        "first_break": failures[0] if failures else None,
        "later_breaks": failures[1:],
    }


def replay(subject, body, output=None):
    rules = extract_matters(subject, body)
    proposed, rejected = (
        validate_proposals(subject, body, output, legacy_compat=False)
        if output is not None
        else ([], [])
    )
    return {
        "experiment": "retained_text_postprocessing",
        "model_evidence": "historical_or_mock_output" if output is not None else "not_run",
        "external_calls": 0,
        "body_sha256": hash_excerpt_bytes(body),
        "schema_sha256": hash_canonical_object(TypedProposedMatters.model_json_schema()),
        "prompt_version": PROMPT_VERSION,
        "extraction_version": EXTRACTION_VERSION,
        "validation_version": VALIDATION_VERSION,
        "rules_only": [m.payload() for m in rules],
        "model_only": [m.payload() for m in proposed],
        "combined": [m.payload() for m in merge_matters(rules, proposed)],
        "rejected": rejected,
        "qualified_cost": None,
        "qualified_cost_reason": "business_acceptance_not_run",
    }


QUALITY_COUNTS = (
    "wrong_subject",
    "wrong_stage",
    "wrong_time",
    "false_merge",
    "duplicate_split",
    "partially_useful",
    "unknown_time",
    "manual_reviews",
)


def scorecard(judgments, *, benchmark=None):
    """只汇总冻结基准的显式裁决；未评分样本保留在分母说明，不猜测正确率。"""
    reviewed = [j for j in judgments if j is not None]
    if benchmark is not None:
        if len(benchmark) != len(judgments) or any(
            type(b.get("expected")) is not int
            or b["expected"] < 0
            or not isinstance(b.get("allowed_date_precision"), list)
            or not b["allowed_date_precision"]
            or set(b["allowed_date_precision"]) - {"day", "month", "year", "unknown"}
            for b in benchmark
        ):
            raise ValueError("invalid_frozen_benchmark")
        if any(
            j is not None and j.get("expected") != b["expected"]
            for j, b in zip(judgments, benchmark, strict=True)
        ):
            raise ValueError("judgment_denominator_differs_from_benchmark")
    required = ("expected", "output", "correct_output", "matched_expected", *QUALITY_COUNTS)
    totals = dict.fromkeys(required, 0)
    for j in reviewed:
        for field in ("cost", "seconds"):
            value = j.get(field)
            if value is not None and (
                type(value) not in (int, float) or not math.isfinite(value) or value < 0
            ):
                raise ValueError("invalid_resource_measurement")
        if any(type(j.get(key)) is not int or j[key] < 0 for key in required):
            raise ValueError("explicit_nonnegative_judgment_counts_required")
        if j["correct_output"] > j["output"] or j["matched_expected"] > j["expected"]:
            raise ValueError("judgment_count_exceeds_denominator")
        for key in required:
            totals[key] += j[key]
    cost = (
        sum(j["cost"] for j in reviewed)
        if reviewed and all(j.get("cost") is not None for j in reviewed)
        else None
    )
    durations = [j.get("seconds") for j in reviewed]
    complete = bool(judgments) and len(reviewed) == len(judgments)
    formal = complete and benchmark is not None
    subset = {
        "counts": totals,
        "precision": totals["correct_output"] / totals["output"] if totals["output"] else None,
        "known_set_recall": totals["matched_expected"] / totals["expected"]
        if totals["expected"]
        else None,
    }
    expected_total = sum(b["expected"] for b in benchmark) if benchmark is not None else None
    missing_expected = (
        sum(b["expected"] for j, b in zip(judgments, benchmark, strict=True) if j is None)
        if benchmark is not None
        else None
    )
    return {
        "status": "not_recorded"
        if not reviewed
        else "partial_judgments"
        if not complete
        else "frozen_benchmark_judgments"
        if formal
        else "complete_judgments_unfrozen",
        "formal_metrics_available": formal,
        "sample_count": len(judgments),
        "reviewed_count": len(reviewed),
        "missing_judgments": len(judgments) - len(reviewed),
        "missing_count": len(judgments) - len(reviewed),
        "expected_total": expected_total,
        "recall_bounds": [
            totals["matched_expected"] / expected_total,
            (totals["matched_expected"] + missing_expected) / expected_total,
        ]
        if expected_total
        else None,
        "reviewed_subset": subset,
        "counts_basis": "reviewed_subset",
        "counts": totals,
        "precision": subset["precision"] if formal else None,
        "known_set_recall": subset["known_set_recall"] if formal else None,
        "cost": cost,
        "resource_basis": "reviewed_subset",
        "cost_per_qualified": cost / totals["matched_expected"]
        if cost is not None and totals["matched_expected"]
        else None,
        "seconds": sum(durations) if durations and all(t is not None for t in durations) else None,
    }
