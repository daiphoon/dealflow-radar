from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.providers import MAX_IMPORT_FILE_BYTES, ManualResearchImportProvider


def _write_json(path: Path, payload: dict[str, object]) -> bytes:
    content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    path.write_bytes(content)
    return content


def test_manual_provider_loads_strict_public_json(
    tmp_path: Path,
    manual_import_payload: dict[str, object],
) -> None:
    content = _write_json(tmp_path / "batch.json", manual_import_payload)

    loaded = ManualResearchImportProvider("batch.json", allowed_root=tmp_path).load()

    assert loaded.file_hash == hashlib.sha256(content).hexdigest()
    assert loaded.source_filename == "batch.json"
    assert loaded.batch.batch_id == "manual-batch-001"
    assert loaded.batch.records[0].requires_human_review is True


def test_manual_provider_rejects_path_outside_private_root(
    tmp_path: Path,
    manual_import_payload: dict[str, object],
) -> None:
    allowed_root = tmp_path / "private"
    allowed_root.mkdir()
    outside = tmp_path / "outside.json"
    _write_json(outside, manual_import_payload)

    with pytest.raises(ValueError, match="inside the private import directory"):
        ManualResearchImportProvider(outside, allowed_root=allowed_root).load()


def test_manual_provider_rejects_non_json_file(tmp_path: Path) -> None:
    path = tmp_path / "batch.txt"
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON files"):
        ManualResearchImportProvider(path, allowed_root=tmp_path).load()


def test_manual_provider_rejects_oversized_file(tmp_path: Path) -> None:
    path = tmp_path / "large.json"
    path.write_bytes(b"x" * (MAX_IMPORT_FILE_BYTES + 1))

    with pytest.raises(ValueError, match="exceeds"):
        ManualResearchImportProvider(path, allowed_root=tmp_path).load()


@pytest.mark.parametrize(
    ("field", "value"),
    [("license_status", "internal"), ("schema_version", "2.0")],
)
def test_manual_provider_rejects_unsupported_batch_values(
    tmp_path: Path,
    manual_import_payload: dict[str, object],
    field: str,
    value: str,
) -> None:
    manual_import_payload[field] = value
    _write_json(tmp_path / "batch.json", manual_import_payload)

    with pytest.raises(ValidationError):
        ManualResearchImportProvider("batch.json", allowed_root=tmp_path).load()


def test_manual_provider_requires_human_review(
    tmp_path: Path,
    manual_import_payload: dict[str, object],
) -> None:
    records = manual_import_payload["records"]
    assert isinstance(records, list)
    records[0]["requires_human_review"] = False
    _write_json(tmp_path / "batch.json", manual_import_payload)

    with pytest.raises(ValidationError):
        ManualResearchImportProvider("batch.json", allowed_root=tmp_path).load()


@pytest.mark.parametrize("field", ["source_published_at", "occurred_at"])
def test_manual_provider_rejects_naive_record_times(
    tmp_path: Path,
    manual_import_payload: dict[str, object],
    field: str,
) -> None:
    records = manual_import_payload["records"]
    assert isinstance(records, list)
    records[0][field] = "2026-07-14T10:00:00"
    _write_json(tmp_path / "batch.json", manual_import_payload)

    with pytest.raises(ValidationError, match=field):
        ManualResearchImportProvider("batch.json", allowed_root=tmp_path).load()


@pytest.mark.parametrize(
    "canonical_url",
    ["https://", "https://user:password@example.invalid/document"],
)
def test_manual_provider_rejects_invalid_or_credentialed_urls(
    tmp_path: Path,
    manual_import_payload: dict[str, object],
    canonical_url: str,
) -> None:
    records = manual_import_payload["records"]
    assert isinstance(records, list)
    records[0]["canonical_url"] = canonical_url
    _write_json(tmp_path / "batch.json", manual_import_payload)

    with pytest.raises(ValidationError, match="canonical_url"):
        ManualResearchImportProvider("batch.json", allowed_root=tmp_path).load()
