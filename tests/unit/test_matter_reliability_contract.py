from types import SimpleNamespace

import pytest

from backend.app.matter_validation import equivalent_value
from backend.app.research_matters import Matter, compatible, extract_matters, validate_proposals

SUBJECT = SimpleNamespace(legal_name="示例甲科技有限公司", aliases=("示例甲",), legal_aliases=())


def proposal(body, **extra):
    return {
        "matters": [
            {
                "subject": "示例甲",
                "action_quote": body,
                "subtype": "company_financing",
                "subject_role": "fundraiser",
                "status": "reported",
                **extra,
            }
        ]
    }


def test_model_completes_rule_missed_field_without_calling_extractor(monkeypatch):
    body = "示例甲本轮筹得1亿元融资资金。"
    raw = proposal(
        body, fields={"financing": {"value": "1亿元", "quote": body, "role": "financing"}}
    )
    monkeypatch.setattr(
        "backend.app.research_matters.extract_matters",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("validator must not invoke candidate extractor")
        ),
    )
    got, rejected = validate_proposals(SUBJECT, body, raw)
    assert not rejected and got[0].fields["financing"]["value"] == "1亿元"


def test_independent_validation_rejects_valuation_as_financing_only():
    body = "示例甲完成1亿元A轮融资，估值10亿元。"
    got, _ = validate_proposals(
        SUBJECT,
        body,
        proposal(
            body,
            fields={
                "financing": {"value": "10亿元", "quote": body, "role": "financing"},
                "round": {"value": "A轮", "quote": body, "role": "round"},
            },
        ),
    )
    assert got and got[0].fields["round"]["value"] == "A轮"
    assert got[0].fields.get("financing", {}).get("value") != "10亿元"
    assert "rejected_field:financing" in got[0].issues


@pytest.mark.parametrize(
    "role,value,expected",
    [
        ("registered_capital_before", "1亿元", True),
        ("registered_capital_after", "2亿元", True),
        ("registered_capital_before", "2亿元", False),
        ("registered_capital_after", "1亿元", False),
    ],
)
def test_registered_capital_role_matches_transition_position(role, value, expected):
    from backend.app.matter_validation import validate_field

    body = "示例甲注册资本由1亿元变更为2亿元。"
    assert validate_field(role, value, body, body, "registered_capital")[0] is expected


def test_other_company_financing_not_attributed_to_investor():
    body = "示例乙完成1亿元B轮融资，示例甲领投。"
    assert not extract_matters(SUBJECT, body)
    got, rejected = validate_proposals(SUBJECT, body, proposal(body))
    assert not got and rejected


@pytest.mark.parametrize(
    "body",
    [
        "示例乙被示例甲收购。",
        "示例甲于2026年9月1日收购示例乙，示例乙被示例甲收购。",
    ],
)
def test_passive_acquisition_buyer_cannot_become_target(body):
    got = extract_matters(SUBJECT, body)
    assert not any(m.subtype == "acquisition_target" for m in got)
    assert any(m.subtype == "outbound_investment" for m in got)
    candidates, rejected = validate_proposals(
        SUBJECT,
        body,
        proposal(body, subtype="acquisition_target", subject_role="target"),
        legacy_compat=False,
    )
    assert not candidates and rejected


def test_passive_acquisition_target_remains_supported():
    body = "示例甲被示例乙收购。"
    assert [m.subtype for m in extract_matters(SUBJECT, body)] == ["acquisition_target"]
    candidates, rejected = validate_proposals(
        SUBJECT,
        body,
        proposal(body, subtype="acquisition_target", subject_role="target"),
        legacy_compat=False,
    )
    assert candidates and not rejected


def test_same_paragraph_financing_and_application_both_retained():
    got = extract_matters(SUBJECT, "示例甲完成1亿元A轮融资，同时向港交所提交上市申请。")
    assert {m.subtype for m in got} == {"company_financing", "ipo_application"}


def financing(amount, investor, day="2026-06-01", transaction=None):
    fields = {
        "round": {"value": "A轮"},
        "financing": {"value": amount},
        "investors": {"value": investor},
        "date": {"iso": day},
    }
    if transaction:
        fields["transaction_id"] = {"value": transaction}
    return Matter(
        "financing_cap_table",
        "company_financing",
        "示例甲",
        "brand:示例甲",
        f"示例甲完成{amount}A轮融资，由{investor}领投",
        "reported",
        fields,
    )


def test_same_round_does_not_merge_independent_financings():
    assert not compatible(
        financing("1亿元", "青杉资本"), financing("2亿元", "白杨资本", "2026-08-01")
    )


def test_same_transaction_amount_conflict_is_a_comparison_decision():
    from backend.app.matter_comparison import compare_matters

    result = compare_matters(
        financing("1亿元", "青杉资本", transaction="交易甲"),
        financing("2亿元", "青杉资本", transaction="交易甲"),
    )
    assert result.decision == "field_conflict" and result.same_matter
    assert "financing" in result.field_differences


@pytest.mark.parametrize(
    "a,b,equal",
    [
        ("1亿元", "10000万元", True),
        ("1.0亿元", "10000万元", True),
        ("1.00亿元", "1亿元", True),
        ("近1亿元", "1亿元", False),
        ("不超过1亿元", "1亿元", False),
        ("1亿美元", "1亿元", False),
    ],
)
def test_amount_comparison_keeps_currency_and_precision(a, b, equal):
    assert equivalent_value(a, b) == equal


def test_user_isolated_reproduction_against_actual_repository_functions():
    import hashlib

    from backend.app.evidence_integrity import hash_excerpt_bytes
    from backend.app.research_matters import digest

    text = "示例山海完成1亿元A轮融资"
    # 旧 digest 保留对象身份用途；证据写入改用与读取一致的逐字字节算法。
    assert digest(text) != hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert hash_excerpt_bytes(text) == hashlib.sha256(text.encode("utf-8")).hexdigest()
    a = Matter(
        "financing_cap_table",
        "company_financing",
        "示例山海",
        "legal_entity",
        "A",
        "reported",
        {
            "round": {"value": "A轮"},
            "financing": {"value": "1亿元"},
            "investors": {"value": "甲投资"},
        },
    )
    b = Matter(
        "financing_cap_table",
        "company_financing",
        "示例山海",
        "legal_entity",
        "B",
        "reported",
        {
            "round": {"value": "A轮"},
            "financing": {"value": "2亿元"},
            "investors": {"value": "乙投资"},
        },
    )
    assert not compatible(a, b)


def test_long_document_window_retains_tail_subject_and_offsets():
    import json

    from backend.app.config import WebResearchPolicy
    from backend.app.research_extraction import extraction_payload

    body = "普通背景。" * 1700 + "\n示例甲本轮筹得1亿元融资资金，但否认已挂牌上市。"
    payload = extraction_payload(SUBJECT, body, WebResearchPolicy())
    data = json.loads(payload["messages"][1]["content"])
    assert data["input_truncated"]
    assert any("筹得1亿元" in w["text"] and "否认" in w["text"] for w in data["source_windows"])
    for w in data["source_windows"]:
        assert body[w["start"] : w["end"]] == w["text"]


def test_buyer_target_and_scoped_denial():
    buyer = extract_matters(SUBJECT, "示例甲收购示例乙公司股权。")
    target = extract_matters(SUBJECT, "示例甲被示例乙公司收购。")
    assert buyer[0].subtype == "outbound_investment"
    assert target[0].subtype == "acquisition_target"
    values = extract_matters(SUBJECT, "示例甲完成1亿元A轮融资，但否认计划赴港上市。")
    assert next(m for m in values if m.subtype == "company_financing").status == "reported"
    assert next(m for m in values if m.subtype == "ipo_listing_plan").status == "denied"


def test_source_channel_not_inherited_from_host_grade():
    from backend.app.matter_retention import source_channel

    document = SimpleNamespace(
        canonical_url="https://example.gov.cn/community/post",
        payload={"excerpt": "示例甲完成A轮融资，本文为AI生成内容", "content_extraction": {}},
    )
    assert source_channel(document, "A") == ("generated_commentary", "E")
    document.payload = {"excerpt": "用户自行发布材料", "content_extraction": {}}
    assert source_channel(document, "A") == ("user_post", "D")


def test_report_date_not_matter_identity_and_ipo_project_stages():
    from backend.app.matter_comparison import compare_matters

    first = Matter(
        "exit_liquidity",
        "ipo_application",
        "示例甲",
        "legal_entity",
        "示例甲递交上市申请，项目编号HK001",
        "reported",
        {"project_id": {"value": "HK001"}},
    )
    next_stage = Matter(
        "exit_liquidity",
        "ipo_hearing",
        "示例甲",
        "legal_entity",
        "示例甲通过聆讯，项目编号HK001",
        "reported",
        {"project_id": {"value": "HK001"}},
    )
    new_project = Matter(
        "exit_liquidity",
        "ipo_application",
        "示例甲",
        "legal_entity",
        "示例甲再次递交上市申请，项目编号HK002",
        "reported",
        {"project_id": {"value": "HK002"}},
    )
    assert compare_matters(first, next_stage).decision == "progress_update"
    assert not compare_matters(first, new_project).same_matter
    a = financing("1亿元", "青杉资本")
    b = financing("1亿元", "青杉资本")
    a.fields["date"] = {"value": "2026年1月1日", "iso": "2026-01-01", "role": "disclosed"}
    b.fields["date"] = {"value": "2026年8月1日", "iso": "2026-08-01", "role": "disclosed"}
    assert compare_matters(a, b).same_matter


@pytest.mark.parametrize(
    "value", ["中文“引文”", "首行\n次行", "首行\r\n次行", " 保留尾空格 ", "é", "e\u0301"]
)
def test_exact_utf8_hash_contract(value):
    import hashlib

    from backend.app.evidence_integrity import hash_excerpt_bytes

    assert hash_excerpt_bytes(value) == hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_typed_wrong_actor_does_not_escape_with_positive_quote():
    body = "示例甲领投，示例乙完成1亿元B轮融资。"
    assert not validate_proposals(SUBJECT, body, proposal(body))[0]


def test_publication_date_cannot_be_model_occurrence_date():
    body = "示例甲于2026年9月22日披露已完成1亿元A轮融资。"
    got, rejected = validate_proposals(
        SUBJECT,
        body,
        proposal(
            body, fields={"date": {"value": "2026年9月22日", "quote": body, "role": "occurred"}}
        ),
    )
    assert got and "date" not in got[0].fields
    assert any(r.get("field") == "date" for r in rejected)


def test_model_cannot_quote_away_same_matter_denial():
    body = "示例甲已上市，但该公司否认上市消息。"
    got, _ = validate_proposals(
        SUBJECT,
        body,
        {
            "matters": [
                {
                    "subject": "示例甲",
                    "action_quote": "示例甲已上市",
                    "subtype": "ipo_listed",
                    "subject_role": "issuer",
                    "status": "reported",
                }
            ]
        },
    )
    assert got[0].status == "denied" and "否认" in got[0].action


def test_outbound_investment_is_not_company_receiving_funding():
    body = "示例甲战略投资示例乙公司500万元。"
    assert not validate_proposals(SUBJECT, body, proposal(body))[0]


def test_new_model_contract_cannot_fall_back_to_rule_classification():
    body = "示例甲完成1亿元A轮融资。"
    values, rejected = validate_proposals(
        SUBJECT,
        body,
        {"matters": [{"subject": "示例甲", "action_quote": body}]},
        legacy_compat=False,
    )
    assert not values and rejected[0]["reason"] == "invalid_matter_schema"
