from datetime import date

import httpx
import pytest

from backend.app.config import SourceMonitoringPolicy, WebResearchPolicy
from backend.app.financing_events import extract_financing, same_matter
from backend.app.models import Company
from backend.app.research_subject import (
    BusinessExcerptSelector,
    ResearchSubject,
    matched_name,
    short_business_query,
)
from backend.app.source_fetcher import SourceFetchError, TrustedSourceFetcher
from backend.app.web_research_service import _document_matches_company, _query_for, _subject_match
from backend.app.web_search import SearchResult, _bounded_baidu_query

NAME = "示例山海设备有限公司"
ALIAS = "示例山海"


def subject():
    return ResearchSubject(Company(legal_name=NAME, credit_code="91310000999999999J"), (ALIAS,))


def test_aliases_share_search_and_body_basis_without_extra_search_groups():
    company = subject()
    assert _query_for(company, "融资") == f'("{NAME}" OR "{ALIAS}") 融资'
    assert matched_name(company, f"{ALIAS}完成B轮融资。") == ALIAS
    assert _subject_match(
        company,
        SearchResult(
            provider_record_id="one",
            source_name="测试来源",
            published_at="2026-06-01",
            title=f"{ALIAS}融资",
            snippet="报道",
            url="https://example.com/article",
        ),
    )
    assert _document_matches_company(
        company,
        "https://example.com/article",
        f"{ALIAS}完成B轮融资。",
        "https://example.com/article",
    )
    assert not WebResearchPolicy().incremental_research_enabled


@pytest.mark.parametrize("topic", ["融资", "产品"])
def test_short_queries_keep_the_confirmed_name_and_single_topic_at_provider_boundary(topic):
    query = short_business_query(subject(), topic)
    assert query == f'"{ALIAS}" {topic}'
    assert _bounded_baidu_query(query) == query
    assert "OR" not in query and NAME not in query


def test_short_query_uses_stable_shortest_business_alias_without_deriving_a_name():
    company = ResearchSubject(
        subject().company, ("示例山海设备", "旧名称", ALIAS), legal_aliases=("旧名称",)
    )
    assert short_business_query(company, "融资") == f'"{ALIAS}" 融资'
    assert short_business_query(ResearchSubject(company.company, ()), "融资") == f'"{NAME}" 融资'
    assert (
        short_business_query(
            ResearchSubject(company.company, ("旧名称",), legal_aliases=("旧名称",)), "融资"
        )
        == f'"{NAME}" 融资'
    )


@pytest.mark.parametrize(
    "body",
    [
        "示例山海股份有限公司完成B轮融资。",
        "示例山海科技有限公司完成B轮融资。",
        "示例山海的子公司完成B轮融资。",
        "另一公司完成B轮融资。",
    ],
)
def test_similar_and_child_names_are_not_the_verified_brand(body):
    assert matched_name(subject(), body) is None
    assert extract_financing(subject(), body, date(2026, 6, 1)) is None


def test_financing_amount_investors_and_disclosure_precision():
    candidate = extract_financing(
        subject(),
        "2026年6月1日，示例山海宣布完成近4亿元B轮融资。本轮融资由示例甲资本领投，示例乙基金跟投。",
        date(2026, 6, 4),
    )
    assert candidate.round == "B轮"
    assert candidate.amount_text == "近4亿元"
    assert candidate.investors == ("示例乙基金", "示例甲资本")
    assert candidate.disclosed_on == "2026-06-01"
    assert candidate.occurred_on is None
    assert candidate.subject_scope == "brand:示例山海"
    assert not candidate.issues


def test_unknowns_and_distinct_rounds_are_not_filled_or_merged():
    candidate = extract_financing(
        subject(), "示例山海完成B轮融资，金额及投资方未披露。", date(2026, 6, 1)
    )
    assert candidate.amount_text is None and not candidate.investors
    assert candidate.occurred_on is None
    other = extract_financing(subject(), "示例山海完成C轮融资。", date(2026, 6, 1))
    assert not same_matter(candidate, other)
    legal = extract_financing(subject(), f"{NAME}完成B轮融资。", date(2026, 6, 1))
    assert not same_matter(candidate, legal)
    later = extract_financing(subject(), "示例山海完成B轮融资。", date(2026, 7, 1))
    assert not same_matter(candidate, later)


@pytest.mark.parametrize(
    "body",
    [
        "示例山海拟完成B轮融资。",
        "示例山海参与其他公司B轮融资。",
        "示例山海旗下子公司完成B轮融资。",
        "示例山海是一家设备公司。其他公司完成B轮融资。",
    ],
)
def test_financing_requires_recipient_and_completed_change(body):
    assert extract_financing(subject(), body, date(2026, 6, 1)) is None


def fetch(body):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, text="User-agent: *\nAllow: /", headers={"content-type": "text/plain"}
            )
        return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})

    fetcher = TrustedSourceFetcher(
        SourceMonitoringPolicy(min_request_interval_ms=0),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=lambda *_: {"93.184.216.34"},
    )
    try:
        return fetcher.check(
            source_type="single_page",
            root_domain="example.com",
            start_url="https://example.com/article",
            retention_policy="minimal_excerpt",
            conditional_state={},
            excerpt_selector=BusinessExcerptSelector(subject()),
        )
    finally:
        fetcher.close()


def test_body_after_leading_1500_chars_retains_contiguous_excerpt_and_position():
    irrelevant = "行业概况资料。" * 350
    body = (
        "<html><head><title>融资报道</title></head><body><nav>示例山海完成C轮融资。</nav>"
        f"<article><p>{irrelevant}</p><p>示例山海完成近4亿元B轮融资。</p>"
        "<p>本轮融资由示例资本领投。</p></article></body></html>"
    )
    document = fetch(body).documents[0]
    assert document.metadata["excerpt_start"] > 1500
    assert document.metadata["extraction_method"] == "business_passage"
    assert "B轮" in document.excerpt and "示例资本" in document.excerpt
    assert "C轮" not in document.excerpt
    assert len(document.excerpt) <= 1500
    assert document.content_hash


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (
            '<html><body><div id="root"></div><script>render()</script></body></html>',
            "dynamic_rendering_required",
        ),
        ("<html><body></body></html>", "static_body_missing"),
        ("<html><body>请完成验证码以继续访问</body></html>", "captcha_required"),
    ],
)
def test_unreadable_bodies_have_specific_reasons(body, error):
    with pytest.raises(SourceFetchError) as caught:
        fetch(body)
    assert caught.value.code == error


@pytest.mark.parametrize(
    "amount", ["近两亿元", "数千万元", "约1.2亿元", "超过5000万美元", "上亿元"]
)
def test_public_amount_qualifier_is_preserved(amount):
    candidate = extract_financing(subject(), f"示例山海完成{amount}B轮融资。", date(2026, 6, 1))
    assert candidate.amount_text == amount


def test_repeated_headline_uses_complete_body_without_inventing_a_round():
    body = (
        "示例山海完成近4亿元融资，示例龙珠领投\n"
        "2026年06月29日 17:31\n来源：示例报\n"
        "6月29日，投资机构发文宣布，设备企业 SKY 示例山海近日完成近4亿元人民币新一轮融资。"
        "本轮融资由示例龙珠领投，其他资金持续加码。\n"
        "原标题：示例山海完成近4亿元融资，示例龙珠领投"
    )
    candidate = extract_financing(subject(), body, date(2026, 6, 29))
    assert candidate.amount_text == "近4亿元人民币"
    assert candidate.investors == ("示例龙珠",)
    assert candidate.round is None and candidate.occurred_on is None
    assert candidate.disclosed_on == "2026-06-29"
    assert candidate.issues == ("round_unknown",)
    assert "本轮融资由示例龙珠领投" in candidate.evidence


def test_bilingual_brand_prefix_does_not_match_embedded_latin_names_or_a_subsidiary():
    assert matched_name(subject(), "SKY 示例山海近日完成融资。") == ALIAS
    latin = ResearchSubject(subject().company, ("SKY",))
    assert matched_name(latin, "OTHER_Skyline完成融资") is None
    assert matched_name(latin, "ASKY完成融资") is None
    assert matched_name(subject(), "SKY 示例山海的子公司完成融资。") is None


@pytest.mark.parametrize(
    "other",
    [
        "示例山海完成C轮融资。",
        "示例山海完成A轮融资，随后完成B轮融资。",
        "示例山海完成近3亿元B轮融资。",
        "2026年7月1日，示例山海完成近4亿元B轮融资。",
    ],
)
def test_distinct_financing_disclosures_remain_ambiguous(other):
    candidate = extract_financing(
        subject(),
        "2026年6月1日，示例山海完成近4亿元B轮融资。\n" + other,
        date(2026, 6, 1),
    )
    assert "multiple_financing_mentions" in candidate.issues


def test_visible_article_header_supplies_date_without_guessing_timezone():
    title = "示例山海完成近4亿元融资"
    document = fetch(
        f"<html><head><title>{title} _ 示例财经网</title></head><body>"
        f"<h1>{title}</h1><div>2026年06月29日 17:31</div><div>来源：示例报</div>"
        "<p>示例山海近日完成近4亿元融资。</p></body></html>"
    ).documents[0]
    assert document.published_at.date() == date(2026, 6, 29)
    assert document.metadata["published_at_precision"] == "day"
    assert document.metadata["published_at_raw"] == "2026年06月29日 17:31"


@pytest.mark.parametrize(
    "header",
    [
        "<h1>其他文章标题不能用于本篇新闻</h1><div>2026年06月29日</div><div>来源：示例报</div>",
        "<h1>示例山海完成近4亿元融资</h1><div>2026年06月29日</div>",
        "<footer><h1>示例山海完成近4亿元融资</h1><div>2026年06月29日</div><div>来源：示例报</div></footer>",
        "<h1>示例山海完成近4亿元融资</h1><div>2026年02月30日</div><div>来源：示例报</div>",
    ],
)
def test_arbitrary_body_footer_or_invalid_dates_are_not_publication_dates(header):
    document = fetch(
        "<html><head><title>示例山海完成近4亿元融资</title></head><body>"
        + header
        + "<p>示例山海近日完成近4亿元融资，本轮资金用于新产品研发。</p></body></html>"
    ).documents[0]
    assert document.published_at is None


def test_editorial_financial_channel_does_not_promote_user_generated_sibling_channels():
    from backend.app.web_research_service import _source_rank

    def rank(host):
        return _source_rank(
            subject(),
            SearchResult(None, NAME, f"https://{host}/a/article.html", "", None, None),
        ).tier

    assert rank("finance.eastmoney.com") == "trusted_media_article"
    assert rank("caifuhao.eastmoney.com") == "locatable_source_page"
