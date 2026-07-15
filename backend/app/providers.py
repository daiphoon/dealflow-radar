from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.event_schema import Direction, EventType, Fact, RiskSeverity, SourceQuality

MAX_IMPORT_FILE_BYTES = 1024 * 1024
DEFAULT_PRIVATE_IMPORT_ROOT = (
    Path(__file__).resolve().parents[2] / "data" / "private" / "research_imports"
)


def _validate_public_http_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        has_host = parsed.hostname is not None
    except ValueError as error:
        raise ValueError("URL must be a valid public HTTP(S) URL") from error
    if not has_host or parsed.username is not None or parsed.password is not None:
        raise ValueError("URL must include a host and must not include credentials")
    return value


class MockFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: str
    unit: str | None = None


class MockResearchRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_record_id: str
    company_legal_name: str
    company_alias: str
    registered_region: str
    title: str
    canonical_url: str
    published_at: datetime
    evidence_excerpt: str
    event_type: EventType
    event_subtype: str
    direction: Direction
    materiality_score: int = Field(ge=0, le=100)
    risk_severity: RiskSeverity
    confidence_score: float = Field(ge=0, le=1)
    source_quality: SourceQuality
    facts: list[MockFact] = Field(min_length=1)
    uncertainties: list[str] = Field(default_factory=list)
    requires_human_review: bool = True


class MockResearchProvider:
    code = "mock_research"
    external_calls = 0
    estimated_cost = 0

    def __init__(self, fixture_path: Path | None = None) -> None:
        self.fixture_path = fixture_path or (
            Path(__file__).resolve().parents[2] / "data" / "sample" / "mock_research.json"
        )
        self.load_count = 0

    def load(self) -> list[MockResearchRecord]:
        self.load_count += 1
        payload = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        return [MockResearchRecord.model_validate(item) for item in payload]


class CompanyIdentityEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_name: str = Field(min_length=1, max_length=240)
    credit_code: str | None = Field(default=None, pattern=r"^[0-9A-Z]{18}$")
    registered_region: str | None = Field(default=None, max_length=120)
    official_website: str | None = Field(default=None, pattern=r"^https?://")

    @field_validator("official_website")
    @classmethod
    def validate_official_website(cls, value: str | None) -> str | None:
        return _validate_public_http_url(value) if value is not None else None


class ManualResearchRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_record_id: str = Field(min_length=1, max_length=160)
    company_identity_evidence: CompanyIdentityEvidence
    source_code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    source_name: str = Field(min_length=1, max_length=200)
    canonical_url: str = Field(min_length=1, max_length=1000, pattern=r"^https?://")
    source_published_at: datetime | None = None
    occurred_at: datetime | None = None
    title: str = Field(min_length=1, max_length=200)
    evidence_excerpt: str = Field(min_length=1, max_length=1000)
    event_type: EventType
    event_subtype: str = Field(min_length=1, max_length=64)
    direction: Direction
    materiality_score: int = Field(ge=0, le=100)
    risk_severity: RiskSeverity
    confidence_score: float = Field(ge=0, le=1)
    source_quality: SourceQuality
    facts: list[Fact] = Field(min_length=1, max_length=50)
    uncertainties: list[str] = Field(default_factory=list, max_length=20)
    requires_human_review: Literal[True]

    @field_validator("canonical_url")
    @classmethod
    def validate_canonical_url(cls, value: str) -> str:
        return _validate_public_http_url(value)

    @field_validator("source_published_at", "occurred_at")
    @classmethod
    def require_record_timezones(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("record timestamps must include a timezone")
        return value


class ManualResearchImportBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    batch_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    queried_at: datetime
    research_tool: str = Field(min_length=1, max_length=80)
    agent_name: str | None = Field(default=None, max_length=120)
    original_query: str = Field(min_length=1, max_length=1000)
    target_company_hint: str = Field(min_length=1, max_length=240)
    license_status: Literal["public"]
    records: list[ManualResearchRecord] = Field(min_length=1, max_length=500)

    @field_validator("queried_at")
    @classmethod
    def require_queried_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("queried_at must include a timezone")
        return value


@dataclass(frozen=True)
class LoadedResearchImport:
    batch: ManualResearchImportBatch
    file_hash: str
    source_filename: str


class ManualResearchImportProvider:
    code = "manual_research_import"
    parser_version = "1"
    external_calls = 0
    estimated_cost = 0

    def __init__(
        self,
        file_path: str | Path,
        *,
        allowed_root: Path | None = None,
    ) -> None:
        self.file_path = Path(file_path)
        self.allowed_root = (allowed_root or DEFAULT_PRIVATE_IMPORT_ROOT).resolve()

    def load(self) -> LoadedResearchImport:
        path = self.file_path
        if not path.is_absolute():
            path = self.allowed_root / path
        path = path.resolve()
        try:
            path.relative_to(self.allowed_root)
        except ValueError as error:
            raise ValueError(
                "research import file must be inside the private import directory"
            ) from error
        if path.suffix.lower() != ".json":
            raise ValueError("ManualResearchImportProvider accepts JSON files only")
        if path.stat().st_size > MAX_IMPORT_FILE_BYTES:
            raise ValueError("research import file exceeds the 1 MiB limit")
        content = path.read_bytes()
        payload = json.loads(content.decode("utf-8"))
        return LoadedResearchImport(
            batch=ManualResearchImportBatch.model_validate(payload),
            file_hash=hashlib.sha256(content).hexdigest(),
            source_filename=path.name,
        )
