from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from backend.app.config import TianyanchaIdentityPolicy
from backend.app.tianyancha import TianyanchaIdentityProvider, TianyanchaProviderError

LEGAL_NAME = "示例星河科技一号有限公司"
CREDIT_CODE = "91310000MA1K000006"


def _write_manifest(tmp_path: Path, *, legal_name: str = LEGAL_NAME) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "request_id": "licensed-identity-test-001",
                "queries": [{"legal_name": legal_name, "credit_code": CREDIT_CODE}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _search_content(*, name: str = LEGAL_NAME, total: int = 1) -> dict[str, object]:
    return {
        "items": [
            {
                "id": 123456,
                "name": name,
                "creditCode": CREDIT_CODE,
                "regStatus": "存续",
            }
        ],
        "pageNum": "1",
        "pageSize": "20",
        "total": total,
    }


def _registration_content(
    *,
    name: str = LEGAL_NAME,
    credit_code: str = CREDIT_CODE,
    economic_zone: str | None = None,
) -> dict[str, object]:
    base: dict[str, object] = {
        "name": name,
        "creditCode": credit_code,
        "regStatus": "存续",
        "city": "苏州市",
        "district": "姑苏区",
        "regInstitute": "苏州工业园区行政审批局",
        "updateTimes": "2026-07-17 13:12:53",
        # These licensed response fields may be retained only in the private cache.
        "phoneNumber": "private-phone",
        "email": "private@example.invalid",
        "legalPersonName": "不应进入身份记录",
    }
    if economic_zone is not None:
        base["economicFunctionZone1"] = economic_zone
    return {"sources": {"base": base, "type": {"status": "ok"}}}


def _response(content: dict[str, object], *, as_string: bool = False) -> httpx.Response:
    return httpx.Response(
        200,
        json={"content": json.dumps(content, ensure_ascii=False) if as_string else content},
        headers={"content-type": "application/json"},
    )


def _provider(
    tmp_path: Path,
    handler: httpx.MockTransport,
    *,
    policy: TianyanchaIdentityPolicy | None = None,
) -> TianyanchaIdentityProvider:
    client = httpx.Client(transport=handler, follow_redirects=False)
    return TianyanchaIdentityProvider(
        _write_manifest(tmp_path),
        authorization="test-authorization",
        policy=policy or TianyanchaIdentityPolicy(min_request_interval_ms=0),
        allowed_root=tmp_path,
        cache_root=tmp_path / "cache",
        client=client,
    )


def test_provider_maps_licensed_identity_without_persisting_contact_fields(
    tmp_path: Path,
) -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if payload["tool_name"] == "search_companies":
            return _response(_search_content(total=22), as_string=True)
        return _response(_registration_content(economic_zone="苏州工业园区"))

    provider = _provider(tmp_path, httpx.MockTransport(handler))

    loaded = provider.load()

    record = loaded.batch.records[0]
    assert loaded.batch.verification_basis == "licensed_business_data"
    assert loaded.batch.license_status == "permission_confirmed"
    assert loaded.batch.source.code == "tianyancha_licensed_business_data"
    assert record.legal_name == LEGAL_NAME
    assert record.credit_code == CREDIT_CODE
    assert record.registered_region == "苏州市/苏州工业园区"
    assert record.registration_authority == "苏州工业园区行政审批局"
    assert record.provider_metadata["candidate_count"] == 22
    assert record.provider_metadata["provider_company_id"] == "123456"
    persisted_payload = json.dumps(record.model_dump(mode="json"), ensure_ascii=False)
    assert "private-phone" not in persisted_payload
    assert "private@example.invalid" not in persisted_payload
    assert "不应进入身份记录" not in persisted_payload
    assert provider.external_calls == 2
    assert provider.cache_hits == 0
    assert [request["tool_name"] for request in requests] == [
        "search_companies",
        "get_company_registration_info",
    ]


def test_provider_cache_reuse_makes_zero_external_calls(tmp_path: Path) -> None:
    call_count = 0

    def first_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        payload = json.loads(request.content)
        if payload["tool_name"] == "search_companies":
            return _response(_search_content())
        return _response(_registration_content())

    first = _provider(tmp_path, httpx.MockTransport(first_handler))
    first_loaded = first.load()
    assert call_count == 2

    def forbidden_handler(_: httpx.Request) -> httpx.Response:
        pytest.fail("a fresh private cache entry must prevent a network call")

    second = _provider(tmp_path, httpx.MockTransport(forbidden_handler))
    second_loaded = second.load()

    assert second.external_calls == 0
    assert second.cache_hits == 2
    assert second_loaded.file_hash == first_loaded.file_hash
    assert second_loaded.batch.batch_id == first_loaded.batch.batch_id
    assert all(path.stat().st_mode & 0o077 == 0 for path in (tmp_path / "cache").glob("*.json"))


def test_provider_rejects_tampered_private_cache(tmp_path: Path) -> None:
    def first_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["tool_name"] == "search_companies":
            return _response(_search_content())
        return _response(_registration_content())

    first = _provider(tmp_path, httpx.MockTransport(first_handler))
    first.load()
    cache_path = next((tmp_path / "cache").glob("*.json"))
    envelope = json.loads(cache_path.read_text(encoding="utf-8"))
    envelope["content"]["tampered"] = True
    cache_path.write_text(json.dumps(envelope), encoding="utf-8")

    second = _provider(
        tmp_path,
        httpx.MockTransport(lambda _: pytest.fail("tampered cache must fail closed")),
    )
    with pytest.raises(TianyanchaProviderError, match="content hash"):
        second.load()


def test_provider_rejects_credit_code_mismatch_fail_closed(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["tool_name"] == "search_companies":
            return _response(_search_content())
        return _response(_registration_content(credit_code="91310000MA1K000014"))

    provider = _provider(tmp_path, httpx.MockTransport(handler))

    with pytest.raises(TianyanchaProviderError, match="credit code"):
        provider.load()


def test_provider_rejects_missing_exact_candidate_before_registration_call(
    tmp_path: Path,
) -> None:
    call_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        content = _search_content()
        items = content["items"]
        assert isinstance(items, list)
        items[0]["creditCode"] = "91310000MA1K000014"
        return _response(content)

    provider = _provider(tmp_path, httpx.MockTransport(handler))

    with pytest.raises(TianyanchaProviderError, match="exact credit code"):
        provider.load()
    assert call_count == 1


@pytest.mark.parametrize("status", [302, 400])
def test_provider_does_not_follow_redirect_or_retry_client_errors(
    tmp_path: Path,
    status: int,
) -> None:
    call_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(status, headers={"location": "https://example.invalid/redirect"})

    provider = _provider(tmp_path, httpx.MockTransport(handler))

    with pytest.raises(TianyanchaProviderError, match=f"HTTP {status}"):
        provider.load()
    assert call_count == 1


def test_provider_retries_one_transient_error_within_request_cap(tmp_path: Path) -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(503, json={"error": "temporary"})
        payload = json.loads(request.content)
        if payload["tool_name"] == "search_companies":
            return _response(_search_content())
        return _response(_registration_content())

    provider = _provider(
        tmp_path,
        httpx.MockTransport(handler),
        policy=TianyanchaIdentityPolicy(
            max_companies_per_run=1,
            min_request_interval_ms=0,
            retry_limit=1,
            max_requests_per_run=3,
        ),
    )

    provider.load()

    assert provider.external_calls == 3


def test_provider_rejects_oversized_or_non_json_response(tmp_path: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"x" * 101,
            headers={"content-type": "text/plain"},
        )

    provider = _provider(
        tmp_path,
        httpx.MockTransport(handler),
        policy=TianyanchaIdentityPolicy(
            min_request_interval_ms=0,
            max_response_bytes=100,
        ),
    )

    with pytest.raises(TianyanchaProviderError, match="response size"):
        provider.load()


def test_provider_validates_endpoint_and_manifest_before_network(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path, legal_name=" ")
    transport = httpx.MockTransport(lambda _: pytest.fail("invalid input must not call network"))

    with pytest.raises(ValueError, match="legal_name"):
        TianyanchaIdentityProvider(
            path,
            authorization="test-authorization",
            policy=TianyanchaIdentityPolicy(min_request_interval_ms=0),
            allowed_root=tmp_path,
            cache_root=tmp_path / "cache",
            client=httpx.Client(transport=transport),
        )

    with pytest.raises(ValueError, match="endpoint"):
        TianyanchaIdentityPolicy(endpoint_url="https://example.invalid/tools/call")
