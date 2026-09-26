from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.app.auth import (
    AuthenticationChallengeRequiredError,
    AuthenticationProviderUnavailableError,
    AuthenticationRateLimitedError,
    AuthTokenSet,
    CloudBaseIdentityProvider,
    IdentityProvider,
    InvalidAccessTokenError,
    InvalidAuthenticationFlowError,
    InvitationRequiredError,
    PhoneVerificationRateLimiter,
    VerifiedIdentity,
    dummy_verification_challenge,
    normalize_email,
    normalize_mainland_phone,
    record_authentication_event,
    resolve_local_user,
)
from backend.app.config import Settings
from backend.app.database import build_engine, build_session_factory, set_request_context
from backend.app.models import ReviewQueue, Tenant, User, utc_now
from backend.app.personal_features import (
    PersonalFeatureAccessError,
    PersonalFeatureLimitError,
    PersonalFeatureNotFoundError,
    PersonalRequestConflictError,
    PersonalRequestTransitionError,
    add_personal_watchlist_item,
    approve_platform_company_request_for_research,
    cancel_personal_company_request,
    create_inclusion_request,
    create_personal_company_report,
    create_quota_increase_request,
    create_refresh_request,
    decide_platform_company_request,
    decide_platform_quota_increase_request,
    ensure_company_search_available,
    get_personal_company_report,
    get_personal_usage_summary,
    list_personal_company_reports,
    list_personal_company_requests,
    list_personal_quota_increase_requests,
    list_personal_watchlist,
    list_platform_company_requests,
    list_platform_quota_increase_requests,
    record_company_search,
    record_personal_company_view,
    remove_personal_watchlist_item,
)
from backend.app.providers import MockResearchProvider
from backend.app.schemas import (
    AuthEmailLoginIn,
    AuthEmailVerificationIn,
    AuthEmailVerificationOut,
    AuthMeOut,
    AuthPhoneLoginIn,
    AuthPhoneVerificationIn,
    AuthTokenOut,
    AuthTokenRefreshIn,
    CandidateDocumentDecisionIn,
    CandidateDocumentDecisionOut,
    CandidateDocumentOut,
    CandidateResearchImportIn,
    CandidateResearchImportOut,
    CompanyDetail,
    CompanyListItem,
    CompanySearchResult,
    CompanySuggestion,
    EvidenceDetailOut,
    IdentityResolutionIn,
    IdentityResolutionOut,
    IngestResult,
    PersonalCompanyReportOut,
    PersonalCompanyReportSummaryOut,
    PersonalCompanyRequestCancelIn,
    PersonalCompanyRequestDecisionIn,
    PersonalCompanyRequestOut,
    PersonalCompanyRequestResearchApprovalIn,
    PersonalCompanyViewIn,
    PersonalCompanyViewOut,
    PersonalInclusionRequestIn,
    PersonalQuotaIncreaseDecisionIn,
    PersonalQuotaIncreaseRequestIn,
    PersonalQuotaIncreaseRequestOut,
    PersonalReportCreateIn,
    PersonalUsageSummaryOut,
    PersonalWatchlistItemOut,
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
    get_evidence_detail,
    ingest_mock_records,
    list_companies,
    list_review_workbench,
    list_sharing_candidates,
    list_user_role_codes,
    promote_private_event,
    reject_private_event,
    request_refresh,
    resolve_identity_review,
    retract_shared_event,
    search_companies,
    suggest_companies,
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
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_demo_user_id: str | None = Header(default=None, alias="X-Demo-User-Id"),
    session: Session = Depends(get_session),
) -> User:
    settings: Settings = request.app.state.settings
    if settings.auth_provider == "demo":
        if x_demo_user_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
        try:
            user_id = UUID(x_demo_user_id)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized"
            ) from error
        user = session.get(User, user_id)
        tenant = session.get(Tenant, user.tenant_id) if user is not None else None
        if user is None or user.status != "active" or tenant is None or tenant.status != "active":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
    else:
        access_token = _bearer_token(authorization)
        provider: IdentityProvider = request.app.state.identity_provider
        try:
            identity = provider.verify_access_token(access_token)
        except (InvalidAccessTokenError, InvalidAuthenticationFlowError) as error:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized"
            ) from error
        except (
            AuthenticationProviderUnavailableError,
            AuthenticationRateLimitedError,
            AuthenticationChallengeRequiredError,
        ) as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="authentication_unavailable",
            ) from error
        try:
            user = resolve_local_user(session, identity)
        except InvitationRequiredError as error:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="invitation_required",
            ) from error
    set_request_context(session, user.id, user.tenant_id)
    return user


def _bearer_token(authorization: str | None) -> str:
    if authorization is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.casefold() != "bearer" or not 8 <= len(token) <= 8192:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")
    return token


def create_app(
    settings: Settings | None = None,
    identity_provider: IdentityProvider | None = None,
    phone_verification_limiter: PhoneVerificationRateLimiter | None = None,
) -> FastAPI:
    resolved = settings or Settings.from_env()
    app = FastAPI(title="Dealflow Radar", version="0.1.0")
    engine = build_engine(resolved.database_url)
    app.state.settings = resolved
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)
    app.state.identity_provider = identity_provider
    app.state.phone_verification_limiter = phone_verification_limiter or (
        PhoneVerificationRateLimiter(
            cooldown_seconds=resolved.cloudbase_auth_policy.phone_code_cooldown_seconds,
            daily_limit=resolved.cloudbase_auth_policy.phone_daily_limit,
            environment_daily_limit=(resolved.cloudbase_auth_policy.phone_environment_daily_limit),
        )
    )
    if resolved.auth_provider == "cloudbase" and identity_provider is None:
        app.state.identity_provider = CloudBaseIdentityProvider(resolved.cloudbase_auth_policy)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": resolved.app_mode}

    @app.get("/ready")
    def readiness() -> dict[str, str]:
        try:
            with app.state.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError as error:
            raise HTTPException(status_code=503, detail="database_unavailable") from error
        return {"status": "ready", "database": "reachable"}

    def require_cloudbase_provider() -> IdentityProvider:
        if resolved.auth_provider != "cloudbase" or app.state.identity_provider is None:
            raise HTTPException(status_code=404, detail="cloudbase_auth_disabled")
        return app.state.identity_provider

    def complete_provider_login(
        provider: IdentityProvider,
        token_set: AuthTokenSet,
        session: Session,
        *,
        event_type: str,
        allow_email_link: bool,
    ) -> AuthTokenOut:
        access_token = token_set.access_token
        try:
            identity = provider.verify_access_token(access_token)
            user = resolve_local_user(
                session,
                identity,
                allow_email_link=allow_email_link,
            )
        except InvitationRequiredError as error:
            try:
                provider.sign_out(access_token)
            except (
                AuthenticationProviderUnavailableError,
                AuthenticationRateLimitedError,
                AuthenticationChallengeRequiredError,
            ):
                pass
            raise HTTPException(status_code=403, detail="invitation_required") from error
        except (InvalidAccessTokenError, InvalidAuthenticationFlowError) as error:
            raise HTTPException(status_code=401, detail="invalid_authentication") from error
        set_request_context(session, user.id, user.tenant_id)
        if event_type == "session_started":
            user.last_login_at = utc_now()
        record_authentication_event(session, user, identity, event_type)
        session.commit()
        return AuthTokenOut(
            access_token=token_set.access_token,
            refresh_token=token_set.refresh_token,
            expires_in=token_set.expires_in,
        )

    @app.post(
        "/api/v1/auth/email/verification",
        response_model=AuthEmailVerificationOut,
    )
    def request_email_verification(
        payload: AuthEmailVerificationIn,
    ) -> AuthEmailVerificationOut:
        provider = require_cloudbase_provider()
        try:
            challenge = provider.send_email_code(normalize_email(payload.email))
        except InvalidAuthenticationFlowError:
            challenge = dummy_verification_challenge()
        except AuthenticationRateLimitedError as error:
            raise HTTPException(status_code=429, detail="authentication_rate_limited") from error
        except AuthenticationChallengeRequiredError as error:
            raise HTTPException(
                status_code=409, detail="authentication_challenge_required"
            ) from error
        except AuthenticationProviderUnavailableError as error:
            raise HTTPException(status_code=503, detail="authentication_unavailable") from error
        return AuthEmailVerificationOut(
            verification_id=challenge.verification_id,
            expires_in=challenge.expires_in,
        )

    @app.post("/api/v1/auth/email/login", response_model=AuthTokenOut)
    def email_login(
        payload: AuthEmailLoginIn,
        session: Session = Depends(get_session),
    ) -> AuthTokenOut:
        provider = require_cloudbase_provider()
        try:
            token_set = provider.sign_in_with_email_code(
                payload.verification_id,
                payload.verification_code,
            )
            return complete_provider_login(
                provider,
                token_set,
                session,
                event_type="session_started",
                allow_email_link=True,
            )
        except HTTPException:
            raise
        except (InvalidAuthenticationFlowError, InvalidAccessTokenError) as error:
            raise HTTPException(status_code=401, detail="invalid_authentication") from error
        except AuthenticationRateLimitedError as error:
            raise HTTPException(status_code=429, detail="authentication_rate_limited") from error
        except AuthenticationChallengeRequiredError as error:
            raise HTTPException(
                status_code=409, detail="authentication_challenge_required"
            ) from error
        except AuthenticationProviderUnavailableError as error:
            raise HTTPException(status_code=503, detail="authentication_unavailable") from error

    @app.post(
        "/api/v1/auth/phone/verification",
        response_model=AuthEmailVerificationOut,
    )
    def request_phone_verification(
        payload: AuthPhoneVerificationIn,
    ) -> AuthEmailVerificationOut:
        if not resolved.cloudbase_auth_policy.phone_login_enabled:
            raise HTTPException(status_code=404, detail="phone_auth_disabled")
        provider = require_cloudbase_provider()
        try:
            phone_number = normalize_mainland_phone(payload.phone_number)
        except InvalidAuthenticationFlowError:
            challenge = dummy_verification_challenge()
        else:
            try:
                app.state.phone_verification_limiter.consume(phone_number)
                challenge = provider.send_phone_code(phone_number)
            except InvalidAuthenticationFlowError:
                challenge = dummy_verification_challenge()
            except AuthenticationRateLimitedError as error:
                raise HTTPException(
                    status_code=429, detail="authentication_rate_limited"
                ) from error
            except AuthenticationChallengeRequiredError as error:
                raise HTTPException(
                    status_code=409, detail="authentication_challenge_required"
                ) from error
            except AuthenticationProviderUnavailableError as error:
                raise HTTPException(status_code=503, detail="authentication_unavailable") from error
        return AuthEmailVerificationOut(
            verification_id=challenge.verification_id,
            expires_in=challenge.expires_in,
        )

    @app.post("/api/v1/auth/phone/login", response_model=AuthTokenOut)
    def phone_login(
        payload: AuthPhoneLoginIn,
        session: Session = Depends(get_session),
    ) -> AuthTokenOut:
        if not resolved.cloudbase_auth_policy.phone_login_enabled:
            raise HTTPException(status_code=404, detail="phone_auth_disabled")
        provider = require_cloudbase_provider()
        try:
            token_set = provider.sign_in_with_phone_code(
                payload.verification_id,
                payload.verification_code,
            )
            return complete_provider_login(
                provider,
                token_set,
                session,
                event_type="session_started",
                allow_email_link=False,
            )
        except HTTPException:
            raise
        except (InvalidAuthenticationFlowError, InvalidAccessTokenError) as error:
            raise HTTPException(status_code=401, detail="invalid_authentication") from error
        except AuthenticationRateLimitedError as error:
            raise HTTPException(status_code=429, detail="authentication_rate_limited") from error
        except AuthenticationChallengeRequiredError as error:
            raise HTTPException(
                status_code=409, detail="authentication_challenge_required"
            ) from error
        except AuthenticationProviderUnavailableError as error:
            raise HTTPException(status_code=503, detail="authentication_unavailable") from error

    @app.post("/api/v1/auth/token/refresh", response_model=AuthTokenOut)
    def refresh_authentication_token(
        payload: AuthTokenRefreshIn,
        session: Session = Depends(get_session),
    ) -> AuthTokenOut:
        provider = require_cloudbase_provider()
        try:
            token_set = provider.refresh_tokens(payload.refresh_token)
            return complete_provider_login(
                provider,
                token_set,
                session,
                event_type="session_refreshed",
                allow_email_link=False,
            )
        except HTTPException:
            raise
        except (InvalidAuthenticationFlowError, InvalidAccessTokenError) as error:
            raise HTTPException(status_code=401, detail="invalid_authentication") from error
        except AuthenticationRateLimitedError as error:
            raise HTTPException(status_code=429, detail="authentication_rate_limited") from error
        except AuthenticationChallengeRequiredError as error:
            raise HTTPException(
                status_code=409, detail="authentication_challenge_required"
            ) from error
        except AuthenticationProviderUnavailableError as error:
            raise HTTPException(status_code=503, detail="authentication_unavailable") from error

    @app.get("/api/v1/auth/me", response_model=AuthMeOut)
    def authentication_me(
        user: User = Depends(get_current_user), session: Session = Depends(get_session)
    ) -> AuthMeOut:
        return AuthMeOut(
            user_id=user.id,
            tenant_id=user.tenant_id,
            email=user.email,
            display_name=user.display_name,
            auth_provider=user.auth_provider or "demo",
            roles=list_user_role_codes(session, user.id),
        )

    @app.post("/api/v1/auth/logout", status_code=204)
    def authentication_logout(
        response: Response,
        authorization: str | None = Header(default=None, alias="Authorization"),
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> Response:
        provider = require_cloudbase_provider()
        access_token = _bearer_token(authorization)
        identity = VerifiedIdentity(
            provider=user.auth_provider or "cloudbase",
            subject=user.auth_subject or "unknown",
            email=user.email,
        )
        try:
            provider.sign_out(access_token)
        except (
            AuthenticationProviderUnavailableError,
            AuthenticationRateLimitedError,
            AuthenticationChallengeRequiredError,
        ) as error:
            record_authentication_event(
                session,
                user,
                identity,
                "session_ended",
                outcome="failed",
                reason_code="provider_unavailable",
            )
            session.commit()
            raise HTTPException(status_code=503, detail="authentication_unavailable") from error
        record_authentication_event(session, user, identity, "session_ended")
        session.commit()
        response.status_code = 204
        return response

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
        try:
            settings = app.state.settings
            record_company_search(session, user, settings.personal_entitlement_policy)
            results = search_companies(session, user, q, settings.refresh_policy)
            session.commit()
            return results
        except PersonalFeatureLimitError as error:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "personal_usage_limit_reached",
                    "feature": error.feature,
                    "limit": error.limit,
                },
            ) from error

    @app.get("/api/v1/companies/suggestions", response_model=list[CompanySuggestion])
    def company_suggestions(
        q: str = Query(min_length=2, max_length=240),
        limit: int = Query(default=8, ge=1, le=8),
        _user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> list[CompanySuggestion]:
        try:
            ensure_company_search_available(
                session,
                _user,
                app.state.settings.personal_entitlement_policy,
            )
            return suggest_companies(session, q, limit=limit)
        except PersonalFeatureLimitError as error:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "personal_usage_limit_reached",
                    "feature": error.feature,
                    "limit": error.limit,
                },
            ) from error

    @app.get("/api/v1/me/usage", response_model=PersonalUsageSummaryOut)
    def personal_usage(
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> PersonalUsageSummaryOut:
        return get_personal_usage_summary(
            session,
            user,
            app.state.settings.personal_entitlement_policy,
        )

    @app.get("/api/v1/me/watchlist", response_model=list[PersonalWatchlistItemOut])
    def personal_watchlist(
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> list[PersonalWatchlistItemOut]:
        return list_personal_watchlist(
            session,
            user,
            app.state.settings.refresh_policy,
            app.state.settings.web_research_policy.watchlist,
        )

    @app.post(
        "/api/v1/me/watchlist/{company_id}",
        response_model=PersonalWatchlistItemOut,
    )
    def personal_watchlist_add(
        company_id: UUID,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> PersonalWatchlistItemOut:
        try:
            settings = app.state.settings
            return add_personal_watchlist_item(
                session,
                user,
                company_id,
                settings.personal_entitlement_policy,
                settings.refresh_policy,
            )
        except PersonalFeatureNotFoundError as error:
            raise HTTPException(status_code=404, detail="company not found") from error
        except PersonalFeatureLimitError as error:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "personal_usage_limit_reached",
                    "feature": error.feature,
                    "limit": error.limit,
                },
            ) from error

    @app.delete("/api/v1/me/watchlist/{company_id}", status_code=204)
    def personal_watchlist_remove(
        company_id: UUID,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> Response:
        remove_personal_watchlist_item(session, user, company_id)
        return Response(status_code=204)

    @app.get(
        "/api/v1/me/company-requests",
        response_model=list[PersonalCompanyRequestOut],
    )
    def personal_company_requests(
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> list[PersonalCompanyRequestOut]:
        return list_personal_company_requests(session, user)

    @app.post(
        "/api/v1/me/company-requests/inclusion",
        response_model=PersonalCompanyRequestOut,
    )
    def personal_inclusion_request(
        payload: PersonalInclusionRequestIn,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> PersonalCompanyRequestOut:
        try:
            return create_inclusion_request(
                session,
                user,
                app.state.settings.personal_entitlement_policy,
                company_name=payload.company_name,
                credit_code=payload.credit_code,
            )
        except PersonalRequestConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except PersonalFeatureLimitError as error:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "personal_usage_limit_reached",
                    "feature": error.feature,
                    "limit": error.limit,
                },
            ) from error

    @app.post(
        "/api/v1/me/company-requests/refresh/{company_id}",
        response_model=PersonalCompanyRequestOut,
    )
    def personal_refresh_request(
        company_id: UUID,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> PersonalCompanyRequestOut:
        try:
            return create_refresh_request(
                session,
                user,
                app.state.settings.personal_entitlement_policy,
                company_id,
            )
        except PersonalFeatureNotFoundError as error:
            raise HTTPException(status_code=404, detail="company not found") from error
        except PersonalFeatureLimitError as error:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "personal_usage_limit_reached",
                    "feature": error.feature,
                    "limit": error.limit,
                },
            ) from error

    @app.post(
        "/api/v1/me/company-requests/{request_id}/cancel",
        response_model=PersonalCompanyRequestOut,
    )
    def personal_company_request_cancel(
        request_id: UUID,
        payload: PersonalCompanyRequestCancelIn,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> PersonalCompanyRequestOut:
        try:
            return cancel_personal_company_request(
                session,
                user,
                request_id,
                reason=payload.reason,
            )
        except PersonalFeatureNotFoundError as error:
            raise HTTPException(status_code=404, detail="request not found") from error
        except PersonalRequestTransitionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get(
        "/api/v1/me/quota-increase-requests",
        response_model=list[PersonalQuotaIncreaseRequestOut],
    )
    def personal_quota_increase_requests(
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> list[PersonalQuotaIncreaseRequestOut]:
        return list_personal_quota_increase_requests(session, user)

    @app.post(
        "/api/v1/me/quota-increase-requests",
        response_model=PersonalQuotaIncreaseRequestOut,
    )
    def personal_quota_increase_request_create(
        payload: PersonalQuotaIncreaseRequestIn,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> PersonalQuotaIncreaseRequestOut:
        return create_quota_increase_request(
            session,
            user,
            requested_daily_extra=payload.requested_daily_extra,
            requested_monthly_extra=payload.requested_monthly_extra,
            reason=payload.reason,
        )

    @app.get(
        "/api/v1/platform/company-requests",
        response_model=list[PersonalCompanyRequestOut],
    )
    def platform_company_requests(
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> list[PersonalCompanyRequestOut]:
        try:
            return list_platform_company_requests(session, user)
        except PersonalFeatureAccessError as error:
            raise HTTPException(status_code=403, detail="forbidden_scope") from error

    @app.patch(
        "/api/v1/platform/company-requests/{request_id}",
        response_model=PersonalCompanyRequestOut,
    )
    def platform_company_request_decision(
        request_id: UUID,
        payload: PersonalCompanyRequestDecisionIn,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> PersonalCompanyRequestOut:
        try:
            return decide_platform_company_request(
                session,
                user,
                request_id,
                status=payload.status,
                reason=payload.reason,
            )
        except PersonalFeatureAccessError as error:
            raise HTTPException(status_code=403, detail="forbidden_scope") from error
        except PersonalFeatureNotFoundError as error:
            raise HTTPException(status_code=404, detail="request not found") from error
        except PersonalRequestTransitionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post(
        "/api/v1/platform/company-requests/{request_id}/approve-research",
        response_model=PersonalCompanyRequestOut,
    )
    def platform_company_request_research_approval(
        request_id: UUID,
        payload: PersonalCompanyRequestResearchApprovalIn,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> PersonalCompanyRequestOut:
        try:
            return approve_platform_company_request_for_research(
                session,
                user,
                request_id,
                company_id=payload.company_id,
                reason=payload.reason,
            )
        except PersonalFeatureAccessError as error:
            raise HTTPException(status_code=403, detail="forbidden_scope") from error
        except PersonalFeatureNotFoundError as error:
            raise HTTPException(status_code=404, detail="request not found") from error
        except PersonalRequestTransitionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get(
        "/api/v1/platform/quota-increase-requests",
        response_model=list[PersonalQuotaIncreaseRequestOut],
    )
    def platform_quota_increase_requests(
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> list[PersonalQuotaIncreaseRequestOut]:
        try:
            return list_platform_quota_increase_requests(session, user)
        except PersonalFeatureAccessError as error:
            raise HTTPException(status_code=403, detail="forbidden_scope") from error

    @app.patch(
        "/api/v1/platform/quota-increase-requests/{request_id}",
        response_model=PersonalQuotaIncreaseRequestOut,
    )
    def platform_quota_increase_request_decision(
        request_id: UUID,
        payload: PersonalQuotaIncreaseDecisionIn,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> PersonalQuotaIncreaseRequestOut:
        try:
            return decide_platform_quota_increase_request(
                session,
                user,
                request_id,
                status=payload.status,
                approved_daily_extra=payload.approved_daily_extra,
                approved_monthly_extra=payload.approved_monthly_extra,
                effective_until=payload.effective_until,
                reason=payload.reason,
            )
        except PersonalFeatureAccessError as error:
            raise HTTPException(status_code=403, detail="forbidden_scope") from error
        except PersonalFeatureNotFoundError as error:
            raise HTTPException(status_code=404, detail="quota request not found") from error
        except PersonalRequestTransitionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post(
        "/api/v1/me/companies/{company_id}/view",
        response_model=PersonalCompanyViewOut,
    )
    def personal_company_view(
        company_id: UUID,
        payload: PersonalCompanyViewIn | None = None,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> PersonalCompanyViewOut:
        try:
            return record_personal_company_view(
                session,
                user,
                company_id,
                rendered_versions=payload.rendered_versions if payload else None,
            )
        except PersonalFeatureNotFoundError as error:
            raise HTTPException(status_code=404, detail="company not found") from error

    @app.post(
        "/api/v1/me/companies/{company_id}/reports",
        response_model=PersonalCompanyReportOut,
    )
    def personal_company_report_create(
        company_id: UUID,
        payload: PersonalReportCreateIn,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> PersonalCompanyReportOut:
        try:
            return create_personal_company_report(
                session,
                user,
                app.state.settings.personal_entitlement_policy,
                app.state.settings.refresh_policy,
                company_id,
                idempotency_key=payload.idempotency_key,
                archive_new_timepoint=payload.archive_new_timepoint,
            )
        except PersonalFeatureAccessError as error:
            raise HTTPException(status_code=403, detail="report_content_restricted") from error
        except PersonalFeatureNotFoundError as error:
            raise HTTPException(status_code=404, detail="company not found") from error
        except PersonalRequestConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except PersonalFeatureLimitError as error:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "personal_usage_limit_reached",
                    "feature": error.feature,
                    "limit": error.limit,
                },
            ) from error

    @app.get(
        "/api/v1/me/reports",
        response_model=list[PersonalCompanyReportSummaryOut],
    )
    def personal_company_reports(
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> list[PersonalCompanyReportSummaryOut]:
        return list_personal_company_reports(session, user)

    @app.get(
        "/api/v1/me/reports/{report_id}",
        response_model=PersonalCompanyReportOut,
    )
    def personal_company_report(
        report_id: UUID,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> PersonalCompanyReportOut:
        try:
            return get_personal_company_report(session, user, report_id)
        except PersonalFeatureNotFoundError as error:
            raise HTTPException(status_code=404, detail="report not found") from error

    @app.get("/api/v1/companies/{company_id}", response_model=CompanyDetail)
    def company_detail(
        company_id: UUID,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> CompanyDetail:
        try:
            settings = app.state.settings
            shared_research_refresh = (
                settings.web_research_policy.incremental_research_enabled
                and settings.web_research_policy.topic_planning_enabled
            )
            detail = get_company_detail(
                session,
                user,
                company_id,
                settings.refresh_policy,
                auto_refresh_enabled=settings.auto_refresh_enabled,
                shared_research_refresh=shared_research_refresh,
            )
            if detail.is_platform_shared and shared_research_refresh:
                from backend.app.company_refresh import schedule_stale_company

                detail.automatic_refresh = schedule_stale_company(session, user, detail, settings)
            return detail
        except NotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/api/v1/evidence/{evidence_id}", response_model=EvidenceDetailOut)
    def evidence_detail(
        evidence_id: UUID,
        user: User = Depends(get_current_user),
        session: Session = Depends(get_session),
    ) -> EvidenceDetailOut:
        try:
            return get_evidence_detail(session, user, evidence_id)
        except NotFoundError as error:
            raise HTTPException(status_code=404, detail="evidence detail not found") from error

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
                observation_id=payload.observation_id,
                tender_events_enabled=app.state.settings.tender_events_enabled,
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
