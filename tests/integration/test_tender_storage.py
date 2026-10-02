from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from backend.app.config import RefreshPolicy
from backend.app.demo import (
    ALPHA_FUND_ID,
    ALPHA_TENANT_ID,
    ALPHA_USER_ID,
    BETA_USER_ID,
    NO_ACCESS_USER_ID,
)
from backend.app.models import (
    EntityMention,
    Event,
    EventEvidence,
    EventFact,
    EventFactSupport,
    EventObservation,
    Fund,
    FundAccessGrant,
    RawDocument,
    Role,
    Source,
    User,
    UserRoleAssignment,
)
from backend.app.providers import MockResearchProvider
from backend.app.services import (
    AccessDeniedError,
    ImportConflictError,
    get_company_detail,
    ingest_mock_records,
)
from backend.app.tender_storage import persist_tender_candidate
from tests.support.tender_storage import (
    CASES,
    COMPANY_ID,
    _actor,
    _candidate,
    _counts,
    _persist,
    _source_document,
)
from tests.support.tender_storage import database as database


def test_duplicate_reprint_and_correction_keep_history(database):
    original = _candidate(database, _source_document(database))
    first = _persist(database, original)
    before = _counts(database)
    duplicate = _persist(database, original)
    assert duplicate.status == "duplicate" and duplicate.observation_id == first.observation_id
    assert _counts(database) == before
    reprint = _candidate(database, _source_document(database, "reprint"))
    second = _persist(database, reprint)
    correction = _candidate(database, _source_document(database, "correction"))
    third = _persist(database, correction)
    assert first.event_created and not second.event_created and not third.event_created
    assert first.event_id == second.event_id == third.event_id
    with Session(database.owner) as session:
        event = session.get(Event, first.event_id)
        observations = list(
            session.scalars(select(EventObservation).order_by(EventObservation.created_at))
        )
        assert [item.observation_kind for item in observations] == [
            "initial",
            "same_facts",
            "correction_candidate",
        ]
        assert observations[0].candidate_payload["candidate"]["amount"] == "12345000.00"
        assert observations[2].candidate_payload["candidate"]["amount"] == "12000000.00"
        assert observations[0].fact_version != observations[2].fact_version
        assert {item["value"] for item in event.facts if item["name"] == "中标金额"} == {"12345000"}
        assert event.status == "candidate" and event.occurred_at is None
        assert observations[0].occurred_on.isoformat() == "2026-08-03"
        assert observations[0].date_precision == "day"
        for observation in observations:
            doc = session.get(RawDocument, observation.raw_document_id)
            for link in observation.candidate_payload["field_links"]:
                support = session.get(EventFactSupport, UUID(link["support_id"]))
                evidence = session.get(EventEvidence, UUID(link["evidence_id"]))
                locator = support.evidence_locator
                assert (
                    doc.payload["excerpt"][locator["start_offset"] : locator["end_offset"]]
                    == evidence.evidence_excerpt
                )
                assert support.event_id == evidence.event_id == event.id
    with Session(database.app) as session:
        user = _actor(session)
        detail = get_company_detail(
            session, user, COMPANY_ID, RefreshPolicy(), auto_refresh_enabled=False
        )
        visible = next(item for item in detail.unconfirmed_leads if item.id == first.event_id)
        assert {item.value for item in visible.fact_ledger if item.name == "中标金额"} == {
            "12345000"
        }


def test_frozen_candidate_corpus_persists_expected_groups_and_raw_unknowns(database):
    groups = {}
    candidates = []
    for case_id, case in CASES.items():
        if case["expected"]["status"] != "candidate":
            continue
        candidate = _candidate(database, _source_document(database, case_id))
        result = _persist(database, candidate)
        group = case["expected"]["event_group"] or case_id
        groups.setdefault(group, set()).add(result.event_id)
        candidates.append(candidate)
        with Session(database.owner) as session:
            observation = session.get(EventObservation, result.observation_id)
            assert observation.candidate_payload["candidate"] == candidate.model_dump(mode="json")
    assert len(candidates) == 16 and len(groups) == 8
    assert all(len(event_ids) == 1 for event_ids in groups.values())
    assert len(set().union(*groups.values())) == 8
    before = _counts(database)
    assert before[:2] == (8, 16)
    for candidate in candidates:
        assert _persist(database, candidate).status == "duplicate"
    assert _counts(database) == before


def test_different_matters_missing_identifiers_and_unknown_dates(database):
    ids = []
    for case_id in (
        "award",
        "other_project",
        "other_lot",
        "shortlisted",
        "missing_project",
        "missing_project",
    ):
        result = _persist(database, _candidate(database, _source_document(database, case_id)))
        ids.append(result.event_id)
    assert len(set(ids)) == 6
    result = _persist(database, _candidate(database, _source_document(database, "unknown_date")))
    assert result.event_id == ids[0]
    with Session(database.owner) as session:
        observation = session.get(EventObservation, result.observation_id)
        assert observation.observation_kind == "incomplete"
        assert observation.occurred_on is None and observation.date_precision == "unknown"
        assert observation.candidate_payload["candidate"]["reported_date"] == "8月3日"


def test_conflict_and_correction_arriving_first_do_not_overwrite_projection(database):
    correction = _persist(database, _candidate(database, _source_document(database, "correction")))
    with Session(database.owner) as session:
        before = session.get(Event, correction.event_id).facts
    original = _persist(database, _candidate(database, _source_document(database)))
    assert original.event_id == correction.event_id
    with Session(database.owner) as session:
        assert session.get(Event, original.event_id).facts == before
        assert (
            session.get(EventObservation, original.observation_id).observation_kind == "conflicting"
        )


def test_changing_stored_document_cannot_move_or_replace_an_observation(database):
    document_id = _source_document(database)
    original = _candidate(database, document_id)
    result = _persist(database, original)
    before = _counts(database)
    with Session(database.owner) as session:
        document = session.get(RawDocument, document_id)
        changed_text = CASES["other_project"]["document"]["body"]
        document.payload = {"excerpt": changed_text, "fixture_only": True}
        document.content_hash = hashlib.sha256(changed_text.encode()).hexdigest()
        session.commit()
    changed = _candidate(database, document_id)
    assert changed.business_key != original.business_key
    with pytest.raises(ImportConflictError, match="append a new document"):
        _persist(database, changed)
    assert _counts(database) == before
    with Session(database.owner) as session:
        observation = session.get(EventObservation, result.observation_id)
        assert observation.candidate_payload["candidate"] == original.model_dump(mode="json")


@pytest.mark.parametrize("tamper", ["amount", "document", "quote", "offset", "company"])
def test_forged_candidates_are_rejected_before_writes(database, tamper):
    candidate = _candidate(database, _source_document(database))
    if tamper == "amount":
        candidate = candidate.model_copy(update={"amount": Decimal("1")})
    elif tamper == "document":
        candidate = candidate.model_copy(
            update={"document": candidate.document.model_copy(update={"document_id": uuid4()})}
        )
    elif tamper == "quote":
        candidate.evidence[0].span.quote = "无依据的替换文本"
    elif tamper == "offset":
        candidate.evidence[0].span.start_offset = 999
    else:
        candidate = candidate.model_copy(
            update={
                "subject": candidate.subject.model_copy(
                    update={"credit_code": "91310000MA1K000019"}
                )
            }
        )
    before = _counts(database)
    with pytest.raises((AccessDeniedError, ImportConflictError)):
        _persist(database, candidate)
    assert _counts(database) == before


@pytest.mark.parametrize(
    "revocation", ["document_license", "source_license", "mention", "inactive"]
)
def test_current_database_authorization_is_rechecked_even_for_duplicates(database, revocation):
    document_id = _source_document(database)
    candidate = _candidate(database, document_id)
    _persist(database, candidate)
    before = _counts(database)
    with Session(database.owner) as session:
        doc = session.get(RawDocument, document_id)
        if revocation == "document_license":
            doc.license_status = "unknown"
        elif revocation == "source_license":
            session.get(Source, doc.source_id).license_status = "unknown"
        elif revocation == "inactive":
            session.get(User, NO_ACCESS_USER_ID).status = "inactive"
        else:
            mention = session.scalar(
                select(EntityMention).where(EntityMention.raw_document_id == doc.id)
            )
            mention.resolution_status = "unresolved"
        session.commit()
    with pytest.raises((AccessDeniedError, ImportConflictError)):
        _persist(database, candidate)
    assert _counts(database) == before


def test_permission_scopes_do_not_merge_or_grant_access(database):
    personal_a = _candidate(database, _source_document(database))
    personal_b = _candidate(database, _source_document(database, owner_id=BETA_USER_ID))
    organization = _candidate(
        database, _source_document(database, scope="organization_private", owner_id=ALPHA_TENANT_ID)
    )
    system = _candidate(database, _source_document(database, scope="system_restricted"))
    first = _persist(database, personal_a)
    second = _persist(database, personal_b, BETA_USER_ID)
    third = _persist(database, organization, ALPHA_USER_ID)
    assert len({first.event_id, second.event_id, third.event_id}) == 3
    for candidate, actor in (
        (personal_a, BETA_USER_ID),
        (personal_b, NO_ACCESS_USER_ID),
        (organization, NO_ACCESS_USER_ID),
        (organization, BETA_USER_ID),
        (system, ALPHA_USER_ID),
    ):
        with pytest.raises(AccessDeniedError):
            _persist(database, candidate, actor)
    if database.postgres:
        for actor, expected in (
            (NO_ACCESS_USER_ID, {first.event_id}),
            (BETA_USER_ID, {second.event_id}),
            (ALPHA_USER_ID, {third.event_id}),
        ):
            with Session(database.app) as session:
                _actor(session, actor)
                assert set(session.scalars(select(EventObservation.event_id))) == expected


def test_platform_shared_requires_platform_role(database):
    candidate = _candidate(database, _source_document(database, scope="platform_shared"))
    for actor in (NO_ACCESS_USER_ID, BETA_USER_ID):
        with pytest.raises(AccessDeniedError):
            _persist(database, candidate, actor)
    result = _persist(database, candidate, ALPHA_USER_ID)
    if database.postgres:
        with Session(database.app) as session:
            _actor(session, BETA_USER_ID)
            assert session.get(EventObservation, result.observation_id) is not None
    with Session(database.owner) as session:
        role = session.scalar(select(Role).where(Role.code == "platform_admin"))
        grant = session.scalar(
            select(UserRoleAssignment).where(UserRoleAssignment.role_id == role.id)
        )
        grant.valid_until = datetime.now(UTC) - timedelta(days=1)
        session.commit()
    with pytest.raises(AccessDeniedError):
        _persist(database, candidate, ALPHA_USER_ID)


def test_fund_grant_must_cover_the_company_and_remain_active(database):
    candidate = _candidate(
        database, _source_document(database, scope="organization_private", owner_id=ALPHA_TENANT_ID)
    )
    with Session(database.owner) as session:
        unrelated_fund = Fund(tenant_id=ALPHA_TENANT_ID, code="unrelated", name="虚构无持仓基金")
        session.add(unrelated_fund)
        session.flush()
        session.add(FundAccessGrant(user_id=NO_ACCESS_USER_ID, fund_id=unrelated_fund.id))
        session.commit()
    with pytest.raises(AccessDeniedError):
        _persist(database, candidate)
    with Session(database.owner) as session:
        grant = FundAccessGrant(user_id=NO_ACCESS_USER_ID, fund_id=ALPHA_FUND_ID)
        session.add(grant)
        session.commit()
        grant_id = grant.id
    result = _persist(database, candidate)
    with Session(database.owner) as session:
        session.get(FundAccessGrant, grant_id).valid_until = datetime.now(UTC) - timedelta(days=1)
        session.commit()
    before = _counts(database)
    with pytest.raises(AccessDeniedError):
        _persist(database, candidate)
    assert _counts(database) == before
    if database.postgres:
        with Session(database.app) as session:
            _actor(session)
            assert session.get(EventObservation, result.observation_id) is None


def test_disabled_and_outer_rollback_leave_no_partial_records(database):
    candidate = _candidate(database, _source_document(database))
    before = _counts(database)
    with Session(database.app) as session:
        actor = _actor(session)
        assert persist_tender_candidate(session, actor, candidate).status == "disabled"
        session.commit()
    assert _counts(database) == before
    with Session(database.app) as session:
        actor = _actor(session)
        persist_tender_candidate(session, actor, candidate, enabled=True)
        session.rollback()
    assert _counts(database) == before


def test_failure_after_evidence_creation_rolls_back_the_slice(database, monkeypatch):
    import backend.app.tender_storage as storage

    candidate = _candidate(database, _source_document(database))
    append = storage._append_evidence

    def fail_after_append(*args):
        append(*args)
        raise RuntimeError("injected failure after evidence")

    monkeypatch.setattr(storage, "_append_evidence", fail_after_append)
    before = _counts(database)
    with Session(database.app) as session:
        actor = _actor(session)
        with pytest.raises(RuntimeError, match="injected"):
            persist_tender_candidate(session, actor, candidate, enabled=True)
        session.commit()
    assert _counts(database) == before


@pytest.mark.parametrize("same_document", [True, False])
def test_postgres_concurrent_ingest_is_idempotent(database, same_document):
    if not database.postgres:
        pytest.skip("concurrency contract is verified against primary PostgreSQL")
    left = _candidate(database, _source_document(database))
    right = left if same_document else _candidate(database, _source_document(database, "reprint"))
    barrier = Barrier(2)

    def ingest(candidate):
        with Session(database.app) as session:
            actor = _actor(session)
            barrier.wait(timeout=10)
            result = persist_tender_candidate(session, actor, candidate, enabled=True)
            session.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(ingest, (left, right)))
    assert results[0].event_id == results[1].event_id
    assert sum(result.event_created for result in results) == 1
    assert _counts(database)[0:2] == (1, 1 if same_document else 2)


def test_fk_rejects_cross_event_support_and_postgres_observations_are_append_only(database):
    first = _persist(database, _candidate(database, _source_document(database)))
    second = _persist(database, _candidate(database, _source_document(database, "other_project")))
    with Session(database.owner) as session:
        fact = session.scalar(select(EventFact).where(EventFact.event_id == first.event_id))
        evidence = session.scalar(
            select(EventEvidence).where(EventEvidence.event_id == second.event_id)
        )
        session.add(
            EventFactSupport(
                event_id=first.event_id,
                event_fact_id=fact.id,
                event_evidence_id=evidence.id,
                support_status="supported",
                policy_version="invalid-test",
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()
    if database.postgres:
        with Session(database.app) as session:
            _actor(session)
            result = session.execute(
                update(EventObservation).values(observation_kind="conflicting")
            )
            assert result.rowcount == 0
            session.commit()
        with Session(database.app) as session:
            _actor(session)
            with pytest.raises(DBAPIError):
                session.execute(text("DELETE FROM event_observations"))


@pytest.mark.parametrize("forgery", ["foreign_document", "foreign_creator"])
def test_postgres_rejects_observation_scope_and_creator_forgery(database, forgery):
    if not database.postgres:
        pytest.skip("RLS contract is verified against primary PostgreSQL")
    result = _persist(database, _candidate(database, _source_document(database)))
    foreign_document = _source_document(database, owner_id=BETA_USER_ID)
    with Session(database.app) as session:
        _actor(session)
        observation = session.get(EventObservation, result.observation_id)
        session.add(
            EventObservation(
                event_id=result.event_id,
                raw_document_id=(
                    foreign_document
                    if forgery == "foreign_document"
                    else observation.raw_document_id
                ),
                schema_version="invalid-fixture-v1",
                fact_version=observation.fact_version,
                observation_kind="initial",
                date_precision="unknown",
                candidate_payload=observation.candidate_payload,
                created_by=BETA_USER_ID if forgery == "foreign_creator" else NO_ACCESS_USER_ID,
            )
        )
        with pytest.raises(DBAPIError, match="row-level security"):
            session.flush()


def test_migration_keeps_existing_records_and_refuses_history_loss(database):
    config = Config("alembic.ini")
    with Session(database.owner) as session:
        ingest_mock_records(session, MockResearchProvider())

    def snapshot():
        with database.owner.connect() as connection:
            return {
                table: connection.execute(text(f"SELECT * FROM {table} ORDER BY id")).all()
                for table in (
                    "companies",
                    "investments",
                    "events",
                    "raw_documents",
                    "event_evidence",
                    "event_facts",
                )
            }

    before = snapshot()
    command.downgrade(config, "0027")
    assert "event_observations" not in inspect(database.owner).get_table_names()
    assert snapshot() == before
    command.upgrade(config, "head")
    command.check(config)
    assert snapshot() == before
    result = _persist(database, _candidate(database, _source_document(database)))
    with pytest.raises(RuntimeError, match="Event observations exist"):
        command.downgrade(config, "0027")
    with Session(database.owner) as session:
        assert session.get(EventObservation, result.observation_id) is not None
    assert snapshot()["companies"] == before["companies"]
