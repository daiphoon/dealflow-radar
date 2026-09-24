from types import SimpleNamespace

from backend.app.matter_fragments import continuous_quote
from backend.app.research_evaluation import funnel
from backend.app.research_plan import plan_intent


def test_intents_rotate_without_changing_budget():
    first = plan_intent("financing_cap_table", [])
    second = plan_intent(
        "financing_cap_table",
        [
            {
                "search_groups": {
                    "a": {**first, "topic_category": "financing_cap_table", "attempted_at": "now"}
                }
            }
        ],
    )
    assert first["topic_intent"] == "company_financing"
    assert second["topic_intent"] == "fund_commitment"


def test_discontinuous_window_cannot_be_one_quote():
    document = SimpleNamespace(
        payload={
            "excerpt": "甲完成融资\n乙计划上市",
            "content_extraction": {
                "retained_spans": [
                    {"stored_start": 0, "stored_end": 5},
                    {"stored_start": 6, "stored_end": 11},
                ]
            },
        }
    )
    assert continuous_quote(document, "甲完成融资")
    assert not continuous_quote(document, "完成融资\n乙计划")


def test_funnel_keeps_unknown_and_multiple_breaks():
    got = funnel({"search_hit": False, "api_visible": False})
    assert got["first_break"] == "search_hit"
    assert got["later_breaks"] == ["api_visible"]
    assert got["stages"]["body_available"] == "not_recorded"


def test_model_selection_is_reasoned_and_not_every_document():
    from backend.app.research_extraction import model_selection_reason

    assert model_selection_reason("甲公司拟认缴基金", []) == "complex_roles_or_stage"
    assert any(model_selection_reason("普通联系方式" + str(i), []) is None for i in range(20))


def test_scorecard_preserves_missing_and_zero_qualified_denominators():
    from backend.app.research_evaluation import QUALITY_COUNTS, scorecard

    zero = {
        "expected": 4,
        "output": 2,
        "correct_output": 0,
        "matched_expected": 0,
        "cost": 1,
        "seconds": 3,
        **dict.fromkeys(QUALITY_COUNTS, 0),
    }
    result = scorecard([zero, None])
    assert result["known_set_recall"] == 0 and result["precision"] == 0
    assert result["cost_per_qualified"] is None and result["missing_judgments"] == 1
    assert scorecard([None])["precision"] is None
