from decimal import Decimal

from backend.app.config import WebResearchPolicy
from backend.app.research_cost_preview import preview_cost
from backend.app.semantic_content import event_version


def test_semantics_ignores_evidence_and_processing_but_tracks_facts():
    event = {
        "event_subtype": "company_financing",
        "facts": [{"name": "融资金额", "value": "1亿元", "unit": None}],
    }
    same = {
        **event,
        "evidence": [{"id": "different"}],
        "observed_at": "tomorrow",
        "facts": [{"name": "融资金额", "value": "10000万元", "unit": None}],
    }
    assert event_version(event) == event_version(same)
    assert event_version(event) != event_version({**event, "status": "retracted"})


def test_model_cost_includes_tokens():
    policy = WebResearchPolicy(
        incremental_research_enabled=True,
        matter_processing_enabled=True,
        matter_model_enabled=True,
        extraction_model="fixture",
        model_input_price_per_million=Decimal("2"),
        model_output_price_per_million=Decimal("8"),
    )
    got = preview_cost(policy, 2)
    assert got["input_tokens"] == 96000 and got["output_tokens"] == 12000
    assert got["planned_cost_upper_bound"] is None
    assert got["operations_per_company"][-1]["cash_upper_bound"] == "0.144"


def test_equivalent_field_ledger_and_investor_sets_do_not_notify():
    a = {
        "facts": [{"name": "投资方", "value": "甲、乙"}],
        "fact_ledger": [{"name": "融资金额", "value": "1亿元", "support_status": "supported"}],
    }
    b = {
        "facts": [{"name": "投资方", "value": "乙、 甲"}],
        "fact_ledger": [{"name": "融资金额", "value": "10000万元", "support_status": "supported"}],
    }
    assert event_version(a) == event_version(b)
    b["fact_ledger"][0]["support_status"] = "conflicted"
    assert event_version(a) != event_version(b)
