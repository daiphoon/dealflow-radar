"""短查询、历史任务和真实覆盖口径；全部搜索与网页均使用 Mock。"""

from sqlalchemy import select

from backend.app.models import CompanyAlias
from backend.app.research_coverage import SEARCH_GROUPS, category_coverage
from backend.app.research_subject import QUERY_STRATEGY_VERSION, SHORT_QUERY_STRATEGY_VERSION
from backend.app.web_research_service import (
    _query_for,
    _store_search_response,
    prepare_pending_research_requests,
)
from backend.app.web_search import MockSearchProvider, SearchRequest
from tests.integration import test_readability_fallback as fallback

database = fallback.database
research = fallback.research


def test_new_job_freezes_both_queries_before_resume_and_fallback(research):
    r = research
    r.step()
    job = r.job()
    assert job.coverage["query_strategy_version"] == SHORT_QUERY_STRATEGY_VERSION
    assert [g["query_text"] for g in job.coverage["search_groups"].values()] == r.queries
    alias = r.session.scalar(select(CompanyAlias).where(CompanyAlias.company_id == r.company.id))
    alias.alias = "新的名称"
    alias.normalized_alias = "新的名称"
    r.session.commit()
    assert r.run().status == "completed"
    assert [call.query for call in r.primary.calls] == r.queries
    # 别名改变后第二组会立即回退，第一组则在正文失败后回退；查询内容仍被冻结。
    assert sorted(call.query for call in r.fallback.calls) == sorted(r.queries)
    assert not r.job().coverage["stats"]["events_created"]


def test_short_queries_do_not_claim_other_categories_were_searched(research):
    r = research
    r.primary = MockSearchProvider("baidu", {q: [] for q in r.queries})
    r.fallback = MockSearchProvider("bocha")
    assert r.run().status == "completed"
    rows = {row.category: row for row in category_coverage(r.job().coverage)}
    assert rows["financing_cap_table"].status == "no_records"
    assert rows["product_technology"].status == "no_records"
    assert all(
        row.status == "not_configured"
        for category, row in rows.items()
        if category not in {"financing_cap_table", "product_technology"}
    )


def test_existing_v1_job_keeps_old_queries_and_strategy(research):
    r = research
    user = fallback.incremental.curated.enter(r.session)
    prepare_pending_research_requests(r.session, user, r.policy)
    job = r.job()
    coverage = dict(job.coverage)
    coverage["query_strategy_version"] = QUERY_STRATEGY_VERSION
    job.coverage = coverage
    r.session.commit()
    fallback.incremental.curated.enter(r.session)
    old_queries = [_query_for(r.subject, terms) for _, terms in SEARCH_GROUPS]
    r.primary = MockSearchProvider("baidu", {q: [fallback.row("article")] for q in old_queries})
    assert r.run().status == "completed"
    assert [call.query for call in r.primary.calls] == old_queries
    assert not r.fallback.calls
    assert r.job().coverage["query_strategy_version"] == QUERY_STRATEGY_VERSION


def test_new_query_does_not_reuse_old_wide_query_cache(research):
    r = research
    old_query = _query_for(r.subject, SEARCH_GROUPS[0][1])
    old_provider = MockSearchProvider("baidu", {old_query: [fallback.row("old-answer")]})
    _store_search_response(
        r.session,
        r.subject,
        old_provider.search(SearchRequest(old_query)),
        SEARCH_GROUPS[0][0],
        old_query,
        r.policy,
    )
    r.session.commit()
    assert r.run().status == "completed"
    assert [call.query for call in r.primary.calls] == r.queries
    assert "/old-answer" not in r.seen
