from backend.app import research_plan as plan
from backend.app.config import WebResearchPolicy
from backend.app.research_coverage import COVERAGE_VERSION, category_coverage
from backend.app.web_research_service import _readability_fallback_group


def coverage(categories=None):
    assignments = categories or plan.plan_topics([])
    return {
        "query_strategy_version": plan.VERSION,
        "category_coverage_version": COVERAGE_VERSION,
        "search_groups": {
            g: {
                "topic_category": c,
                "topic": plan.TOPICS[c],
                "status": "pending",
                "providers": {
                    "baidu": {"status": "completed", "qualified_subject_results": 3},
                    "bocha": {"status": "not_called"},
                },
            }
            for g, c in assignments.items()
        },
        "source_routes": plan.coverage_routes(assignments, "baidu", "bocha"),
        "candidates": [],
        "documents": [],
        "stats": {},
    }


def test_all_topics_get_a_turn_without_reading_initial_facts():
    histories, checked = [], []
    for _ in range(4):
        assignments = plan.plan_topics(histories)
        checked.extend(assignments.values())
        item = coverage(assignments)
        for state in item["search_groups"].values():
            state["attempted_at"] = "2026-09-20T00:00:00+00:00"
        histories.append(item)
    assert len(set(checked)) == len(plan.TOPICS) == 8
    assert list(plan.plan_topics(histories).values()) == checked[:2]


def test_target_articles_precede_home_directory_and_cross_topic_review():
    item = coverage()
    candidates = [
        {"url": "https://example.invalid/", "coverage_category": None},
        {"url": "https://example.invalid/PRODUCTS", "coverage_category": "product_technology"},
        {"url": "https://example.invalid/ipo", "coverage_category": "exit_liquidity"},
        {"url": "https://example.invalid/ipo2", "coverage_category": "exit_liquidity"},
        {"url": "https://example.invalid/finance", "coverage_category": "financing_cap_table"},
    ]
    ordered = plan.order_candidates(candidates, item)
    assert [c["url"].rsplit("/", 1)[-1] for c in ordered[:2]] == ["finance", "ipo"]
    assert {c["url"] for c in candidates} == {c["url"] for c in ordered}
    assert plan.document_limit(item, WebResearchPolicy()) == 12
    assert plan.document_limit({}, WebResearchPolicy()) == 3


def test_internal_documents_do_not_satisfy_a_topic_or_block_fallback():
    item = coverage()
    item["documents"] = [
        {
            "url": str(i),
            "status": "created",
            "coverage_category": "financing_cap_table",
            "quality_gate": {"status": "internal_only"},
        }
        for i in range(3)
    ]
    item["candidates"] = [
        {"url": str(i), "coverage_category": "financing_cap_table"} for i in range(3)
    ]
    assert _readability_fallback_group(item, WebResearchPolicy()) == "business_capital"
    rows = {c.category: c for c in category_coverage(item)}
    assert rows["financing_cap_table"].evidence_count == 0
    assert rows["legal_compliance"].status == "not_checked"


def test_fallback_waits_for_existing_target_candidates_and_hard_http_limit():
    item = coverage()
    item["candidates"] = [
        {"url": "https://example.invalid/f", "coverage_category": "financing_cap_table"},
        {"url": "https://example.invalid/i", "coverage_category": "exit_liquidity"},
    ]
    assert _readability_fallback_group(item, WebResearchPolicy()) is None
    item["documents"] = [{"url": c["url"], "status": "failed"} for c in item["candidates"]]
    item["stats"] = {"fetch_calls": 8}
    assert _readability_fallback_group(item, WebResearchPolicy()) is None


def test_freshness_requires_complete_readable_checks_and_original_timestamps():
    from copy import deepcopy
    from datetime import datetime

    item = coverage()
    category = "financing_cap_table"
    group = item["search_groups"]["business_capital"]
    group.update(status="completed", checked_at="2026-09-15T00:00:00+00:00", subject_results=1)
    item["candidates"] = [{"url": "https://example.invalid/f", "coverage_category": category}]
    document = {
        "url": "https://example.invalid/f",
        "coverage_category": category,
        "status": "reused",
        "checked_at": "2026-09-10T00:00:00+00:00",
        "quality_gate": {"status": "eligible"},
    }
    item["documents"] = [document]
    assert plan.successful_topic_checks([item])[category] == datetime.fromisoformat(
        "2026-09-10T00:00:00+00:00"
    )
    for patch in (
        {"checked_at": None},
        {"status": "failed"},
        {"quality_gate": {"status": "internal_only"}},
    ):
        changed = deepcopy(item)
        changed["documents"][0].update(patch)
        assert category not in plan.successful_topic_checks([changed])
    item["candidates"].append(
        {"url": "https://example.invalid/unread", "coverage_category": category}
    )
    assert category not in plan.successful_topic_checks([item])
    item["candidates"], item["documents"] = [], []
    group["subject_results"] = 0
    assert category in plan.successful_topic_checks([item])


def test_recovery_terms_only_use_discovered_content_and_avoid_ambiguous_rounds():
    assert plan.recovery_terms("financing_cap_table", []) == "融资 股权 公告"
    found = [{"coverage_category": "financing_cap_table", "title": "示例公司完成B轮融资"}]
    assert plan.recovery_terms("financing_cap_table", found) == "B轮 融资 公告"
    found[0]["snippet"] = "此前还完成A轮融资"
    assert plan.recovery_terms("financing_cap_table", found) == "融资 股权 公告"


def test_all_eight_categories_need_same_passage_subject_and_change():
    from backend.app.models import Company, utc_now
    from backend.app.web_research_service import _content_quality_decision

    examples = {
        "financing_cap_table": "完成融资",
        "exit_liquidity": "启动IPO辅导备案",
        "legal_compliance": "收到行政处罚",
        "financial_operation": "营收增长",
        "contract_commercial": "签订合同",
        "product_technology": "发布新产品",
        "governance_people": "董事长离职",
        "capacity_assets": "新工厂投产",
    }
    company = Company(legal_name="示例主题测试有限公司", identity_status="verified")
    for category, change in examples.items():
        body = f"{company.legal_name}{change}。"
        decision = _content_quality_decision(
            company,
            title=body,
            excerpt=body,
            published_at=utc_now(),
            observed_at=utc_now(),
            policy=WebResearchPolicy(),
        )
        assert decision.eligible and decision.event_type == category, (category, decision)
        unrelated = _content_quality_decision(
            company,
            title=body,
            excerpt=f"{company.legal_name}成立多年。其他企业{change}。",
            published_at=utc_now(),
            observed_at=utc_now(),
            policy=WebResearchPolicy(),
        )
        assert not unrelated.eligible
