from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.demo import ALPHA_USER_ID, BETA_USER_ID
from backend.app.models import (
    CompanySnapshot,
    EntityMention,
    Event,
    EventEvidence,
    RawDocument,
    ResearchImport,
    ReviewQueue,
    Source,
    UsageLedger,
    User,
)
from backend.app.providers import ManualResearchImportProvider
from backend.app.services import (
    AccessDeniedError,
    ImportConflictError,
    decide_review,
    import_manual_research,
)


def _provider(
    tmp_path: Path,
    payload: dict[str, object],
    filename: str = "batch.json",
) -> ManualResearchImportProvider:
    (tmp_path / filename).write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return ManualResearchImportProvider(filename, allowed_root=tmp_path)


def test_manual_import_is_idempotent_and_keeps_unresolved_out_of_events(
    tmp_path: Path,
    manual_import_payload: dict[str, object],
    migrated_app: FastAPI,
) -> None:
    records = manual_import_payload["records"]
    assert isinstance(records, list)
    unresolved = copy.deepcopy(records[0])
    unresolved["external_record_id"] = "manual-record-002"
    unresolved["canonical_url"] = "https://example.invalid/manual-002"
    unresolved["title"] = "未匹配公司的公开线索"
    unresolved["company_identity_evidence"]["legal_name"] = "不存在的示例公司"
    records.append(unresolved)
    provider = _provider(tmp_path, manual_import_payload)

    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        first = import_manual_research(session, user, provider)
        second = import_manual_research(session, user, provider)

        assert first.status == "completed_with_unresolved"
        assert first.records_seen == 2
        assert first.documents_created == 2
        assert first.events_created == 1
        assert first.reviews_created == 2
        assert first.resolved_records == 1
        assert first.unresolved_records == 1
        assert first.external_calls == 0
        assert first.estimated_cost == Decimal("0")
        assert second.status == "duplicate"
        assert second.documents_created == 0
        assert second.events_created == 0

        batch = session.scalar(select(ResearchImport))
        event = session.scalar(select(Event))
        unresolved_mention = session.scalar(
            select(EntityMention).where(EntityMention.resolution_status == "unresolved")
        )
        documents = list(session.scalars(select(RawDocument).order_by(RawDocument.title)))
        ledger = session.scalar(
            select(UsageLedger).where(UsageLedger.operation == "manual_research_import")
        )
        reviews = list(session.scalars(select(ReviewQueue)))
        assert batch is not None
        assert batch.status == "completed_with_unresolved"
        assert batch.record_count == 2
        assert batch.resolved_count == 1
        assert batch.unresolved_count == 1
        assert event is not None and event.status == "in_review"
        assert event.fingerprint_version == "manual-v1"
        assert event.occurred_at is None
        assert event.published_at == datetime(2026, 7, 14, 10, 0)
        assert unresolved_mention is not None
        assert unresolved_mention.candidate_company_id is None
        assert len(documents) == 2
        assert all(document.research_import_id == batch.id for document in documents)
        assert all("original_query" not in document.payload for document in documents)
        assert len(reviews) == 2
        assert any(review.event_id == event.id for review in reviews)
        assert any(
            review.entity_mention_id == unresolved_mention.id and review.event_id is None
            for review in reviews
        )
        entity_review = next(review for review in reviews if review.entity_mention_id is not None)
        with pytest.raises(AccessDeniedError, match="identity resolution"):
            decide_review(session, entity_review.id, user, "approve", "不能跳过主体解析")
        assert entity_review.status == "pending"
        assert ledger is not None
        assert ledger.external_calls == 0
        assert ledger.estimated_cost == Decimal("0")
        assert session.scalar(select(func.count()).select_from(EventEvidence)) == 1
        assert session.scalar(select(func.count()).select_from(ReviewQueue)) == 2
        assert session.scalar(select(func.count()).select_from(CompanySnapshot)) == 0
        assert session.scalar(select(func.count()).select_from(ResearchImport)) == 1
        assert session.scalar(select(func.count()).select_from(UsageLedger)) == 1

    with TestClient(migrated_app, raise_server_exceptions=False) as client:
        response = client.get(
            "/api/v1/reviews",
            headers={"X-Demo-User-Id": str(ALPHA_USER_ID)},
        )
        assert response.status_code == 200
        review_subjects = {
            (item["event_id"] is None, item["entity_mention_id"] is None)
            for item in response.json()
        }
        assert review_subjects == {(False, True), (True, False)}

        disabled = client.get(
            "/api/v1/reviews/workbench",
            headers={"X-Demo-User-Id": str(ALPHA_USER_ID)},
        )
        assert disabled.status_code == 404
        assert disabled.json()["detail"] == "review_workbench_disabled"

        migrated_app.state.settings = replace(
            migrated_app.state.settings,
            review_workbench_enabled=True,
        )
        workbench = client.get(
            "/api/v1/reviews/workbench",
            headers={"X-Demo-User-Id": str(ALPHA_USER_ID)},
        )
        assert workbench.status_code == 200
        items = workbench.json()
        event_item = next(item for item in items if item["event"] is not None)
        mention_item = next(item for item in items if item["event"] is None)

        assert event_item["company_legal_name"] == "示例星河科技一号有限公司"
        assert event_item["event"]["title"] == "示例公司签署公开测试合同"
        assert event_item["event"]["facts"] == [
            {"name": "contract", "value": "测试合同", "unit": None}
        ]
        assert event_item["event"]["uncertainties"] == ["未披露金额"]
        assert len(event_item["event"]["evidence"]) == 1
        assert event_item["event"]["evidence"][0]["source_name"] == "示例官方来源"
        assert event_item["mention_text"] is None
        assert mention_item["event_id"] is None
        assert mention_item["mention_text"] == "不存在的示例公司"
        assert mention_item["resolution_status"] == "unresolved"
        assert mention_item["company_legal_name"] is None

        forbidden = client.get(
            "/api/v1/reviews/workbench",
            headers={"X-Demo-User-Id": str(BETA_USER_ID)},
        )
        assert forbidden.status_code == 403


def test_manual_import_adds_independent_evidence_to_an_in_review_event(
    tmp_path: Path,
    manual_import_payload: dict[str, object],
    migrated_app: FastAPI,
) -> None:
    records = manual_import_payload["records"]
    assert isinstance(records, list)
    facts = records[0]["facts"]
    assert isinstance(facts, list)
    facts.append({"name": "counterparty", "value": "示例客户", "unit": None})
    confirming_record = copy.deepcopy(records[0])
    confirming_facts = confirming_record["facts"]
    assert isinstance(confirming_facts, list)
    confirming_facts.reverse()
    confirming_record["external_record_id"] = "manual-confirmation-002"
    confirming_record["source_code"] = "manual_second_official"
    confirming_record["source_name"] = "第二个示例官方来源"
    confirming_record["canonical_url"] = "https://second.example.invalid/confirmation-002"
    confirming_record["source_published_at"] = "2026-07-15T11:00:00+08:00"
    confirming_record["title"] = "第二个来源确认示例合同"
    records.append(confirming_record)
    provider = _provider(tmp_path, manual_import_payload)

    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        result = import_manual_research(session, user, provider)

        assert result.documents_created == 2
        assert result.events_created == 1
        assert result.reviews_created == 1
        assert session.scalar(select(func.count()).select_from(Event)) == 1
        assert session.scalar(select(func.count()).select_from(EventEvidence)) == 2
        assert session.scalar(select(func.count()).select_from(ReviewQueue)) == 1


def test_manual_import_does_not_attach_unreviewed_evidence_to_published_event(
    tmp_path: Path,
    manual_import_payload: dict[str, object],
    migrated_app: FastAPI,
) -> None:
    first_provider = _provider(tmp_path, manual_import_payload, "first.json")
    second_payload = copy.deepcopy(manual_import_payload)
    second_payload["batch_id"] = "manual-batch-002"
    second_records = second_payload["records"]
    assert isinstance(second_records, list)
    second_records[0]["external_record_id"] = "manual-published-confirmation"
    second_records[0]["source_code"] = "manual_post_publish_official"
    second_records[0]["source_name"] = "发布后的示例来源"
    second_records[0]["canonical_url"] = "https://later.example.invalid/confirmation"
    second_records[0]["source_published_at"] = "2026-07-16T11:00:00+08:00"
    second_provider = _provider(tmp_path, second_payload, "second.json")

    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        import_manual_research(session, user, first_provider)
        event_review = session.scalar(select(ReviewQueue).where(ReviewQueue.event_id.is_not(None)))
        assert event_review is not None
        decide_review(session, event_review.id, user, "approve", "验证发布后证据隔离")

        result = import_manual_research(session, user, second_provider)

        event = session.scalar(select(Event))
        evidence_review = session.scalar(
            select(ReviewQueue).where(ReviewQueue.entity_mention_id.is_not(None))
        )
        assert result.events_created == 0
        assert result.reviews_created == 1
        assert event is not None and event.status == "published"
        assert evidence_review is not None
        assert "existing_event_new_evidence" in evidence_review.trigger_rules
        assert session.scalar(select(func.count()).select_from(Event)) == 1
        assert session.scalar(select(func.count()).select_from(EventEvidence)) == 1
        assert session.scalar(select(func.count()).select_from(CompanySnapshot)) == 1


def test_manual_import_requires_institution_admin(
    tmp_path: Path,
    migrated_app: FastAPI,
) -> None:
    provider = ManualResearchImportProvider("missing.json", allowed_root=tmp_path)

    with migrated_app.state.session_factory() as session:
        user = session.get(User, BETA_USER_ID)
        assert user is not None
        with pytest.raises(AccessDeniedError, match="institution_admin"):
            import_manual_research(session, user, provider)

        assert session.scalar(select(func.count()).select_from(ResearchImport)) == 0


def test_manual_import_rejects_reused_batch_id_with_different_file(
    tmp_path: Path,
    manual_import_payload: dict[str, object],
    migrated_app: FastAPI,
) -> None:
    first_provider = _provider(tmp_path, manual_import_payload, "first.json")
    changed_payload = copy.deepcopy(manual_import_payload)
    changed_records = changed_payload["records"]
    assert isinstance(changed_records, list)
    changed_records[0]["title"] = "同批次编号下被修改的内容"
    second_provider = _provider(tmp_path, changed_payload, "second.json")

    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        import_manual_research(session, user, first_provider)
        with pytest.raises(ImportConflictError, match="batch_id"):
            import_manual_research(session, user, second_provider)

        assert session.scalar(select(func.count()).select_from(ResearchImport)) == 1


def test_manual_import_rolls_back_the_whole_batch_on_source_conflict(
    tmp_path: Path,
    manual_import_payload: dict[str, object],
    migrated_app: FastAPI,
) -> None:
    records = manual_import_payload["records"]
    assert isinstance(records, list)
    conflicting = copy.deepcopy(records[0])
    conflicting["external_record_id"] = "manual-record-conflict"
    conflicting["canonical_url"] = "https://example.invalid/manual-conflict"
    conflicting["source_name"] = "冲突的来源名称"
    records.append(conflicting)
    provider = _provider(tmp_path, manual_import_payload)

    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        document_count = session.scalar(select(func.count()).select_from(RawDocument))

        with pytest.raises(ImportConflictError, match="source_code"):
            import_manual_research(session, user, provider)

        assert session.scalar(select(func.count()).select_from(RawDocument)) == document_count
        assert session.scalar(select(func.count()).select_from(ResearchImport)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(Source)
                .where(Source.code == "manual_example_official")
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(UsageLedger)
                .where(UsageLedger.operation == "manual_research_import")
            )
            == 0
        )
