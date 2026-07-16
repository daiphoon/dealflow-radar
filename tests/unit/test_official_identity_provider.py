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
    assert len(loaded.file_hash) == 64
    assert len(provider.parser_version) <= 16
    assert provider.external_calls == 0
    assert provider.estimated_cost == 0


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
