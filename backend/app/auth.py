from __future__ import annotations

import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.config import CloudBaseAuthPolicy
from backend.app.database import set_request_context
from backend.app.models import AuthenticationAuditLog, Tenant, User

logger = logging.getLogger(__name__)


class InvalidAccessTokenError(Exception):
    pass


class InvalidAuthenticationFlowError(Exception):
    pass


class AuthenticationProviderUnavailableError(Exception):
    pass


class AuthenticationRateLimitedError(Exception):
    pass


class AuthenticationChallengeRequiredError(Exception):
    pass


class InvitationRequiredError(Exception):
    pass


@dataclass(frozen=True)
class VerifiedIdentity:
    provider: str
    subject: str
    email: str


@dataclass(frozen=True)
class VerificationChallenge:
    verification_id: str
    expires_in: int


@dataclass(frozen=True)
class AuthTokenSet:
    access_token: str
    refresh_token: str
    expires_in: int


class IdentityProvider(Protocol):
    def send_email_code(self, email: str) -> VerificationChallenge: ...

    def sign_in_with_email_code(
        self, verification_id: str, verification_code: str
    ) -> AuthTokenSet: ...

    def refresh_tokens(self, refresh_token: str) -> AuthTokenSet: ...

    def verify_access_token(self, access_token: str) -> VerifiedIdentity: ...

    def sign_out(self, access_token: str) -> None: ...


class CloudBaseIdentityProvider:
    def __init__(
        self,
        policy: CloudBaseAuthPolicy,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not policy.env_id:
            raise ValueError("CLOUDBASE_ENV_ID is required")
        self._policy = policy
        self._base_url = f"https://{policy.env_id}.api.tcloudbasegateway.com"
        self._transport = transport

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        access_token: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": self._policy.user_agent,
        }
        if access_token is not None:
            if not 8 <= len(access_token) <= 8192:
                raise InvalidAccessTokenError
            headers["Authorization"] = f"Bearer {access_token}"
        try:
            with httpx.Client(
                timeout=self._policy.timeout_seconds,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                with client.stream(
                    method,
                    f"{self._base_url}{path}",
                    headers=headers,
                    json=payload,
                ) as response:
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > self._policy.max_response_bytes:
                            raise AuthenticationProviderUnavailableError
                    status_code = response.status_code
                    request_id = (response.headers.get("X-Request-Id") or "")[:128]
        except AuthenticationProviderUnavailableError:
            raise
        except httpx.HTTPError as error:
            raise AuthenticationProviderUnavailableError from error

        try:
            data = json.loads(content) if content else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AuthenticationProviderUnavailableError from error
        if not isinstance(data, dict):
            raise AuthenticationProviderUnavailableError
        if status_code == 200:
            return data

        error_code = str(data.get("error") or "")
        logger.warning(
            "CloudBase authentication request rejected path=%s status=%s error=%s "
            "error_code=%s request_id=%s",
            path,
            status_code,
            error_code[:64],
            str(data.get("error_code") or "")[:32],
            request_id,
        )
        if error_code == "rate_limit_exceeded" or status_code == 429:
            raise AuthenticationRateLimitedError
        if error_code == "captcha_required":
            raise AuthenticationChallengeRequiredError
        if 400 <= status_code < 500:
            raise InvalidAuthenticationFlowError
        raise AuthenticationProviderUnavailableError

    def _client_id_payload(self) -> dict[str, object]:
        if not self._policy.client_id:
            return {}
        return {"client_id": self._policy.client_id}

    def send_email_code(self, email: str) -> VerificationChallenge:
        data = self._request_json(
            "POST",
            "/auth/v1/verification",
            payload={"email": email, "target": "USER"},
        )
        verification_id = data.get("verification_id")
        expires_in = data.get("expires_in")
        if not isinstance(verification_id, str) or not 8 <= len(verification_id) <= 2000:
            raise AuthenticationProviderUnavailableError
        if not isinstance(expires_in, int) or not 1 <= expires_in <= 3600:
            raise AuthenticationProviderUnavailableError
        return VerificationChallenge(verification_id=verification_id, expires_in=expires_in)

    def sign_in_with_email_code(self, verification_id: str, verification_code: str) -> AuthTokenSet:
        verification = self._request_json(
            "POST",
            "/auth/v1/verification/verify",
            payload={
                **self._client_id_payload(),
                "verification_id": verification_id,
                "verification_code": verification_code,
            },
        )
        verification_token = verification.get("verification_token")
        if not isinstance(verification_token, str) or not 8 <= len(verification_token) <= 8192:
            raise AuthenticationProviderUnavailableError
        data = self._request_json(
            "POST",
            "/auth/v1/signin",
            payload={
                **self._client_id_payload(),
                "verification_token": verification_token,
            },
        )
        return self._parse_token_set(data)

    def refresh_tokens(self, refresh_token: str) -> AuthTokenSet:
        if not 8 <= len(refresh_token) <= 8192:
            raise InvalidAuthenticationFlowError
        data = self._request_json(
            "POST",
            "/auth/v1/token",
            payload={
                **self._client_id_payload(),
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        return self._parse_token_set(data)

    @staticmethod
    def _parse_token_set(data: dict[str, Any]) -> AuthTokenSet:
        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        expires_in = data.get("expires_in")
        if (
            not isinstance(access_token, str)
            or not 8 <= len(access_token) <= 8192
            or not isinstance(refresh_token, str)
            or not 8 <= len(refresh_token) <= 8192
            or not isinstance(expires_in, int)
            or not 1 <= expires_in <= 86_400
        ):
            raise AuthenticationProviderUnavailableError
        return AuthTokenSet(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
        )

    def verify_access_token(self, access_token: str) -> VerifiedIdentity:
        try:
            data = self._request_json(
                "GET",
                "/auth/v1/user/me",
                access_token=access_token,
            )
        except InvalidAuthenticationFlowError as error:
            raise InvalidAccessTokenError from error
        subject = data.get("sub")
        email = data.get("email")
        status = data.get("status")
        if (
            not isinstance(subject, str)
            or not 1 <= len(subject) <= 255
            or not isinstance(email, str)
            or not 3 <= len(email) <= 320
        ):
            logger.warning(
                "CloudBase authentication profile was malformed has_subject=%s "
                "has_email=%s status=%s",
                isinstance(subject, str),
                isinstance(email, str),
                str(status)[:32],
            )
            raise AuthenticationProviderUnavailableError
        if not isinstance(status, str):
            logger.warning("CloudBase authentication profile omitted account status")
            raise AuthenticationProviderUnavailableError
        if status.upper() != "ACTIVE":
            logger.warning(
                "CloudBase authentication profile rejected inactive account status=%s",
                status[:32],
            )
            raise InvalidAccessTokenError
        try:
            normalized_email = normalize_email(email)
        except InvalidAuthenticationFlowError as error:
            logger.warning("CloudBase authentication profile contained a malformed email")
            raise AuthenticationProviderUnavailableError from error
        return VerifiedIdentity(
            provider="cloudbase",
            subject=subject,
            email=normalized_email,
        )

    def sign_out(self, access_token: str) -> None:
        try:
            self._request_json(
                "POST",
                "/auth/v1/user/signout",
                access_token=access_token,
                payload=self._client_id_payload(),
            )
        except InvalidAuthenticationFlowError:
            return


def normalize_email(value: str) -> str:
    normalized = value.strip().casefold()
    if (
        not 3 <= len(normalized) <= 320
        or normalized.count("@") != 1
        or any(character.isspace() for character in normalized)
    ):
        raise InvalidAuthenticationFlowError
    local_part, domain = normalized.rsplit("@", 1)
    if not local_part or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise InvalidAuthenticationFlowError
    return normalized


def subject_hash(provider: str, subject: str) -> str:
    return hashlib.sha256(f"{provider}:{subject}".encode()).hexdigest()


def record_authentication_event(
    session: Session,
    user: User,
    identity: VerifiedIdentity,
    event_type: str,
    *,
    outcome: str = "succeeded",
    reason_code: str | None = None,
) -> None:
    session.add(
        AuthenticationAuditLog(
            tenant_id=user.tenant_id,
            user_id=user.id,
            provider=identity.provider,
            subject_hash=subject_hash(identity.provider, identity.subject),
            event_type=event_type,
            outcome=outcome,
            reason_code=reason_code,
        )
    )


def resolve_local_user(
    session: Session,
    identity: VerifiedIdentity,
    *,
    allow_email_link: bool = False,
) -> User:
    user = session.scalar(
        select(User)
        .join(Tenant, Tenant.id == User.tenant_id)
        .where(
            User.auth_provider == identity.provider,
            User.auth_subject == identity.subject,
            User.status == "active",
            Tenant.status == "active",
        )
    )
    if user is not None:
        return user

    if not allow_email_link:
        raise InvitationRequiredError

    candidates = list(
        session.scalars(
            select(User)
            .join(Tenant, Tenant.id == User.tenant_id)
            .where(
                func.lower(User.email) == identity.email,
                User.status == "active",
                Tenant.status == "active",
                User.auth_provider.is_(None),
                User.auth_subject.is_(None),
            )
        )
    )
    if len(candidates) != 1:
        raise InvitationRequiredError
    user = candidates[0]
    user.auth_provider = identity.provider
    user.auth_subject = identity.subject
    try:
        session.flush()
        set_request_context(session, user.id, user.tenant_id)
        record_authentication_event(session, user, identity, "identity_linked")
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise InvitationRequiredError from error
    return user


def dummy_verification_challenge() -> VerificationChallenge:
    return VerificationChallenge(
        verification_id=secrets.token_urlsafe(32),
        expires_in=600,
    )
