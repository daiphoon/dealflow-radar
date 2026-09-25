from dataclasses import replace

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.models import EventEvidence, EventObservation
from tests.integration import test_curated_import as curated
from tests.integration.test_diagnostic_service import diagnostic as diagnostic
from tests.integration.test_incremental_research import initial
from tests.integration.test_research_matter_storage import ingest

database = curated.database


def test_saved_support_is_not_current_revalidation(diagnostic, database, tmp_path):
    service, principal = diagnostic
    curated.curator(database)
    with Session(database.app, expire_on_commit=False) as s:
        user, company = initial(s, tmp_path, mode="identity_only")
        body = "示例山海完成1亿元A轮融资，由示例机构投资。"
        event, _, doc, _ = ingest(s, user, company, body, "https://example.com/lineage")
        assert event is not None
        event_id, company_id = event.id, company.id
        s.commit()
    principal = replace(principal, company_ids=frozenset([company_id]))
    result = service.call(principal, "get_event_lineage", {"event_id": str(event_id)})
    assert any(i["record_kind"] == "field_support" for i in result["items"])
    for item in result["items"]:
        assert item["current_support"] is None
        assert item["validity"] == "not_revalidated"
    with Session(database.app) as s:
        user = curated.enter(s)
        # Reprocess through the real ingress with a new extraction version; append only.
        from unittest.mock import patch

        from backend.app.models import Company

        with patch(
            "backend.app.research_matter_storage.EXTRACTION_VERSION", "fixture-extraction-next"
        ):
            ingest(s, user, s.get(Company, company_id), body, "https://example.com/lineage")
        s.commit()
    with Session(database.owner) as s:
        observations = list(
            s.scalars(select(EventObservation).where(EventObservation.event_id == event_id))
        )
        assert len({o.processing_version for o in observations}) >= 2
        for evidence in s.scalars(select(EventEvidence).where(EventEvidence.event_id == event_id)):
            evidence.display_allowed = False
        s.commit()
    result = service.call(principal, "get_event_lineage", {"event_id": str(event_id)})
    assert all(i["current_support"] is None for i in result["items"])


def test_view_owner_has_only_column_whitelist_and_no_public_escape(diagnostic, database):
    service, principal = diagnostic
    reader = service.engine.url.username
    with database.owner.connect() as c:
        assert not c.scalar(
            text("SELECT has_table_privilege(:r,'public.personal_company_reports','SELECT')"),
            {"r": reader + "_views"},
        )
        assert not c.scalar(
            text("SELECT has_column_privilege(:r,'public.raw_documents','payload','SELECT')"),
            {"r": reader + "_views"},
        )
    with service.engine.connect() as c:
        assert not c.scalar(text("SELECT has_schema_privilege(current_user,'public','CREATE')"))
        assert not c.scalar(
            text(
                "SELECT has_function_privilege(current_user,"
                "'public.watchlist_monitor_targets()','EXECUTE')"
            )
        )
    assert service.call(principal, "find_company", {"query": "示例"})["items"]
