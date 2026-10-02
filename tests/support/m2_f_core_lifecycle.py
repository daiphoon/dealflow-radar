"""虚构正文经正式身份、存储、审核与用户读取；不调用真实 Provider。"""

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.config import (
    Settings,
    WebResearchPolicy,
)
from backend.app.demo import ALPHA_USER_ID, BETA_USER_ID
from backend.app.main import create_app
from backend.app.research_matter_storage import persist_matters
from backend.app.research_matters import digest, validate_proposals
from backend.app.research_subject import load_subject
from backend.app.source_fetcher import DiscoveredDocument
from backend.app.web_research_service import (
    _content_quality_decision,
    _raw_document,
    _source,
)
from tests.support import curated_import as curated

database = curated.database


DAY = datetime(2026, 8, 18, tzinfo=UTC)


def capture(database, name, value):
    root = os.environ.get("M2F_CAPTURE_ROOT")
    if root:
        path = Path(root) / (name + ("-pg" if database.postgres else "-sqlite") + ".json")
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")
        path.chmod(0o600)


def mock_proposal_ingest(session, user, company, body, url):
    subject = load_subject(session, company)
    discovered = DiscoveredDocument(
        url, "虚构融资资料", DAY, digest(body), body, 200, None, None, "healthy"
    )
    policy = WebResearchPolicy(matter_processing_enabled=True, incremental_research_enabled=True)
    quality = _content_quality_decision(
        subject,
        title=discovered.title,
        excerpt=body,
        published_at=DAY,
        observed_at=datetime.now(UTC),
        policy=policy,
    )
    doc, _ = _raw_document(
        session,
        _source(session),
        subject,
        {},
        discovered,
        SimpleNamespace(id=uuid4(), coverage={"matter_processing": True}),
        quality,
    )
    # Mock 字段由本测试逐字引用；不调用规则抽取补齐模型缺失的关联锚点。
    fields = {}
    for role, pattern in (
        ("round", r"B轮"),
        ("occurred", r"2026年8月18日"),
        ("transaction_id", r"TX-811"),
        ("financing", r"3200万元"),
    ):
        match = re.search(pattern, body)
        if match and not (
            role in {"occurred", "financing"} and ("尚未" in body or "报道不实" in body)
        ):
            fields[role] = {"value": match.group(), "quote": body.rstrip("。"), "role": role}
    matters, rejected = validate_proposals(
        subject,
        body,
        proposed(company.legal_name, body.rstrip("。"), fields=fields),
        legacy_compat=False,
    )
    assert matters and not rejected
    rows, _, _ = persist_matters(session, company, doc, _source(session), user, matters, policy)
    assert len(rows) == 1
    return rows[0], doc


def proposed(name, quote, status="reported", fields=None):
    return {
        "matters": [
            {
                "subject": name,
                "subtype": "company_financing",
                "subject_role": "fundraiser",
                "status": status,
                "action_quote": quote,
                "fields": fields or {},
            }
        ]
    }


CASES = [
    ("完成B轮融资", "reported"),
    ("B轮融资已完成", "reported"),
    ("B轮融资交割完成", "reported"),
    ("B轮融资已交割", "reported"),
    ("启动B轮融资，计划募集3200万元，融资尚未交割完成", "planned"),
    ("拟募集B轮融资3200万元", "planned"),
    ("签署B轮融资投资协议，尚未交割", "planned"),
    ("收到B轮融资增资款", "reported"),
    ("B轮融资工商变更完成", "reported"),
    ("否认此前已完成B轮融资的报道，称该报道不实", "denied"),
    ("澄清：本轮B轮融资已经完成，尚未使用全部资金", "reported"),
    ("完成B轮融资，但新工厂尚未投产", "reported"),
    ("披露一项B轮融资安排，未披露交割日期、未说明是否完成", "reported"),
]


def app_for(database):
    return create_app(
        Settings(
            database_url=database.app.url.render_as_string(hide_password=False),
            app_mode="demo",
            external_calls_enabled=False,
            paid_api_calls_enabled=False,
            auto_refresh_enabled=False,
            review_workbench_enabled=True,
        )
    )


def report_roundtrip(database, company_id, *, expected):
    app = app_for(database)
    try:
        with TestClient(app) as client:
            headers = {"X-Demo-User-Id": str(ALPHA_USER_ID)}
            created = client.post(
                f"/api/v1/me/companies/{company_id}/reports",
                headers=headers,
                json={"idempotency_key": "7" * 64},
            )
            assert created.status_code == 200, created.text
            body = created.json()
            assert expected in body["markdown"]
            read = client.get("/api/v1/me/reports/" + body["id"], headers=headers)
            assert read.status_code == 200 and read.json()["markdown"] == body["markdown"]
            retry = client.post(
                f"/api/v1/me/companies/{company_id}/reports",
                headers=headers,
                json={"idempotency_key": "7" * 64},
            )
            assert retry.status_code == 200 and retry.json()["id"] == body["id"]
            reuse = client.post(
                f"/api/v1/me/companies/{company_id}/reports",
                headers=headers,
                json={"idempotency_key": "8" * 64},
            )
            assert reuse.status_code == 200 and reuse.json()["id"] == body["id"]
            assert (
                client.get(
                    "/api/v1/me/reports/" + body["id"],
                    headers={"X-Demo-User-Id": str(BETA_USER_ID)},
                ).status_code
                == 404
            )
            assert client.get("/api/v1/me/reports/" + body["id"]).status_code == 401
            return body
    finally:
        app.state.engine.dispose()
