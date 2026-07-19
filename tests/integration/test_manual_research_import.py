from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.app.config import PublicationPolicy
from backend.app.demo import ALPHA_USER_ID, BETA_USER_ID
from backend.app.models import (
    Company,
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
from backend.app.providers import DocumentVerification, ManualResearchImportProvider
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


class StubDocumentVerifier:
    def __init__(self, status: str = "healthy", http_status: int | None = 200) -> None:
        self.status = status
        self.http_status = http_status
        self.calls = 0

    def verify(self, url: str) -> DocumentVerification:
        self.calls += 1
        return DocumentVerification(
            status=self.status,
            checked_at=datetime(2026, 7, 15, 8, 0, tzinfo=UTC),
            http_status=self.http_status,
            final_url=url,
            reason="test_verification",
            external_calls=0,
        )


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
    unresolved["source_code"] = "manual_unresolved_source"
    unresolved["source_name"] = "未解析示例来源"
    unresolved["title"] = "未匹配公司的公开线索"
    unresolved["company_identity_evidence"]["legal_name"] = "不存在的示例公司"
    records.append(unresolved)
    record = records[0]
    record["canonical_url"] = "https://official.example/manual-001"
    record["source_quality"] = "B"
    record["company_identity_evidence"]["credit_code"] = "91310000TEST000001"
    record["company_identity_evidence"]["official_website"] = "https://official.example"
    provider = _provider(tmp_path, manual_import_payload)
    verifier = StubDocumentVerifier()

    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        company = session.scalar(
            select(Company).where(Company.legal_name == "示例星河科技一号有限公司")
        )
        assert company is not None
        company.credit_code = "91310000TEST000001"
        company.official_website = "https://official.example"
        session.commit()
        first = import_manual_research(
            session,
            user,
            provider,
            publication_policy=PublicationPolicy(enabled=True),
            document_verifier=verifier,
        )
        second = import_manual_research(
            session,
            user,
            provider,
            publication_policy=PublicationPolicy(enabled=True),
            document_verifier=verifier,
        )

        assert first.status == "completed_with_unresolved"
        assert first.records_seen == 2
        assert first.documents_created == 2
        assert first.events_created == 1
        assert first.reviews_created == 1
        assert first.resolved_records == 1
        assert first.unresolved_records == 1
        assert first.external_calls == 0
        assert first.estimated_cost == Decimal("0")
        assert first.auto_published_records == 1
        assert first.unconfirmed_records == 0
        assert first.identity_review_records == 1
        assert verifier.calls == 1
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
        assert event is not None and event.status == "published"
        assert event.publication_route == "auto_published"
        assert event.publication_policy_version == "identity-first-v1"
        assert event.fingerprint_version == "manual-v1"
        assert event.occurred_at is None
        assert event.published_at == datetime(2026, 7, 14, 10, 0)
        assert unresolved_mention is not None
        assert unresolved_mention.candidate_company_id is None
        assert len(documents) == 2
        assert all(document.research_import_id == batch.id for document in documents)
        assert all("original_query" not in document.payload for document in documents)
        assert len(reviews) == 1
        entity_review = reviews[0]
        assert entity_review.entity_mention_id == unresolved_mention.id
        assert entity_review.event_id is None
        with pytest.raises(AccessDeniedError, match="identity resolution"):
            decide_review(session, entity_review.id, user, "approve", "不能跳过主体解析")
        assert entity_review.status == "pending"
        assert ledger is not None
        assert ledger.external_calls == 0
        assert ledger.estimated_cost == Decimal("0")
        assert session.scalar(select(func.count()).select_from(EventEvidence)) == 1
        assert session.scalar(select(func.count()).select_from(ReviewQueue)) == 1
        assert session.scalar(select(func.count()).select_from(CompanySnapshot)) == 1
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
        assert review_subjects == {(True, False)}

        detail = client.get(
            f"/api/v1/companies/{event.company_id}",
            headers={"X-Demo-User-Id": str(ALPHA_USER_ID)},
        )
        assert detail.status_code == 200
        assert detail.json()["events"] == []
        assert detail.json()["private_events"][0]["publication_route"] == "auto_published"
        assert detail.json()["unconfirmed_leads"] == []

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
        assert len(items) == 1
        mention_item = items[0]
        assert mention_item["event_id"] is None
        assert mention_item["mention_text"] == "不存在的示例公司"
        assert mention_item["resolution_status"] == "unresolved"
        assert mention_item["company_legal_name"] is None

        forbidden = client.get(
            "/api/v1/reviews/workbench",
            headers={"X-Demo-User-Id": str(BETA_USER_ID)},
        )
        assert forbidden.status_code == 403


def test_manual_import_adds_independent_evidence_to_an_unconfirmed_lead(
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
        assert result.reviews_created == 0
        assert result.auto_published_records == 0
        assert result.unconfirmed_records == 2
        assert session.scalar(select(func.count()).select_from(Event)) == 1
        assert session.scalar(select(func.count()).select_from(EventEvidence)) == 2
        assert session.scalar(select(func.count()).select_from(ReviewQueue)) == 0
        event = session.scalar(select(Event))
        assert event is not None and event.status == "candidate"
        assert event.publication_route == "unconfirmed_lead"
        assert "source_url_unchecked" in event.publication_reasons


def test_broken_source_becomes_visible_unconfirmed_lead_without_review(
    tmp_path: Path,
    manual_import_payload: dict[str, object],
    migrated_app: FastAPI,
) -> None:
    records = manual_import_payload["records"]
    assert isinstance(records, list)
    record = records[0]
    record["source_published_at"] = None
    facts = record["facts"]
    assert isinstance(facts, list)
    facts.append({"name": "source_displayed_date", "value": "2026-07-14", "unit": None})
    provider = _provider(tmp_path, manual_import_payload)

    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        result = import_manual_research(
            session,
            user,
            provider,
            publication_policy=PublicationPolicy(enabled=True),
            document_verifier=StubDocumentVerifier("broken", 404),
        )

        event = session.scalar(select(Event))
        assert result.unconfirmed_records == 1
        assert result.reviews_created == 0
        assert event is not None and event.status == "candidate"
        assert event.published_at is None
        assert event.published_on.isoformat() == "2026-07-14"
        assert event.publication_route == "unconfirmed_lead"
        assert "source_url_broken" in event.publication_reasons
        assert session.scalar(select(func.count()).select_from(CompanySnapshot)) == 0
        company_id = event.company_id

    with TestClient(migrated_app) as client:
        response = client.get(
            f"/api/v1/companies/{company_id}",
            headers={"X-Demo-User-Id": str(ALPHA_USER_ID)},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["events"] == []
        assert payload["unconfirmed_leads"][0]["published_on"] == "2026-07-14"
        evidence = payload["unconfirmed_leads"][0]["evidence"][0]
        assert evidence["url_health_status"] == "broken"
        assert evidence["url_http_status"] == 404


def test_public_access_license_cannot_auto_publish(
    tmp_path: Path,
    manual_import_payload: dict[str, object],
    migrated_app: FastAPI,
) -> None:
    manual_import_payload["license_status"] = "public_access"
    provider = _provider(tmp_path, manual_import_payload)

    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        result = import_manual_research(
            session,
            user,
            provider,
            publication_policy=PublicationPolicy(enabled=True),
            document_verifier=StubDocumentVerifier(),
        )

        event = session.scalar(select(Event))
        assert result.auto_published_records == 0
        assert result.unconfirmed_records == 1
        assert event is not None and event.status == "candidate"
        assert "source_license_not_public" in event.publication_reasons
        assert session.scalar(select(func.count()).select_from(CompanySnapshot)) == 0


def test_auto_published_event_without_source_date_keeps_snapshot_date_unknown(
    tmp_path: Path,
    manual_import_payload: dict[str, object],
    migrated_app: FastAPI,
) -> None:
    records = manual_import_payload["records"]
    assert isinstance(records, list)
    records[0]["source_published_at"] = None
    provider = _provider(tmp_path, manual_import_payload)

    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        result = import_manual_research(
            session,
            user,
            provider,
            publication_policy=PublicationPolicy(enabled=True),
            document_verifier=StubDocumentVerifier(),
        )

        event = session.scalar(select(Event))
        snapshot = session.scalar(select(CompanySnapshot))
        assert result.auto_published_records == 1
        assert event is not None and event.published_on is None
        assert snapshot is not None and snapshot.data_as_of is None
        assert any("缺少发生或来源日期" in gap for gap in snapshot.information_gaps)


def test_high_risk_record_stays_unconfirmed_even_with_healthy_source(
    tmp_path: Path,
    manual_import_payload: dict[str, object],
    migrated_app: FastAPI,
) -> None:
    records = manual_import_payload["records"]
    assert isinstance(records, list)
    records[0]["risk_severity"] = "high"
    provider = _provider(tmp_path, manual_import_payload)

    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        result = import_manual_research(
            session,
            user,
            provider,
            document_verifier=StubDocumentVerifier(),
        )

        event = session.scalar(select(Event))
        assert result.unconfirmed_records == 1
        assert result.reviews_created == 0
        assert event is not None and event.status == "candidate"
        assert "high_risk_unconfirmed" in event.publication_reasons


def test_source_url_check_budget_defers_remaining_records(
    tmp_path: Path,
    manual_import_payload: dict[str, object],
    migrated_app: FastAPI,
) -> None:
    records = manual_import_payload["records"]
    assert isinstance(records, list)
    second = copy.deepcopy(records[0])
    second["external_record_id"] = "manual-record-002"
    second["event_subtype"] = "second_contract"
    second["title"] = "第二条公开测试合同"
    records.append(second)
    provider = _provider(tmp_path, manual_import_payload)
    verifier = StubDocumentVerifier()

    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        result = import_manual_research(
            session,
            user,
            provider,
            PublicationPolicy(enabled=True, max_source_url_checks_per_import=1),
            verifier,
        )

        events = list(session.scalars(select(Event).order_by(Event.title)))
        assert result.auto_published_records == 1
        assert result.unconfirmed_records == 1
        assert verifier.calls == 1
        assert {event.status for event in events} == {"candidate", "published"}
        deferred = next(event for event in events if event.status == "candidate")
        assert "source_url_unchecked" in deferred.publication_reasons


def test_manual_import_auto_attaches_verified_evidence_to_published_event(
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
    verifier = StubDocumentVerifier()

    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        assert user is not None
        first = import_manual_research(
            session,
            user,
            first_provider,
            publication_policy=PublicationPolicy(enabled=True),
            document_verifier=verifier,
        )
        assert first.auto_published_records == 1

        result = import_manual_research(
            session,
            user,
            second_provider,
            publication_policy=PublicationPolicy(enabled=True),
            document_verifier=verifier,
        )

        event = session.scalar(select(Event))
        assert result.events_created == 0
        assert result.reviews_created == 0
        assert result.auto_published_records == 1
        assert event is not None and event.status == "published"
        assert session.scalar(select(func.count()).select_from(Event)) == 1
        assert session.scalar(select(func.count()).select_from(EventEvidence)) == 2
        assert session.scalar(select(func.count()).select_from(ReviewQueue)) == 0
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
