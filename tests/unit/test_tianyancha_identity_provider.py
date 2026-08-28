from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from backend.app.config import TianyanchaIdentityPolicy
from backend.app.tianyancha import (
    TianyanchaIdentityNeedsInputError,
    TianyanchaIdentityProvider,
    TianyanchaProviderError,
)

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


def _research_content(tool_name: str) -> dict[str, object]:
    if tool_name == "get_shareholder_info":
        return {
            "sources": {
                "holder": {
                    "total": 2,
                    "items": [
                        {
                            "name": "示例股东甲",
                            "capital": "100 万元",
                            "ftShareholding": "2025-01-02",
                        },
                        {
                            "name": "示例股东乙",
                            "capital": "50 万元",
                            "ftShareholding": "2025-03-04",
                        },
                    ],
                }
            }
        }
    if tool_name == "get_risk_overview":
        return {
            "total": 1,
            "toolRisks": [{"riskType": "司法记录", "riskLevel": "提示", "count": 1}],
        }
    if tool_name == "get_ipr_score":
        return {
            "inventionLicensingCount": 1,
            "softwareCopyrightCount": 2,
            "scienceAndTechnologyScore": 80,
        }
    if tool_name == "get_bidding_info":
        return {
            "total": "1",
            "items": [
                {
                    "title": "示例采购项目",
                    "publishTime": "2026-08-20",
                    "purchaser": "示例采购方",
                    "bidUrl": "https://example.gov.cn/bid/1",
                }
            ],
        }
    if tool_name == "get_historical_registration":
        return {
            "sources": {
                "cb": {
                    "changeList": [
                        {
                            "changeItem": "注册资本",
                            "changeTime": "2026-08-01",
                            "contentBefore": "100 万元",
                            "contentAfter": "200 万元",
                        }
                    ]
                }
            }
        }
    return {
        "total": 1,
        "riskTotal": 8,
        "groupCount": 1,
        "riskGroups": [{"groupName": "任职相关记录", "count": 1}],
    }


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


def test_single_identity_lookup_by_credit_code_uses_only_registration_tool(
    tmp_path: Path,
) -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        return _response(_registration_content(economic_zone="苏州工业园区"))

    provider = _provider(tmp_path, httpx.MockTransport(handler))

    result = provider.lookup_identity(company_name=None, credit_code=CREDIT_CODE)

    assert result.legal_name == LEGAL_NAME
    assert result.credit_code == CREDIT_CODE
    assert result.registered_region == "苏州市/苏州工业园区"
    assert result.registration_status == "存续"
    assert result.candidate_count is None
    assert provider.external_calls == 1
    assert [request["tool_name"] for request in requests] == ["get_company_registration_info"]


def test_cache_only_identity_lookup_never_falls_through_to_network(tmp_path: Path) -> None:
    def first_handler(_: httpx.Request) -> httpx.Response:
        return _response(_registration_content())

    first = _provider(tmp_path, httpx.MockTransport(first_handler))
    first.lookup_identity(company_name=None, credit_code=CREDIT_CODE)

    def forbidden_handler(_: httpx.Request) -> httpx.Response:
        pytest.fail("cache-only identity lookup must never call the network")

    cached = _provider(tmp_path, httpx.MockTransport(forbidden_handler))
    result = cached.lookup_cached_identity(company_name=None, credit_code=CREDIT_CODE)

    assert result is not None
    assert result.credit_code == CREDIT_CODE
    assert cached.external_calls == 0
    assert cached.cache_hits == 1


def test_cache_only_identity_lookup_returns_none_for_a_miss(tmp_path: Path) -> None:
    provider = _provider(
        tmp_path,
        httpx.MockTransport(lambda _: pytest.fail("cache miss must not call the network")),
    )

    result = provider.lookup_cached_identity(company_name=None, credit_code=CREDIT_CODE)

    assert result is None
    assert provider.external_calls == 0
    assert provider.cache_hits == 0


def test_begin_run_resets_accounting_but_preserves_cross_item_rate_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delays: list[float] = []
    provider = TianyanchaIdentityProvider(
        _write_manifest(tmp_path),
        authorization="test-authorization",
        policy=TianyanchaIdentityPolicy(min_request_interval_ms=1500),
        allowed_root=tmp_path,
        cache_root=tmp_path / "cache",
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _: _response(_registration_content()))
        ),
        sleeper=delays.append,
    )
    monkeypatch.setattr("backend.app.tianyancha.time.monotonic", lambda: 100.0)

    provider.lookup_identity(company_name=None, credit_code=CREDIT_CODE)
    assert provider.external_calls == 1
    next((tmp_path / "cache").glob("*.json")).unlink()

    provider.begin_run()
    provider.lookup_identity(company_name=None, credit_code=CREDIT_CODE)

    assert provider.external_calls == 1
    assert provider.cache_hits == 0
    assert delays == [1.5]


def test_credit_code_controls_lookup_while_user_confirms_returned_legal_name(
    tmp_path: Path,
) -> None:
    provider = _provider(
        tmp_path,
        httpx.MockTransport(lambda _: _response(_registration_content())),
    )

    result = provider.lookup_identity(company_name="用户误填的公司名称", credit_code=CREDIT_CODE)

    assert result.legal_name == LEGAL_NAME
    assert result.credit_code == CREDIT_CODE
    assert provider.external_calls == 1


def test_single_identity_lookup_by_exact_legal_name_requires_one_unique_entity(
    tmp_path: Path,
) -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if payload["tool_name"] == "search_companies":
            return _response(_search_content(total=4))
        return _response(_registration_content())

    provider = _provider(tmp_path, httpx.MockTransport(handler))

    result = provider.lookup_identity(company_name=LEGAL_NAME, credit_code=None)

    assert result.legal_name == LEGAL_NAME
    assert result.credit_code == CREDIT_CODE
    assert result.candidate_count == 4
    assert provider.external_calls == 2
    assert [request["tool_name"] for request in requests] == [
        "search_companies",
        "get_company_registration_info",
    ]


def test_single_identity_lookup_rejects_ambiguous_name_before_registration(
    tmp_path: Path,
) -> None:
    call_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        content = _search_content()
        items = content["items"]
        assert isinstance(items, list)
        items.append(
            {
                "id": 789012,
                "name": LEGAL_NAME,
                "creditCode": "91310000MA1K000014",
                "regStatus": "存续",
            }
        )
        return _response(content)

    provider = _provider(tmp_path, httpx.MockTransport(handler))

    with pytest.raises(TianyanchaIdentityNeedsInputError, match="credit code required"):
        provider.lookup_identity(company_name=LEGAL_NAME, credit_code=None)
    assert call_count == 1


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


def test_six_research_modules_use_approved_tools_and_conservative_classification(
    tmp_path: Path,
) -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        tool_name = payload["tool_name"]
        if tool_name == "get_company_registration_info":
            return _response(_registration_content())
        return _response(_research_content(tool_name))

    provider = _provider(tmp_path, httpx.MockTransport(handler))
    provider.lookup_identity(company_name=None, credit_code=CREDIT_CODE)
    modules = [
        "company_base",
        "risk",
        "intellectual_property",
        "operation",
        "history",
        "executive",
    ]
    results = {
        module: provider.lookup_research_module(
            module_code=module,  # type: ignore[arg-type]
            legal_name=LEGAL_NAME,
            credit_code=CREDIT_CODE,
            provider_company_id="123456",
        )
        for module in modules
    }

    assert [request["tool_name"] for request in requests] == [
        "get_company_registration_info",
        "get_shareholder_info",
        "get_risk_overview",
        "get_ipr_score",
        "get_bidding_info",
        "get_historical_registration",
        "get_person_risk_overview",
    ]
    assert requests[1]["arguments"] == {
        "searchKey": CREDIT_CODE,
        "pageNum": 1,
        "pageSize": 10,
    }
    assert requests[-1]["arguments"] == {
        "searchKey": LEGAL_NAME,
        "humanName": "不应进入身份记录",
    }
    assert all(result.records for result in results.values())
    assert results["risk"].records[0].classification == "licensed_source_record"
    assert results["executive"].records[0].classification == "licensed_source_record"
    assert results["risk"].records[0].risk_severity == "none"
    assert results["executive"].records[0].direction == "unknown"
    assert results["executive"].records[0].evidence_detail is not None
    assert results["executive"].records[0].evidence_detail.total_records == 8
    assert results["company_base"].records[0].evidence_detail is not None
    assert len(results["company_base"].records[0].evidence_detail.records) == 2
    company_base_payload = json.dumps(
        results["company_base"].records[0].evidence_detail.model_dump(mode="json"),
        ensure_ascii=False,
    )
    assert "private-phone" not in company_base_payload
    assert "private@example.invalid" not in company_base_payload
    assert "持股比例" not in company_base_payload
    assert "2025-01-02" not in company_base_payload
    assert results["operation"].records[0].evidence_detail is not None
    assert results["operation"].records[0].evidence_detail.records[0].source_url == (
        "https://example.gov.cn/bid/1"
    )
    for module in {"company_base", "intellectual_property", "operation", "history"}:
        assert results[module].records[0].classification == "verified_fact"
    assert provider.external_calls == 7

    def forbidden_handler(_: httpx.Request) -> httpx.Response:
        pytest.fail("fresh module cache must prevent network calls")

    cached = _provider(tmp_path, httpx.MockTransport(forbidden_handler))
    cached_results = [
        cached.lookup_cached_research_module(
            module_code=module,  # type: ignore[arg-type]
            legal_name=LEGAL_NAME,
            credit_code=CREDIT_CODE,
            provider_company_id="123456",
        )
        for module in modules
    ]
    assert all(result is not None for result in cached_results)
    assert cached.external_calls == 0
    assert cached.cache_hits == 8


def test_research_module_handles_empty_result_and_rejects_subject_conflict(
    tmp_path: Path,
) -> None:
    responses = iter(
        [
            _response({"_empty": True, "_warnings": ["no matching records"]}),
            _response(
                {
                    "total": 1,
                    "items": [{"creditCode": "91310000MA1K000014"}],
                }
            ),
        ]
    )
    provider = _provider(tmp_path, httpx.MockTransport(lambda _: next(responses)))

    empty = provider.lookup_research_module(
        module_code="operation",
        legal_name=LEGAL_NAME,
        credit_code=CREDIT_CODE,
        provider_company_id="123456",
    )
    assert empty.no_reliable_data is True
    assert empty.records == []
    assert empty.warnings == ["no matching records"]

    with pytest.raises(TianyanchaProviderError, match="credit code conflicts"):
        provider.lookup_research_module(
            module_code="risk",
            legal_name=LEGAL_NAME,
            credit_code=CREDIT_CODE,
            provider_company_id="123456",
        )


def test_research_record_count_prefers_positive_nested_total_and_ignores_metadata_lists(
    tmp_path: Path,
) -> None:
    responses = iter(
        [
            _response(
                {
                    "sources": {
                        "cb": {
                            "total": 0,
                            "changeList": [
                                {
                                    "changeItem": "企业名称",
                                    "changeTime": "2026-01-01",
                                    "contentBefore": "旧名称",
                                    "contentAfter": LEGAL_NAME,
                                }
                            ],
                        },
                        "history": {"total": 7, "items": [{"id": "history-1"}]},
                    }
                }
            ),
            _response(
                {
                    "companyName": LEGAL_NAME,
                    "profileTags": ["technology", "product"],
                    "scienceAndTechnologyScore": 80,
                    "inventionLicensingCount": 0,
                    "softwareCopyrightCount": 0,
                }
            ),
        ]
    )
    provider = _provider(tmp_path, httpx.MockTransport(lambda _: next(responses)))

    history = provider.lookup_research_module(
        module_code="history",
        legal_name=LEGAL_NAME,
        credit_code=CREDIT_CODE,
        provider_company_id="123456",
    )
    intellectual_property = provider.lookup_research_module(
        module_code="intellectual_property",
        legal_name=LEGAL_NAME,
        credit_code=CREDIT_CODE,
        provider_company_id="123456",
    )

    assert history.records[0].facts == [{"name": "历史变更记录数", "value": "1", "unit": "条"}]
    assert "1 条" in history.records[0].summary
    assert intellectual_property.records == []
    assert intellectual_property.no_reliable_data is True


def test_zero_count_modules_do_not_generate_events_and_contacts_are_not_projected(
    tmp_path: Path,
) -> None:
    responses = iter(
        [
            _response(_registration_content()),
            _response({"sources": {"holder": {"total": 0, "items": []}}}),
            _response({"total": 0, "toolRisks": []}),
        ]
    )
    provider = _provider(tmp_path, httpx.MockTransport(lambda _: next(responses)))
    provider.lookup_identity(company_name=None, credit_code=CREDIT_CODE)

    company_base = provider.lookup_research_module(
        module_code="company_base",
        legal_name=LEGAL_NAME,
        credit_code=CREDIT_CODE,
        provider_company_id=None,
    )
    risk = provider.lookup_research_module(
        module_code="risk",
        legal_name=LEGAL_NAME,
        credit_code=CREDIT_CODE,
        provider_company_id=None,
    )

    assert company_base.records == []
    assert company_base.no_reliable_data is True
    assert risk.records == []
    assert risk.no_reliable_data is True
    projected = json.dumps(
        [record.model_dump(mode="json") for record in company_base.records],
        ensure_ascii=False,
    )
    assert "private-phone" not in projected
    assert "private@example.invalid" not in projected


def test_evidence_record_urls_only_allow_https_provider_or_government_hosts() -> None:
    assert TianyanchaIdentityProvider._https_url("https://www.tianyancha.com/bid/1") == (
        "https://www.tianyancha.com/bid/1"
    )
    assert TianyanchaIdentityProvider._https_url("https://example.gov.cn/bid/1") == (
        "https://example.gov.cn/bid/1"
    )
    assert TianyanchaIdentityProvider._https_url("http://example.gov.cn/bid/1") is None
    assert TianyanchaIdentityProvider._https_url("https://user@example.gov.cn/bid/1") is None
    assert TianyanchaIdentityProvider._https_url("https://example.gov.cn:444/bid/1") is None
    assert TianyanchaIdentityProvider._https_url("https://phishing.example/bid/1") is None
