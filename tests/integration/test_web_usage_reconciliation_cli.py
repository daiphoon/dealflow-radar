import json
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.app import web_research_budget as budget
from backend.app.config import WebResearchPolicy
from backend.app.demo import ALPHA_USER_ID, BETA_USER_ID
from backend.app.models import UsageLedger, User
from scripts import reconcile_web_research_usage as cli
from tests.integration.test_bounded_web_research import SHARED_COMPANY_ID, _grant_platform_admin


def test_admin_can_inspect_then_reconcile_without_providers(migrated_app, monkeypatch, capsys):
    _grant_platform_admin(migrated_app)
    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        monkeypatch.setenv("WEB_RESEARCH_WORKER_USER_ID", str(user.id))
        monkeypatch.setenv("WORKER_TENANT_ID", str(user.tenant_id))
        row = budget.reserve(
            session,
            user,
            WebResearchPolicy(),
            task_key="fixture:cli",
            subject="fictional",
            company_id=SHARED_COMPANY_ID,
            provider="web_search_baidu",
            operation="company_discovery",
            token="one",
            calls=1,
            unit_price=Decimal("0"),
            task_limit=4,
        )
        budget.dispatch(session, user, row, WebResearchPolicy(), cancelled=lambda: False)
        budget.recover(session, task_key=row.task_key)
        session.commit()
        usage_id = row.id

    def no_provider(*args, **kwargs):
        raise AssertionError("reconciliation must not instantiate a provider")

    monkeypatch.setattr("backend.app.web_search.BaiduSearchProvider.__init__", no_provider)
    monkeypatch.setattr("sys.argv", ["reconcile", str(usage_id)])
    cli.main()
    preview = json.loads(capsys.readouterr().out)
    assert preview["state"] == "uncertain" and preview["amount"] is None
    assert preview["applied"] is False
    monkeypatch.setattr(
        "sys.argv",
        [
            "reconcile",
            str(usage_id),
            "--apply",
            "--calls",
            "1",
            "--actual-cost",
            "0",
            "--reference",
            "fixture-receipt",
            "--reason",
            "本地虚构回执核对",
        ],
    )
    cli.main()
    applied = json.loads(capsys.readouterr().out)
    assert applied["state"] == "settled" and applied["cost_status"] == "actual"
    assert applied["external_calls_by_this_command"] == 0
    with migrated_app.state.session_factory() as session:
        assert (
            session.scalar(select(UsageLedger).where(UsageLedger.id == usage_id)).external_calls
            == 1
        )
        other = session.get(User, BETA_USER_ID)
        monkeypatch.setenv("WEB_RESEARCH_WORKER_USER_ID", str(other.id))
        monkeypatch.setenv("WORKER_TENANT_ID", str(other.tenant_id))
    with pytest.raises(PermissionError, match="platform_admin"):
        cli.main()


def test_old_zero_cost_stays_unknown_until_explicit_receipt(migrated_app):
    _grant_platform_admin(migrated_app)
    with migrated_app.state.session_factory() as session:
        user = session.get(User, ALPHA_USER_ID)
        row = UsageLedger(
            tenant_id=user.tenant_id,
            provider="web_search_baidu",
            operation="company_discovery",
            external_calls=1,
            estimated_cost=Decimal("0"),
            idempotency_key=uuid4().hex,
            metrics={},
        )
        session.add(row)
        session.commit()
        assert row.usage_state == "legacy" and row.cost_status == "unknown"
        budget.reconcile(
            session,
            user,
            row.id,
            calls=1,
            actual_cost=Decimal("0"),
            reference="fixture-free-receipt",
            reason="明确免费回执",
        )
        session.commit()
        assert row.usage_state == "legacy" and row.quota_scope is None
        assert row.cost_status == "actual" and row.estimated_cost == 0
        assert row.metrics["reconciliation"]["previous"]["cost_status"] == "unknown"
