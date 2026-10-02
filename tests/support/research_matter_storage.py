from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from backend.app.config import (
    WebResearchPolicy,
)
from backend.app.models import (
    utc_now,
)
from backend.app.research_matters import digest
from backend.app.research_subject import load_subject
from backend.app.source_fetcher import DiscoveredDocument
from backend.app.web_research_service import (
    _candidate_event,
    _content_quality_decision,
    _raw_document,
    _source,
)
from tests.support import curated_import as curated

database = curated.database


POLICY = WebResearchPolicy(
    incremental_research_enabled=True, topic_planning_enabled=True, matter_processing_enabled=True
)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda *_: pytest.fail("Unexpected real HTTP")
    )


def ingest(session, user, company, body, url, day=None):
    subject = load_subject(session, company)
    discovered = DiscoveredDocument(
        url, "示例公开资料", day, digest(body), body, 200, None, None, "healthy"
    )
    quality = _content_quality_decision(
        subject,
        title=discovered.title,
        excerpt=body,
        published_at=day,
        observed_at=utc_now(),
        policy=POLICY,
    )
    document, new = _raw_document(
        session,
        _source(session),
        subject,
        {},
        discovered,
        SimpleNamespace(id=uuid4(), coverage={"matter_processing": True}),
        quality,
    )
    event, created, quality = _candidate_event(
        session, subject, document, _source(session), POLICY, new_document=new, actor=user
    )
    return event, created, document, quality


class Model:
    code = "mock"

    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def extract(self, payload):
        self.calls += 1
        assert "tools" not in payload
        if self.fail:
            raise ValueError("malformed response")
        return {"output": {"matters": []}, "input_tokens": 100, "output_tokens": 10}
