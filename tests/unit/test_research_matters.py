from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from backend.app.config import WebResearchPolicy
from backend.app.research_acquisition import (
    TavilyDocumentProvider,
    TavilySearchProvider,
    allowed_url,
    source_date,
)
from backend.app.research_extraction import DeepSeekMatterProvider
from backend.app.research_matters import extract_matters, validate_proposals
from backend.app.source_fetcher import SourceFetchError
from backend.app.web_search import SearchRequest

SUBJECT = SimpleNamespace(legal_name="示例山海设备有限公司", aliases=("示例山海",))


@pytest.mark.parametrize(
    "text,subtype,role,value",
    [
        ("示例山海完成近一亿元A轮融资，估值达10亿元", "company_financing", "valuation", "10亿元"),
        ("示例山海拟发行不超过400万股", "ipo_offering", "share_quantity", "不超过400万股"),
        ("示例山海认缴示例基金5000万元", "fund_commitment", "commitment", "5000万元"),
        ("示例山海对外投资500万元购买示例公司股权", "outbound_investment", "investment", "500万元"),
        (
            "示例山海注册资本由100万元增加至200万元",
            "registered_capital",
            "registered_capital_after",
            "200万元",
        ),
    ],
)
def test_typed_amounts(text, subtype, role, value):
    matter = extract_matters(SUBJECT, text)[0]
    assert matter.subtype == subtype
    assert matter.fields[role]["value"] == value
    if role == "share_quantity":
        assert "financing" not in matter.fields and matter.status == "planned"


@pytest.mark.parametrize(
    "text,subtype",
    [
        ("示例山海通过港交所聆讯", "ipo_hearing"),
        ("示例山海完成上市辅导备案", "ipo_guidance"),
        ("示例山海完成IPO辅导，2026年4月2日签订了辅导协议", "ipo_guidance_completed"),
        ("示例山海计划2026年12月2日正式挂牌上市", "ipo_listing_plan"),
    ],
)
def test_ipo_semantics_and_no_borrowed_date(text, subtype):
    matter = extract_matters(SUBJECT, text)[0]
    assert matter.subtype == subtype
    assert not matter.fields.get("date", {}).get("iso")


def test_multiple_categories_local_alias_denial_and_unrelated_navigation():
    text = (
        "示例山海设备有限公司（以下简称“山海设备”）\n"
        "山海设备完成A轮融资。公司中标500万元合同。\n"
        "示例山海否认计划赴港上市。\n"
        "相关推荐\n示例山海正式挂牌上市。"
    )
    matters = extract_matters(SUBJECT, text)
    assert [m.subtype for m in matters] == [
        "company_financing",
        "contract_award",
        "ipo_listing_plan",
    ]
    assert matters[0].scope == "legal_entity" and matters[2].status == "denied"
    proposed, rejected = validate_proposals(
        SUBJECT, text, {"matters": [{"subject": "山海设备", "action_quote": "山海设备完成A轮融资"}]}
    )
    assert not rejected and proposed[0].scope == "legal_entity"


@pytest.mark.parametrize(
    "action,category",
    [
        ("完成A轮融资", "financing_cap_table"),
        ("通过港交所聆讯", "exit_liquidity"),
        ("中标500万元合同", "contract_commercial"),
        ("发布新机器人", "product_technology"),
        ("营收达到1亿元", "financial_operation"),
        ("董事长辞任", "governance_people"),
        ("被法院执行", "legal_compliance"),
        ("新建示例工厂", "capacity_assets"),
    ],
)
def test_eight_category_contract(action, category):
    assert extract_matters(SUBJECT, "示例山海" + action)[0].category == category


def test_model_wrong_money_removed_without_losing_supported_fields():
    text = "示例山海完成1亿元A轮融资，估值10亿元。"
    proposed, _ = validate_proposals(
        SUBJECT,
        text,
        {
            "matters": [
                {
                    "subject": "示例山海",
                    "action_quote": text[:-1],
                    "fields": {
                        "amount": {"value": "10亿元", "quote": text[:-1], "role": "financing"}
                    },
                }
            ]
        },
    )
    assert proposed[0].fields["financing"]["value"] == "1亿元"
    assert "rejected_field:amount" in proposed[0].issues


def test_source_date_conflict_and_business_dates():
    assert (
        source_date("标题\n2026-09-20 10:30 来源：示例\n正文")[0].date().isoformat() == "2026-09-20"
    )
    assert source_date("2026年4月2日签署了协议")[0] is None
    assert source_date("2026年9月20日取得境外备案")[0] is None
    assert source_date("正文无日期", "2026-09-20")[0] is None
    assert source_date("发布时间：2026-09-20\n更新时间：2026-09-21")[0] is None


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/x",
        "http://example.com/x",
        "https://x.local/x",
        "https://example.com.evil.org/x",
        "https://user:pass@example.com/x",
    ],
)
def test_acquisition_url_boundary(url):
    assert not allowed_url(url, ("example.com",))


def test_tavily_search_preserves_body_not_summary_and_drops_bad_row():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "示例",
                        "url": "https://example.com/a",
                        "content": "摘要",
                        "raw_content": "正文\n原文",
                        "published_date": "2026-09-20",
                    },
                    {"title": None, "url": None},
                ]
            },
        )
    )
    provider = TavilySearchProvider(
        "test-only",
        timeout_seconds=5,
        max_response_bytes=10000,
        user_agent="test",
        client=httpx.Client(transport=transport),
    )
    result = provider.search(SearchRequest(query="示例"))
    assert len(result.results) == 1
    assert result.results[0].source_body == "正文\n原文"


def test_extract_byte_budget_and_exact_url():
    policy = replace(
        WebResearchPolicy(), tavily_enabled=True, tavily_allowed_domains=("example.com",)
    )
    provider = TavilyDocumentProvider(
        "test-only",
        policy,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, json={"results": [{"url": "https://evil.org", "raw_content": "正文"}]}
                )
            )
        ),
    )
    with pytest.raises(SourceFetchError, match="no requested"):
        provider.extract("https://example.com/a", "示例", 1000)
    assert provider.request_count == 1 and provider.downloaded_bytes > 0
    with pytest.raises(SourceFetchError, match="exceeds budget"):
        provider.extract("https://example.com/a", "示例", 2)


def test_model_quote_cannot_remove_generated_source_warning_or_denial():
    body = "示例山海通过港交所聆讯。示例山海否认该消息。\n本文包含人工智能生成内容"
    values, _ = validate_proposals(
        SUBJECT,
        body,
        {"matters": [{"subject": "示例山海", "action_quote": "示例山海通过港交所聆讯"}]},
    )
    assert values[0].status == "denied"
    assert "generated_source_not_fact" in values[0].issues


def test_model_policy_default_off_and_invalid_enablement():
    policy = WebResearchPolicy()
    assert (
        not policy.matter_processing_enabled
        and not policy.matter_model_enabled
        and not policy.tavily_enabled
    )
    with pytest.raises(ValueError, match="explicit enablement"):
        replace(policy, primary_provider="tavily")
    with pytest.raises(ValueError, match="configured model"):
        replace(policy, matter_model_enabled=True)


@pytest.mark.parametrize(
    "text,subtype",
    [
        ("示例山海获得示例机构战略投资", "company_financing"),
        ("示例山海战略投资示例技术公司股权", "outbound_investment"),
    ],
)
def test_explicit_investment_recipient_direction(text, subtype):
    assert extract_matters(SUBJECT, text)[0].subtype == subtype


@pytest.mark.parametrize("text", ["示例山海子公司完成A轮融资", "示例山海旗下子公司通过港交所聆讯"])
def test_subsidiary_action_not_bound_to_parent(text):
    assert extract_matters(SUBJECT, text) == []


def test_model_preserves_confirmed_former_name_scope():
    subject = SimpleNamespace(
        legal_name=SUBJECT.legal_name, aliases=SUBJECT.aliases, legal_aliases=SUBJECT.aliases
    )
    body = "示例山海完成1亿元A轮融资"
    proposed, rejected = validate_proposals(
        subject, body, {"matters": [{"subject": "示例山海", "action_quote": body}]}
    )
    assert not rejected
    assert proposed[0].scope == extract_matters(subject, body)[0].scope == "legal_entity"


@pytest.mark.parametrize(
    "response", [[], {"choices": []}, {"choices": [None]}, {"choices": [{"message": None}]}]
)
def test_malformed_model_envelope_enters_controlled_failure_path(response):
    provider = DeepSeekMatterProvider(
        "test-only",
        WebResearchPolicy(),
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))
        ),
    )
    with pytest.raises(ValueError, match="model response"):
        provider.extract({})


@pytest.mark.parametrize("name", ["示例山海科技有限公司", "示例山海控股股份有限公司"])
def test_similar_company_name_cannot_supply_target_matter(name):
    body = f"{name}完成1亿元A轮融资"
    assert extract_matters(SUBJECT, body) == []
    proposed, rejected = validate_proposals(
        SUBJECT, body, {"matters": [{"subject": "示例山海", "action_quote": body}]}
    )
    assert not proposed and rejected
    assert extract_matters(SUBJECT, SUBJECT.legal_name + "完成1亿元A轮融资")
