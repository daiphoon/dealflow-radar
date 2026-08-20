from __future__ import annotations

import httpx
import pytest

from backend.app.auth import (
    AuthenticationProviderUnavailableError,
    AuthenticationRateLimitedError,
    AuthTokenSet,
    CloudBaseIdentityProvider,
    InvalidAccessTokenError,
    InvalidAuthenticationFlowError,
    PhoneVerificationRateLimiter,
    normalize_mainland_phone,
)
from backend.app.config import CloudBaseAuthPolicy


def _provider(handler: httpx.MockTransport) -> CloudBaseIdentityProvider:
    return CloudBaseIdentityProvider(
        CloudBaseAuthPolicy(
            env_id="demo-env-123",
            client_id="demo-client-456",
            timeout_seconds=2,
            max_response_bytes=1024,
        ),
        transport=handler,
    )


def test_verifies_active_email_identity_against_fixed_gateway() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "sub": "cloudbase-subject-1",
                "email": "Invitee@Example.COM",
                "status": "ACTIVE",
                "providers": [
                    {
                        "id": "email",
                        "provider_user_id": "invitee@example.com",
                    }
                ],
                "groups": [{"id": "platform_admin"}],
            },
        )

    provider = _provider(httpx.MockTransport(handler))
    identity = provider.verify_access_token("valid-access-token")

    assert identity.subject == "cloudbase-subject-1"
    assert identity.email == "invitee@example.com"
    assert identity.provider == "cloudbase"
    assert requests[0].url == ("https://demo-env-123.api.tcloudbasegateway.com/auth/v1/user/me")
    assert requests[0].headers["authorization"] == "Bearer valid-access-token"


def test_accepts_native_email_account_without_provider_list() -> None:
    provider = _provider(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "sub": "native-email-subject",
                    "email": "Native.User@Example.COM",
                    "status": "ACTIVE",
                    "providers": {},
                },
            )
        )
    )

    identity = provider.verify_access_token("valid-access-token")

    assert identity.provider == "cloudbase"
    assert identity.subject == "native-email-subject"
    assert identity.email == "native.user@example.com"


def test_rejects_inactive_identity() -> None:
    payload = {
        "sub": "subject",
        "email": "invitee@example.com",
        "status": "SUSPENDED",
    }
    provider = _provider(httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))

    with pytest.raises(InvalidAccessTokenError):
        provider.verify_access_token("valid-access-token")


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "invitee@example.com", "status": "ACTIVE"},
        {"sub": "subject", "email": "not-an-email", "status": "ACTIVE"},
        {"sub": "subject", "email": "invitee@example.com"},
    ],
)
def test_treats_malformed_success_profile_as_provider_failure(
    payload: dict[str, object],
) -> None:
    provider = _provider(httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))

    with pytest.raises(AuthenticationProviderUnavailableError):
        provider.verify_access_token("valid-access-token")


def test_rejects_redirect_and_oversized_responses() -> None:
    redirecting = _provider(
        httpx.MockTransport(
            lambda _: httpx.Response(302, headers={"location": "https://evil.test"})
        )
    )
    with pytest.raises(AuthenticationProviderUnavailableError):
        redirecting.verify_access_token("valid-access-token")

    oversized = _provider(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200, content=b"x" * 1025, headers={"content-type": "text/plain"}
            )
        )
    )
    with pytest.raises(AuthenticationProviderUnavailableError):
        oversized.verify_access_token("valid-access-token")


def test_treats_non_rate_limit_client_errors_as_invalid_authentication() -> None:
    provider = _provider(
        httpx.MockTransport(lambda _: httpx.Response(404, json={"error": "user_not_found"}))
    )

    with pytest.raises(InvalidAuthenticationFlowError):
        provider.send_email_code("missing@example.com")


def test_email_code_login_refresh_and_logout_use_documented_routes() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/auth/v1/verification":
            assert request.read().decode() == '{"email":"invitee@example.com","target":"USER"}'
            return httpx.Response(
                200, json={"verification_id": "verification-1", "expires_in": 600}
            )
        if request.url.path == "/auth/v1/verification/verify":
            return httpx.Response(
                200,
                json={"verification_token": "verification-token-1", "expires_in": 600},
            )
        if request.url.path == "/auth/v1/signin":
            return httpx.Response(
                200,
                json={
                    "token_type": "Bearer",
                    "access_token": "access-token-1",
                    "refresh_token": "refresh-token-1",
                    "expires_in": 7200,
                },
            )
        if request.url.path == "/auth/v1/token":
            return httpx.Response(
                200,
                json={
                    "token_type": "Bearer",
                    "access_token": "access-token-2",
                    "refresh_token": "refresh-token-2",
                    "expires_in": 7200,
                },
            )
        if request.url.path == "/auth/v1/user/signout":
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected request: {request.url}")

    provider = _provider(httpx.MockTransport(handler))
    challenge = provider.send_email_code("invitee@example.com")
    signed_in = provider.sign_in_with_email_code(challenge.verification_id, "123456")
    refreshed = provider.refresh_tokens(signed_in.refresh_token)
    provider.sign_out(refreshed.access_token)

    assert signed_in == AuthTokenSet("access-token-1", "refresh-token-1", 7200)
    assert refreshed == AuthTokenSet("access-token-2", "refresh-token-2", 7200)
    assert paths == [
        "/auth/v1/verification",
        "/auth/v1/verification/verify",
        "/auth/v1/signin",
        "/auth/v1/token",
        "/auth/v1/user/signout",
    ]


def test_phone_code_login_uses_existing_user_only_and_documented_routes() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/auth/v1/verification":
            return httpx.Response(
                200, json={"verification_id": "phone-verification-1", "expires_in": 600}
            )
        if request.url.path == "/auth/v1/verification/verify":
            return httpx.Response(200, json={"verification_token": "phone-token-1"})
        if request.url.path == "/auth/v1/signin":
            return httpx.Response(
                200,
                json={
                    "access_token": "phone-access-token",
                    "refresh_token": "phone-refresh-token",
                    "expires_in": 7200,
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    provider = _provider(httpx.MockTransport(handler))
    challenge = provider.send_phone_code("+86 13800138000")
    tokens = provider.sign_in_with_phone_code(challenge.verification_id, "123456")

    assert tokens == AuthTokenSet("phone-access-token", "phone-refresh-token", 7200)
    assert requests[0].url.path == "/auth/v1/verification"
    assert requests[0].read().decode() == ('{"phone_number":"+86 13800138000","target":"USER"}')
    assert [request.url.path for request in requests] == [
        "/auth/v1/verification",
        "/auth/v1/verification/verify",
        "/auth/v1/signin",
    ]


@pytest.mark.parametrize(
    "value",
    ["13800138000", "+8613800138000", "+86 138 0013 8000", "0086-138-0013-8000"],
)
def test_normalizes_mainland_phone_without_persisting_input_format(value: str) -> None:
    assert normalize_mainland_phone(value) == "+86 13800138000"


@pytest.mark.parametrize("value", ["", "12800138000", "1380013800", "+1 3800138000"])
def test_rejects_invalid_mainland_phone(value: str) -> None:
    with pytest.raises(InvalidAuthenticationFlowError):
        normalize_mainland_phone(value)


def test_phone_verification_rate_limiter_enforces_cooldown_and_bounded_windows() -> None:
    now = 1_000_000.0

    def clock() -> float:
        return now

    limiter = PhoneVerificationRateLimiter(
        cooldown_seconds=60,
        daily_limit=2,
        environment_daily_limit=3,
        clock=clock,
    )
    limiter.consume("+86 13800138000")
    with pytest.raises(AuthenticationRateLimitedError):
        limiter.consume("+86 13800138000")

    now += 60
    limiter.consume("+86 13800138000")
    now += 60
    with pytest.raises(AuthenticationRateLimitedError):
        limiter.consume("+86 13800138000")

    limiter.consume("+86 13900139000")
    now += 60
    with pytest.raises(AuthenticationRateLimitedError):
        limiter.consume("+86 13700137000")

    now += 24 * 60 * 60
    limiter.consume("+86 13800138000")
