"""事项身份判定先于字段差异；缺少区分依据时不猜测合并。"""

import re
from dataclasses import dataclass, field

from backend.app.matter_dates import occurrence
from backend.app.matter_validation import equivalent_value

COMPARISON_VERSION = "matter-comparison-v3"


@dataclass(frozen=True)
class MergeDecision:
    decision: str
    same_matter: bool
    reason: str
    field_differences: dict = field(default_factory=dict)
    version: str = COMPARISON_VERSION


def compare_matters(left, right):
    if (left.category, left.scope) != (right.category, right.scope):
        return MergeDecision("new_matter", False, "category_or_scope_differs")
    a, b = left.fields, right.fields

    def value(fields, key):
        if key in {"date", "occurred"}:
            item = occurrence(fields)
            return item.get("iso") or item.get("interval_start")
        item = fields.get(key, {})
        return item.get("value")

    def same_value(key, av, bv):
        if key == "investors":
            return {v.strip() for v in re.split(r"、|及|和|与", av)} == {
                v.strip() for v in re.split(r"、|及|和|与", bv)
            }
        if key in {"date", "occurred"}:
            left_date, right_date = occurrence(a), occurrence(b)
            # A month is not silently equal to a precise day.
            if left_date.get("precision", "day") != right_date.get("precision", "day"):
                return False
        return equivalent_value(av, bv)

    def equal(key):
        av, bv = value(a, key), value(b, key)
        return bool(av and bv and same_value(key, av, bv))

    def differs(key):
        av, bv = value(a, key), value(b, key)
        return bool(av and bv and not same_value(key, av, bv))

    if any(differs(k) for k in ("transaction_id", "project_id")):
        return MergeDecision("new_matter", False, "explicit_identity_differs")
    anchor = equal("transaction_id") or equal("project_id")
    if left.subtype != right.subtype:
        if anchor and left.subtype.startswith("ipo_") and right.subtype.startswith("ipo_"):
            return MergeDecision("related_stage", False, "same_project_new_stage")
        if (
            left.subtype.startswith("ipo_")
            and right.subtype.startswith("ipo_")
            and equal("market")
            and not differs("application_cycle")
        ):
            return MergeDecision("related_stage", False, "same_issuer_market_possible_process")
        return MergeDecision("new_matter", False, "different_stage_or_action")
    if differs("application_cycle") or differs("acquisition_stage"):
        return MergeDecision(
            "related_stage", False, "separate_application_or_acquisition_milestone"
        )
    exact = re.sub(r"\s+|[。；;]$", "", left.action) == re.sub(r"\s+|[。；;]$", "", right.action)
    if not anchor and not exact:
        if differs("date") or differs("round"):
            return MergeDecision("new_matter", False, "event_date_or_round_differs")
        if left.subtype == "company_financing":
            # 轮次或金额相同不够；要求发生日及另一项区分特征，或轮次+金额+投资方。
            anchor = (
                equal("date") and (equal("investors") or (equal("round") and equal("financing")))
            ) or (equal("round") and equal("financing") and equal("investors"))
        if left.subtype.startswith("ipo_"):
            anchor = equal("date") and equal("market") and not differs("application_cycle")
        if left.subtype in {"outbound_investment", "acquisition_target"}:
            anchor = (
                equal("date")
                and equal("buyer")
                and equal("target")
                and equal("acquisition_scope")
                and equal("acquisition_stage")
            )
        if not anchor:
            return MergeDecision("ambiguous", False, "insufficient_matter_identity")
    differences = {
        k: {"before": value(a, k), "after": value(b, k)}
        for k in (set(a) & set(b)) - {"disclosed", "planned"}
        if differs(k)
    }
    if differences:
        return MergeDecision("field_conflict", True, "same_identity_different_values", differences)
    if right.status == "denied" and left.status != "denied":
        return MergeDecision("correction_candidate", True, "explicit_denial_same_matter")
    if right.status != left.status:
        return MergeDecision("progress_update", True, "same_matter_status_change")
    if set(b) - set(a):
        return MergeDecision("add_fields", True, "new_supported_fields")
    return MergeDecision("source_support", True, "same_matter_matching_claims")
