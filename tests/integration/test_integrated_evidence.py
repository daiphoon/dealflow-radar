from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Company, CompanyAlias, EventObservation, utc_now
from backend.app.research_matter_storage import previous_matters
from backend.app.research_subject import load_subject
from backend.app.source_fetcher import DiscoveredDocument
from backend.app.web_research_service import _content_quality_decision, _raw_document, _source
from tests.integration import test_curated_import as curated
from tests.integration.test_incremental_research import initial
from tests.integration.test_research_matter_storage import POLICY, ingest

database = curated.database


@pytest.mark.parametrize("reverse", [False, True])
def test_shared_source_keeps_distinct_retained_representations(database, tmp_path, reverse):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        other = session.scalar(select(Company).where(Company.id != company.id))
        pairs = [
            (company, company.legal_name + "完成A轮融资。"),
            (other, other.legal_name + "完成B轮融资。"),
        ]
        if reverse:
            pairs.reverse()
        docs = []
        for target, excerpt in pairs:
            discovered = DiscoveredDocument(
                "https://example.com/shared",
                "公开合集",
                None,
                "a" * 64,
                excerpt,
                200,
                None,
                None,
                "healthy",
            )
            quality = _content_quality_decision(
                target,
                title=discovered.title,
                excerpt=excerpt,
                published_at=None,
                observed_at=utc_now(),
                policy=POLICY,
            )
            args = (
                session,
                _source(session),
                target,
                {},
                discovered,
                SimpleNamespace(id=uuid4(), coverage={"matter_processing": True}),
                quality,
            )
            doc, created = _raw_document(*args)
            assert created and doc.payload["excerpt"] == excerpt
            assert _raw_document(*args)[0].id == doc.id
            docs.append(doc)
        assert docs[0].id != docs[1].id
        assert docs[0].content_hash == docs[1].content_hash


def test_former_legal_name_is_usable_without_changing_old_records(database, tmp_path):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        _, company = initial(session, tmp_path, mode="identity_only")
        session.add(
            CompanyAlias(
                company_id=company.id,
                alias="虚构前身有限公司",
                normalized_alias="虚构前身有限公司",
                alias_type="former_legal_name",
                visibility_scope="platform_shared",
                verification_status="verified",
            )
        )
        session.flush()
        assert "虚构前身有限公司" in load_subject(session, company).legal_aliases


def test_superseded_observation_not_current_support(database, tmp_path):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        event, _, doc, _ = ingest(
            session, user, company, "示例山海完成A轮融资。", "https://example.com/v"
        )
        row = session.scalar(select(EventObservation).where(EventObservation.event_id == event.id))
        from backend.app.research_matter_storage import persist_matters
        from backend.app.research_matters import extract_matters

        subject = load_subject(session, company)
        persist_matters(
            session,
            company,
            doc,
            _source(session),
            user,
            extract_matters(subject, doc.payload["excerpt"]),
            POLICY,
            processing_version="replacement-version",
        )
        assert len(previous_matters(session, event, subject)) == 1
        assert (
            session.scalar(
                select(EventObservation).where(
                    EventObservation.event_id == event.id,
                    EventObservation.processing_version == "replacement-version",
                )
            )
            is not None
        )
        assert session.get(EventObservation, row.id) is not None


def test_same_document_other_visible_quote_does_not_revive_withdrawn_observation(
    database, tmp_path
):
    from backend.app.evidence_integrity import hash_excerpt_bytes
    from backend.app.models import EventEvidence

    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        user, company = initial(session, tmp_path, mode="identity_only")
        event, _, doc, _ = ingest(
            session, user, company, "示例山海完成A轮融资。", "https://example.com/partial"
        )
        original = session.scalar(select(EventEvidence).where(EventEvidence.event_id == event.id))
        sibling = EventEvidence(
            id=uuid4(),
            event_id=event.id,
            raw_document_id=doc.id,
            source_event_evidence_id=None,
            visibility_scope="platform_shared",
            evidence_excerpt="同一文档另一个片段",
            span_hash=hash_excerpt_bytes("同一文档另一个片段"),
            support_type=original.support_type,
            display_allowed=True,
            display_license_status="public",
            display_url_health_status="healthy",
            display_detail_payload=original.display_detail_payload,
        )
        session.add(sibling)
        original.display_allowed = False
        session.flush()
        assert previous_matters(session, event, load_subject(session, company)) == []
        assert sibling.display_allowed
