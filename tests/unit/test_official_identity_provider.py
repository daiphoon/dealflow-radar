from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from backend.app.providers import ManualOfficialIdentityImportProvider


def _payload() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "batch_id": "official-identity-test-001",
        "queried_at": "2026-07-16T09:00:00+08:00",
        "source": {
            "code": "national_enterprise_credit_publicity_system",
            "name": "国家企业信用信息公示系统",
            "base_url": "https://bt.gsxt.gov.cn/",
        },
        "original_query": "示例公司",
        "target_company_hint": "示例公司",
        "license_status": "public",
        "records": [
            {
                "external_record_id": "official-record-001",
                "query_text": "示例公司",
                "legal_name": "示例公司",
                "credit_code": "91310000MA1K000006",
                "registered_region": "虚构省甲市",
                "registration_status": "存续",
                "canonical_url": "https://bt.gsxt.gov.cn/",
                "checked_at": "2026-07-16T09:00:00+08:00",
            }
        ],
    }


def _provider(tmp_path: Path, payload: dict[str, object]) -> ManualOfficialIdentityImportProvider:
    (tmp_path / "identity.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return ManualOfficialIdentityImportProvider("identity.json", allowed_root=tmp_path)


def test_official_identity_provider_validates_and_hashes_local_json(tmp_path: Path) -> None:
    provider = _provider(tmp_path, _payload())

    loaded = provider.load()

    assert loaded.batch.records[0].credit_code == "91310000MA1K000006"
    assert loaded.batch.source.base_url == "https://bt.gsxt.gov.cn/"
    assert loaded.batch.verification_basis == "official_government"
    assert len(loaded.file_hash) == 64
    assert len(provider.parser_version) <= 16
    assert provider.external_calls == 0
    assert provider.estimated_cost == 0


def test_identity_provider_rejects_retired_licensed_business_imports(tmp_path: Path) -> None:
    payload = _payload()
    payload["verification_basis"] = "licensed_business_data"
    payload["license_status"] = "permission_confirmed"

    with pytest.raises(ValueError, match="verification_basis|license_status"):
        _provider(tmp_path, payload).load()


def test_identity_provider_rejects_unnecessary_provider_metadata(tmp_path: Path) -> None:
    payload = _payload()
    records = payload["records"]
    assert isinstance(records, list)
    records[0]["provider_metadata"] = {"phoneNumber": "must-not-enter-business-database"}

    with pytest.raises(ValueError, match="provider_metadata"):
        _provider(tmp_path, payload).load()


@pytest.mark.parametrize(
    ("license_status", "source_url", "record_url", "message"),
    [
        (
            "permission_confirmed",
            "https://bt.gsxt.gov.cn/",
            "https://bt.gsxt.gov.cn/",
            "license_status",
        ),
    ],
)
def test_identity_provider_cross_validates_basis_license_and_domain(
    tmp_path: Path,
    license_status: str,
    source_url: str,
    record_url: str,
    message: str,
) -> None:
    payload = _payload()
    payload["license_status"] = license_status
    source = payload["source"]
    assert isinstance(source, dict)
    source["base_url"] = source_url
    records = payload["records"]
    assert isinstance(records, list)
    records[0]["canonical_url"] = record_url

    with pytest.raises(ValueError, match=message):
        _provider(tmp_path, payload).load()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("canonical_url", "https://example.com/company", "government or GSXT"),
        ("credit_code", "91310000MA1K000007", "checksum"),
    ],
)
def test_official_identity_provider_rejects_untrusted_or_invalid_identity(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    payload = copy.deepcopy(_payload())
    records = payload["records"]
    assert isinstance(records, list)
    records[0][field] = value

    with pytest.raises(ValueError, match=message):
        _provider(tmp_path, payload).load()


def test_official_identity_provider_rejects_files_outside_private_root(
    tmp_path: Path,
) -> None:
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="private identity directory"):
        ManualOfficialIdentityImportProvider(outside, allowed_root=allowed_root).load()


def _exchange_payload() -> dict:
    return json.loads(Path("data/sample/exchange_identity_import.json").read_text())


def test_exchange_identity_keeps_its_basis_and_manual_evidence(tmp_path: Path) -> None:
    provider = _provider(tmp_path, _exchange_payload())
    loaded = provider.load()
    assert loaded.batch.verification_basis == "exchange_disclosure"
    assert loaded.batch.records[0].identity_fields_confirmed is True
    assert loaded.batch.records[0].evidence_locator
    assert loaded.batch.review_reason
    assert provider.external_calls == provider.estimated_cost == 0


@pytest.mark.parametrize(
    "url",
    [
        "http://www.hkexnews.hk/listedco/listconews/sehk/demo.pdf",
        "https://www.hkexnews.hk.attacker.invalid/listedco/listconews/sehk/demo.pdf",
        "https://attacker.hkexnews.hk/listedco/listconews/sehk/demo.pdf",
        "https://user:password@www.hkexnews.hk/listedco/listconews/sehk/demo.pdf",
        "https://www.hkexnews.hk:444/listedco/listconews/sehk/demo.pdf",
        "https://127.0.0.1/listedco/listconews/sehk/demo.pdf",
        "https://www.hkexnews.hk/",
        "https://www.hkexnews.hk/listedco/listconews/sehk/demo.html",
        "https://www.hkexnews.hk/listedco/listconews/sehk/../other.pdf",
        "https://www.hkexnews.hk/listedco/listconews/sehk/%2e%2e/other.pdf",
        "https://www.hkexnews.hk/listedco/listconews/sehk/demo.pdf?url=https://example.com",
        "https://www.csrc.gov.cn/listedco/listconews/sehk/demo.pdf",
    ],
)
def test_exchange_identity_rejects_non_disclosure_urls(tmp_path: Path, url: str) -> None:
    payload = _exchange_payload()
    payload["records"][0]["canonical_url"] = url
    with pytest.raises(ValueError):
        _provider(tmp_path, payload).load()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("identity_fields_confirmed", False),
        ("identity_fields_confirmed", "true"),
        ("evidence_excerpt", "此处没有信用代码"),
        ("evidence_locator", "   "),
        ("registered_region", "   "),
        ("data_updated_at", None),
        ("data_updated_at", "2027-01-01T00:00:00+08:00"),
        ("checked_at", "2027-01-01T00:00:00+08:00"),
        ("credit_code", "91310000MA1K000007"),
    ],
)
def test_exchange_identity_requires_manual_field_confirmation(
    tmp_path: Path, field: str, value: object
) -> None:
    payload = _exchange_payload()
    payload["records"][0][field] = value
    with pytest.raises(ValueError):
        _provider(tmp_path, payload).load()


@pytest.mark.parametrize("field", ["review_reason", "verification_basis", "source"])
def test_exchange_identity_cannot_claim_government_basis(tmp_path: Path, field: str) -> None:
    payload = _exchange_payload()
    payload[field] = {
        "review_reason": " ",
        "verification_basis": "official_government",
        "source": {"code": "gov", "name": "政府", "base_url": "https://www.csrc.gov.cn/"},
    }[field]
    with pytest.raises(ValueError):
        _provider(tmp_path, payload).load()
