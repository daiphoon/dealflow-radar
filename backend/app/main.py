from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import Settings
from backend.app.database import build_engine, build_session_factory, set_request_context
from backend.app.models import ReviewQueue, User
from backend.app.providers import MockResearchProvider
from backend.app.schemas import (
    CandidateDocumentDecisionIn,
    CandidateDocumentDecisionOut,
    CandidateDocumentOut,
    CandidateResearchImportIn,
    CandidateResearchImportOut,
    CompanyDetail,
    CompanyListItem,
    CompanySearchResult,
    IdentityResolutionIn,
    IdentityResolutionOut,
    IngestResult,
    RefreshResult,
    ReviewDecisionIn,
    ReviewOut,
    ReviewWorkbenchOut,
    SharingActionOut,
    SharingCandidateOut,
    SharingPromotionIn,
    SharingRejectionIn,
    SharingRetractionIn,
    SourceCheckBatchRequest,
    SourceCheckRequest,
    SourceCheckRunOut,
    TrustedSourceCreate,
    TrustedSourceOut,
    TrustedSourceUpdate,
)
from backend.app.services import (
    AccessDeniedError,
    NotFoundError,
    PromotionEligibilityError,
    decide_review,
    get_company_detail,
    ingest_mock_records,
    list_companies,
    list_review_workbench,
    list_sharing_candidates,
    promote_private_event,
    reject_private_event,
    request_refresh,
    resolve_identity_review,
    retract_shared_event,
    search_companies,
    user_has_role,
)
from backend.app.source_monitoring import (
    SourceMonitoringAccessError,
    SourceMonitoringConflictError,
    SourceMonitoringNotFoundError,
    SourceMonitoringValidationError,
    create_trusted_source,
    decide_candidate_document,
    import_candidate_research,
    list_candidate_documents,
    list_source_check_runs,
    list_trusted_sources,
    queue_company_source_checks,
    queue_source_check,
    queue_source_check_batch,
    update_trusted_source,
)


def get_session(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_current_user(
    x_demo_user_id: str | None = Header(default=None, alias="X-Demo-User-Id"),
    session: Session = Depends(get_session),
) -> User:
    if x_demo_user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
    try:
        user_id = UUID(x_demo_user_id)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized"
        ) from error
    user = session.get(User, user_id)
    if user is None or user.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
    set_request_context(session, user.id, user.tenant_id)
    return user


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_env()
    app = FastAPI(title="Dealflow Radar", version="0.1.0")
    engine = build_engine(resolved.database_url)
    app.state.settings = resolved
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": resolved.app_mode}

    @app.get("/api/v1/companies", response_model=list[CompanyListItem])
    def companies(
        user: User = Depends(get_current_user), session: Session = Depends(get_session)
    ) -> list[CompanyListItem]:
        return list_companies(session, user.id, app.state.settings.refresh_policy)

    @app.get("/api/v1/companies/search", response_model=list[CompanySearchResult])
    def company_search(
        q: str = Query(min_length=1, max_length=240),
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> list[CompanySearchResult]:
        return search_companies(session, user, q, app.state.settings.refresh_policy)

    @app.get("/api/v1/companies/{company_id}", response_model=CompanyDetail)
    def company_detail(
        company_id: UUID,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> CompanyDetail:
        try:
            settings = app.state.settings
            return get_company_detail(
                session,
                user,
                company_id,
                settings.refresh_policy,
                auto_refresh_enabled=settings.auto_refresh_enabled,
            )
        except NotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/api/v1/demo/ingest", response_model=IngestResult)
    def demo_ingest(
        user: User = Depends(get_current_user), session: Session = Depends(get_session)
    ) -> IngestResult:
        if not user_has_role(session, user.id, "institution_admin"):
            raise HTTPException(status_code=403, detail="forbidden_scope")
        return ingest_mock_records(session, MockResearchProvider())

    @app.get("/api/v1/reviews", response_model=list[ReviewOut])
    def reviews(
        user: User = Depends(get_current_user), session: Session = Depends(get_session)
    ) -> list[ReviewQueue]:
        if not user_has_role(session, user.id, "reviewer"):
            raise HTTPException(status_code=403, detail="forbidden_scope")
        return list(
            session.scalars(
                select(ReviewQueue)
                .where(ReviewQueue.tenant_id == user.tenant_id)
                .order_by(ReviewQueue.created_at)
            )
        )

    @app.get("/api/v1/reviews/workbench", response_model=list[ReviewWorkbenchOut])
    def review_workbench(
        user: User = Depends(get_current_user), session: Session = Depends(get_session)
    ) -> list[ReviewWorkbenchOut]:
        if not app.state.settings.review_workbench_enabled:
            raise HTTPException(status_code=404, detail="review_workbench_disabled")
        try:
            return list_review_workbench(session, user, app.state.settings.identity_policy)
        except AccessDeniedError as error:
            raise HTTPException(status_code=403, detail="forbidden_scope") from error

    @app.post("/api/v1/reviews/{review_id}/decision", response_model=ReviewOut)
    def review_decision(
        review_id: UUID,
        payload: ReviewDecisionIn,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> ReviewQueue:
        try:
            return decide_review(session, review_id, user, payload.decision, payload.reason)
        except NotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except AccessDeniedError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error

    @app.post(
        "/api/v1/reviews/{review_id}/identity-resolution",
        response_model=IdentityResolutionOut,
    )
    def identity_resolution(
        review_id: UUID,
        payload: IdentityResolutionIn,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> IdentityResolutionOut:
        if not app.state.settings.review_workbench_enabled:
            raise HTTPException(status_code=404, detail="review_workbench_disabled")
        try:
            return resolve_identity_review(
                session,
                review_id,
                user,
                payload.verification_id,
                payload.reason,
                app.state.settings.identity_policy,
                app.state.settings.publication_policy,
            )
        except NotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except AccessDeniedError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error

    @app.get("/api/v1/sharing-candidates", response_model=list[SharingCandidateOut])
    def sharing_candidates(
        user: User = Depends(get_current_user), session: Session = Depends(get_session)
    ) -> list[SharingCandidateOut]:
        if not app.state.settings.review_workbench_enabled:
            raise HTTPException(status_code=404, detail="review_workbench_disabled")
        try:
            return list_sharing_candidates(session, user)
        except AccessDeniedError as error:
            raise HTTPException(status_code=403, detail="forbidden_scope") from error

    @app.post(
        "/api/v1/events/{source_event_id}/sharing/promotion",
        response_model=SharingActionOut,
    )
    def sharing_promotion(
        source_event_id: UUID,
        payload: SharingPromotionIn,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> SharingActionOut:
        if not app.state.settings.review_workbench_enabled:
            raise HTTPException(status_code=404, detail="review_workbench_disabled")
        try:
            return promote_private_event(
                session,
                source_event_id,
                user,
                title=payload.title,
                summary=payload.summary,
                reason=payload.reason,
                evidence_ids=payload.evidence_ids,
                confirm_evidence_support=payload.confirm_evidence_support,
                confirm_unchecked_links=payload.confirm_unchecked_links,
                auto_publish_enabled=app.state.settings.publication_policy.enabled,
            )
        except NotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except AccessDeniedError as error:
            raise HTTPException(status_code=403, detail="forbidden_scope") from error
        except PromotionEligibilityError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post(
        "/api/v1/events/{source_event_id}/sharing/rejection",
        response_model=SharingActionOut,
    )
    def sharing_rejection(
        source_event_id: UUID,
        payload: SharingRejectionIn,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> SharingActionOut:
        if not app.state.settings.review_workbench_enabled:
            raise HTTPException(status_code=404, detail="review_workbench_disabled")
        try:
            return reject_private_event(session, source_event_id, user, payload.reason)
        except NotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except AccessDeniedError as error:
            raise HTTPException(status_code=403, detail="forbidden_scope") from error
        except PromotionEligibilityError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post(
        "/api/v1/shared-events/{shared_event_id}/retraction",
        response_model=SharingActionOut,
    )
    def sharing_retraction(
        shared_event_id: UUID,
        payload: SharingRetractionIn,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> SharingActionOut:
        if not app.state.settings.review_workbench_enabled:
            raise HTTPException(status_code=404, detail="review_workbench_disabled")
        try:
            return retract_shared_event(session, shared_event_id, user, payload.reason)
        except NotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except AccessDeniedError as error:
            raise HTTPException(status_code=403, detail="forbidden_scope") from error
        except PromotionEligibilityError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/v1/companies/{company_id}/refresh", response_model=RefreshResult)
    def refresh_company(
        company_id: UUID,
        dry_run: bool = Query(default=True),
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> RefreshResult:
        try:
            return request_refresh(
                session,
                user,
                company_id,
                app.state.settings.refresh_policy,
                dry_run=dry_run,
            )
        except NotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/api/v1/trusted-sources", response_model=TrustedSourceOut)
    def trusted_source_create(
        payload: TrustedSourceCreate,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> TrustedSourceOut:
        try:
            return create_trusted_source(session, user, payload)
        except SourceMonitoringAccessError as error:
            raise HTTPException(status_code=403, detail="forbidden_scope") from error
        except SourceMonitoringNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except SourceMonitoringConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except SourceMonitoringValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/v1/trusted-sources", response_model=list[TrustedSourceOut])
    def trusted_source_list(
        company_id: UUID | None = Query(default=None),
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> list[TrustedSourceOut]:
        try:
            return list_trusted_sources(session, user, company_id)
        except SourceMonitoringAccessError as error:
            raise HTTPException(status_code=403, detail="forbidden_scope") from error

    @app.patch("/api/v1/trusted-sources/{source_id}", response_model=TrustedSourceOut)
    def trusted_source_update(
        source_id: UUID,
        payload: TrustedSourceUpdate,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> TrustedSourceOut:
        try:
            return update_trusted_source(session, user, source_id, payload)
        except SourceMonitoringAccessError as error:
            raise HTTPException(status_code=403, detail="forbidden_scope") from error
        except SourceMonitoringNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except SourceMonitoringValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post(
        "/api/v1/trusted-sources/{source_id}/runs",
        response_model=SourceCheckRunOut,
    )
    def trusted_source_run_create(
        source_id: UUID,
        payload: SourceCheckRequest,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> SourceCheckRunOut:
        try:
            return queue_source_check(
                session,
                user,
                source_id,
                app.state.settings.source_monitoring_policy,
                dry_run=payload.dry_run,
            )
        except SourceMonitoringAccessError as error:
            raise HTTPException(status_code=403, detail="forbidden_scope") from error
        except SourceMonitoringNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except SourceMonitoringValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post(
        "/api/v1/companies/{company_id}/trusted-source-runs",
        response_model=list[SourceCheckRunOut],
    )
    def company_trusted_source_runs(
        company_id: UUID,
        payload: SourceCheckRequest,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> list[SourceCheckRunOut]:
        try:
            return queue_company_source_checks(
                session,
                user,
                company_id,
                app.state.settings.source_monitoring_policy,
                dry_run=payload.dry_run,
            )
        except SourceMonitoringAccessError as error:
            raise HTTPException(status_code=403, detail="forbidden_scope") from error
        except SourceMonitoringNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post(
        "/api/v1/trusted-source-runs/batch",
        response_model=list[SourceCheckRunOut],
    )
    def trusted_source_run_batch(
        payload: SourceCheckBatchRequest,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> list[SourceCheckRunOut]:
        try:
            return queue_source_check_batch(
                session,
                user,
                payload.source_ids,
                app.state.settings.source_monitoring_policy,
                dry_run=payload.dry_run,
            )
        except SourceMonitoringAccessError as error:
            raise HTTPException(status_code=403, detail="forbidden_scope") from error
        except SourceMonitoringNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except SourceMonitoringValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/api/v1/trusted-source-runs", response_model=list[SourceCheckRunOut])
    def trusted_source_run_list(
        company_id: UUID | None = Query(default=None),
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> list[SourceCheckRunOut]:
        try:
            return list_source_check_runs(session, user, company_id)
        except SourceMonitoringAccessError as error:
            raise HTTPException(status_code=403, detail="forbidden_scope") from error

    @app.get("/api/v1/candidate-documents", response_model=list[CandidateDocumentOut])
    def candidate_document_list(
        company_id: UUID | None = Query(default=None),
        processing_status: str | None = Query(default=None, max_length=32),
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> list[CandidateDocumentOut]:
        try:
            return list_candidate_documents(
                session,
                user,
                company_id=company_id,
                processing_status=processing_status,
            )
        except SourceMonitoringAccessError as error:
            raise HTTPException(status_code=403, detail="forbidden_scope") from error
        except SourceMonitoringValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post(
        "/api/v1/candidate-documents/{candidate_id}/decision",
        response_model=CandidateDocumentDecisionOut,
    )
    def candidate_document_decision(
        candidate_id: UUID,
        payload: CandidateDocumentDecisionIn,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> CandidateDocumentDecisionOut:
        try:
            return decide_candidate_document(
                session,
                user,
                candidate_id,
                payload.decision,
                payload.reason,
            )
        except SourceMonitoringAccessError as error:
            raise HTTPException(status_code=403, detail="forbidden_scope") from error
        except SourceMonitoringNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except SourceMonitoringConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post(
        "/api/v1/candidate-documents/{candidate_id}/research-import",
        response_model=CandidateResearchImportOut,
    )
    def candidate_document_research_import(
        candidate_id: UUID,
        payload: CandidateResearchImportIn,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> CandidateResearchImportOut:
        try:
            return import_candidate_research(
                session,
                user,
                candidate_id,
                payload,
                app.state.settings,
            )
        except SourceMonitoringAccessError as error:
            raise HTTPException(status_code=403, detail="forbidden_scope") from error
        except SourceMonitoringNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except SourceMonitoringConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except SourceMonitoringValidationError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    return app


app = create_app()
