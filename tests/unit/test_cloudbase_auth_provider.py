from __future__ import annotations

import httpx
import pytest

from backend.app.auth import (
    AuthenticationProviderUnavailableError,
    AuthTokenSet,
    CloudBaseIdentityProvider,
    InvalidAccessTokenError,
    InvalidAuthenticationFlowError,
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


@pytest.mark.parametrize(
    "payload",
    [
        {
            "sub": "subject",
            "email": "invitee@example.com",
            "status": "SUSPENDED",
            "providers": [{"id": "email", "provider_user_id": "invitee@example.com"}],
        },
        {
            "sub": "subject",
            "email": "invitee@example.com",
            "status": "ACTIVE",
            "providers": [{"id": "github", "provider_user_id": "invitee@example.com"}],
        },
    ],
)
def test_rejects_inactive_or_unverified_email_identity(payload: dict[str, object]) -> None:
    provider = _provider(httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))

    with pytest.raises(InvalidAccessTokenError):
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
