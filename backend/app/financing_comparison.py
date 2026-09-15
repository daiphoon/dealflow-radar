"""融资事项关联与字段比较；关联材料不升级其事实确认状态。"""

from datetime import date

from backend.app.research_subject import normalize

COMPARISON_VERSION = "financing-comparison-v1"
RELATED_DISCLOSURE_DAYS = 1


def amount_key(value):
    # 只兼容同一公开金额是否带人民币后缀，不换算金额、币种或近似程度。
    return normalize(value).removesuffix("人民币") if value else None


def field_comparison(left, right):
    if not left and not right:
        return "not_disclosed"
    if not left:
        return "additional"
    if not right:
        return "not_repeated"
    return "matched" if left == right else "different"


def compare_financing(left, right):
    left_investors = {normalize(item) for item in left.investors}
    right_investors = {normalize(item) for item in right.investors}
    fields = {
        "subject_scope": field_comparison(left.subject_scope, right.subject_scope),
        "round": field_comparison(left.round, right.round),
        "amount_text": field_comparison(
            amount_key(left.amount_text), amount_key(right.amount_text)
        ),
        "investors": field_comparison(left_investors, right_investors),
        "disclosed_on": field_comparison(left.disclosed_on, right.disclosed_on),
        "occurred_on": field_comparison(left.occurred_on, right.occurred_on),
    }
    if left_investors and right_investors:
        if left_investors < right_investors:
            fields["investors"] = "additional"
        elif right_investors < left_investors:
            fields["investors"] = "not_repeated"
        elif left_investors & right_investors and left_investors != right_investors:
            fields["investors"] = "partial_overlap"
    try:
        days = abs(
            (date.fromisoformat(left.disclosed_on) - date.fromisoformat(right.disclosed_on)).days
        )
    except (TypeError, ValueError):
        days = None
    result = {
        "version": COMPARISON_VERSION,
        "relation": "unlinked",
        "fields": fields,
        "reasons": [],
    }
    if left.subject_scope != right.subject_scope:
        result["reasons"] = ["different_subject_scope"]
    elif fields["round"] == "different":
        result["reasons"] = ["different_round"]
    elif (set(left.issues) | set(right.issues)) - {"round_unknown"}:
        result["reasons"] = ["ambiguous_or_undated_material"]
    elif left.round and right.round and days == 0:
        # 明确轮次与同日的差异材料继续留在原事项，不覆盖其原事实。
        if fields["amount_text"] == fields["investors"] == "different" and not right.is_correction:
            result["reasons"] = ["incompatible_amount_and_investors"]
        else:
            result.update(relation="exact_matter", reasons=["same_scope_round_and_disclosure"])
    elif (
        days is not None
        and days <= RELATED_DISCLOSURE_DAYS
        and fields["amount_text"] == "matched"
        and left_investors & right_investors
        and fields["occurred_on"] != "different"
        and not left.is_correction
        and not right.is_correction
    ):
        result.update(
            relation="compatible_evidence",
            reasons=["same_scope_amount_and_investor", "same_or_adjacent_disclosure_day"],
        )
        if days:
            fields["disclosed_on"] = "related_date"
    else:
        result["reasons"] = ["insufficient_common_fields"]
    return result
