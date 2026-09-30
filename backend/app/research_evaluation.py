"""离线评估只调用生产抽取/验证；缺失阶段不推断成功。"""

import math
from dataclasses import asdict
from types import SimpleNamespace

from backend.app.evidence_integrity import hash_canonical_object, hash_excerpt_bytes
from backend.app.matter_comparison import compare_matters
from backend.app.matter_validation import VALIDATION_VERSION
from backend.app.research_extraction import merge_matters
from backend.app.research_matters import (
    EXTRACTION_VERSION,
    PROMPT_VERSION,
    Matter,
    TypedProposedMatters,
    extract_matters,
    validate_proposals,
)

RELATION_LABELS = ("SAME_OCCURRENCE", "RELATED_STAGE", "UNRELATED", "AMBIGUOUS")


def evaluate_relations(cases):
    """复用正式比较与抽取；四类关系与多事项拆分各有独立分母。"""
    matrix = {a: dict.fromkeys(RELATION_LABELS, 0) for a in RELATION_LABELS}
    rows, roundup = [], []
    for case in cases:
        if case["label"] == "ROUNDUP":
            matters = extract_matters(SimpleNamespace(**case["subject"]), case["text"])
            actual = [m.category for m in matters]
            roundup.append(
                {
                    "id": case["id"],
                    "actual_categories": actual,
                    "correct": sorted(actual) == sorted(case["expected_categories"]),
                }
            )
            continue
        subject = SimpleNamespace(**case["subject"]) if case.get("subject") else None
        decision = compare_matters(Matter(**case["left"]), Matter(**case["right"]), subject=subject)
        actual = (
            "RELATED_STAGE"
            if decision.decision == "related_stage"
            else "AMBIGUOUS"
            if decision.decision == "ambiguous"
            else "SAME_OCCURRENCE"
            if decision.same_matter
            else "UNRELATED"
        )
        matrix[case["label"]][actual] += 1
        rows.append(
            {
                "id": case["id"],
                "stratum": case.get("family"),
                "gold": case["label"],
                "actual": actual,
                "decision": asdict(decision),
            }
        )
    metrics = {}
    for label in RELATION_LABELS:
        tp = matrix[label][label]
        predicted, expected = (
            sum(matrix[a][label] for a in RELATION_LABELS),
            sum(matrix[label].values()),
        )
        precision, recall = tp / predicted if predicted else 0, tp / expected if expected else 0
        metrics[label] = {
            "n": expected,
            "precision": precision,
            "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0,
        }
    return {
        "confusion_matrix": matrix,
        "metrics": metrics,
        "macro_f1": sum(row["f1"] for row in metrics.values()) / len(metrics),
        "cases": rows,
        "roundup": roundup,
        "roundup_accuracy": sum(row["correct"] for row in roundup) / len(roundup)
        if roundup
        else None,
        "serious_false_merge": sum(
            row["actual"] == "SAME_OCCURRENCE" and row["gold"] in {"UNRELATED", "RELATED_STAGE"}
            for row in rows
        ),
    }


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
LAYERS = ("extracted_correct", "evidence_supported", "confirmed", "delivered")
LINKED_V2 = "linked-four-layer-v2"


def linked_judgment(
    expected_ids,
    output_ids,
    decisions,
    *,
    absence_reason=None,
    schema_version="linked-four-layer-v1",
):
    """逐输出的可追溯四层裁决；不从发布数量反推抽取正确性。"""
    expected, outputs = set(expected_ids), set(output_ids)
    if schema_version not in {"linked-four-layer-v1", LINKED_V2}:
        raise ValueError("unsupported_judgment_version")
    if len(expected) != len(expected_ids) or len(outputs) != len(output_ids):
        raise ValueError("duplicate_evaluation_identity")
    if {d.get("output_id") for d in decisions} != outputs or len(decisions) != len(outputs):
        raise ValueError("every_output_requires_one_linked_decision")
    if not outputs and absence_reason != "no_system_output":
        raise ValueError("explicit_absence_reason_required")
    matched = set()
    counts = dict.fromkeys(QUALITY_COUNTS, 0)
    layers = dict.fromkeys(LAYERS, 0)
    layer_matches = {key: set() for key in LAYERS}
    for decision in decisions:
        if not decision.get("reason") or any(
            key not in decision
            or not (
                type(decision[key]) is bool
                or (
                    schema_version == LINKED_V2
                    and decision[key] is None
                    and isinstance(decision.get("not_evaluated_reasons"), dict)
                    and isinstance(decision["not_evaluated_reasons"].get(key), str)
                    and decision["not_evaluated_reasons"][key].strip()
                )
            )
            for key in layers
        ):
            raise ValueError("explicit_four_layer_judgment_required")
        if decision["evidence_supported"] and (
            not decision.get("evidence_id") or not decision.get("quote")
        ):
            raise ValueError("supported_output_requires_evidence_locator")
        benchmark_id = decision.get("benchmark_id")
        if benchmark_id is not None and benchmark_id not in expected:
            raise ValueError("unknown_benchmark_identity")
        if decision["extracted_correct"] and decision["evidence_supported"] and benchmark_id:
            if benchmark_id in matched:
                counts["duplicate_split"] += 1
            matched.add(benchmark_id)
        for key in layers:
            layers[key] += int(decision[key] is True)
            prerequisites = (
                ("extracted_correct",)
                if key == "extracted_correct"
                else ("extracted_correct", "evidence_supported")
                if key == "evidence_supported"
                else ("extracted_correct", "evidence_supported", key)
            )
            if benchmark_id and all(decision[p] is True for p in prerequisites):
                layer_matches[key].add(benchmark_id)
        for key in counts:
            if key != "duplicate_split":
                counts[key] += int(key in decision.get("errors", []))
    return {
        "schema_version": schema_version,
        "expected_ids": list(expected_ids),
        "output_ids": list(output_ids),
        "expected": len(expected),
        "output": len(outputs),
        "correct_output": layers["extracted_correct"],
        "matched_expected": len(matched),
        **counts,
        "layers": layers,
        "layer_assessments": {
            key: {
                "evaluated": sum(type(d[key]) is bool for d in decisions),
                "passed": layers[key],
                "failed": sum(d[key] is False for d in decisions),
                "not_evaluated": sum(d[key] is None for d in decisions),
                "matched_expected": len(layer_matches[key]),
                "expected_evaluated": len(expected)
                if all(
                    type(d[p]) is bool
                    for d in decisions
                    for p in (
                        ("extracted_correct",)
                        if key == "extracted_correct"
                        else ("extracted_correct", "evidence_supported")
                        if key == "evidence_supported"
                        else ("extracted_correct", "evidence_supported", key)
                    )
                )
                else 0,
            }
            for key in LAYERS
        },
        "decisions": decisions,
        "absence_reason": absence_reason,
    }


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
        if j.get("schema_version") == LINKED_V2:
            if any(key not in j for key in ("expected_ids", "output_ids", "decisions")):
                raise ValueError("linked_judgment_identities_required")
            canonical = linked_judgment(
                j["expected_ids"],
                j["output_ids"],
                j["decisions"],
                absence_reason=j.get("absence_reason"),
                schema_version=LINKED_V2,
            )
            if any(j.get(k) != canonical[k] for k in (*required, "layer_assessments")):
                raise ValueError("judgment_counts_must_match_linked_decisions")
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
    layer_totals = {}
    for key in LAYERS:
        rows = [j.get("layer_assessments", {}).get(key) for j in reviewed]
        available = complete and all(
            row is not None and row["expected_evaluated"] == j["expected"]
            for row, j in zip(rows, reviewed, strict=True)
        )
        assessed_expected = sum(row["expected_evaluated"] for row in rows if row)
        layer_totals[key] = {
            **{
                field: sum(row[field] for row in rows if row)
                for field in ("evaluated", "passed", "failed", "not_evaluated", "matched_expected")
            },
            "unlinked_output_count": sum(
                j["output"] for row, j in zip(rows, reviewed, strict=True) if row is None
            ),
            "fixed_expected": expected_total,
            "assessed_expected": assessed_expected,
            "expected_coverage": assessed_expected / expected_total if expected_total else None,
            "complete": available,
            "recall": sum(row["matched_expected"] for row in rows if row) / expected_total
            if formal and available and expected_total
            else None,
        }
    versioned = any(j.get("schema_version") == LINKED_V2 for j in reviewed)
    all_layers = all(row["complete"] for row in layer_totals.values())
    candidate_available = formal and (
        not versioned or layer_totals["evidence_supported"]["complete"]
    )
    delivery_recall = layer_totals["delivered"]["recall"]
    extraction_complete = not versioned or layer_totals["extracted_correct"]["complete"]
    candidate_complete = not versioned or layer_totals["evidence_supported"]["complete"]
    if not extraction_complete:
        subset["precision"] = None
    if not candidate_complete:
        subset["known_set_recall"] = None
    unknown_expected = missing_expected
    if versioned and benchmark is not None:
        unknown_expected = sum(
            b["expected"]
            for j, b in zip(judgments, benchmark, strict=True)
            if j is None
            or j.get("layer_assessments", {})
            .get("evidence_supported", {})
            .get("expected_evaluated")
            != b["expected"]
        )
    qualified = (
        layer_totals["delivered"]["matched_expected"] if versioned else totals["matched_expected"]
    )
    qualified_available = not versioned or (formal and layer_totals["delivered"]["complete"])
    return {
        "status": "not_recorded"
        if not reviewed
        else "partial_judgments"
        if not complete
        else "frozen_benchmark_judgments"
        if formal
        else "complete_judgments_unfrozen",
        "formal_metrics_available": formal and (not versioned or all_layers),
        "evaluation_status": "complete"
        if complete and (not versioned or all_layers)
        else "not_evaluated_layers_remain",
        "metric_basis": "layered_evaluation" if versioned else "legacy_candidate_counts",
        "layer_assessments": layer_totals,
        "candidate_supported_recall": subset["known_set_recall"] if candidate_available else None,
        "business_delivery_recall": delivery_recall,
        "sample_count": len(judgments),
        "reviewed_count": len(reviewed),
        "missing_judgments": len(judgments) - len(reviewed),
        "missing_count": len(judgments) - len(reviewed),
        "expected_total": expected_total,
        "recall_bounds": [
            totals["matched_expected"] / expected_total,
            min(1, (totals["matched_expected"] + unknown_expected) / expected_total),
        ]
        if expected_total
        else None,
        "recall_bounds_basis": "candidate_supported" if versioned else "legacy_candidate_counts",
        "reviewed_subset": subset,
        "counts_basis": "reviewed_subset",
        "counts": totals,
        "precision": subset["precision"] if formal and extraction_complete else None,
        "known_set_recall": subset["known_set_recall"] if formal and not versioned else None,
        "cost": cost,
        "resource_basis": "reviewed_subset",
        "cost_per_qualified": cost / qualified
        if qualified_available and cost is not None and qualified
        else None,
        "qualified_basis": "business_delivery" if versioned else "legacy_candidate_counts",
        "seconds": sum(durations) if durations and all(t is not None for t in durations) else None,
    }
