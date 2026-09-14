"""按组提前回退和读取排序；使用虚构来源与 Mock HTTP，不访问真实网站。"""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from backend.app import web_research_service
from backend.app.models import Event, UsageLedger, utc_now
from backend.app.research_subject import QUERY_STRATEGY_VERSION
from backend.app.source_fetcher import TrustedSourceFetcher
from backend.app.web_research_service import SEARCH_GROUPS, _readability_fallback_group
from backend.app.web_search import MockSearchProvider
from tests.integration import test_readability_fallback as fallback

database = fallback.database
research = fallback.research


def grouped_sources(r, *, limit=8):
    r.policy = replace(r.policy, max_fetch_requests_per_job=limit)
    financing = [
        replace(fallback.row(path), url=f"https://{host}.example.com/{path}")
        for host, path in (
            ("one", "blocked/a"),
            ("one", "blocked/b"),
            ("two", "captcha"),
            ("three", "blocked/c"),
            ("four", "blocked/d"),
            ("five", "blocked/e"),
        )
    ]
    support = replace(
        fallback.row("service"),
        title="示例山海服务与支持",
        snippet="示例山海产品的日常服务说明",
        url="https://example.gov.cn/service",
        published_at="2026-09-14",
    )
    article = replace(fallback.row("article"), url="https://replacement.example.com/article")
    r.primary = MockSearchProvider(
        "baidu", {r.queries[0]: financing, r.queries[1]: [financing[-1], support]}
    )
    r.fallback = MockSearchProvider("bocha", {r.queries[0]: [article, article]})

    def factory(policy):
        def handler(request):
            r.seen.append(str(request.url))
            if request.url.path == "/robots.txt":
                return httpx.Response(
                    200,
                    text="User-agent: *\nDisallow: /blocked/\n",
                    headers={"content-type": "text/plain"},
                )
            if request.url.path == "/captcha":
                body = "请完成验证码"
            elif request.url.path == "/service":
                body = "<main><p>示例山海服务与支持，请联系公司了解产品的日常服务。</p></main>"
            else:
                assert request.url.path == "/article"
                body = (
                    '<meta property="article:published_time" content="2026-06-01">'
                    "<main><p>示例山海完成近4亿元B轮融资，本轮资金将用于产品研发。</p></main>"
                )
            return httpx.Response(
                200, text=body, headers={"content-type": "text/html; charset=utf-8"}
            )

        return TrustedSourceFetcher(
            policy,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            resolver=lambda *_: {"93.184.216.34"},
            sleep=lambda _: None,
        )

    r.fetcher_factory = factory
    return financing, support, article


@pytest.mark.parametrize("limit", [8, 12])
def test_financing_recovers_before_support_page_with_original_or_larger_budget(research, limit):
    r = research
    financing, support, article = grouped_sources(r, limit=limit)
    old_facts = {
        e.id: list(e.facts)
        for e in r.session.scalars(
            select(Event).where(Event.company_id == r.company.id, Event.status == "published")
        )
    }
    stage = f"readability_fallback:{SEARCH_GROUPS[0][0]}"
    assert r.run(stage).stage == stage
    job = r.job()
    assert job.coverage["stats"]["fetch_calls"] == len(r.seen) == 6
    assert job.coverage["follow_up_strategy_version"] == "same-query-readable-fallback-v2"
    assert job.coverage["candidate_index"] == len(financing)
    assert job.coverage["candidates"][-1]["url"] == support.url
    assert job.coverage["candidates"][-1]["source_tier"] == "government"
    assert not r.fallback.calls and not any("example.gov.cn" in url for url in r.seen)
    prefix = deepcopy(job.coverage["candidates"][:6])
    documents = deepcopy(job.coverage["documents"])

    assert r.step().stage == "fetch"
    job = r.job()
    assert job.coverage["candidate_index"] == 6
    assert job.coverage["candidates"][:6] == prefix
    assert job.coverage["documents"] == documents
    assert [c["url"] for c in job.coverage["candidates"][6:]] == [article.url, support.url]
    assert job.coverage["stats"]["fetch_calls"] == 6
    assert r.run().status == "completed"
    job = r.job()
    assert job.coverage["stats"]["events_created"] == 1
    assert job.coverage["stats"]["fetch_calls"] == len(r.seen) == (8 if limit == 8 else 10)
    assert [call.query for call in r.fallback.calls] == r.queries[:1]
    assert r.seen.count(article.url) == 1
    assert (support.url in r.seen) is (limit == 12)
    if limit == 12:
        assert r.seen.index(article.url) < r.seen.index(support.url)
    assert {
        e.id: list(e.facts) for e in r.session.scalars(select(Event).where(Event.id.in_(old_facts)))
    } == old_facts
    assert not list(
        r.session.scalars(
            select(UsageLedger).where(UsageLedger.usage_state.in_(["reserved", "in_flight"]))
        )
    )
    assert r.step().status == "idle"


def test_group_waits_for_its_shared_candidate_but_not_other_groups(research):
    r = research
    financing, _, _ = grouped_sources(r)
    assert r.run("fetch").stage == "fetch"
    shared = next(c for c in r.job().coverage["candidates"] if c["url"] == financing[-1].url)
    assert set(shared["query_kinds"]) == {code for code, _ in SEARCH_GROUPS}
    for _ in financing[:-1]:
        assert r.step().stage == "fetch"
        assert not r.fallback.calls
    job = r.job()
    assert _readability_fallback_group(job.coverage, r.policy) is None
    assert r.step().stage == "fetch"
    job = r.job()
    assert _readability_fallback_group(job.coverage, r.policy) == SEARCH_GROUPS[0][0]
    old = deepcopy(job.coverage)
    old["query_strategy_version"] = QUERY_STRATEGY_VERSION
    assert _readability_fallback_group(old, r.policy) is None


def test_last_candidate_slot_prefers_recovered_article_over_static_page(research):
    r = research
    _, support, article = grouped_sources(r)
    r.policy = replace(r.policy, max_candidate_urls=8)
    another_static = replace(support, url="https://example.gov.cn/support.html")
    r.fallback = MockSearchProvider("bocha", {r.queries[0]: [another_static, article]})
    stage = f"readability_fallback:{SEARCH_GROUPS[0][0]}"
    assert r.run(stage).stage == stage
    assert r.step().stage == "fetch"
    job = r.job()
    urls = [c["url"] for c in job.coverage["candidates"]]
    assert len(urls) == 8 and article.url in urls and another_static.url not in urls
    assert r.run().status == "completed"
    assert r.job().coverage["stats"]["events_created"] == 1
    assert len(r.seen) == 8


def test_fallback_prioritizes_existing_pending_url_without_duplicate_fetch(research):
    r = research
    financing, support, article = grouped_sources(r)
    other = replace(article, url="https://other.example.com/article")
    r.primary = MockSearchProvider(
        "baidu", {r.queries[0]: financing, r.queries[1]: [other, article, support]}
    )
    assert r.run("fetch").stage == "fetch"
    job = r.job()
    coverage = deepcopy(job.coverage)
    candidates = {c["url"]: c for c in coverage["candidates"]}
    coverage["candidates"] = [candidates[row.url] for row in [*financing, other, article, support]]
    job.coverage = coverage
    r.session.commit()
    stage = f"readability_fallback:{SEARCH_GROUPS[0][0]}"
    assert r.run(stage).stage == stage
    prefix = deepcopy(r.job().coverage["candidates"][:6])
    assert r.step().stage == "fetch"
    job = r.job()
    assert job.coverage["candidates"][:6] == prefix
    selected = job.coverage["candidates"][6]
    assert selected["url"] == article.url
    assert set(selected["discovered_by"]) == {"baidu", "bocha"}
    assert set(selected["query_kinds"]) == {code for code, _ in SEARCH_GROUPS}
    state = job.coverage["search_groups"][SEARCH_GROUPS[0][0]]["providers"]["bocha"]
    assert state["candidate_count_added"] == 0 and state["candidate_count_reprioritized"] == 1
    assert r.run().status == "completed"
    assert r.seen.count(article.url) == 1 and other.url not in r.seen
    assert len(r.seen) == 8 and len(r.fallback.calls) == 1


def test_interrupted_early_fallback_keeps_prefix_and_reuses_settled_search(research, monkeypatch):
    r = research
    _, _, article = grouped_sources(r)
    stage = f"readability_fallback:{SEARCH_GROUPS[0][0]}"
    assert r.run(stage).stage == stage
    prefix = deepcopy(r.job().coverage["candidates"][:6])
    original = web_research_service._provider_response

    def interrupt(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("simulated interruption after early fallback settlement")

    monkeypatch.setattr(web_research_service, "_provider_response", interrupt)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        r.step()
    monkeypatch.setattr(web_research_service, "_provider_response", original)
    r.session.rollback()
    job = r.job()
    job.leased_until = utc_now() - timedelta(seconds=1)
    r.session.commit()
    assert r.run().status == "completed"
    job = r.job()
    assert job.coverage["candidates"][:6] == prefix
    assert job.coverage["stats"]["search_calls"] == 3
    assert job.coverage["stats"]["fetch_calls"] == 8
    assert len(r.fallback.calls) == 1 and r.seen.count(article.url) == 1


@pytest.mark.parametrize("outcome", ["empty", "static_only"])
def test_empty_or_static_fallback_keeps_pending_business_article(research, outcome):
    r = research
    pending = fallback.row("product-article")
    r.primary = MockSearchProvider(
        "baidu", {r.queries[0]: [fallback.row("blocked/source")], r.queries[1]: [pending]}
    )
    # Keep the unread financing candidate first, as in a saved task waiting for a fetch step.
    assert r.run("fetch").stage == "fetch"
    job = r.job()
    coverage = deepcopy(job.coverage)
    coverage["candidates"].sort(key=lambda c: "blocked" not in c["url"])
    job.coverage = coverage
    r.session.commit()
    static = replace(fallback.row("support"), title="示例山海服务与支持")
    r.fallback = MockSearchProvider(
        "bocha", {r.queries[0]: [static] if outcome == "static_only" else []}
    )
    stage = f"readability_fallback:{SEARCH_GROUPS[0][0]}"
    assert r.run(stage).stage == stage
    assert "/product-article" not in r.seen
    assert r.step().stage == "fetch"
    job = r.job()
    assert job.coverage["candidates"][job.coverage["candidate_index"]]["url"] == pending.url
    assert r.run().status == "completed"
    assert r.seen.count("/product-article") == 1
    assert [call.query for call in r.fallback.calls] == r.queries[:1]
