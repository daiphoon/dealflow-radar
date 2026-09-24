"""虚构正文的真实写入、证据账本和受限读取契约。"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import RefreshPolicy
from backend.app.database import set_request_context
from backend.app.demo import BETA_TENANT_ID, BETA_USER_ID
from backend.app.fact_support import materialize_event_fact_ledger
from backend.app.models import EventEvidence, User
from backend.app.research_matters import digest
from backend.app.services import get_company_detail
from tests.integration import test_curated_import as curated
from tests.integration import test_research_matter_storage as matter_storage
from tests.integration.test_incremental_research import initial

database = matter_storage.database
ingest = matter_storage.ingest


def test_legacy_ledger_read_without_rematerialization(database, tmp_path):
    from backend.app.models import EventFactSupport

    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        event, _, _, _ = ingest(
            session,
            user,
            company,
            "示例山海完成1亿元C轮融资。",
            "https://example.com/legacy-ledger",
            datetime(2026, 9, 2, tzinfo=UTC),
        )
        ids = event.id, company.id
        session.commit()
    # 模拟 0034 数据：旧哈希、旧字段判断、没有新签名；升级读取不得要求写回。
    with Session(database.owner) as session:
        evidence = session.scalar(select(EventEvidence).where(EventEvidence.event_id == ids[0]))
        evidence.span_hash = digest(evidence.evidence_excerpt)
        payload = dict(evidence.display_detail_payload)
        payload.pop("excerpt_hash_version")
        evidence.display_detail_payload = payload
        for support in session.scalars(
            select(EventFactSupport).where(EventFactSupport.event_id == ids[0])
        ):
            support.deterministic_checks = {"citation_complete": False}
            support.support_status = "unsupported"
        session.commit()
    with Session(database.app) as session:
        set_request_context(session, BETA_USER_ID, BETA_TENANT_ID)
        detail = get_company_detail(
            session,
            session.get(User, BETA_USER_ID),
            ids[1],
            RefreshPolicy(),
            auto_refresh_enabled=False,
        )
        out = next(e for e in detail.platform_unconfirmed_leads if e.id == ids[0])
        assert {"name": "融资金额", "value": "1亿元", "unit": None} in out.facts
        assert not session.new and not session.dirty and not session.deleted
        assert all(
            support.deterministic_checks == {"citation_complete": False}
            for support in session.scalars(
                select(EventFactSupport).where(EventFactSupport.event_id == ids[0])
            )
        )


def test_denial_cannot_support_the_denied_financing_amount(database, tmp_path):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        event, _, _, _ = ingest(
            session,
            user,
            company,
            "示例山海完成1亿元C轮融资，交易编号T001。",
            "https://example.com/claim",
            datetime(2026, 9, 2, tzinfo=UTC),
        )
        denial, _, _, _ = ingest(
            session,
            user,
            company,
            "示例山海否认完成1亿元C轮融资，交易编号T001。",
            "https://example.com/denial",
            datetime(2026, 9, 3, tzinfo=UTC),
        )
        assert denial.id == event.id
        session.commit()
        user = curated.enter(session)
        out = next(
            e
            for e in get_company_detail(
                session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
            ).platform_unconfirmed_leads
            if e.id == event.id
        )
        assert not any(f["name"] == "融资金额" for f in out.facts)
        assert (
            next(f for f in out.fact_ledger if f.name == "融资金额").support_status == "conflicting"
        )
        assert out.evidence and out.matter_observations
        assert "冲突" in out.summary and "证据已撤回" not in out.summary


@pytest.mark.parametrize(
    "body, subtype",
    [
        ("示例溪流被示例山海收购。", "outbound_investment"),
        ("示例山海被示例溪流收购。", "acquisition_target"),
    ],
)
def test_passive_acquisition_role_through_storage_and_read(database, tmp_path, body, subtype):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        event, _, _, _ = ingest(
            session,
            user,
            company,
            body,
            "https://example.com/acquisition-role",
            datetime(2026, 9, 2, tzinfo=UTC),
        )
        assert event.event_subtype == subtype
        session.commit()
        user = curated.enter(session)
        out = next(
            e
            for e in get_company_detail(
                session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
            ).platform_unconfirmed_leads
            if e.id == event.id
        )
        assert out.event_subtype == subtype
        assert out.information_status == "source_supported_unconfirmed"


@pytest.mark.parametrize("legacy", [False, True])
def test_typed_financing_write_ledger_read_and_tampering(database, tmp_path, legacy):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path)
        event, _, _, _ = ingest(
            session,
            user,
            company,
            "示例山海于2026年9月1日完成1亿元C轮融资，估值10亿元。",
            "https://example.com/reliability",
            datetime(2026, 9, 2, tzinfo=UTC),
        )
        evidence = session.scalar(select(EventEvidence).where(EventEvidence.event_id == event.id))
        if legacy:
            evidence.span_hash = digest(evidence.evidence_excerpt)
            payload = dict(evidence.display_detail_payload)
            payload.pop("excerpt_hash_version", None)
            evidence.display_detail_payload = payload
        materialize_event_fact_ledger(session, event)
        if legacy:
            from scripts.audit_matter_evidence import audit

            inspection = audit(session)
            assert inspection["counts"].get("legacy_matter_json_string") == 1
            assert inspection["writes"] == 0
            assert evidence.span_hash == digest(evidence.evidence_excerpt)
        session.commit()
        user = curated.enter(session)
        detail = get_company_detail(
            session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
        )
        out = next(e for e in detail.platform_unconfirmed_leads if e.id == event.id)
        amount = next(f for f in out.fact_ledger if f.name == "融资金额")
        assert amount.value == "1亿元"
        assert amount.support_status == "supported"
        assert next(f for f in out.fact_ledger if f.name == "估值").value == "10亿元"
        evidence.evidence_excerpt += "篡改"
        materialize_event_fact_ledger(session, event)
        session.commit()
        user = curated.enter(session)
        detail = get_company_detail(
            session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
        )
        out = next(e for e in detail.platform_unconfirmed_leads if e.id == event.id)
        assert all(f.support_status != "supported" for f in out.fact_ledger)
        assert "1亿元" not in str(out.facts)


def test_partial_withdrawal_removes_only_unsupported_fields(database, tmp_path):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path)
        first, _, _, _ = ingest(
            session,
            user,
            company,
            "示例山海完成1亿元C轮融资。",
            "https://example.com/original",
            datetime(2026, 9, 2, tzinfo=UTC),
        )
        second, _, _, _ = ingest(
            session,
            user,
            company,
            "示例山海完成C轮融资。",
            "https://example.com/supplement",
            datetime(2026, 9, 3, tzinfo=UTC),
        )
        original = session.scalar(select(EventEvidence).where(EventEvidence.event_id == first.id))
        supplement = session.scalar(
            select(EventEvidence).where(EventEvidence.event_id == second.id)
        )
        values = {
            c.name: getattr(supplement, c.name)
            for c in EventEvidence.__table__.columns
            if c.name not in {"id", "event_id"}
        }
        session.add(EventEvidence(event_id=first.id, **values))
        materialize_event_fact_ledger(session, first)
        session.commit()
        ids = first.id, company.id, original.id
    with Session(database.owner) as owner:
        owner.get(EventEvidence, ids[2]).display_allowed = False
        owner.commit()
    with Session(database.app) as session:
        set_request_context(session, BETA_USER_ID, BETA_TENANT_ID)
        viewer = session.get(User, BETA_USER_ID)
        out = next(
            e
            for e in get_company_detail(
                session, viewer, ids[1], RefreshPolicy(), auto_refresh_enabled=False
            ).platform_unconfirmed_leads
            if e.id == ids[0]
        )
        assert "1亿元" not in out.summary
        assert not any(f["name"] == "融资金额" for f in out.facts)
        assert not any(f.name == "融资金额" for f in out.fact_ledger)
        assert any(f["name"] == "融资轮次" and f["value"] == "C轮" for f in out.facts)


def test_shared_hash_chain_and_unknown_version_fail_closed(database, tmp_path):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path)
        event, _, doc, _ = ingest(
            session,
            user,
            company,
            "示例山海宣布：“完成1亿元C轮融资”。\n未披露新增估值。",
            "https://example.com/quoted",
            datetime(2026, 9, 2, tzinfo=UTC),
        )
        origin = session.scalar(select(EventEvidence).where(EventEvidence.event_id == event.id))
        values = {
            c.name: getattr(origin, c.name)
            for c in EventEvidence.__table__.columns
            if c.name not in {"id", "raw_document_id", "source_event_evidence_id"}
        }
        shared = EventEvidence(raw_document_id=None, source_event_evidence_id=origin.id, **values)
        session.add(shared)
        materialize_event_fact_ledger(session, event)
        from backend.app.models import EventFactSupport

        support = session.scalar(
            select(EventFactSupport).where(
                EventFactSupport.event_evidence_id == shared.id,
                EventFactSupport.support_status == "supported",
            )
        )
        assert support is not None
        origin.display_detail_payload = {
            **origin.display_detail_payload,
            "excerpt_hash_version": "unknown-v99",
        }
        materialize_event_fact_ledger(session, event)
        assert all(
            s.support_status == "unsupported"
            for s in session.scalars(
                select(EventFactSupport).where(EventFactSupport.event_evidence_id == shared.id)
            )
        )
        origin.display_detail_payload = {
            **origin.display_detail_payload,
            "excerpt_hash_version": "sha256-utf8-exact-v1",
        }
        shared.evidence_excerpt = "越界摘录" + shared.evidence_excerpt
        materialize_event_fact_ledger(session, event)
        assert all(
            s.support_status == "unsupported"
            for s in session.scalars(
                select(EventFactSupport).where(EventFactSupport.event_evidence_id == shared.id)
            )
        )


def test_same_document_additional_fields_versions_and_retraction(database, tmp_path):
    from sqlalchemy import func

    from backend.app.models import EventFact, EventObservation
    from backend.app.research_matter_storage import persist_matters
    from backend.app.research_matters import extract_matters
    from backend.app.research_subject import load_subject
    from backend.app.web_research_service import _source
    from tests.integration.test_research_matter_storage import POLICY

    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        from unittest.mock import patch

        body = "示例山海完成1亿元C轮融资，由示例机构领投。"
        older = extract_matters(load_subject(session, company), body)
        older[0].fields.pop("investors")
        with patch("backend.app.research_extraction.extract_matters", return_value=older):
            event, _, doc, _ = ingest(
                session,
                user,
                company,
                body,
                "https://example.com/versioned",
                datetime(2026, 9, 2, tzinfo=UTC),
            )
        subject = load_subject(session, company)
        candidate = extract_matters(subject, doc.payload["excerpt"])[0]
        original = list(event.facts)
        candidate.fields["valuation"] = {
            "value": "10亿元",
            "quote": candidate.action,
            "role": "valuation",
        }
        # 无依据字段在入库前局部拒绝，原提议留在观测处置审计。
        result = persist_matters(
            session,
            company,
            doc,
            _source(session),
            user,
            [candidate],
            POLICY,
            processing_version="fixture-new-version",
        )
        assert result[0][0].id == event.id
        assert (
            session.scalar(
                select(func.count())
                .select_from(EventObservation)
                .where(EventObservation.event_id == event.id)
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(EventEvidence)
                .where(EventEvidence.event_id == event.id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(EventFact).where(EventFact.event_id == event.id, EventFact.name == "估值")
            )
            is None
        )
        latest = session.scalar(
            select(EventObservation).where(
                EventObservation.event_id == event.id,
                EventObservation.processing_version == "fixture-new-version",
            )
        )
        assert any(
            d.get("field") == "valuation" and d.get("proposed_value") == "10亿元"
            for d in latest.candidate_payload["dispositions"]
        )
        persist_matters(
            session,
            company,
            doc,
            _source(session),
            user,
            [candidate],
            POLICY,
            processing_version="fixture-new-version",
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(EventObservation)
                .where(EventObservation.event_id == event.id)
            )
            == 2
        )
        assert event.facts == original
        session.commit()
        user = curated.enter(session)
        out = next(
            e
            for e in get_company_detail(
                session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
            ).platform_unconfirmed_leads
            if e.id == event.id
        )
        assert not any(f["name"] == "估值" for f in out.facts)
        assert any(
            f["name"] == "投资方（来源口径）" and f["value"] == "示例机构" for f in out.facts
        )
        assert not any(f["name"] == "投资方（来源口径）" for f in original)
        evidence = session.scalar(select(EventEvidence).where(EventEvidence.event_id == event.id))
        evidence.display_allowed = False
        session.flush()
        assert (
            persist_matters(
                session,
                company,
                doc,
                _source(session),
                user,
                [candidate],
                POLICY,
                processing_version="fixture-v3",
            )[0]
            == []
        )
        assert not evidence.display_allowed


def test_postgres_same_document_concurrent_observation(database, tmp_path):
    if not database.postgres:
        pytest.skip("real PostgreSQL concurrency case")
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import func

    from backend.app.models import Company, EventObservation, RawDocument
    from backend.app.research_matter_storage import persist_matters
    from backend.app.research_matters import extract_matters
    from backend.app.research_subject import load_subject
    from backend.app.web_research_service import _source
    from tests.integration.test_research_matter_storage import POLICY

    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        event, _, doc, _ = ingest(
            session,
            user,
            company,
            "示例山海完成1亿元C轮融资。",
            "https://example.com/concurrent",
            datetime(2026, 9, 2, tzinfo=UTC),
        )
        ids = company.id, doc.id, event.id
        session.commit()
    barrier = Barrier(2)

    def run(_):
        with Session(database.app) as session:
            user = curated.enter(session)
            company = session.get(Company, ids[0])
            doc = session.get(RawDocument, ids[1])
            candidate = extract_matters(load_subject(session, company), doc.payload["excerpt"])
            barrier.wait(timeout=10)
            rows, _, _ = persist_matters(
                session,
                company,
                doc,
                _source(session),
                user,
                candidate,
                POLICY,
                processing_version="fixture-replay-v2",
            )
            eid = rows[0].id
            session.commit()
            return eid

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run, range(2))) == [ids[2], ids[2]]
    with Session(database.owner) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(EventObservation)
                .where(EventObservation.event_id == ids[2])
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(EventEvidence)
                .where(EventEvidence.event_id == ids[2])
            )
            == 1
        )


def test_offline_walkthrough_conflict_retraction_and_current_view(database, tmp_path):
    """业务演示：正文→事项→补字段→冲突→撤证，原始事件不改写。"""
    import json
    import os
    from pathlib import Path

    from backend.app.investor_analysis import _research_analysis_request
    from backend.app.models import CompanySnapshot, EventObservation, utc_now
    from scripts.audit_matter_evidence import audit

    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        snapshots = list(
            session.scalars(select(CompanySnapshot).where(CompanySnapshot.company_id == company.id))
        )
        snapshot_before = [(r.id, r.last_checked_at) for r in snapshots]
        today = utc_now()
        first, _, doc, _ = ingest(
            session,
            user,
            company,
            "示例山海完成1亿元C轮融资，交易编号TX001。",
            "https://example.com/original-demo",
            today,
        )
        original = list(first.facts)
        second, created, _, _ = ingest(
            session,
            user,
            company,
            "示例山海完成1亿元C轮融资，交易编号TX001，由示例机构领投。",
            "https://example.com/support-demo",
            today,
        )
        assert second.id == first.id and not created
        session.flush()

        def shown():
            return next(
                e
                for e in get_company_detail(
                    session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
                ).platform_unconfirmed_leads
                if e.id == first.id
            )

        supported = shown()
        assert any(f["name"] == "投资方（来源口径）" for f in supported.facts)
        conflict, created, _, _ = ingest(
            session,
            user,
            company,
            "示例山海完成2亿元C轮融资，交易编号TX001，由示例机构领投。",
            "https://example.com/conflict-demo",
            today,
        )
        assert conflict.id == first.id and not created
        conflicting = shown()
        assert not any(f["name"] == "融资金额" for f in conflicting.facts)
        assert {f.support_status for f in conflicting.fact_ledger if f.name == "融资金额"} == {
            "conflicting"
        }
        conflict_evidence = session.scalar(
            select(EventEvidence).where(
                EventEvidence.event_id == first.id,
                EventEvidence.display_canonical_url == "https://example.com/conflict-demo",
            )
        )
        conflict_evidence.display_allowed = False
        session.flush()
        restored = shown()
        assert not any(f.name == "融资金额" and f.value == "2亿元" for f in restored.fact_ledger)
        assert any(f["name"] == "融资金额" and f["value"] == "1亿元" for f in restored.facts)
        # 再撤掉金额的全部支持；没有残留在摘要或模型输入中。
        for evidence in session.scalars(
            select(EventEvidence).where(EventEvidence.event_id == first.id)
        ):
            evidence.display_allowed = False
        session.flush()
        withdrawn = shown()
        assert withdrawn.facts == [] and "1亿元" not in withdrawn.summary
        from backend.app.personal_features import _report_markdown

        report = _report_markdown(company, None, [withdrawn], today, RefreshPolicy())
        assert "1亿元" not in report and "2亿元" not in report
        assert _research_analysis_request(session, first) is None
        assert first.facts == original
        assert [(r.id, r.last_checked_at) for r in snapshots] == snapshot_before
        audit_result = audit(session)
        assert audit_result["writes"] == 0 and audit_result["counts"].get("exact") == 3
        rows = list(
            session.scalars(select(EventObservation).where(EventObservation.event_id == first.id))
        )
        assert len(rows) == 3
        assert any(
            r.candidate_payload["merge_decision"]["decision"] == "field_conflict" for r in rows
        )
        out = {
            "fixture_only": True,
            "database": "postgresql" if database.postgres else "sqlite",
            "external_calls": 0,
            "steps": [
                {
                    "stage": name,
                    "facts": value.facts,
                    "summary": value.summary,
                    "statuses": [
                        {"name": f.name, "value": f.value, "status": f.support_status}
                        for f in value.fact_ledger
                    ],
                }
                for name, value in [
                    ("source_supported", supported),
                    ("conflicting", conflicting),
                    ("conflict_evidence_withdrawn", restored),
                    ("all_evidence_withdrawn", withdrawn),
                ]
            ],
            "original_event_unchanged": first.facts == original,
            "snapshot_unchanged": True,
            "observations": len(rows),
            "audit": audit_result,
        }
        destination = os.getenv("RELIABILITY_DEMO_OUTPUT")
        if destination:
            base = Path(destination)
            base.mkdir(parents=True, exist_ok=True)
            (base / f"walkthrough-{out['database']}.json").write_text(
                json.dumps(out, ensure_ascii=False, indent=2)
            )


@pytest.mark.parametrize(
    "body,day,expected",
    [
        ("示例山海完成1亿元D轮融资。", None, "date_unknown"),
        (
            "示例山海于2020年1月1日完成1亿元D轮融资。",
            datetime(2020, 1, 2, tzinfo=UTC),
            "historical",
        ),
        (
            "示例山海于2020年1月1日完成1亿元D轮融资。",
            datetime(2026, 9, 22, tzinfo=UTC),
            "historical",
        ),
    ],
)
def test_historical_and_undated_admission_does_not_claim_recent(
    database, tmp_path, body, day, expected
):
    from sqlalchemy import func

    from backend.app.models import CompanySnapshot, ReviewQueue

    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        review_count = session.scalar(select(func.count()).select_from(ReviewQueue))
        event, created, _, _ = ingest(
            session, user, company, body, "https://example.com/history", day
        )
        assert created and event.publication_route == "unconfirmed_lead"
        out = next(
            e
            for e in get_company_detail(
                session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
            ).platform_unconfirmed_leads
            if e.id == event.id
        )
        assert (
            out.temporal_status == expected
            and out.information_status == "source_supported_unconfirmed"
        )
        assert all(m.temporal_status == expected for m in out.matter_observations)
        assert session.scalar(select(func.count()).select_from(ReviewQueue)) == review_count
        assert all(
            r.last_checked_at is None
            for r in session.scalars(
                select(CompanySnapshot).where(CompanySnapshot.company_id == company.id)
            )
        )
        if expected == "date_unknown":
            assert out.occurred_at is None


def test_url_new_body_is_new_document_and_old_record_retained(database, tmp_path):
    from sqlalchemy import func

    from backend.app.models import RawDocument

    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        a, _, first, _ = ingest(
            session, user, company, "示例山海完成1亿元A轮融资。", "https://example.com/revised"
        )
        b, _, second, _ = ingest(
            session, user, company, "示例山海完成2亿元B轮融资。", "https://example.com/revised"
        )
        assert first.id != second.id and a.id != b.id
        assert first.payload["excerpt"] == "示例山海完成1亿元A轮融资。"
        assert (
            session.scalar(
                select(func.count())
                .select_from(RawDocument)
                .where(RawDocument.canonical_url == "https://example.com/revised")
            )
            == 2
        )


def test_rule_missed_model_candidate_reaches_supported_ledger(database, tmp_path):
    from backend.app.research_matter_storage import persist_matters
    from backend.app.research_matters import validate_proposals
    from backend.app.research_subject import load_subject
    from backend.app.web_research_service import _source
    from tests.integration.test_research_matter_storage import POLICY

    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        body = "示例山海本轮筹得1亿元融资资金。"
        missing, _, doc, _ = ingest(session, user, company, body, "https://example.com/model-gap")
        assert missing is None
        proposed, rejected = validate_proposals(
            load_subject(session, company),
            body,
            {
                "matters": [
                    {
                        "subject": "示例山海",
                        "action_quote": body,
                        "subtype": "company_financing",
                        "subject_role": "fundraiser",
                        "status": "reported",
                        "fields": {
                            "financing": {"value": "1亿元", "quote": body, "role": "financing"}
                        },
                    }
                ]
            },
        )
        assert proposed and not rejected
        events, created, _ = persist_matters(
            session, company, doc, _source(session), user, proposed, POLICY
        )
        assert created == 1
        out = next(
            e
            for e in get_company_detail(
                session, user, company.id, RefreshPolicy(), auto_refresh_enabled=False
            ).platform_unconfirmed_leads
            if e.id == events[0].id
        )
        assert any(
            f.name == "融资金额" and f.value == "1亿元" and f.support_status == "supported"
            for f in out.fact_ledger
        )
        assert out.publication_route == "unconfirmed_lead" and out.status != "published"
