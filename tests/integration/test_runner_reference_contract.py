"""正式会话和固定业务日期回归；全部资料虚构，禁止真实 HTTP。"""

import hashlib
import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.config import Settings, WebResearchCostPolicy, WebResearchPolicy
from backend.app.database import build_session_factory, request_session, set_request_context
from backend.app.demo import (
    ALPHA_FUND_ID,
    ALPHA_TENANT_ID,
    ALPHA_USER_ID,
    BETA_TENANT_ID,
    BETA_USER_ID,
    NO_ACCESS_USER_ID,
)
from backend.app.evidence_integrity import hash_canonical_object
from backend.app.main import create_app
from backend.app.models import (
    CompanyResearchJob,
    Event,
    EventObservation,
    Fund,
    PersonalCompanyReport,
    PersonalCompanyRequest,
    User,
)
from backend.app.research_plan import TOPICS
from backend.app.research_subject import load_subject, short_business_query
from backend.app.source_fetcher import TrustedSourceFetcher
from backend.app.web_research_service import prepare_pending_research_requests
from backend.app.web_search import MockSearchProvider, SearchResult
from scripts import research_validation_runner as runner
from scripts.research_validation_contract import runtime_binding
from scripts.run_web_research_worker import _with_worker_session
from tests.integration import test_curated_import as curated
from tests.integration.test_incremental_research import initial

database = curated.database
REF = datetime.fromisoformat("2026-09-29T00:00:00+08:00")
POLICY = WebResearchPolicy(incremental_research_enabled=True, matter_processing_enabled=True)
HEADERS = {"X-Demo-User-Id": str(NO_ACCESS_USER_ID)}


def capture(database, name, data):
    target = os.getenv("RUNNER_REFERENCE_ARTIFACT_DIR")
    if target:
        path = Path(target) / (name + ("-pg" if database.postgres else "-sqlite") + ".json")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n")
        path.chmod(0o600)


def settings(database):
    return Settings(
        database_url=database.app.url.render_as_string(hide_password=False),
        app_mode="demo",
        external_calls_enabled=False,
        paid_api_calls_enabled=False,
        auto_refresh_enabled=False,
        web_research_policy=POLICY,
    )


def test_official_worker_session_rebinds_commit_rollback_and_explicit_select(database):
    if not database.postgres:
        pytest.skip("transaction-local identity requires actual non-owner PostgreSQL")

    def inspect(session, user):
        rows = []
        for operation in ("initial", "commit", "rollback", "commit", "rollback"):
            if operation == "commit":
                session.commit()
            elif operation == "rollback":
                session.rollback()
            current = session.scalar(text("SELECT current_setting('app.current_user_id', true)"))
            rows.append({"operation": operation, "current_user": current})
            assert current == str(ALPHA_USER_ID), rows
            session.expire(user)
            session.refresh(user)
            assert session.scalar(select(User).where(User.id == ALPHA_USER_ID)) is user
        capture(database, "session-transaction-matrix", rows)
        return {"rows": rows}

    _with_worker_session(settings(database), ALPHA_USER_ID, ALPHA_TENANT_ID, inspect)


@pytest.mark.parametrize("reference", [REF, REF.astimezone(UTC)])
def test_report_fixed_business_window_from_normal_request(database, tmp_path, reference):
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as session:
        _, company = initial(session, tmp_path, mode="identity_only")
        company_id = company.id
    app = create_app(settings(database))
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/me/company-requests/refresh/{company_id}", headers=HEADERS
            )
            assert response.status_code == 200, response.text
            request_id = UUID(response.json()["id"])

            def prepare(session, user):
                prepare_pending_research_requests(session, user, POLICY, reference_at=reference)
                # 正式提交后重新SQL读取，不靠已加载的ORM缓存冒充权限持久化。
                from backend.app.database import set_request_context

                set_request_context(session, user.id, user.tenant_id)
                request = session.get(PersonalCompanyRequest, request_id)
                return dict(session.get(CompanyResearchJob, request.research_job_id).coverage)

            coverage = _with_worker_session(
                settings(database), ALPHA_USER_ID, ALPHA_TENANT_ID, prepare
            )
            saved = client.post(
                f"/api/v1/me/companies/{company_id}/reports",
                headers=HEADERS,
                json={"idempotency_key": "f" * 64},
            )
            assert saved.status_code == 200, saved.text
            data = saved.json()
            capture(
                database,
                "reference-before-after-" + str(reference.utcoffset()),
                {"coverage": coverage, "report": data},
            )
            assert "资料窗口：2025-09-29 至 2026-09-29" in data["markdown"]
            assert data["as_of"][:10] != "2026-09-29"
            url = "/api/v1/me/reports/" + data["id"]
            assert client.get(url, headers=HEADERS).json()["markdown"] == data["markdown"]
            assert client.get(url).status_code == 401
            assert client.get(url, headers={"X-Demo-User-Id": str(BETA_USER_ID)}).status_code == 404
    finally:
        app.state.engine.dispose()


@pytest.fixture
def isolated_runner(database, tmp_path):
    if not database.postgres:
        pytest.skip("formal runner requires non-owner PostgreSQL")
    with database.owner.begin() as connection:
        name = connection.scalar(text("SELECT current_database()"))
        connection.execute(text(f"COMMENT ON DATABASE \"{name}\" IS '{runner.ISOLATION_MARKER}'"))
        role = database.app.url.username
        connection.execute(text(f'GRANT SELECT ON alembic_version TO "{role}"'))
    curated.curator(database)
    with request_session(
        build_session_factory(database.app), ALPHA_USER_ID, ALPHA_TENANT_ID
    ) as session:
        _, company = initial(session, tmp_path, mode="identity_only")
        subject = load_subject(session, company)
        identity = tmp_path / "identity.json"
        identity.write_text(
            json.dumps(
                {
                    "company_order": [
                        {
                            "stable_company_key": "fictive-1",
                            "ucc": company.credit_code,
                            "legal_name": company.legal_name,
                            "reviewed_aliases": list(subject.aliases),
                            "region": company.registered_region,
                            "reference_at": REF.isoformat(),
                            "event_window": ["2025-09-29", "2026-09-29"],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        company_id = company.id
    cfg = replace(
        settings(database),
        web_research_policy=replace(
            POLICY,
            topic_planning_enabled=True,
            max_search_calls_per_job=16,
            max_fetch_requests_per_job=32,
            extraction_model="fixture-model",
            model_input_price_per_million=Decimal("2"),
            model_output_price_per_million=Decimal("8"),
            cost=WebResearchCostPolicy(
                task_limit=Decimal("1"),
                company_daily_limit=Decimal("1"),
                company_weekly_limit=Decimal("1"),
                system_weekly_limit=Decimal("1"),
                system_monthly_limit=Decimal("1"),
            ),
        ),
    )
    app = create_app(runner.readonly_settings(cfg))
    with TestClient(app) as client:
        response = client.post(f"/api/v1/me/company-requests/refresh/{company_id}", headers=HEADERS)
        assert response.status_code == 200
        contract = {
            "reference_at": REF.isoformat(),
            "module_root": str(Path.cwd()),
            "identity_path": str(identity),
            "request_id": response.json()["id"],
            "company_id": str(company_id),
            "worker_user_id": str(ALPHA_USER_ID),
            "worker_tenant_id": str(ALPHA_TENANT_ID),
            "attempt_id": str(uuid4()),
            "output_dir": str(tmp_path / "attempt"),
            "mode": "identity_search",
            "boundary_mode": "mock",
            "source_urls": [],
            "max_steps": 40,
            "outer_seconds": 180,
        }
        yield cfg, contract, subject, client
    app.state.engine.dispose()


class OfflineBoundaries:
    def __init__(self, subject, monkeypatch, *, model=False, failure=False):
        self.search_calls, self.requests, self.model_inputs = [], [], []
        self.url = "https://example.com/runner-funding"
        self.quote = (
            subject.legal_name
            + "B轮融资交割完成，融资金额3200万元，由示例远湾基金领投，但新工厂尚未投产"
        )
        self.body = "2026年9月29日，" + self.quote + "。"
        row = SearchResult(
            "fixture", "示例融资公告", self.url, self.body, "示例公开来源", "2026-09-29"
        )
        owner = self

        class Search(MockSearchProvider):
            def search(self, query, **kwargs):
                owner.search_calls.append(query)
                if failure:
                    raise RuntimeError("first-dispatch-failure")
                return super().search(query, **kwargs)

        self.providers = {
            "baidu": Search(
                "baidu", {short_business_query(subject, TOPICS[key]): [row] for key in TOPICS}
            ),
            "bocha": MockSearchProvider("bocha"),
        }

        class Model:
            code = "mock"

            def extract(self, payload):
                from tests.integration.test_m2_f_core_lifecycle import proposed

                owner.model_inputs.append(json.loads(payload["messages"][1]["content"]))
                return {
                    "output": proposed(subject.legal_name, owner.quote),
                    "input_tokens": 120,
                    "output_tokens": 80,
                }

        self.matter_provider = Model() if model else None
        self.document_provider = None
        monkeypatch.setattr(
            httpx.HTTPTransport, "handle_request", lambda _, request: self.response(request)
        )

    def response(self, request):
        from tests.unit.test_research_network import Peer

        self.requests.append(str(request.url))
        assert request.url.host in {"example.com", "www.python.org", "www.iana.org"}
        extras = {"network_stream": Peer("93.184.216.34")}
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                headers={"content-type": "text/plain"},
                text="User-agent: *\nAllow: /\n",
                extensions=extras,
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            extensions=extras,
            text=(
                '<html><head><meta property="article:published_time" '
                'content="2026-09-29T00:00:00+08:00">'
                "</head><body><main><p>" + self.body + "</p></main></body></html>"
            ),
        )

    def fetcher_factory(self, policy):
        return TrustedSourceFetcher(policy, resolver=lambda *_: ["93.184.216.34"])

    def close(self):
        for provider in self.providers.values():
            provider.close()


def bind_contract(contract, cfg):
    contract["binding_without_runner"] = runtime_binding(
        Path.cwd(), contract["identity_path"], cfg.web_research_policy, REF
    )
    contract["binding"] = runner.execution_binding(cfg, Path.cwd(), contract["identity_path"], REF)
    return hash_canonical_object(contract)


@pytest.mark.network_gate
@pytest.mark.parametrize(
    "model,mode", [(False, "identity_search"), (True, "identity_search"), (True, "given_sources")]
)
def test_same_runner_full_worker_report_and_reload(
    isolated_runner, database, model, mode, monkeypatch
):
    cfg, contract, subject, client = isolated_runner
    cfg = replace(
        cfg, web_research_policy=replace(cfg.web_research_policy, matter_model_enabled=model)
    )
    boundary = OfflineBoundaries(subject, monkeypatch, model=model)
    contract["mode"] = mode
    if mode == "given_sources":
        contract["source_urls"] = [boundary.url]
    approval = bind_contract(contract, cfg)
    receipt = runner.run_validation(
        contract, cfg, lambda _: boundary, approved_contract_sha256=approval
    )
    assert receipt["first_error"] is None, receipt
    assert receipt["native_job_status"] == "completed", receipt
    assert receipt["driver_exit_reason"] == "native_terminal"
    assert receipt["attempt_finished"] and receipt["evidence_sealed"]
    assert len(receipt["steps"]) >= 2
    assert receipt["coverage"]["business_reference_date"] == "2026-09-29"
    if mode == "identity_search":
        assert boundary.search_calls
        for row in receipt["coverage"]["search_groups"].values():
            assert row["query_plan"]["business_reference_date"] == "2026-09-29"
    else:
        assert boundary.search_calls == []
    assert bool(boundary.model_inputs) == model
    assert all(row["business_reference_date"] == "2026-09-29" for row in boundary.model_inputs)
    assert receipt["coverage"]["network_preflight"]["status"] == "research_network_ready"
    assert all(
        row.get("peer_verified") for row in receipt["coverage"]["network_preflight"]["checks"]
    )
    with request_session(
        build_session_factory(database.app), ALPHA_USER_ID, ALPHA_TENANT_ID
    ) as session:
        events = session.scalars(select(Event).where(Event.company_id == subject.id)).all()
        assert len(events) == 1
        assert session.scalar(
            select(EventObservation).where(EventObservation.event_id == events[0].id)
        )
        job = session.get(CompanyResearchJob, UUID(receipt["job_id"]))
        session.rollback()
        session.refresh(job)
        assert job.status == "completed"
    saved = client.post(
        f"/api/v1/me/companies/{subject.id}/reports",
        headers=HEADERS,
        json={"idempotency_key": "a" * 64},
    )
    assert saved.status_code == 200, saved.text
    report = saved.json()
    assert "资料窗口：2025-09-29 至 2026-09-29" in report["markdown"]
    assert "B轮" in report["markdown"]
    assert report["report_version"] == "personal-company-v5"
    url = "/api/v1/me/reports/" + report["id"]
    before_read = read_business_digest(database)
    assert client.get(url, headers=HEADERS).json()["markdown"] == report["markdown"]
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"X-Demo-User-Id": str(BETA_USER_ID)}).status_code == 404
    assert read_business_digest(database) == before_read
    assert (
        client.post(
            f"/api/v1/me/companies/{subject.id}/reports",
            headers=HEADERS,
            json={"idempotency_key": "b" * 64},
        ).json()["id"]
        == report["id"]
    )
    capture(
        database,
        f"chain-{mode}-{model}",
        {
            "receipt": receipt,
            "report": report,
            "boundary_calls": {
                "search": len(boundary.search_calls),
                "mock_http": len(boundary.requests),
                "model": len(boundary.model_inputs),
            },
        },
    )
    calls = len(boundary.search_calls)
    with pytest.raises(ValueError, match="must_not_restart"):
        runner.run_validation(contract, cfg, lambda _: boundary, approved_contract_sha256=approval)
    assert len(boundary.search_calls) == calls


def read_business_digest(database):
    tables = (
        "company_research_jobs",
        "refresh_jobs",
        "personal_event_view_receipts",
        "personal_company_reports",
        "personal_report_requests",
        "personal_watchlist_items",
        "usage_ledger",
        "event_observations",
        "event_evidence",
        "event_facts",
        "event_fact_supports",
    )
    with database.owner.connect() as connection:
        content = {
            table: connection.execute(text(f"SELECT row_to_json(t) FROM {table} t ORDER BY id"))
            .scalars()
            .all()
            for table in tables
        }
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()


def test_old_report_and_idempotency_preserved_across_template_advance(
    isolated_runner, database, monkeypatch
):
    from backend.app import personal_features

    cfg, contract, subject, client = isolated_runner
    bind_contract(contract, cfg)

    def prepare(session, user):
        return prepare_pending_research_requests(
            session, user, cfg.web_research_policy, reference_at=REF
        )

    _with_worker_session(cfg, ALPHA_USER_ID, ALPHA_TENANT_ID, prepare)
    endpoint = f"/api/v1/me/companies/{subject.id}/reports"
    monkeypatch.setattr(personal_features, "_REPORT_VERSION", "personal-company-v4")
    old = client.post(endpoint, headers=HEADERS, json={"idempotency_key": "c" * 64}).json()
    assert old["report_version"] == "personal-company-v4"
    with request_session(
        build_session_factory(database.app), NO_ACCESS_USER_ID, ALPHA_TENANT_ID
    ) as session:
        saved = session.get(PersonalCompanyReport, UUID(old["id"]))
        before = (saved.markdown, saved.content_hash, saved.idempotency_key)
    monkeypatch.setattr(personal_features, "_REPORT_VERSION", "personal-company-v5")
    new = client.post(endpoint, headers=HEADERS, json={"idempotency_key": "d" * 64}).json()
    assert new["id"] != old["id"] and new["report_version"] == "personal-company-v5"
    assert (
        client.get("/api/v1/me/reports/" + old["id"], headers=HEADERS).json()["markdown"]
        == old["markdown"]
    )
    assert (
        client.post(endpoint, headers=HEADERS, json={"idempotency_key": "c" * 64}).json()["id"]
        == old["id"]
    )
    with request_session(
        build_session_factory(database.app), NO_ACCESS_USER_ID, ALPHA_TENANT_ID
    ) as session:
        saved = session.get(PersonalCompanyReport, UUID(old["id"]))
        assert (saved.markdown, saved.content_hash, saved.idempotency_key) == before
    capture(
        database,
        "legacy-report-immutability",
        {"old": old, "new": new, "preserved_hash": before[1]},
    )


def test_runner_outer_stop_and_preflight_factory_guard(isolated_runner, database, monkeypatch):
    cfg, contract, subject, _ = isolated_runner
    contract["max_steps"] = 1
    approval = bind_contract(contract, cfg)
    boundary = OfflineBoundaries(subject, monkeypatch)
    receipt = runner.run_validation(
        contract, cfg, lambda _: boundary, approved_contract_sha256=approval
    )
    assert receipt["driver_exit_reason"] == "outer_step_limit", receipt
    assert receipt["native_job_status"] == "partial"
    assert receipt["attempt_finished"] and receipt["evidence_sealed"]
    assert len(boundary.search_calls) == 1
    assert any(row["state"] == "settled" for row in receipt["usage_ledger"])
    capture(database, "outer-stop-native-partial", receipt)


@pytest.mark.network_gate
def test_runner_first_error_preserved_with_secondary_snapshot_error(
    isolated_runner, database, monkeypatch
):
    cfg, contract, subject, _ = isolated_runner
    approval = bind_contract(contract, cfg)
    boundary = OfflineBoundaries(subject, monkeypatch, failure=True)

    def bad_snapshot(session, job_id):
        session.execute(text("SELECT * FROM deliberately_missing_snapshot_table"))

    monkeypatch.setattr(runner, "_snapshot", bad_snapshot)
    receipt = runner.run_validation(
        contract, cfg, lambda _: boundary, approved_contract_sha256=approval
    )
    assert receipt["first_error"]["class"] == "RuntimeError", receipt
    assert receipt["secondary_errors"][0]["stage"] == "snapshot"
    assert (
        "first-dispatch-failure" in (Path(contract["output_dir"]) / "first-error.json").read_text()
    )
    assert len(boundary.search_calls) == 1
    with request_session(
        build_session_factory(database.app), ALPHA_USER_ID, ALPHA_TENANT_ID
    ) as session:
        job = session.get(CompanyResearchJob, UUID(receipt["job_id"]))
        assert job.status == "running"
        from backend.app.models import UsageLedger

        states = session.scalars(
            select(UsageLedger).where(UsageLedger.task_key == f"web-research:{job.id}")
        ).all()
        assert states and states[0].usage_state in {"in_flight", "uncertain"}
    capture(database, "first-and-secondary-error", receipt)


def test_identity_bound_session_cannot_switch_and_pool_has_no_context(database):
    if not database.postgres:
        pytest.skip("actual transaction-local PostgreSQL")
    factory = build_session_factory(database.app)
    with request_session(factory, ALPHA_USER_ID, ALPHA_TENANT_ID) as session:
        session.get(User, ALPHA_USER_ID)
        with pytest.raises(ValueError, match="cannot_change"):
            set_request_context(session, BETA_USER_ID, ALPHA_TENANT_ID)
    with factory() as session:
        assert not session.scalar(text("SELECT current_setting('app.current_user_id',true)"))
        assert session.get(Fund, ALPHA_FUND_ID) is None
    with request_session(factory, BETA_USER_ID, BETA_TENANT_ID) as session:
        assert session.get(Fund, ALPHA_FUND_ID) is None
    with request_session(factory, ALPHA_USER_ID, BETA_TENANT_ID) as session:
        assert session.get(Fund, ALPHA_FUND_ID) is None


@pytest.mark.parametrize("fault", ["factory", "hash", "owner", "identity", "window"])
def test_runner_rejects_before_boundary_creation(isolated_runner, database, fault, monkeypatch):
    cfg, contract, _, _ = isolated_runner
    if fault == "owner":
        cfg = replace(cfg, database_url=database.owner.url.render_as_string(hide_password=False))
    if fault in {"identity", "window"}:
        path = Path(contract["identity_path"])
        data = json.loads(path.read_text())
        data["company_order"][0]["reference_at" if fault == "identity" else "event_window"] = (
            None if fault == "identity" else ["2025-09-28", "2026-09-28"]
        )
        path.write_text(json.dumps(data))
    calls = []
    if fault == "window":
        with pytest.raises(ValueError, match="window_differs"):
            bind_contract(contract, cfg)
        return
    approval = bind_contract(contract, cfg)
    if fault == "hash":
        contract["max_steps"] = 2
    if fault == "factory":
        from sqlalchemy.orm import sessionmaker

        monkeypatch.setattr(runner, "build_session_factory", lambda engine: sessionmaker(engine))
    try:
        result = runner.run_validation(
            contract, cfg, lambda _: calls.append("provider"), approved_contract_sha256=approval
        )
        assert result["first_error"] and result["driver_exit_reason"] == "first_error"
    except ValueError as error:
        assert fault in {"hash", "identity"}, error
    assert not calls
