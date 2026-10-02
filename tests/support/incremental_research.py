from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from backend.app.config import WebResearchPolicy
from backend.app.financing_events import digest
from backend.app.models import (
    Company,
)
from backend.app.research_subject import (
    load_subject,
)
from backend.app.source_fetcher import DiscoveredDocument
from backend.app.web_research_service import (
    _candidate_event,
    _content_quality_decision,
    _raw_document,
    _source,
)
from tests.curated_fixtures import CREDIT_CODE
from tests.support import curated_import as curated

database = curated.database


POLICY = WebResearchPolicy(incremental_research_enabled=True)


DAY = datetime(2026, 6, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda *_: pytest.fail("Unexpected real HTTP call")
    )


def initial(session, tmp_path, mode="initial_data"):
    curated.apply(session, curated.loaded(tmp_path, mode=mode))
    user = curated.enter(session)
    company = session.scalar(select(Company).where(Company.credit_code == CREDIT_CODE))
    return user, company


def ingest(session, user, company, body, url="https://example.com/financing", day=DAY):
    subject = load_subject(session, company)
    source = _source(session)
    discovered = DiscoveredDocument(
        canonical_url=url,
        title="示例融资披露",
        published_at=day,
        content_hash=digest(body),
        excerpt=body,
        http_status=200,
        etag=None,
        last_modified=None,
        link_health_status="healthy",
        metadata={"extraction_method": "business_passage"},
    )
    quality = _content_quality_decision(
        subject,
        title=discovered.title,
        excerpt=body,
        published_at=day,
        observed_at=datetime(2026, 9, 13, tzinfo=UTC),
        policy=POLICY,
    )
    document, new = _raw_document(
        session, source, subject, {}, discovered, SimpleNamespace(id=uuid4()), quality
    )
    session.flush()
    event, created, quality = _candidate_event(
        session, subject, document, source, POLICY, new_document=new, actor=user
    )
    assert quality.eligible
    return event, document, created
