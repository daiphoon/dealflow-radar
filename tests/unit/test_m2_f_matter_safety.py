"""M2-F 通用回归：真实规则、验证和比较入口，全部是虚构主体。"""

from types import SimpleNamespace

import pytest

from backend.app.matter_comparison import compare_matters
from backend.app.research_matters import Matter, extract_matters

SUBJECT = SimpleNamespace(legal_name="示例远澜科技有限公司", aliases=(), legal_aliases=())


@pytest.mark.parametrize(
    "action,subtype,status",
    [
        ("与示例航空签署租赁协议", "contract_award", "reported"),
        ("与示例客户签订框架协议", "contract_award", "reported"),
        ("计划与示例客户签署合作协议", "contract_award", "planned"),
        ("正式完成首次商业运输", "contract_award", "reported"),
        ("新产品获得装机批准", "product_milestone", "reported"),
        ("新产品取得供应商准入", "product_milestone", "reported"),
        ("新产品通过适航技术审核", "product_milestone", "reported"),
        ("推出新型公开测试产品", "product_milestone", "reported"),
        ("计划发布新产品", "product_milestone", "planned"),
        ("否认新产品已获批", "product_milestone", "denied"),
    ],
)
def test_grounded_non_financing_actions_keep_actual_stage(action, subtype, status):
    body = SUBJECT.legal_name + action + "，未披露金额。"
    rows = extract_matters(SUBJECT, body)
    assert len(rows) == 1
    assert rows[0].subtype == subtype and rows[0].status == status
    assert rows[0].action in body
    assert "financing" not in rows[0].fields and "revenue" not in rows[0].fields


def test_same_wording_different_subjects_never_merges():
    first = Matter(
        "contract_commercial", "contract_award", "示例甲", "legal_entity", "签署合同", "reported"
    )
    other = Matter(
        "contract_commercial", "contract_award", "示例乙", "legal_entity", "签署合同", "reported"
    )
    result = compare_matters(first, other)
    assert not result.same_matter and result.decision == "new_matter"


def test_verified_legal_alias_is_same_subject_only_with_bound_identity_context():
    first = Matter(
        "contract_commercial",
        "contract_award",
        SUBJECT.legal_name,
        "legal_entity",
        "签署合同",
        "reported",
    )
    alias = Matter(
        "contract_commercial", "contract_award", "远澜", "legal_entity", "签署合同", "reported"
    )
    identity = SimpleNamespace(legal_name=SUBJECT.legal_name, aliases=("远澜",))
    assert compare_matters(first, alias, subject=identity).same_matter
    other = Matter(
        "contract_commercial", "contract_award", "另一主体", "legal_entity", "签署合同", "reported"
    )
    assert not compare_matters(first, other, subject=identity).same_matter


def test_transaction_plan_and_reported_stage_link_without_overwriting():
    fields = {
        "transaction_id": {"value": "FICTIVE-901", "quote": "FICTIVE-901", "role": "transaction_id"}
    }
    first = Matter(
        "financing_cap_table",
        "company_financing",
        "示例甲",
        "legal_entity",
        "拟融资",
        "planned",
        fields,
    )
    later = Matter(
        "financing_cap_table",
        "company_financing",
        "示例甲",
        "legal_entity",
        "完成融资",
        "reported",
        fields,
    )
    result = compare_matters(first, later)
    assert result.decision == "related_stage" and not result.same_matter


def test_identical_generic_action_does_not_merge_different_rounds():
    def row(round_value):
        return Matter(
            "financing_cap_table",
            "company_financing",
            "示例甲",
            "legal_entity",
            "完成融资",
            "reported",
            {"round": {"value": round_value, "quote": round_value, "role": "round"}},
        )

    result = compare_matters(row("A轮"), row("B轮"))
    assert result.decision == "new_matter" and not result.same_matter


def test_unknown_event_date_and_registration_do_not_become_cash_completion():
    body = SUBJECT.legal_name + "完成A轮融资。披露日期为2026年9月1日，交割日期未公开。"
    row = extract_matters(SUBJECT, body)[0]
    assert "occurred" not in row.fields
    registration = extract_matters(
        SUBJECT, SUBJECT.legal_name + "注册资本由100万元增至200万元，工商变更完成。"
    )
    assert len(registration) == 1 and registration[0].subtype == "registered_capital"


@pytest.mark.parametrize("round_name", ["A2轮", "B1轮", "Pre-A2轮"])
def test_numbered_round_remains_grounded_and_does_not_collapse_to_letter_round(round_name):
    row = extract_matters(SUBJECT, SUBJECT.legal_name + "完成" + round_name + "融资。")[0]
    assert row.fields["round"]["value"] == round_name
    assert row.fields["round"]["quote"] in row.action
    other = extract_matters(SUBJECT, SUBJECT.legal_name + "完成A轮融资。")[0]
    assert not compare_matters(row, other).same_matter
