"""新主题查询的替代来源仍在同一任务预算内，重试复用已经冻结的查询。"""

from dataclasses import replace

from backend.app.research_plan import TOPICS
from backend.app.research_subject import short_business_query
from backend.app.web_search import MockSearchProvider
from tests.integration import test_readability_fallback as fallback

database = fallback.database
research = fallback.research


def test_topic_recovery_uses_discovered_round_once_and_reads_replacement(research):
    r = research
    r.policy = replace(r.policy, topic_planning_enabled=True)
    r.queries = [short_business_query(r.subject, topic) for topic in list(TOPICS.values())[:2]]
    r.primary = MockSearchProvider(
        "baidu",
        {
            r.queries[0]: [fallback.row("blocked/source")],
            r.queries[1]: [],
        },
    )
    refined = short_business_query(r.subject, "B轮 融资 公告")
    r.fallback = MockSearchProvider("bocha", {refined: [fallback.row("article")]})
    assert r.run().status == "completed"
    job = r.job()
    group = job.coverage["search_groups"]["business_capital"]
    assert group["fallback_query_text"] == refined
    assert sum(c.query == refined for c in r.fallback.calls) == 1
    assert "/article" in r.seen and "/blocked/source" not in r.seen
    assert job.coverage["stats"]["search_calls"] <= 4
    assert job.coverage["stats"]["fetch_calls"] == len(r.seen) <= 8
    assert job.coverage["follow_up_strategy_version"] == "topic-readable-recovery-v1"
    assert any(
        d.get("quality_gate", {}).get("status") == "eligible" for d in job.coverage["documents"]
    )
    calls = len(r.primary.calls) + len(r.fallback.calls)
    assert r.step().status == "idle"
    assert len(r.primary.calls) + len(r.fallback.calls) == calls


def test_full_candidate_queue_keeps_replacement_and_defers_unread_lower_priority(research):
    r = research
    r.policy = replace(r.policy, topic_planning_enabled=True)
    queries = [short_business_query(r.subject, topic) for topic in list(TOPICS.values())[:2]]
    unrelated = [
        replace(
            fallback.row(f"other/{i}"), title="示例山海发布新产品", snippet="示例山海发布新产品"
        )
        for i in range(6)
    ]
    r.primary = MockSearchProvider(
        "baidu",
        {
            queries[0]: [fallback.row(f"blocked/{i}") for i in range(6)],
            queries[1]: unrelated,
        },
    )
    refined = short_business_query(r.subject, "B轮 融资 公告")
    r.fallback = MockSearchProvider("bocha", {refined: [fallback.row("article")]})
    for _ in range(24):
        r.step()
        job = r.job()
        if job.coverage.get("deferred_candidates"):
            break
    assert len(job.coverage["candidates"]) == 12
    assert any(c["url"].endswith("/article") for c in job.coverage["candidates"])
    assert job.coverage["deferred_candidates"][0]["deferred_reason"] == "topic_recovery_queue_limit"
    assert r.run().status == "completed"
    assert "/article" in r.seen
    assert r.job().coverage["stats"]["fetch_calls"] <= 8
