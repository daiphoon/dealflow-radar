import json

import pytest

from backend.app.curated_workbook import CuratedWorkbookProvider
from tests.curated_fixtures import COMPANY_KEY, workbook_file


def load(path, mode="initial_data", company_keys=(COMPANY_KEY,)):
    return CuratedWorkbookProvider(
        path, dataset_key="fixture", mode=mode, company_keys=company_keys, allowed_root=path.parent
    ).load()


def test_identity_only_does_not_expose_business_answers(tmp_path):
    from dataclasses import asdict

    path = workbook_file(tmp_path / "fixture.xlsx")
    loaded = load(path, "identity_only")
    assert len(loaded.companies) == 1
    assert loaded.records == () and loaded.issues == ()
    data = json.dumps(asdict(loaded), ensure_ascii=False)
    assert "一亿元" not in data and "/news/" not in data and "2026-06-01" not in data
    assert loaded.selection_key != load(path).selection_key


def test_workbook_preserves_month_unknown_dates_sources_and_leads(tmp_path):
    loaded = load(workbook_file(tmp_path / "fixture.xlsx"))
    assert not loaded.issues
    assert len(loaded.records) == 3
    first, month, lead = loaded.records
    assert len(first.sources) == 2 and first.confirmed
    assert first.sources[0].metadata["证据定位提示"] == "段落一"
    assert month.public_metadata()["date_text"] == "2024-04"
    assert month.event_fields()["occurred_at"] is None
    assert month.event_fields()["published_on"] is None
    assert not lead.confirmed


@pytest.mark.parametrize(
    "field,value",
    [
        ("统一社会信用代码", "913100009999999999"),
        ("工商全称", '=HYPERLINK("https://example.invalid")'),
    ],
)
def test_bad_identity_stays_a_targeted_issue(tmp_path, field, value):
    path = workbook_file(
        tmp_path / "fixture.xlsx", change=lambda data: data["公司总表"][0].update({field: value})
    )
    result = load(path, "identity_only")
    assert not result.companies and result.issues


def test_invalid_event_does_not_drop_other_rows_or_turn_into_fact(tmp_path):
    path = workbook_file(
        tmp_path / "fixture.xlsx",
        change=lambda data: data["事件明细"][0].update({"工商全称": "示例其他主体有限公司"}),
    )
    result = load(path)
    assert len(result.companies) == 1
    assert len(result.records) == 2
    assert result.issues[0].sheet == "事件明细"


def test_private_root_and_read_only_file(tmp_path):
    import hashlib

    path = workbook_file(tmp_path / "fixture.xlsx")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    load(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with pytest.raises(ValueError, match="private import directory"):
        CuratedWorkbookProvider(
            path, dataset_key="fixture", mode="identity_only", allowed_root=tmp_path / "other"
        ).load()


@pytest.mark.parametrize("summary", ["破产申请尚无正式认定", "来源冲突，金额待核"])
def test_batch_confirmation_does_not_promote_uncertain_or_severe_claims(tmp_path, summary):
    path = workbook_file(
        tmp_path / "fixture.xlsx",
        change=lambda data: data["事件明细"][0].update({"事件摘要": summary}),
    )
    assert not load(path).records[0].confirmed


def test_duplicate_record_ids_are_isolated_instead_of_last_row_winning(tmp_path):
    def change(data):
        data["事件明细"][1]["事件ID"] = data["事件明细"][0]["事件ID"]

    result = load(workbook_file(tmp_path / "fixture.xlsx", change=change))
    assert len(result.records) == 1 and result.records[0].sheet == "待核事项"
    assert len(result.issues) == 2


def test_eight_categories_and_more_than_five_records_are_preserved(tmp_path):
    from copy import deepcopy

    from backend.app.research_plan import TOPICS

    def change(data):
        first = data["事件明细"][0]
        data["事件明细"] = [
            {**deepcopy(first), "事件ID": f"E{i}", "事件类别": category}
            for i, category in enumerate(TOPICS)
        ]
        data["待核事项"].extend(
            [
                {
                    "线索ID": "N1",
                    "公司ID": "多家",
                    "待核主题": "全表说明",
                    "目前信息": "保留来源口径",
                },
                {
                    "线索ID": "N2",
                    "公司ID": "C001/C001",
                    "待核主题": "适用公司",
                    "目前信息": "不自动生成事件",
                },
            ]
        )

    result = load(workbook_file(tmp_path / "fixture.xlsx", change=change))
    assert not result.issues
    assert len(result.records) == 9 and len(result.notes) == 2
    assert set(r.event_type for r in result.records if r.sheet == "事件明细") == set(TOPICS)
    assert result.notes[0]["kind"] == "dataset_note"
    assert result.notes[1]["company_keys"] == ["C001", "C001"]
    assert load(tmp_path / "fixture.xlsx", "identity_only").notes == ()
