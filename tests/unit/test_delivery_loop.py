"""直接调用生产入口的交付闭环回归；仅使用虚构材料。"""

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from backend.app.config import SourceMonitoringPolicy
from backend.app.matter_validation import action_supported, actor_supported
from backend.app.research_matters import extract_matters, validate_proposals
from backend.app.source_fetcher import SourceFetchError, TrustedSourceFetcher, _pdf_published_at

SUBJECT = SimpleNamespace(legal_name="示例甲科技有限公司", aliases=("示例甲",), legal_aliases=())


def test_three_dates_survive_field_order():
    body = "2026年9月1日报道：示例甲于2024年6月完成A轮融资，计划于2027年上市。"
    fields = {
        "happened": {"value": "2024年6月", "quote": body, "role": "occurred"},
        "reported": {"value": "2026年9月1日", "quote": body, "role": "disclosed"},
        "expected": {"value": "2027年", "quote": body, "role": "planned"},
    }
    results = []
    for ordered in (fields, dict(reversed(list(fields.items())))):
        got, rejected = validate_proposals(
            SUBJECT,
            body,
            {
                "matters": [
                    {
                        "subject": "示例甲",
                        "action_quote": body,
                        "subtype": "company_financing",
                        "subject_role": "fundraiser",
                        "status": "reported",
                        "fields": ordered,
                    }
                ]
            },
            legacy_compat=False,
        )
        assert not rejected
        results.append(got[0])
    assert results[0].fields == results[1].fields
    assert results[0].fields["occurred"]["precision"] == "month"
    assert results[0].fields["disclosed"]["precision"] == "day"
    assert results[0].fields["planned"]["precision"] == "year"
    assert results[0].status == "reported"


@pytest.mark.parametrize(
    "body",
    [
        "示例甲于2024年6月1日完成A轮融资。",
        "示例甲完成A轮融资，完成时间为2024年6月1日。",
    ],
)
def test_occurrence_before_or_after_action(body):
    got = extract_matters(SUBJECT, body)
    assert got[0].fields["occurred"]["iso"] == "2024-06-01"


def test_ipo_prospectus_is_not_offering():
    assert not action_supported("示例甲向港交所递交招股书。", "ipo_offering")


@pytest.mark.parametrize(
    "subtype,body",
    [
        ("company_financing", "示例甲参与会议，示例乙完成A轮融资。"),
        ("ipo_hearing", "示例甲出席论坛，示例乙通过港交所聆讯。"),
    ],
)
def test_paragraph_cooccurrence_does_not_prove_actor(subtype, body):
    assert not actor_supported("示例甲", body, subtype)


def test_history_and_latest_stage_are_distinct():
    got = extract_matters(SUBJECT, "示例甲于2024年完成辅导备案，2026年9月1日通过上市聆讯。")
    assert {m.subtype for m in got} == {"ipo_guidance", "ipo_hearing"}


def test_second_application_is_preserved():
    got = extract_matters(SUBJECT, "示例甲于2026年9月1日二次递表港交所。")
    assert [m.subtype for m in got] == ["ipo_application"]
    assert got[0].fields["application_cycle"]["value"] == "二次"


def test_pdf_business_date_is_not_publication():
    assert _pdf_published_at("示例甲于2024年6月1日完成A轮融资。")[0] is None


@pytest.mark.parametrize("failure", [httpx.RemoteProtocolError, httpx.ReadTimeout])
def test_stream_error_retains_partial_bytes_and_type(failure):
    class Broken(httpx.SyncByteStream):
        def __iter__(self):
            yield b"part"
            raise failure("interrupted")

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                headers={"content-type": "text/html"},
                stream=Broken(),
                request=r,
            )
        )
    ) as client:
        fetcher = TrustedSourceFetcher(
            SourceMonitoringPolicy(),
            client=client,
            allow_private_test_hosts=True,
        )
        with pytest.raises(SourceFetchError):
            fetcher._request_once("https://example.invalid/a", {}, allow_plain_text=False)
        assert fetcher.downloaded_bytes == 4
        assert fetcher.request_count == 1
        assert fetcher.request_log[-1]["bytes"] == 4
        assert fetcher.request_log[-1]["error_type"] == failure.__name__


def test_current_temporal_view_can_age_without_mutating_observation():
    from backend.app.matter_retention import temporal_status

    matter = extract_matters(SUBJECT, "示例甲于2026年6月1日完成A轮融资。")[0]
    doc = SimpleNamespace(
        observed_at=datetime(2026, 6, 2, tzinfo=UTC), published_on=datetime(2026, 6, 1).date()
    )
    assert temporal_status(matter, doc, 30) == "recent"
    assert temporal_status(matter, doc, 30, as_of=datetime(2026, 9, 1, tzinfo=UTC)) == "historical"


def test_investor_order_is_not_event_identity():
    from backend.app.matter_comparison import compare_matters

    a = extract_matters(SUBJECT, "示例甲完成1亿元A轮融资，由甲基金、乙基金联合领投。")[0]
    b = extract_matters(SUBJECT, "示例甲获得1亿元A轮融资，由乙基金、甲基金联合领投。")[0]
    assert compare_matters(a, b).same_matter


def test_ipo_reprints_merge_but_stages_stay_separate():
    from backend.app.matter_comparison import compare_matters

    a = extract_matters(SUBJECT, "示例甲于2026年9月1日向港交所提交上市申请。")[0]
    b = extract_matters(SUBJECT, "示例甲于2026-09-01向港交所递交招股书。")[0]
    c = extract_matters(SUBJECT, "示例甲于2026年9月20日通过港交所聆讯。")[0]
    assert compare_matters(a, b).same_matter
    assert compare_matters(a, b).decision == "source_support"
    relation = compare_matters(a, c)
    assert not relation.same_matter and relation.decision == "related_stage"


def test_multicategory_document_covers_each_actual_category():
    from backend.app.research_plan import eligible_document

    doc = {
        "status": "created",
        "coverage_category": None,
        "matter_categories": ["financing_cap_table", "exit_liquidity"],
        "quality_gate": {"status": "eligible"},
    }
    assert eligible_document(doc, "financing_cap_table")
    assert eligible_document(doc, "exit_liquidity")


@pytest.mark.parametrize("actual_category", [None, "exit_liquidity"])
def test_read_document_completes_search_topic_without_claiming_topic_evidence(actual_category):
    from copy import deepcopy

    from backend.app import research_plan as plan
    from tests.unit.test_research_plan import coverage

    item = coverage()
    category = "financing_cap_table"
    checked_at = "2026-09-15T00:00:00+00:00"
    item["search_groups"]["business_capital"].update(
        status="completed", checked_at=checked_at, subject_results=1
    )
    candidate = {"url": "https://example.invalid/article", "coverage_category": category}
    item["candidates"] = [candidate]
    item["documents"] = [
        {
            "url": candidate["url"],
            "status": "created",
            "coverage_category": actual_category,
            "matter_categories": [actual_category] if actual_category else [],
            "searched_topics": [category],
            "disposition": "matters_retained" if actual_category else "no_supported_matter",
            "quality_gate": {"status": "eligible" if actual_category else "internal_only"},
            "checked_at": checked_at,
        }
    ]
    assert plan.successful_topic_checks([item])[category] == datetime.fromisoformat(checked_at)
    assert not plan.eligible_document(item["documents"][0], category)
    for change in ("failed", "unread", "deferred"):
        incomplete = deepcopy(item)
        if change == "failed":
            incomplete["documents"][0]["status"] = "failed"
        elif change == "unread":
            incomplete["documents"] = []
        else:
            incomplete["deferred_candidates"] = [candidate]
        assert category not in plan.successful_topic_checks([incomplete])


def test_ambiguous_curated_date_range_remains_unknown():
    from backend.app.matter_dates import normalize_date

    result = normalize_date("2024年6月1日—2024年7月1日")
    assert result["precision"] == "unknown"
    assert "interval_start" not in result


def test_maintenance_search_allows_old_source_without_changing_discovery():
    from backend.app.config import WebResearchPolicy
    from backend.app.web_research_service import _qualification_reason
    from backend.app.web_search import SearchResult

    subject = SimpleNamespace(
        legal_name="示例甲科技有限公司", credit_code=None, official_website=None
    )
    result = SearchResult(
        "id",
        "示例甲科技有限公司融资",
        "https://example.com/news/one",
        "示例甲科技有限公司完成融资",
        None,
        "2020-01-01",
    )
    policy = WebResearchPolicy()
    assert _qualification_reason(subject, result, policy) == "published_at_old"
    assert _qualification_reason(subject, result, policy, intent="maintenance") == "qualified"


def test_current_financing_does_not_inherit_historical_round_date():
    body = "示例甲宣布完成C轮融资，这是示例甲继2022年完成A轮融资以来的又一进展。"
    current = extract_matters(SUBJECT, body)[0]
    assert not current.fields.get("occurred")


@pytest.mark.parametrize(
    "stage,nearby",
    [
        ("ipo_guidance_completed", "示例甲完成上市辅导备案"),
        ("ipo_application", "示例甲签署上市辅导协议"),
        ("ipo_accepted", "示例甲向港交所递交招股书"),
        ("ipo_hearing", "示例甲收到上市申请受理通知"),
        ("ipo_offering", "示例甲通过上市聆讯"),
        ("ipo_listed", "示例甲启动公开招股"),
    ],
)
def test_nearby_ipo_stage_does_not_support_next_stage(stage, nearby):
    assert not action_supported(nearby, stage)


@pytest.mark.parametrize(
    "finish,content,reason",
    [
        ("length", '{"matters": [', "output_truncated"),
        ("content_filter", "", "non_stop_finish"),
        ("stop", "invalid", "invalid_json"),
    ],
)
def test_model_failures_remain_distinct(finish, content, reason):
    from backend.app.config import WebResearchPolicy
    from backend.app.research_extraction import DeepSeekMatterProvider

    payload = {
        "choices": [{"finish_reason": finish, "message": {"content": content}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7},
    }
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        provider = DeepSeekMatterProvider("mock-fixture", WebResearchPolicy(), client=client)
        result = provider.extract({})
    assert result["output"] is None and result["failure_reason"] == reason
    assert result["finish_reason"] == finish and result["output_tokens"] == 7


def test_duplicate_role_is_ambiguous_independent_of_field_order():
    body = "示例甲于2024年6月完成A轮融资，又于2024年7月完成B轮融资。"
    fields = {
        str(i): {"value": v, "role": "occurred", "quote": body}
        for i, v in enumerate(("2024年6月", "2024年7月"))
    }
    for ordered in (fields, dict(reversed(list(fields.items())))):
        got, rejected = validate_proposals(
            SUBJECT,
            body,
            {
                "matters": [
                    {
                        "subject": "示例甲",
                        "subtype": "company_financing",
                        "subject_role": "fundraiser",
                        "action_quote": body,
                        "status": "reported",
                        "fields": ordered,
                    }
                ]
            },
            legacy_compat=False,
        )
        assert not got[0].fields.get("occurred") and not got[0].fields.get("date")
        assert any(r["reason"] == "ambiguous_field" for r in rejected)


def test_unknown_subtype_has_its_own_rejection():
    body = "示例甲完成A轮融资。"
    got, rejected = validate_proposals(
        SUBJECT,
        body,
        {
            "matters": [
                {
                    "subject": "示例甲",
                    "subtype": "invented_stage",
                    "subject_role": "actor",
                    "action_quote": body,
                    "status": "reported",
                    "fields": {},
                }
            ]
        },
        legacy_compat=False,
    )
    assert not got and rejected == [{"index": 0, "reason": "unknown_subtype"}]


def test_intent_cache_and_verified_name_priority():
    from dataclasses import replace

    from backend.app.research_subject import ResearchSubject, short_business_query
    from backend.app.web_research_service import _search_cache_key

    subject = ResearchSubject(
        SimpleNamespace(legal_name="示例甲科技有限公司"),
        ("甲牌", "示例甲"),
        (),
        (("甲牌", "brand"), ("示例甲", "short_name")),
        reference_at="2026-09-24T00:00:00+00:00",
        event_window_days=90,
    )
    assert short_business_query(subject, "融资") == '"示例甲" 融资'
    assert short_business_query(subject, "融资", alternate=True) == '"甲牌" 融资'
    query = short_business_query(subject, "融资")
    assert _search_cache_key(subject, query) != _search_cache_key(
        replace(subject, research_intent="maintenance"), query
    )


def test_scheduler_defers_reprints_and_bounds_ambiguous_identity_slots():
    from backend.app.research_plan import order_candidates

    coverage = {
        "search_groups": {
            "capital": {"topic_category": "financing_cap_table"},
            "ipo": {"topic_category": "exit_liquidity"},
        }
    }

    def candidate(i, category, title, **extra):
        return {
            "url": f"https://example.com/article/{i}",
            "title": title,
            "coverage_category": category,
            **extra,
        }

    candidates = [
        candidate(1, "financing_cap_table", "示例甲完成新一轮融资公开报道"),
        candidate(2, "financing_cap_table", "示例甲完成新一轮融资公开报道"),
        candidate(3, "exit_liquidity", "首次上市申请", identity_pending=True),
        candidate(4, "exit_liquidity", "另一次申请", identity_pending=True),
    ]
    ordered = order_candidates(candidates, coverage)
    assert [c["url"].rsplit("/", 1)[1] for c in ordered[:2]] == ["1", "3"]
    assert {c["scheduling_reason"] for c in ordered[2:]} == {
        "duplicate_title_deferred",
        "identity_verification_slot_exhausted",
    }
