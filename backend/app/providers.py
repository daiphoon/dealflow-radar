from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.app.event_schema import Direction, EventType, Fact, RiskSeverity, SourceQuality
from backend.app.investor_analysis_schema import (
    InvestorChangeAnalysisRequest,
    LLMProviderResult,
)

MAX_IMPORT_FILE_BYTES = 1024 * 1024
DEFAULT_PRIVATE_IMPORT_ROOT = (
    Path(__file__).resolve().parents[2] / "data" / "private" / "research_imports"
)
DEFAULT_PRIVATE_IDENTITY_IMPORT_ROOT = (
    Path(__file__).resolve().parents[2] / "data" / "private" / "identity_imports"
)
UNIFIED_CREDIT_CODE_CHARSET = "0123456789ABCDEFGHJKLMNPQRTUWXY"
UNIFIED_CREDIT_CODE_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)


class LLMProvider(Protocol):
    code: str
    model: str

    def analyze_investor_change(
        self,
        request: InvestorChangeAnalysisRequest,
    ) -> LLMProviderResult: ...


class LLMProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        external_calls: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated_cost: Decimal = Decimal("0"),
    ) -> None:
        super().__init__(message)
        self.external_calls = external_calls
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.estimated_cost = estimated_cost


def _validate_public_http_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        has_host = parsed.hostname is not None
    except ValueError as error:
        raise ValueError("URL must be a valid public HTTP(S) URL") from error
    if not has_host or parsed.username is not None or parsed.password is not None:
        raise ValueError("URL must include a host and must not include credentials")
    return value


def _validate_official_identity_url(value: str) -> str:
    _validate_public_http_url(value)
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https":
        raise ValueError("official identity URL must use HTTPS")
    if not (
        host == "gsxt.gov.cn"
        or host.endswith(".gsxt.gov.cn")
        or host == "gov.cn"
        or host.endswith(".gov.cn")
    ):
        raise ValueError("official identity URL must use a government or GSXT domain")
    return value


def _validate_identity_https_url(value: str) -> str:
    _validate_public_http_url(value)
    if urlsplit(value).scheme != "https":
        raise ValueError("identity URL must use HTTPS")
    return value


def _validate_tianyancha_identity_url(value: str) -> str:
    _validate_identity_https_url(value)
    host = (urlsplit(value).hostname or "").lower().rstrip(".")
    if host != "www.tianyancha.com":
        raise ValueError("licensed identity URL must use the approved Tianyancha domain")
    return value


def validate_unified_credit_code(value: str) -> str:
    if len(value) != 18 or any(character not in UNIFIED_CREDIT_CODE_CHARSET for character in value):
        raise ValueError("credit_code must use the unified social credit code character set")
    total = sum(
        UNIFIED_CREDIT_CODE_CHARSET.index(character) * weight
        for character, weight in zip(value[:17], UNIFIED_CREDIT_CODE_WEIGHTS, strict=True)
    )
    expected = UNIFIED_CREDIT_CODE_CHARSET[(31 - total % 31) % 31]
    if value[-1] != expected:
        raise ValueError("credit_code checksum is invalid")
    return value


def _validate_public_network_url(value: str) -> str:
    _validate_public_http_url(value)
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("source URL must use HTTP(S)")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise ValueError("source URL host could not be resolved") from error
    if not addresses:
        raise ValueError("source URL host could not be resolved")
    for address in addresses:
        host = str(address[4][0]).split("%", maxsplit=1)[0]
        if not ipaddress.ip_address(host).is_global:
            raise ValueError("source URL must not resolve to a private network")
    return value


class _PublicRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> Request | None:
        _validate_public_network_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class DocumentVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["healthy", "broken", "unavailable", "unchecked"]
    checked_at: datetime | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    final_url: str | None = None
    reason: str
    external_calls: int = Field(ge=0)


class DocumentVerifier(Protocol):
    def verify(self, url: str) -> DocumentVerification: ...


class DisabledDocumentVerifier:
    def verify(self, url: str) -> DocumentVerification:
        return DocumentVerification(
            status="unchecked",
            reason="external_calls_disabled",
            external_calls=0,
        )


class HttpDocumentVerifier:
    def __init__(self, timeout_seconds: int = 10) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds
        self.opener = build_opener(_PublicRedirectHandler())

    def verify(self, url: str) -> DocumentVerification:
        checked_at = datetime.now(UTC)
        try:
            _validate_public_network_url(url)
        except ValueError:
            return DocumentVerification(
                status="unavailable",
                checked_at=checked_at,
                reason="unsafe_or_unresolvable_url",
                external_calls=0,
            )

        external_calls = 0
        for method in ("HEAD", "GET"):
            external_calls += 1
            request = Request(
                url,
                method=method,
                headers={
                    "User-Agent": "DealflowRadar/0.1 source-verification",
                    "Accept": "text/html,application/xhtml+xml",
                    **({"Range": "bytes=0-0"} if method == "GET" else {}),
                },
            )
            try:
                with self.opener.open(request, timeout=self.timeout_seconds) as response:
                    final_url = _validate_public_network_url(response.geturl())
                    status = int(response.status)
                    if method == "GET":
                        response.read(1)
                    return DocumentVerification(
                        status="healthy" if 200 <= status < 400 else "broken",
                        checked_at=checked_at,
                        http_status=status,
                        final_url=final_url,
                        reason="http_success" if 200 <= status < 400 else "http_error",
                        external_calls=external_calls,
                    )
            except HTTPError as error:
                if method == "HEAD" and error.code in {403, 405, 501}:
                    continue
                return DocumentVerification(
                    status="broken",
                    checked_at=checked_at,
                    http_status=error.code,
                    final_url=error.geturl(),
                    reason="http_error",
                    external_calls=external_calls,
                )
            except (TimeoutError, URLError, ValueError):
                return DocumentVerification(
                    status="unavailable",
                    checked_at=checked_at,
                    reason="request_failed",
                    external_calls=external_calls,
                )

        return DocumentVerification(
            status="unavailable",
            checked_at=checked_at,
            reason="request_failed",
            external_calls=external_calls,
        )


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


class OfficialIdentitySource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=500)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return _validate_identity_https_url(value)


class OfficialIdentityRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_record_id: str = Field(min_length=1, max_length=160)
    query_text: str = Field(min_length=1, max_length=240)
    legal_name: str = Field(min_length=1, max_length=240)
    credit_code: str = Field(pattern=r"^[0-9A-Z]{18}$")
    registered_region: str | None = Field(default=None, max_length=120)
    registration_status: str = Field(min_length=1, max_length=64)
    canonical_url: str = Field(min_length=1, max_length=1000)
    checked_at: datetime
    registration_authority: str | None = Field(default=None, max_length=240)
    data_updated_at: datetime | None = None
    provider_metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("provider_metadata")
    @classmethod
    def restrict_provider_metadata(cls, value: dict[str, object]) -> dict[str, object]:
        allowed_fields = {
            "candidate_count",
            "provider_company_id",
            "search_response_hash",
            "registration_response_hash",
        }
        unexpected = set(value) - allowed_fields
        if unexpected:
            raise ValueError("provider_metadata contains fields outside the identity contract")
        candidate_count = value.get("candidate_count")
        if candidate_count is not None and (
            isinstance(candidate_count, bool)
            or not isinstance(candidate_count, int)
            or candidate_count < 0
        ):
            raise ValueError("provider_metadata candidate_count must be a non-negative integer")
        provider_company_id = value.get("provider_company_id")
        if provider_company_id is not None and (
            not isinstance(provider_company_id, str)
            or not provider_company_id.strip()
            or len(provider_company_id) > 120
        ):
            raise ValueError("provider_metadata provider_company_id is invalid")
        for field_name in ("search_response_hash", "registration_response_hash"):
            field_value = value.get(field_name)
            if field_value is not None and (
                not isinstance(field_value, str)
                or len(field_value) != 64
                or any(character not in "0123456789abcdef" for character in field_value)
            ):
                raise ValueError(f"provider_metadata {field_name} must be a SHA-256 hash")
        return value

    @field_validator("credit_code")
    @classmethod
    def validate_credit_code(cls, value: str) -> str:
        return validate_unified_credit_code(value)

    @field_validator("canonical_url")
    @classmethod
    def validate_canonical_url(cls, value: str) -> str:
        return _validate_identity_https_url(value)

    @field_validator("checked_at", "data_updated_at")
    @classmethod
    def require_checked_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("identity timestamps must include a timezone")
        return value


class OfficialIdentityImportBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    batch_id: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    queried_at: datetime
    source: OfficialIdentitySource
    original_query: str = Field(min_length=1, max_length=1000)
    target_company_hint: str = Field(min_length=1, max_length=240)
    verification_basis: Literal["official_government", "licensed_business_data"] = (
        "official_government"
    )
    license_status: Literal["public", "permission_confirmed"]
    records: list[OfficialIdentityRecord] = Field(min_length=1, max_length=500)

    @field_validator("queried_at")
    @classmethod
    def require_queried_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("queried_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_basis_source_and_license(self) -> OfficialIdentityImportBatch:
        if self.verification_basis == "official_government":
            if self.license_status != "public":
                raise ValueError("official government identity requires a public license status")
            _validate_official_identity_url(self.source.base_url)
            for record in self.records:
                _validate_official_identity_url(record.canonical_url)
            return self

        if self.license_status != "permission_confirmed":
            raise ValueError(
                "licensed business identity requires a permission_confirmed license status"
            )
        if self.source.code != "tianyancha_licensed_business_data":
            raise ValueError("licensed business identity must use the approved Tianyancha source")
        _validate_tianyancha_identity_url(self.source.base_url)
        for record in self.records:
            _validate_tianyancha_identity_url(record.canonical_url)
        return self


@dataclass(frozen=True)
class LoadedOfficialIdentityImport:
    batch: OfficialIdentityImportBatch
    file_hash: str
    source_filename: str


class OfficialIdentityProvider(Protocol):
    code: str
    parser_version: str
    external_calls: int
    estimated_cost: int

    def load(self) -> LoadedOfficialIdentityImport: ...


class ManualOfficialIdentityImportProvider:
    code = "manual_official_identity_import"
    parser_version = "identity-v1"
    external_calls = 0
    estimated_cost = 0

    def __init__(
        self,
        file_path: str | Path,
        *,
        allowed_root: Path | None = None,
    ) -> None:
        self.file_path = Path(file_path)
        self.allowed_root = (allowed_root or DEFAULT_PRIVATE_IDENTITY_IMPORT_ROOT).resolve()

    def load(self) -> LoadedOfficialIdentityImport:
        path = self.file_path
        if not path.is_absolute():
            path = self.allowed_root / path
        path = path.resolve()
        try:
            path.relative_to(self.allowed_root)
        except ValueError as error:
            raise ValueError(
                "official identity import file must be inside the private identity directory"
            ) from error
        if path.suffix.lower() != ".json":
            raise ValueError("ManualOfficialIdentityImportProvider accepts JSON files only")
        if path.stat().st_size > MAX_IMPORT_FILE_BYTES:
            raise ValueError("official identity import file exceeds the 1 MiB limit")
        content = path.read_bytes()
        payload = json.loads(content.decode("utf-8"))
        return LoadedOfficialIdentityImport(
            batch=OfficialIdentityImportBatch.model_validate(payload),
            file_hash=hashlib.sha256(content).hexdigest(),
            source_filename=path.name,
        )


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
    source_published_on: date | None = None
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
    # Backward-compatible import hint. Publication is decided by the policy gate.
    requires_human_review: bool | None = None

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
    license_status: Literal[
        "public", "public_access", "permission_confirmed", "unclear", "restricted"
    ]
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


class CandidateResearchImportProvider:
    code = "trusted_source_candidate_import"
    parser_version = "candidate-v1"
    external_calls = 0
    estimated_cost = 0

    def __init__(self, candidate_id: UUID, batch: ManualResearchImportBatch) -> None:
        self.candidate_id = candidate_id
        self.batch = batch

    def load(self) -> LoadedResearchImport:
        content = json.dumps(
            self.batch.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return LoadedResearchImport(
            batch=self.batch,
            file_hash=hashlib.sha256(content).hexdigest(),
            source_filename=f"candidate-{self.candidate_id}.json",
        )
