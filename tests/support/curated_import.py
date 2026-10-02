from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import RefreshPolicy
from backend.app.curated_import import apply_curated_import, preview_curated_import
from backend.app.curated_workbook import CuratedWorkbookProvider
from backend.app.database import set_request_context
from backend.app.demo import ALPHA_TENANT_ID, ALPHA_USER_ID
from backend.app.models import (
    Role,
    User,
    UserRoleAssignment,
    utc_now,
)
from backend.app.services import get_company_detail
from tests.curated_fixtures import COMPANY_KEY, workbook_file
from tests.support import tender_storage as test_tender_storage

database = test_tender_storage.database


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import httpx

    def denied(*args, **kwargs):
        pytest.fail("E4.1 must not make external HTTP calls")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", denied)


def curator(database):
    with Session(database.owner) as session:
        role = session.scalar(select(Role.id).where(Role.code == "platform_admin"))
        session.add(UserRoleAssignment(user_id=ALPHA_USER_ID, role_id=role))
        session.commit()


def loaded(tmp_path, mode="initial_data", change=None):
    path = workbook_file(tmp_path / "fixture.xlsx", change=change)
    return CuratedWorkbookProvider(
        path, dataset_key="fixture", mode=mode, company_keys=(COMPANY_KEY,), allowed_root=tmp_path
    ).load()


def enter(session):
    set_request_context(session, ALPHA_USER_ID, ALPHA_TENANT_ID)
    return session.get(User, ALPHA_USER_ID)


def apply(session, data, **kwargs):
    user = enter(session)
    options = {
        "confirmed_at": utc_now() - timedelta(seconds=1),
        "reason": "负责人已确认虚构测试资料",
        **kwargs,
    }
    plan = preview_curated_import(session, user, data, **options)
    result = apply_curated_import(session, user, data, preview_hash=plan["preview_hash"], **options)
    return result


def detail_for_curator(session, company_id):
    user = enter(session)
    return get_company_detail(
        session, user, company_id, RefreshPolicy(), auto_refresh_enabled=False
    )
