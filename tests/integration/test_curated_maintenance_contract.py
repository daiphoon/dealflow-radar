"""正式 preview/apply 的离线维护验收；不靠手工写表制造升级成功。"""

from copy import deepcopy
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import EventObservation, RawDocument
from tests.support.curated_import import apply, curator, detail_for_curator, enter, loaded
from tests.support.curated_import import database as database


@pytest.mark.parametrize(
    "case",
    [
        "new_evidence",
        "date_precision",
        "strong_candidate",
        "correction",
        "new_stage",
        "different_matter",
        "lead_sheet_boundary",
    ],
)
def test_official_maintenance_preserves_identity_lineage_and_old_documents(
    database, tmp_path, case
):
    curator(database)

    def initial(data):
        if case == "strong_candidate":
            data["事件明细"][0].update(
                {"内容支持状态": "待核", "事件摘要": "示例品牌融资，证据仍不足。"}
            )

    with Session(database.app, expire_on_commit=False) as session:
        first = apply(session, loaded(tmp_path, change=initial))
        company_id = UUID(first["company_id"])
        detail = detail_for_curator(session, company_id)
        old_event_ids = {e.id for e in detail.events}
        old_private_leads = {e.id for e in detail.unconfirmed_leads}
        enter(session)
        documents = {
            d.id: deepcopy((d.content_hash, d.payload))
            for d in session.scalars(select(RawDocument))
        }
        observations = {
            o.id: deepcopy((o.observation_key, o.fact_version, o.candidate_payload))
            for o in session.scalars(select(EventObservation))
        }

        def revision(data):
            record = data["事件明细"][0]
            if case == "new_evidence":
                record.update(
                    {
                        "公开链接2": "https://example.invalid/news/supplement",
                        "信息来源2": "示例新增证据",
                    }
                )
            elif case == "date_precision":
                data["事件明细"][1].update(
                    {"事件/披露日期": "2024-04-12", "日期精度": "日", "实际发生日期": "2024-04-12"}
                )
            elif case == "correction":
                record.update(
                    {
                        "事件摘要": "经更正：示例品牌融资金额为八千万元。",
                        "核验说明": "负责人已复核更正，不删除原记录",
                    }
                )
            elif case == "new_stage":
                data["事件明细"].append(
                    {
                        **record,
                        "事件ID": "E003",
                        "事件类别": "IPO进度",
                        "重要事件": "示例品牌完成上市辅导备案",
                        "事件摘要": "虚构新增阶段，与融资为不同阶段。",
                        "公开链接1": "https://example.invalid/new-stage",
                    }
                )
            elif case == "different_matter":
                data["事件明细"].append(
                    {
                        **record,
                        "事件ID": "E004",
                        "事件类别": "产品与技术",
                        "重要事件": "示例新品获得认证",
                        "事件摘要": "虚构新品获得认证，是另一事项。",
                        "交易/进程组": "G004",
                        "公开链接1": "https://example.invalid/other-matter",
                    }
                )
            elif case == "lead_sheet_boundary":
                data["待核事项"][0].update(
                    {
                        "处理口径": "负责人已核验",
                        "公开链接/定位": "https://example.invalid/strong-proof",
                        "目前信息": "虚构新增证据，仍走待核表入口。",
                    }
                )

        revised_input = loaded(tmp_path, change=revision)
        result = apply(session, revised_input)
        detail = detail_for_curator(session, company_id)
        ids = {e.id for e in detail.events}
        if case in {"new_evidence", "date_precision", "correction"}:
            assert ids == old_event_ids
            assert any(len(e.curated_versions) == 2 for e in detail.events)
        elif case == "strong_candidate":
            assert len(ids) == len(old_event_ids) + 1
            assert len(detail.unconfirmed_leads) == len(old_private_leads) - 1
            assert result["shared_events_created"] == 1
        elif case in {"new_stage", "different_matter"}:
            assert ids > old_event_ids and len(ids - old_event_ids) == 1
        else:
            # 当前正式待核表没有确认字段；修改文字不能偷偷晋升，明确保留入口缺口。
            assert ids == old_event_ids
            assert {e.id for e in detail.unconfirmed_leads} == old_private_leads
            assert result["shared_events_created"] == 0
        assert all(sum(v.is_current for v in e.curated_versions) == 1 for e in detail.events)
        enter(session)
        for document_id, before in documents.items():
            document = session.get(RawDocument, document_id)
            assert (document.content_hash, document.payload) == before
        for observation_id, before in observations.items():
            observation = session.get(EventObservation, observation_id)
            assert (
                observation.observation_key,
                observation.fact_version,
                observation.candidate_payload,
            ) == before
        # 相同输入指同一 Excel 字节；重新写 ZIP 会因时间戳产生另一文件哈希。
        assert apply(session, revised_input)["status"] == "duplicate"
