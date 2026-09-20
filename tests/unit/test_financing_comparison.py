from dataclasses import replace

import pytest

from backend.app.financing_comparison import compare_financing
from backend.app.financing_events import FinancingCandidate


def disclosure(**changes):
    return replace(
        FinancingCandidate(
            "示例品牌",
            "brand:示例品牌",
            "B轮",
            "近2亿元",
            ("示例甲资本", "示例乙资本"),
            "2026-06-01",
            None,
            "虚构材料",
            False,
        ),
        **changes,
    )


@pytest.mark.parametrize("day", ["2026-05-31", "2026-06-01", "2026-06-02"])
def test_missing_round_can_link_with_amount_investor_and_adjacent_date(day):
    new = disclosure(
        round=None,
        issues=("round_unknown",),
        amount_text="近2亿元人民币",
        investors=("示例 甲资本",),
        disclosed_on=day,
    )
    result = compare_financing(disclosure(), new)
    assert result["relation"] == "compatible_evidence"
    assert result["fields"]["round"] == "not_repeated"
    assert result["fields"]["amount_text"] == "matched"
    assert result["fields"]["investors"] == "not_repeated"
    assert new.round is None and new.investors == ("示例 甲资本",)


@pytest.mark.parametrize(
    "changes",
    [
        {"round": "C轮", "issues": ()},
        {"subject_scope": "legal_entity:fictional"},
        {"amount_text": "近3亿元"},
        {"amount_text": "近2亿美元"},
        {"amount_text": "2亿元"},
        {"amount_text": None},
        {"investors": ("另一资本",)},
        {"investors": ()},
        {"disclosed_on": "2026-06-03"},
        {"disclosed_on": None},
        {"issues": ("multiple_financing_mentions", "round_unknown")},
        {"is_correction": True},
    ],
)
def test_partial_material_needs_all_linking_conditions(changes):
    new = disclosure(round=None, issues=("round_unknown",))
    assert compare_financing(disclosure(), replace(new, **changes))["relation"] == "unlinked"


def test_missing_fields_and_partial_investor_lists_are_not_conflicts():
    old = disclosure(investors=(), amount_text=None)
    result = compare_financing(old, disclosure())
    assert result["relation"] == "exact_matter"
    assert result["fields"]["investors"] == result["fields"]["amount_text"] == "additional"
    assert result["fields"]["occurred_on"] == "not_disclosed"
    assert (
        compare_financing(disclosure(), disclosure(investors=("示例甲资本", "另一资本")))["fields"][
            "investors"
        ]
        == "partial_overlap"
    )


def test_explicit_correction_is_kept_as_a_difference_without_mutation():
    old = disclosure()
    result = compare_financing(old, disclosure(amount_text="近3亿元", is_correction=True))
    assert result["relation"] == "exact_matter"
    assert result["fields"]["amount_text"] == "different"
    assert old.amount_text == "近2亿元"


def test_same_round_and_day_do_not_join_incompatible_amount_and_investors():
    new = disclosure(amount_text="近3亿元", investors=("另一资本",))
    assert compare_financing(disclosure(), new)["relation"] == "unlinked"
