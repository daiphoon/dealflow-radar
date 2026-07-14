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
    CompanyDetail,
    CompanyListItem,
    IngestResult,
    RefreshResult,
    ReviewDecisionIn,
    ReviewOut,
)
from backend.app.services import (
    AccessDeniedError,
    NotFoundError,
    decide_review,
    get_company_detail,
    ingest_mock_records,
    list_companies,
    request_refresh,
    user_has_role,
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
        return list_companies(session, user.id)

    @app.get("/api/v1/companies/{company_id}", response_model=CompanyDetail)
    def company_detail(
        company_id: UUID,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> CompanyDetail:
        try:
            return get_company_detail(session, user.id, company_id)
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

    @app.post("/api/v1/companies/{company_id}/refresh", response_model=RefreshResult)
    def refresh_company(
        company_id: UUID,
        dry_run: bool = Query(default=True),
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> RefreshResult:
        try:
            return request_refresh(session, user, company_id, dry_run=dry_run)
        except NotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    return app


app = create_app()
