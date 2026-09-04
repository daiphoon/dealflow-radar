from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.app.demo import (
    ALPHA_TENANT_ID,
    MOCK_SOURCE_ID,
    NO_ACCESS_USER_ID,
    demo_uuid,
)
from backend.app.fact_support import materialize_event_fact_ledger
from backend.app.models import (
    ORGANIZATION_PRIVATE_SCOPE,
    PLATFORM_SHARED_SCOPE,
    EntityMention,
    Event,
    EventEvidence,
    EventFact,
    EventFactSupport,
    RawDocument,
)

SHARED_COMPANY_ID = demo_uuid("company-示例星河科技一号有限公司")
SHARED_COMPANY_NAME = "示例星河科技一号有限公司"
NO_ACCESS_HEADERS = {"X-Demo-User-Id": str(NO_ACCESS_USER_ID)}


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _add_ledger_event(
    app: FastAPI,
    *,
    suffix: str,
    fact_name: str,
    fact_value: str,
    excerpt: str,
    add_matching_mention: bool = True,
    duplicate_fact: bool = False,
) -> tuple[UUID, UUID, UUID]:
    with app.state.session_factory() as session:
        document = RawDocument(
            source_id=MOCK_SOURCE_ID,
            visibility_scope=PLATFORM_SHARED_SCOPE,
            owner_user_id=None,
            owner_tenant_id=None,
            external_record_id=f"fact-ledger-{suffix}",
            canonical_url=f"https://example.invalid/fact-ledger/{suffix}",
            title=f"{SHARED_COMPANY_NAME}事实支持测试 {suffix}",
            published_at=datetime(2026, 9, 4, tzinfo=UTC),
            published_on=None,
            observed_at=datetime(2026, 9, 4, tzinfo=UTC),
            content_hash=_sha256(f"fact-ledger-content:{suffix}"),
            document_dedupe_key=_sha256(f"fact-ledger-document:{suffix}"),
            license_status="synthetic_demo",
            payload={"synthetic_demo": True},
        )
        session.add(document)
        session.flush()
        if add_matching_mention:
            session.add(
                EntityMention(
                    raw_document_id=document.id,
                    visibility_scope=PLATFORM_SHARED_SCOPE,
                    owner_user_id=None,
                    owner_tenant_id=None,
                    candidate_company_id=SHARED_COMPANY_ID,
                    mention_text=SHARED_COMPANY_NAME,
                    match_rule="fact_ledger_test",
                    match_confidence=Decimal("1.000"),
                    resolution_status="verified",
                )
            )
        fact = {"name": fact_name, "value": fact_value, "unit": None}
        event = Event(
            company_id=SHARED_COMPANY_ID,
            visibility_scope=PLATFORM_SHARED_SCOPE,
            owner_user_id=None,
            owner_tenant_id=None,
            event_type="information_quality",
            event_subtype="fact_ledger_test",
            status="published",
            direction="neutral",
            materiality_score=40,
            risk_severity="low",
            confidence_score=Decimal("0.900"),
            source_quality="A",
            title=f"事实支持测试 {suffix}",
            summary=excerpt,
            facts=[fact, fact] if duplicate_fact else [fact],
            uncertainties=[],
            occurred_at=None,
            published_at=datetime(2026, 9, 4, tzinfo=UTC),
            published_on=None,
            observed_at=datetime(2026, 9, 4, tzinfo=UTC),
            fingerprint_version="fact-ledger-test-v1",
            event_fingerprint=_sha256(f"fact-ledger-event:{suffix}"),
            publication_route="human_promoted",
            publication_policy_version="fact-ledger-test-v1",
            publication_reasons=["synthetic_test"],
        )
        session.add(event)
        session.flush()
        evidence = EventEvidence(
            event_id=event.id,
            raw_document_id=document.id,
            source_event_evidence_id=None,
            visibility_scope=PLATFORM_SHARED_SCOPE,
            owner_user_id=None,
            owner_tenant_id=None,
            evidence_excerpt=excerpt,
            span_hash=_sha256(excerpt),
            support_type="supports",
            display_allowed=False,
        )
        session.add(evidence)
        session.flush()
        facts = materialize_event_fact_ledger(session, event)
        session.commit()
        return event.id, evidence.id, facts[0].id


@pytest.mark.parametrize(
    ("suffix", "fact_name", "fact_value", "excerpt", "add_mention", "expected"),
    [
        (
            "supported",
            "注册资本",
            "1000万元",
            f"{SHARED_COMPANY_NAME}注册资本为1000万元。",
            True,
            "supported",
        ),
        (
            "partial",
            "注册资本变化",
            "1000万元，新增200万元",
            f"{SHARED_COMPANY_NAME}注册资本为1000万元。",
            True,
            "partial",
        ),
        (
            "conflicting",
            "注册资本",
            "2000万元",
            f"{SHARED_COMPANY_NAME}注册资本为1000万元。",
            True,
            "conflicting",
        ),
        (
            "pending",
            "注册资本",
            "1000万元",
            f"{SHARED_COMPANY_NAME}注册资本为1000万元。",
            False,
            "pending_review",
        ),
        (
            "unsupported",
            "经营许可",
            "药品生产许可",
            f"{SHARED_COMPANY_NAME}完成一般信息更新。",
            True,
            "unsupported",
        ),
    ],
)
def test_deterministic_support_statuses(
    migrated_app: FastAPI,
    suffix: str,
    fact_name: str,
    fact_value: str,
    excerpt: str,
    add_mention: bool,
    expected: str,
) -> None:
    event_id, _, _ = _add_ledger_event(
        migrated_app,
        suffix=suffix,
        fact_name=fact_name,
        fact_value=fact_value,
        excerpt=excerpt,
        add_matching_mention=add_mention,
    )
    with migrated_app.state.session_factory() as session:
        supports = list(
            session.scalars(select(EventFactSupport).where(EventFactSupport.event_id == event_id))
        )
        assert len(supports) == 1
        assert supports[0].support_status == expected
        assert supports[0].policy_version == "evidence-fact-support-v1"
        assert supports[0].deterministic_checks["citation_complete"] is True


def test_same_subject_and_value_without_fact_relation_requires_review(
    migrated_app: FastAPI,
) -> None:
    event_id, _, _ = _add_ledger_event(
        migrated_app,
        suffix="same-value-unrelated-relation",
        fact_name="法定代表人",
        fact_value="李四",
        excerpt=f"{SHARED_COMPANY_NAME}与李四完成一般业务交流。",
    )
    with migrated_app.state.session_factory() as session:
        support = session.scalar(
            select(EventFactSupport).where(EventFactSupport.event_id == event_id)
        )
        assert support is not None
        assert support.support_status == "pending_review"
        assert support.support_reasons == ["subject_and_value_present_but_fact_relation_not_proven"]


def test_numeric_date_and_source_date_checks_are_recorded(
    migrated_app: FastAPI,
) -> None:
    event_id, _, _ = _add_ledger_event(
        migrated_app,
        suffix="numeric-date-checks",
        fact_name="工商变更",
        fact_value="2026年9月4日，1000万元",
        excerpt=f"{SHARED_COMPANY_NAME}工商变更：2026年9月4日，1000万元。",
    )
    with migrated_app.state.session_factory() as session:
        support = session.scalar(
            select(EventFactSupport).where(EventFactSupport.event_id == event_id)
        )
        assert support is not None
        assert support.support_status == "supported"
        assert "1000" in support.deterministic_checks["number_tokens_expected"]
        assert (
            support.deterministic_checks["number_tokens_matched"]
            == support.deterministic_checks["number_tokens_expected"]
        )
        assert support.deterministic_checks["date_tokens_expected"] == ["2026-09-04"]
        assert support.deterministic_checks["date_tokens_matched"] == ["2026-09-04"]
        assert support.deterministic_checks["source_date_check"] == "matched"


def test_tampered_evidence_locator_is_unsupported(migrated_app: FastAPI) -> None:
    event_id, evidence_id, _ = _add_ledger_event(
        migrated_app,
        suffix="tampered-locator",
        fact_name="注册资本",
        fact_value="1000万元",
        excerpt=f"{SHARED_COMPANY_NAME}注册资本为1000万元。",
    )
    with migrated_app.state.session_factory() as session:
        evidence = session.get(EventEvidence, evidence_id)
        event = session.get(Event, event_id)
        assert evidence is not None
        assert event is not None
        evidence.span_hash = _sha256("不与当前摘录一致")
        materialize_event_fact_ledger(session, event)
        session.commit()

        support = session.scalar(
            select(EventFactSupport).where(EventFactSupport.event_id == event_id)
        )
        assert support is not None
        assert support.support_status == "unsupported"
        assert support.deterministic_checks["citation_complete"] is False
        assert support.support_reasons == ["evidence_locator_integrity_failed"]


def test_duplicate_fact_materialization_is_stable_and_idempotent(
    migrated_app: FastAPI,
) -> None:
    event_id, _, fact_id = _add_ledger_event(
        migrated_app,
        suffix="duplicate",
        fact_name="注册资本",
        fact_value="1000万元",
        excerpt=f"{SHARED_COMPANY_NAME}注册资本为1000万元。",
        duplicate_fact=True,
    )
    with migrated_app.state.session_factory() as session:
        event = session.get(Event, event_id)
        assert event is not None
        repeated = materialize_event_fact_ledger(session, event)
        session.commit()
        assert [fact.id for fact in repeated] == [fact_id]
        stored = list(session.scalars(select(EventFact).where(EventFact.event_id == event_id)))
        supports = list(
            session.scalars(select(EventFactSupport).where(EventFactSupport.event_id == event_id))
        )
        assert len(stored) == 1
        assert stored[0].occurrence_count == 2
        assert len(supports) == 1


def test_database_rejects_cross_event_fact_evidence_attachment(
    migrated_app: FastAPI,
) -> None:
    first_event_id, _, first_fact_id = _add_ledger_event(
        migrated_app,
        suffix="cross-first",
        fact_name="注册资本",
        fact_value="1000万元",
        excerpt=f"{SHARED_COMPANY_NAME}注册资本为1000万元。",
    )
    second_event_id, second_evidence_id, _ = _add_ledger_event(
        migrated_app,
        suffix="cross-second",
        fact_name="成立日期",
        fact_value="2026年9月4日",
        excerpt=f"{SHARED_COMPANY_NAME}成立日期为2026年9月4日。",
    )
    with migrated_app.state.session_factory() as session:
        with pytest.raises(IntegrityError):
            session.add(
                EventFactSupport(
                    event_id=first_event_id,
                    event_fact_id=first_fact_id,
                    event_evidence_id=second_evidence_id,
                    support_status="pending_review",
                    evidence_locator={},
                    deterministic_checks={},
                    support_reasons=["cross_event_test"],
                    policy_version="evidence-fact-support-v1",
                    assessed_at=datetime(2026, 9, 4, tzinfo=UTC),
                )
            )
            session.flush()
        session.rollback()
        assert first_event_id != second_event_id


def test_company_api_only_returns_support_for_visible_evidence(
    client: TestClient,
    migrated_app: FastAPI,
) -> None:
    event_id, shared_evidence_id, _ = _add_ledger_event(
        migrated_app,
        suffix="api-visible",
        fact_name="注册资本",
        fact_value="1000万元",
        excerpt=f"{SHARED_COMPANY_NAME}注册资本为1000万元。",
    )
    with migrated_app.state.session_factory() as session:
        private_excerpt = f"{SHARED_COMPANY_NAME}注册资本为1000万元，机构底稿不得泄露。"
        private_document = RawDocument(
            source_id=MOCK_SOURCE_ID,
            visibility_scope=ORGANIZATION_PRIVATE_SCOPE,
            owner_user_id=None,
            owner_tenant_id=ALPHA_TENANT_ID,
            external_record_id="fact-ledger-private-evidence",
            canonical_url="https://example.invalid/fact-ledger/private-evidence",
            title="机构私有事实底稿",
            published_at=datetime(2026, 9, 4, tzinfo=UTC),
            published_on=None,
            observed_at=datetime(2026, 9, 4, tzinfo=UTC),
            content_hash=_sha256("fact-ledger-private-content"),
            document_dedupe_key=_sha256("fact-ledger-private-document"),
            license_status="private_test",
            payload={"private": True},
        )
        session.add(private_document)
        session.flush()
        session.add(
            EntityMention(
                raw_document_id=private_document.id,
                visibility_scope=ORGANIZATION_PRIVATE_SCOPE,
                owner_user_id=None,
                owner_tenant_id=ALPHA_TENANT_ID,
                candidate_company_id=SHARED_COMPANY_ID,
                mention_text=SHARED_COMPANY_NAME,
                match_rule="fact_ledger_private_test",
                match_confidence=Decimal("1.000"),
                resolution_status="verified",
            )
        )
        private_evidence = EventEvidence(
            event_id=event_id,
            raw_document_id=private_document.id,
            source_event_evidence_id=None,
            visibility_scope=ORGANIZATION_PRIVATE_SCOPE,
            owner_user_id=None,
            owner_tenant_id=ALPHA_TENANT_ID,
            evidence_excerpt=private_excerpt,
            span_hash=_sha256(private_excerpt),
            support_type="supports",
            display_allowed=False,
        )
        session.add(private_evidence)
        session.flush()
        event = session.get(Event, event_id)
        assert event is not None
        materialize_event_fact_ledger(session, event)
        private_evidence_id = private_evidence.id
        session.commit()

    response = client.get(
        f"/api/v1/companies/{SHARED_COMPANY_ID}",
        headers=NO_ACCESS_HEADERS,
    )
    assert response.status_code == 200
    event_payload = next(item for item in response.json()["events"] if item["id"] == str(event_id))
    assert [item["id"] for item in event_payload["evidence"]] == [str(shared_evidence_id)]
    fact_payload = event_payload["fact_ledger"][0]
    assert [item["evidence_id"] for item in fact_payload["evidence_supports"]] == [
        str(shared_evidence_id)
    ]
    assert str(private_evidence_id) not in str(event_payload)
