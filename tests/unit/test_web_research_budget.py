from decimal import Decimal

import pytest

from backend.app import web_research_budget as budget
from backend.app.config import Settings, WebResearchCostPolicy, WebResearchPolicy
from backend.app.web_search import MockSearchProvider


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "0.0000001", "1000000000000"])
def test_invalid_price_or_limit_is_rejected(value):
    with pytest.raises(ValueError):
        WebResearchCostPolicy(baidu_price_per_call=Decimal(value))
    with pytest.raises(ValueError):
        WebResearchCostPolicy(system_monthly_limit=Decimal(value))


def test_unpriced_is_not_free_and_env_prices_are_explicit(monkeypatch):
    class UnpricedProvider:
        code = "baidu"

    assert budget.search_price(UnpricedProvider(), WebResearchPolicy()) is None
    assert budget.search_price(MockSearchProvider("baidu"), WebResearchPolicy()) == 0
    monkeypatch.setenv("WEB_RESEARCH_BAIDU_PRICE_PER_CALL", "")
    monkeypatch.setenv("WEB_RESEARCH_BOCHA_PRICE_PER_CALL", "0")
    monkeypatch.setenv("WEB_RESEARCH_UNKNOWN_PRICE_UPPER_BOUND", "0.123456")
    monkeypatch.setenv("WEB_RESEARCH_SYSTEM_MONTHLY_LIMIT", "5")
    cost = Settings.from_env().web_research_policy.cost
    assert cost.baidu_price_per_call is None
    assert cost.bocha_price_per_call == 0
    assert cost.unknown_price_upper_bound == Decimal("0.123456")
    assert cost.system_monthly_limit == 5
