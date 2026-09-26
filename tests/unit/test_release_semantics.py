from copy import deepcopy

import pytest

from backend.app.semantic_content import canonical_fact, content_version, event_version


def financing(value):
    return {
        "event_subtype": "company_financing",
        "facts": [{"name": "融资金额", "value": value, "unit": None}],
        "fact_ledger": [
            {"name": "融资金额", "value": value, "unit": None, "support_status": "supported"}
        ],
    }


@pytest.mark.parametrize("value", ["1亿元", "1.0亿元", "1.00亿元", "10000万元", "100000000元"])
def test_equal_amounts_have_one_fact_event_and_content_version(value):
    expected, actual = financing("1亿元"), financing(value)
    original = deepcopy(actual)
    assert canonical_fact(actual["facts"][0]) == canonical_fact(expected["facts"][0])
    assert event_version(actual) == event_version(expected)
    assert content_version([actual, expected]) == content_version([expected])
    assert actual == original


@pytest.mark.parametrize(
    "value", ["近1亿元", "约1亿元", "不超过1亿元", "至少1亿元", "1亿美元", "2亿元"]
)
def test_amount_qualifier_currency_and_value_remain_distinct(value):
    assert event_version(financing(value)) != event_version(financing("1亿元"))


def test_fact_and_support_sets_ignore_order_and_duplicate_support():
    first = financing("1亿元")
    first["facts"].append({"name": "融资轮次", "value": "A轮"})
    second = deepcopy(first)
    second["facts"] = list(reversed(first["facts"])) + [first["facts"][0]]
    second["fact_ledger"] *= 2
    second["evidence"] = [{"id": "new-source"}, {"id": "old-source"}]
    assert event_version(first) == event_version(second)
    second["fact_ledger"][0] = {**second["fact_ledger"][0], "support_status": "conflicted"}
    assert event_version(first) != event_version(second)


def test_investor_entity_names_do_not_split_on_characters_inside_names():
    # The extraction contract joins multiple names with 、; 和/与 may be in a legal name.
    a = {"name": "投资方（来源口径）", "value": "和谐资本、与时资本"}
    b = {**a, "value": "与时资本、和谐资本"}
    assert canonical_fact(a) == canonical_fact(b)
    assert canonical_fact(a) != canonical_fact({**a, "value": "谐资本、时资本"})


@pytest.mark.parametrize("change", [{"occurred_on": "2026-09-01"}, {"status": "retracted"}])
def test_material_date_and_retraction_changes_remain_visible(change):
    event = financing("1亿元")
    assert event_version(event) != event_version({**event, **change})
