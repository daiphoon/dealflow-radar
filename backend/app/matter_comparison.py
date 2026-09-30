"""事项身份判定先于字段差异；缺少区分依据时不猜测合并。"""

import re
from dataclasses import dataclass, field

from backend.app.matter_dates import occurrence
from backend.app.matter_validation import equivalent_value

COMPARISON_VERSION = "matter-comparison-v5"


@dataclass(frozen=True)
class MergeDecision:
    decision: str
    same_matter: bool
    reason: str
    field_differences: dict = field(default_factory=dict)
    version: str = COMPARISON_VERSION
    context_basis: str | None = None


def compare_matters(left, right, *, subject=None, left_context=None, right_context=None):
    from backend.app.matter_identity import legal_declarations, resolved_identity, shared_claim

    scopes = [m.scope for m in (left, right)]
    claim_anchor = None
    if subject is not None:
        if any(
            ctx is not None and ctx.company_key != str(getattr(subject, "id", subject.legal_name))
            for ctx in (left_context, right_context)
        ):
            return MergeDecision("new_matter", False, "context_company_binding_differs")
        claim_anchor = shared_claim(subject, left, right, left_context, right_context)
        scopes = [
            resolved_identity(subject, m, ctx)["scope"] if ctx else m.scope
            for m, ctx in ((left, left_context), (right, right_context))
        ]
    left_subject, right_subject = (re.sub(r"\s+", "", m.subject) for m in (left, right))
    verified_names = (
        {re.sub(r"\s+", "", name) for name in (subject.legal_name, *subject.aliases)}
        if subject is not None
        else set()
    )
    if subject is not None:
        for ctx in (left_context, right_context):
            if ctx:
                verified_names.update(
                    re.sub(r"\s+", "", name) for name in legal_declarations(subject, ctx.excerpt)
                )
    if left_subject != right_subject and not {left_subject, right_subject} <= verified_names:
        return MergeDecision("new_matter", False, "subject_differs")
    if (
        claim_anchor
        and "legal_entity" in scopes
        and {left_subject, right_subject} <= verified_names
    ):
        scopes = ["legal_entity", "legal_entity"]
    if left.category != right.category:
        return MergeDecision("new_matter", False, "category_or_scope_differs")
    if scopes[0] != scopes[1]:
        if left_context is not None or right_context is not None:
            return MergeDecision("ambiguous", False, "document_subject_equivalence_unproven")
        return MergeDecision("new_matter", False, "category_or_scope_differs")
    a, b = left.fields, right.fields
    identity_proven = bool(claim_anchor)

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
                intervals = [
                    (d.get("interval_start") or d.get("iso"), d.get("interval_end") or d.get("iso"))
                    for d in (left_date, right_date)
                ]
                return (
                    identity_proven
                    and all(all(pair) for pair in intervals)
                    and (max(pair[0] for pair in intervals) <= min(pair[1] for pair in intervals))
                )
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
    identity_proven = identity_proven or anchor
    if anchor and left.status != right.status and "denied" not in {left.status, right.status}:
        return MergeDecision("related_stage", False, "same_identity_separate_status_milestone")
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
    if not anchor and (differs("round") or differs("date")):
        return MergeDecision("new_matter", False, "event_date_or_round_differs")
    anchor = anchor or bool(claim_anchor)
    if anchor and left.status != right.status and "denied" not in {left.status, right.status}:
        return MergeDecision("related_stage", False, "same_identity_separate_status_milestone")
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
        return MergeDecision(
            "field_conflict",
            True,
            "same_identity_different_values",
            differences,
            context_basis=claim_anchor,
        )
    if right.status == "denied" and left.status != "denied":
        return MergeDecision(
            "correction_candidate", True, "explicit_denial_same_matter", context_basis=claim_anchor
        )
    if right.status != left.status:
        return MergeDecision(
            "progress_update", True, "same_matter_status_change", context_basis=claim_anchor
        )
    if set(b) - set(a):
        return MergeDecision("add_fields", True, "new_supported_fields", context_basis=claim_anchor)
    return MergeDecision(
        "source_support", True, "same_matter_matching_claims", context_basis=claim_anchor
    )
