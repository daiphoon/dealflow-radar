from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.config import TianyanchaIdentityPolicy
from backend.app.providers import (
    DEFAULT_PRIVATE_IDENTITY_IMPORT_ROOT,
    LoadedOfficialIdentityImport,
    OfficialIdentityImportBatch,
    OfficialIdentityRecord,
    OfficialIdentitySource,
    validate_unified_credit_code,
)

DEFAULT_PRIVATE_TIANYANCHA_CACHE_ROOT = (
    Path(__file__).resolve().parents[2] / "data" / "private" / "provider_cache" / "tianyancha"
)


class TianyanchaProviderError(RuntimeError):
    pass


class TianyanchaIdentityNeedsInputError(TianyanchaProviderError):
    pass


class _TianyanchaCacheMiss(Exception):
    pass


class TianyanchaIdentityLookupResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_text: str
    legal_name: str
    credit_code: str = Field(pattern=r"^[0-9A-Z]{18}$")
    registered_region: str
    registration_status: str
    registration_authority: str | None = None
    provider_company_id: str | None = None
    canonical_url: str
    checked_at: datetime
    data_updated_at: datetime | None = None
    response_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_count: int | None = None


TianyanchaResearchModuleCode = Literal[
    "company_base",
    "risk",
    "intellectual_property",
    "operation",
    "history",
    "executive",
]


class TianyanchaEvidenceField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=1000)


class TianyanchaEvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    fields: list[TianyanchaEvidenceField] = Field(default_factory=list, max_length=12)
    source_url: str | None = Field(default=None, max_length=1000)


class TianyanchaEvidenceDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["licensed-structured-evidence-v1"] = "licensed-structured-evidence-v1"
    heading: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    total_records: int | None = Field(default=None, ge=0)
    summary_fields: list[TianyanchaEvidenceField] = Field(default_factory=list, max_length=30)
    records: list[TianyanchaEvidenceRecord] = Field(default_factory=list, max_length=10)


class TianyanchaResearchRecord(BaseModel):
    """A conservative, display-ready projection of one licensed source response."""

    model_config = ConfigDict(extra="forbid")

    external_record_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)
    event_type: Literal[
        "financial_operation",
        "financing_cap_table",
        "contract_commercial",
        "product_technology",
        "governance_people",
        "legal_compliance",
        "capacity_assets",
        "exit_liquidity",
        "information_quality",
    ]
    event_subtype: str = Field(min_length=1, max_length=64)
    direction: Literal["positive", "negative", "neutral", "mixed", "unknown"]
    materiality_score: int = Field(ge=0, le=100)
    risk_severity: Literal["none", "low", "moderate", "high", "critical"]
    confidence_score: Decimal = Field(ge=0, le=1)
    facts: list[dict[str, str | None]] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    occurred_at: datetime | None = None
    published_on: date | None = None
    canonical_url: str = Field(min_length=1, max_length=1000)
    evidence_excerpt: str = Field(min_length=1, max_length=2000)
    classification: Literal["verified_fact", "licensed_source_record", "unconfirmed_lead"]
    classification_reasons: list[str] = Field(default_factory=list)
    evidence_detail: TianyanchaEvidenceDetail | None = None


class TianyanchaResearchModuleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module_code: TianyanchaResearchModuleCode
    tool_name: str = Field(min_length=1, max_length=80)
    checked_at: datetime
    response_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    records: list[TianyanchaResearchRecord] = Field(default_factory=list)
    no_reliable_data: bool
    warnings: list[str] = Field(default_factory=list)

    @field_validator("checked_at")
    @classmethod
    def require_checked_at_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("research checked_at must include a timezone")
        return value


class TianyanchaIdentityQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_name: str = Field(min_length=1, max_length=240)
    credit_code: str = Field(pattern=r"^[0-9A-Z]{18}$")

    @field_validator("legal_name")
    @classmethod
    def require_nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("legal_name must not be blank")
        return value.strip()

    @field_validator("credit_code")
    @classmethod
    def validate_credit_code(cls, value: str) -> str:
        return validate_unified_credit_code(value.upper())


class TianyanchaIdentityManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    request_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    queries: list[TianyanchaIdentityQuery] = Field(min_length=1, max_length=10)

    @field_validator("queries")
    @classmethod
    def require_unique_credit_codes(
        cls, value: list[TianyanchaIdentityQuery]
    ) -> list[TianyanchaIdentityQuery]:
        codes = [query.credit_code for query in value]
        if len(codes) != len(set(codes)):
            raise ValueError("queries must not repeat a credit code")
        return value


class _CacheEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    arguments: dict[str, object]
    fetched_at: datetime
    response_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: dict[str, object]

    @field_validator("fetched_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("cache timestamp must include a timezone")
        return value


@dataclass(frozen=True)
class _ToolResult:
    content: dict[str, object]
    fetched_at: datetime
    response_hash: str


def _sha256(value: str | bytes) -> str:
    content = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(content).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _resolve_private_file(file_path: str | Path, allowed_root: Path) -> Path:
    path = Path(file_path)
    if not path.is_absolute():
        path = allowed_root / path
    path = path.resolve()
    try:
        path.relative_to(allowed_root.resolve())
    except ValueError as error:
        raise ValueError(
            "Tianyancha identity manifest must be inside the private directory"
        ) from error
    if path.suffix.lower() != ".json":
        raise ValueError("Tianyancha identity manifest must be a JSON file")
    return path


def load_tianyancha_identity_manifest(
    file_path: str | Path,
    *,
    allowed_root: Path | None = None,
) -> TianyanchaIdentityManifest:
    root = (allowed_root or DEFAULT_PRIVATE_IDENTITY_IMPORT_ROOT).resolve()
    path = _resolve_private_file(file_path, root)
    if path.stat().st_size > 256 * 1024:
        raise ValueError("Tianyancha identity manifest exceeds the 256 KiB limit")
    return TianyanchaIdentityManifest.model_validate_json(path.read_text(encoding="utf-8"))


class TianyanchaIdentityProvider:
    code = "tianyancha_licensed_identity"
    parser_version = "tyc-id-v1"
    estimated_cost = 0

    _RESEARCH_TOOLS: dict[str, str] = {
        "company_base": "get_shareholder_info",
        "risk": "get_risk_overview",
        "intellectual_property": "get_ipr_score",
        "operation": "get_bidding_info",
        "history": "get_historical_registration",
        "executive": "get_person_risk_overview",
    }
    _RESEARCH_LABELS: dict[str, str] = {
        "company_base": "工商与股东基础",
        "risk": "司法与合规风险",
        "intellectual_property": "知识产权",
        "operation": "经营与公示",
        "history": "历史变更",
        "executive": "董监高与人员",
    }

    def __init__(
        self,
        manifest_path: str | Path | None,
        *,
        authorization: str,
        policy: TianyanchaIdentityPolicy,
        allowed_root: Path | None = None,
        cache_root: Path | None = None,
        client: httpx.Client | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not authorization.strip():
            raise ValueError("Tianyancha authorization is required")
        self.allowed_root = (allowed_root or DEFAULT_PRIVATE_IDENTITY_IMPORT_ROOT).resolve()
        if manifest_path is None:
            self.manifest_path = None
            self.manifest = None
        else:
            self.manifest_path = _resolve_private_file(manifest_path, self.allowed_root)
            self.manifest = load_tianyancha_identity_manifest(
                self.manifest_path,
                allowed_root=self.allowed_root,
            )
            if len(self.manifest.queries) > policy.max_companies_per_run:
                raise ValueError("manifest exceeds TIANYANCHA_IDENTITY_MAX_COMPANIES")
        self.authorization = authorization.strip()
        self.policy = policy
        self.cache_root = (cache_root or DEFAULT_PRIVATE_TIANYANCHA_CACHE_ROOT).resolve()
        self._client = client or httpx.Client(
            timeout=policy.timeout_seconds,
            follow_redirects=False,
        )
        self._owns_client = client is None
        self._sleep = sleeper
        self._clock = clock
        self._last_request_monotonic: float | None = None
        self.external_calls = 0
        self.cache_hits = 0

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def begin_run(self) -> None:
        """Reset per-item accounting while preserving cross-item rate limiting."""
        self.external_calls = 0
        self.cache_hits = 0

    def _cache_path(self, tool_name: str, arguments: dict[str, object]) -> Path:
        cache_key = _sha256(_canonical_json({"tool_name": tool_name, "arguments": arguments}))
        return self.cache_root / f"{cache_key}.json"

    def _read_cache(
        self,
        tool_name: str,
        arguments: dict[str, object],
    ) -> _ToolResult | None:
        path = self._cache_path(tool_name, arguments)
        if not path.exists():
            return None
        try:
            envelope = _CacheEnvelope.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise TianyanchaProviderError("private provider cache is unreadable") from error
        if envelope.tool_name != tool_name or envelope.arguments != arguments:
            raise TianyanchaProviderError("private provider cache key does not match its content")
        if _sha256(_canonical_json(envelope.content)) != envelope.response_hash:
            raise TianyanchaProviderError("private provider cache content hash does not match")
        fetched_at = envelope.fetched_at.astimezone(UTC)
        if self._clock().astimezone(UTC) - fetched_at > timedelta(days=self.policy.cache_ttl_days):
            return None
        self.cache_hits += 1
        return _ToolResult(
            content=envelope.content,
            fetched_at=envelope.fetched_at,
            response_hash=envelope.response_hash,
        )

    def _write_cache(
        self,
        tool_name: str,
        arguments: dict[str, object],
        content: dict[str, object],
        fetched_at: datetime,
    ) -> _ToolResult:
        response_hash = _sha256(_canonical_json(content))
        envelope = _CacheEnvelope(
            tool_name=tool_name,
            arguments=arguments,
            fetched_at=fetched_at,
            response_hash=response_hash,
            content=content,
        )
        self.cache_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.cache_root, 0o700)
        path = self._cache_path(tool_name, arguments)
        temporary = path.with_suffix(f".tmp-{os.getpid()}")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(envelope.model_dump_json())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        finally:
            if temporary.exists():
                temporary.unlink()
        return _ToolResult(
            content=content,
            fetched_at=fetched_at,
            response_hash=response_hash,
        )

    def _wait_for_rate_limit(self) -> None:
        if self._last_request_monotonic is None:
            return
        interval = self.policy.min_request_interval_ms / 1000
        elapsed = time.monotonic() - self._last_request_monotonic
        if elapsed < interval:
            self._sleep(interval - elapsed)

    def _post_tool(self, tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
        if tool_name not in {
            "search_companies",
            "get_company_registration_info",
            *self._RESEARCH_TOOLS.values(),
        }:
            raise ValueError("unsupported Tianyancha tool")
        search_key = arguments.get("searchKey")
        if not isinstance(search_key, str) or not search_key.strip():
            raise ValueError("Tianyancha searchKey must not be blank")
        request_payload = {
            "tool_name": tool_name,
            "arguments": arguments,
            "format": "json",
        }
        for attempt in range(self.policy.retry_limit + 1):
            if self.external_calls >= self.policy.max_requests_per_run:
                raise TianyanchaProviderError("Tianyancha request limit reached")
            self._wait_for_rate_limit()
            try:
                response = self._client.post(
                    self.policy.endpoint_url,
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "Authorization": self.authorization,
                    },
                    json=request_payload,
                )
            except httpx.HTTPError as error:
                self.external_calls += 1
                self._last_request_monotonic = time.monotonic()
                if attempt < self.policy.retry_limit:
                    continue
                raise TianyanchaProviderError("Tianyancha request failed") from error
            self.external_calls += 1
            self._last_request_monotonic = time.monotonic()
            if response.status_code in {429} or response.status_code >= 500:
                if attempt < self.policy.retry_limit:
                    continue
            if not 200 <= response.status_code < 300:
                raise TianyanchaProviderError(f"Tianyancha returned HTTP {response.status_code}")
            if len(response.content) > self.policy.max_response_bytes:
                raise TianyanchaProviderError(
                    "Tianyancha response size exceeds the configured limit"
                )
            content_type = response.headers.get("content-type", "").lower()
            if "json" not in content_type:
                raise TianyanchaProviderError("Tianyancha response is not JSON")
            try:
                payload = response.json()
            except ValueError as error:
                raise TianyanchaProviderError("Tianyancha response is not valid JSON") from error
            if not isinstance(payload, dict):
                raise TianyanchaProviderError("Tianyancha response root must be an object")
            content = payload.get("content", payload)
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except ValueError as error:
                    raise TianyanchaProviderError("Tianyancha content is not valid JSON") from error
            if not isinstance(content, dict):
                raise TianyanchaProviderError("Tianyancha content must be an object")
            return content
        raise TianyanchaProviderError("Tianyancha retry limit reached")

    def _call_tool(self, tool_name: str, arguments: dict[str, object]) -> _ToolResult:
        cached = self._read_cache(tool_name, arguments)
        if cached is not None:
            return cached
        content = self._post_tool(tool_name, arguments)
        return self._write_cache(tool_name, arguments, content, self._clock())

    def _call_cached_tool(self, tool_name: str, arguments: dict[str, object]) -> _ToolResult:
        cached = self._read_cache(tool_name, arguments)
        if cached is None:
            raise _TianyanchaCacheMiss
        return cached

    @staticmethod
    def _exact_candidate(
        content: dict[str, object], credit_code: str
    ) -> tuple[dict[str, object], int]:
        raw_items = content.get("items")
        if not isinstance(raw_items, list):
            raise TianyanchaProviderError("company search response is missing candidate items")
        exact = [
            item
            for item in raw_items
            if isinstance(item, dict) and item.get("creditCode") == credit_code
        ]
        if len(exact) != 1:
            raise TianyanchaProviderError("company search did not return one exact credit code")
        total = content.get("total", len(raw_items))
        try:
            candidate_count = int(total)
        except (TypeError, ValueError) as error:
            raise TianyanchaProviderError("company search candidate count is invalid") from error
        return exact[0], candidate_count

    @staticmethod
    def _registration_base(content: dict[str, object]) -> dict[str, object]:
        sources = content.get("sources")
        base = sources.get("base") if isinstance(sources, dict) else None
        if not isinstance(base, dict):
            raise TianyanchaProviderError("registration response is missing base identity data")
        return base

    @staticmethod
    def _region(base: dict[str, object]) -> str:
        city = str(base.get("city") or "").strip()
        economic_zone = str(base.get("economicFunctionZone1") or "").strip()
        district = str(base.get("district") or "").strip()
        parts = [city, economic_zone or district]
        unique = [part for index, part in enumerate(parts) if part and part not in parts[:index]]
        if not unique:
            raise TianyanchaProviderError("registration response is missing registered region")
        return "/".join(unique)

    @staticmethod
    def _data_updated_at(value: object) -> datetime | None:
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=ZoneInfo("Asia/Shanghai")
            )
        except ValueError as error:
            raise TianyanchaProviderError("registration data update time is invalid") from error

    @staticmethod
    def _normalized_name(value: str) -> str:
        return "".join(value.split()).casefold()

    @classmethod
    def _exact_name_candidate(
        cls,
        content: dict[str, object],
        legal_name: str,
    ) -> tuple[dict[str, object], int]:
        raw_items = content.get("items")
        if not isinstance(raw_items, list):
            raise TianyanchaProviderError("company search response is missing candidate items")
        normalized = cls._normalized_name(legal_name)
        exact = [
            item
            for item in raw_items
            if isinstance(item, dict)
            and isinstance(item.get("name"), str)
            and cls._normalized_name(str(item["name"])) == normalized
        ]
        codes = {
            str(item.get("creditCode") or "").strip().upper()
            for item in exact
            if str(item.get("creditCode") or "").strip()
        }
        if len(exact) != 1 or len(codes) != 1:
            raise TianyanchaIdentityNeedsInputError(
                "company name did not resolve to one exact legal entity; credit code required"
            )
        total = content.get("total", len(raw_items))
        try:
            candidate_count = int(total)
        except (TypeError, ValueError) as error:
            raise TianyanchaProviderError("company search candidate count is invalid") from error
        return exact[0], candidate_count

    def _lookup_identity_with(
        self,
        *,
        company_name: str | None,
        credit_code: str | None,
        call_tool: Callable[[str, dict[str, object]], _ToolResult],
    ) -> TianyanchaIdentityLookupResult:
        normalized_name = company_name.strip() if company_name else None
        normalized_code = validate_unified_credit_code(credit_code.upper()) if credit_code else None
        if not normalized_name and not normalized_code:
            raise ValueError("company_name or credit_code is required")

        search: _ToolResult | None = None
        candidate: dict[str, object] | None = None
        candidate_count: int | None = None
        if normalized_code is None:
            assert normalized_name is not None
            search = call_tool(
                "search_companies",
                {"searchKey": normalized_name, "pageNum": 1, "pageSize": 20},
            )
            candidate, candidate_count = self._exact_name_candidate(
                search.content,
                normalized_name,
            )
            raw_code = str(candidate.get("creditCode") or "").strip().upper()
            try:
                normalized_code = validate_unified_credit_code(raw_code)
            except ValueError as error:
                raise TianyanchaProviderError(
                    "exact company candidate has an invalid credit code"
                ) from error

        registration = call_tool(
            "get_company_registration_info",
            {"searchKey": normalized_code},
        )
        base = self._registration_base(registration.content)
        returned_code = str(base.get("creditCode") or "").strip().upper()
        if returned_code != normalized_code:
            raise TianyanchaProviderError("registration credit code does not match the query")
        legal_name = str(base.get("name") or "").strip()
        registration_status = str(base.get("regStatus") or "").strip()
        if not legal_name:
            raise TianyanchaProviderError("registration response is missing legal name")
        if not registration_status:
            raise TianyanchaProviderError("registration response is missing registration status")
        if (
            normalized_name
            and credit_code is None
            and self._normalized_name(legal_name) != self._normalized_name(normalized_name)
        ):
            raise TianyanchaIdentityNeedsInputError(
                "submitted legal name conflicts with the registered legal name; "
                "credit code required"
            )

        provider_id_value = (candidate or {}).get("id") or base.get("id") or base.get("companyId")
        provider_id = (
            str(provider_id_value).strip()
            if provider_id_value is not None and str(provider_id_value).strip()
            else None
        )
        checked_at = max(
            registration.fetched_at,
            search.fetched_at if search is not None else registration.fetched_at,
        )
        response_hash = (
            registration.response_hash
            if search is None
            else _sha256(f"{search.response_hash}:{registration.response_hash}")
        )
        return TianyanchaIdentityLookupResult(
            query_text=(
                normalized_code if company_name is None else normalized_name or normalized_code
            ),
            legal_name=legal_name,
            credit_code=normalized_code,
            registered_region=self._region(base),
            registration_status=registration_status,
            registration_authority=str(base.get("regInstitute") or "").strip() or None,
            provider_company_id=provider_id,
            canonical_url=(
                f"https://www.tianyancha.com/company/{provider_id}"
                if provider_id
                else "https://www.tianyancha.com/"
            ),
            checked_at=checked_at,
            data_updated_at=self._data_updated_at(base.get("updateTimes")),
            response_hash=response_hash,
            candidate_count=candidate_count,
        )

    def lookup_cached_identity(
        self,
        *,
        company_name: str | None,
        credit_code: str | None,
    ) -> TianyanchaIdentityLookupResult | None:
        """Return a complete fresh cached identity without making a network request."""
        cache_hits_before = self.cache_hits
        try:
            return self._lookup_identity_with(
                company_name=company_name,
                credit_code=credit_code,
                call_tool=self._call_cached_tool,
            )
        except _TianyanchaCacheMiss:
            # A partial cache cannot complete the identity lookup. Do not count
            # it here; the normal lookup will count any reusable entry once.
            self.cache_hits = cache_hits_before
            return None

    def lookup_identity(
        self,
        *,
        company_name: str | None,
        credit_code: str | None,
    ) -> TianyanchaIdentityLookupResult:
        return self._lookup_identity_with(
            company_name=company_name,
            credit_code=credit_code,
            call_tool=self._call_tool,
        )

    @staticmethod
    def _research_summary(content: dict[str, object]) -> str | None:
        value = content.get("_summary")
        if not isinstance(value, str) or not value.strip():
            return None
        return " ".join(value.split())[:1800]

    @staticmethod
    def _research_warnings(content: dict[str, object]) -> list[str]:
        raw = content.get("_warnings")
        if isinstance(raw, str) and raw.strip():
            return [" ".join(raw.split())[:300]]
        if isinstance(raw, list):
            return [" ".join(str(item).split())[:300] for item in raw if str(item).strip()][:10]
        return []

    @staticmethod
    def _research_item_count(content: dict[str, object]) -> int | None:
        explicit_counts: list[int] = []
        for key in ("total", "totalCount", "count"):
            value = content.get(key)
            if isinstance(value, int) and value >= 0:
                explicit_counts.append(value)
            if isinstance(value, str) and value.isdigit():
                explicit_counts.append(int(value))
        nested_counts: list[int] = []
        for value in content.values():
            if isinstance(value, dict):
                nested = TianyanchaIdentityProvider._research_item_count(value)
                if nested is not None:
                    nested_counts.append(nested)
        all_counts = explicit_counts + nested_counts
        if all_counts:
            return max(all_counts)
        for key in ("items", "records", "list", "data"):
            value = content.get(key)
            if isinstance(value, list):
                return len(value)
        return None

    @staticmethod
    def _research_has_data(content: dict[str, object]) -> bool:
        if content.get("_empty") is True:
            return False
        ignored = {"_summary", "_empty", "_warnings", "page", "pageNum", "pageSize"}
        for key, value in content.items():
            if key in ignored or value in (None, "", [], {}):
                continue
            if key in {"total", "totalCount", "count"} and str(value) == "0":
                continue
            return True
        return False

    @staticmethod
    def _subject_codes(value: object) -> set[str]:
        codes: set[str] = set()
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"creditCode", "credit_code"} and isinstance(item, str):
                    normalized = item.strip().upper()
                    if normalized:
                        codes.add(normalized)
                else:
                    codes.update(TianyanchaIdentityProvider._subject_codes(item))
        elif isinstance(value, list):
            for item in value:
                codes.update(TianyanchaIdentityProvider._subject_codes(item))
        return codes

    @staticmethod
    def _display_value(value: object, *, limit: int = 1000) -> str | None:
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            return None
        normalized = " ".join(str(value).split())
        return normalized[:limit] if normalized else None

    @classmethod
    def _detail_fields(
        cls,
        value: dict[str, object],
        mapping: tuple[tuple[str, str], ...],
    ) -> list[TianyanchaEvidenceField]:
        fields: list[TianyanchaEvidenceField] = []
        for key, label in mapping:
            displayed = cls._display_value(value.get(key))
            if displayed is not None:
                fields.append(TianyanchaEvidenceField(label=label, value=displayed))
        return fields

    @staticmethod
    def _https_url(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        parsed = urlsplit(normalized)
        try:
            port = parsed.port
        except ValueError:
            return None
        hostname = (parsed.hostname or "").lower()
        allowed_host = (
            hostname == "tianyancha.com"
            or hostname.endswith(".tianyancha.com")
            or hostname == "gov.cn"
            or hostname.endswith(".gov.cn")
        )
        if (
            parsed.scheme != "https"
            or not allowed_host
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
        ):
            return None
        return normalized[:1000]

    @classmethod
    def _module_projection(
        cls,
        module_code: TianyanchaResearchModuleCode,
        content: dict[str, object],
        registration_base: dict[str, object] | None,
    ) -> tuple[str, list[dict[str, str | None]], TianyanchaEvidenceDetail] | None:
        facts: list[dict[str, str | None]] = []

        if module_code == "company_base":
            sources = content.get("sources")
            holder = sources.get("holder") if isinstance(sources, dict) else None
            items = holder.get("items") if isinstance(holder, dict) else None
            total = holder.get("total") if isinstance(holder, dict) else None
            total_count = (
                int(total) if isinstance(total, (int, str)) and str(total).isdigit() else 0
            )
            if total_count == 0:
                return None
            facts.append({"name": "股东记录数", "value": str(total_count), "unit": "条"})
            summary_fields: list[TianyanchaEvidenceField] = []
            if registration_base is not None:
                summary_fields = cls._detail_fields(
                    registration_base,
                    (
                        ("regStatus", "登记状态"),
                        ("legalPersonName", "法定代表人"),
                        ("regCapital", "注册资本"),
                        ("estiblishTime", "成立日期"),
                        ("regInstitute", "登记机关"),
                    ),
                )
                facts.extend(
                    {"name": field.label, "value": field.value, "unit": None}
                    for field in summary_fields[:4]
                )
            records: list[TianyanchaEvidenceRecord] = []
            for index, item in enumerate(items if isinstance(items, list) else []):
                if not isinstance(item, dict):
                    continue
                name = cls._display_value(item.get("name"), limit=300)
                fields = cls._detail_fields(
                    item,
                    (
                        ("capital", "认缴信息"),
                        ("capitalActl", "实缴信息"),
                    ),
                )
                if name:
                    records.append(
                        TianyanchaEvidenceRecord(
                            title=name,
                            fields=fields,
                        )
                    )
                if len(records) == 10:
                    break
            description = f"授权数据源返回股东记录 {total_count} 条"
            if records:
                description += f"，本页展示前 {len(records)} 条。"
            else:
                description += "。"
            return (
                description,
                facts,
                TianyanchaEvidenceDetail(
                    heading="工商与股东基础资料",
                    description="仅展示与公司身份和股权结构有关的必要字段，不展示联系方式。",
                    total_records=total_count,
                    summary_fields=summary_fields,
                    records=records,
                ),
            )

        if module_code == "operation":
            items = content.get("items")
            raw_total = content.get("total") or content.get("items_total")
            total_count = (
                int(raw_total)
                if isinstance(raw_total, (int, str)) and str(raw_total).isdigit()
                else len(items)
                if isinstance(items, list)
                else 0
            )
            if total_count == 0:
                return None
            records = []
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                title = cls._display_value(item.get("title"), limit=300)
                if not title:
                    continue
                records.append(
                    TianyanchaEvidenceRecord(
                        title=title,
                        fields=cls._detail_fields(
                            item,
                            (
                                ("publishTime", "发布日期"),
                                ("stage", "阶段"),
                                ("bidAmount", "公示金额"),
                                ("purchaser", "采购方"),
                                ("bidWinner", "中标方"),
                                ("province", "地区"),
                                ("type", "类型"),
                                ("enterpriseIdentity", "企业身份"),
                            ),
                        ),
                        source_url=cls._https_url(item.get("bidUrl")),
                    )
                )
                if len(records) == 10:
                    break
            facts.append({"name": "招投标记录数", "value": str(total_count), "unit": "条"})
            return (
                f"授权数据源返回招投标记录 {total_count} 条，本页展示 {len(records)} 条可用明细。",
                facts,
                TianyanchaEvidenceDetail(
                    heading="招投标记录",
                    description="记录来自授权结构化数据；中标、采购或候选身份以每条记录字段为准。",
                    total_records=total_count,
                    records=records,
                ),
            )

        if module_code == "history":
            sources = content.get("sources")
            current = sources.get("cb") if isinstance(sources, dict) else None
            changes = current.get("changeList") if isinstance(current, dict) else None
            items = changes if isinstance(changes, list) else []
            if not items:
                return None
            records = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = cls._display_value(item.get("changeItem"), limit=300) or "工商变更"
                records.append(
                    TianyanchaEvidenceRecord(
                        title=title,
                        fields=cls._detail_fields(
                            item,
                            (
                                ("changeTime", "变更日期"),
                                ("contentBefore", "变更前"),
                                ("contentAfter", "变更后"),
                            ),
                        ),
                    )
                )
                if len(records) == 10:
                    break
            total_count = len(items)
            facts.append({"name": "历史变更记录数", "value": str(total_count), "unit": "条"})
            return (
                f"授权数据源返回工商历史变更记录 {total_count} 条，本页展示前 {len(records)} 条。",
                facts,
                TianyanchaEvidenceDetail(
                    heading="工商历史变更记录",
                    description="展示工商字段变更前后内容；不据此推断变更原因或投资影响。",
                    total_records=total_count,
                    records=records,
                ),
            )

        if module_code == "intellectual_property":
            mapping = (
                ("inventionLicensingCount", "发明授权"),
                ("inventionAnnouncementCount", "发明公布"),
                ("utilityModelCount", "实用新型"),
                ("appearanceDesignCount", "外观设计"),
                ("softwareCopyrightCount", "软件著作权"),
            )
            fields = cls._detail_fields(content, mapping)
            total_count = sum(int(field.value) for field in fields if field.value.isdigit())
            if total_count == 0:
                return None
            score_fields = cls._detail_fields(
                content,
                (
                    ("scienceAndTechnologyScore", "科技综合评分"),
                    ("scienceAndTechnologyGrade", "科技等级"),
                    ("innovationAbilityScore", "创新能力评分"),
                    ("researchCapabilityScore", "研发能力评分"),
                    ("scoreRank", "评分排名"),
                ),
            )
            facts.append({"name": "知识产权记录数", "value": str(total_count), "unit": "条"})
            return (
                f"授权数据源返回知识产权相关记录合计 {total_count} 条。",
                facts,
                TianyanchaEvidenceDetail(
                    heading="知识产权与创新指标",
                    description="数量和评分来自授权数据源，不代表平台对技术先进性或商业价值作出判断。",
                    total_records=total_count,
                    summary_fields=[*fields, *score_fields],
                ),
            )

        if module_code == "risk":
            raw_total = content.get("total")
            total_count = int(raw_total) if isinstance(raw_total, int) else 0
            if total_count == 0:
                return None
            records = []
            for item in [
                *(content.get("toolRisks") if isinstance(content.get("toolRisks"), list) else []),
                *(
                    content.get("relationRiskNotes")
                    if isinstance(content.get("relationRiskNotes"), list)
                    else []
                ),
            ]:
                if not isinstance(item, dict):
                    continue
                title = (
                    cls._display_value(item.get("title"), limit=300)
                    or cls._display_value(item.get("riskType"), limit=300)
                    or "风险相关记录"
                )
                records.append(
                    TianyanchaEvidenceRecord(
                        title=title,
                        fields=cls._detail_fields(
                            item,
                            (
                                ("riskType", "记录类别"),
                                ("riskLevel", "来源分级"),
                                ("count", "记录数量"),
                                ("relatedCompany", "关联主体"),
                            ),
                        ),
                    )
                )
                if len(records) == 10:
                    break
            facts.append({"name": "风险相关概览记录数", "value": str(total_count), "unit": "条"})
            return (
                f"授权数据源返回司法与合规相关概览记录 {total_count} 条，"
                "具体责任、状态和影响尚未判断。",
                facts,
                TianyanchaEvidenceDetail(
                    heading="司法与合规授权来源记录概览",
                    description="当前接口只返回分类和数量，不能作为公司存在重大风险的结论。",
                    total_records=total_count,
                    records=records,
                ),
            )

        raw_total = content.get("riskTotal") or content.get("total")
        total_count = int(raw_total) if isinstance(raw_total, int) else 0
        if total_count == 0:
            return None
        groups = content.get("riskGroups")
        records = []
        for item in groups if isinstance(groups, list) else []:
            if not isinstance(item, dict):
                continue
            title = cls._display_value(item.get("groupName"), limit=300) or "人员相关记录"
            records.append(
                TianyanchaEvidenceRecord(
                    title=title,
                    fields=cls._detail_fields(item, (("count", "记录数量"),)),
                )
            )
        facts.append({"name": "人员相关概览记录数", "value": str(total_count), "unit": "条"})
        group_count = content.get("groupCount") or content.get("total")
        summary_fields = []
        if isinstance(group_count, int):
            summary_fields.append(
                TianyanchaEvidenceField(label="记录分类数", value=str(group_count))
            )
        return (
            f"授权数据源返回人员相关概览记录 {total_count} 条，尚不能据此判断公司或个人存在风险。",
            facts,
            TianyanchaEvidenceDetail(
                heading="人员相关授权来源记录概览",
                description="当前接口只返回分类和数量；同名、任职关系、责任和影响均需进一步核对。",
                total_records=total_count,
                summary_fields=summary_fields,
                records=records[:10],
            ),
        )

    @classmethod
    def _module_record(
        cls,
        *,
        module_code: TianyanchaResearchModuleCode,
        content: dict[str, object],
        response_hash: str,
        canonical_url: str,
        registration_base: dict[str, object] | None,
    ) -> TianyanchaResearchRecord | None:
        if not cls._research_has_data(content):
            return None
        projection = cls._module_projection(module_code, content, registration_base)
        if projection is None:
            return None
        summary, facts, evidence_detail = projection

        event_type = {
            "company_base": "financing_cap_table",
            "risk": "legal_compliance",
            "intellectual_property": "product_technology",
            "operation": "contract_commercial",
            "history": "governance_people",
            "executive": "governance_people",
        }[module_code]
        is_source_overview = module_code in {"risk", "executive"}
        title = {
            "company_base": "工商与股东基础资料",
            "risk": "司法与合规授权来源记录概览",
            "intellectual_property": "知识产权资料",
            "operation": "招投标与经营公示资料",
            "history": "工商历史变更资料",
            "executive": "人员相关授权来源记录概览",
        }[module_code]
        return TianyanchaResearchRecord(
            external_record_id=f"{module_code}:overview",
            title=title,
            summary=summary,
            event_type=event_type,
            event_subtype=f"licensed_{module_code}_overview",
            direction="unknown" if is_source_overview else "neutral",
            materiality_score=45,
            risk_severity="none",
            confidence_score=Decimal("0.900") if not is_source_overview else Decimal("0.800"),
            facts=facts,
            uncertainties=(
                ["已取得授权来源概览，但具体记录、主体责任及影响尚未判断"]
                if is_source_overview
                else []
            ),
            canonical_url=canonical_url,
            evidence_excerpt=summary[:1000],
            classification=("licensed_source_record" if is_source_overview else "verified_fact"),
            classification_reasons=(
                ["licensed_source_record_impact_not_assessed"]
                if is_source_overview
                else ["licensed_structured_routine_fact"]
            ),
            evidence_detail=evidence_detail,
        )

    def _lookup_research_module_with(
        self,
        *,
        module_code: TianyanchaResearchModuleCode,
        legal_name: str,
        credit_code: str,
        provider_company_id: str | None,
        call_tool: Callable[[str, dict[str, object]], _ToolResult],
    ) -> TianyanchaResearchModuleResult:
        normalized_code = validate_unified_credit_code(credit_code.upper())
        normalized_name = legal_name.strip()
        if not normalized_name:
            raise ValueError("legal_name must not be blank")
        tool_name = self._RESEARCH_TOOLS[module_code]
        registration: _ToolResult | None = None
        registration_base: dict[str, object] | None = None
        if module_code in {"company_base", "executive"}:
            # Identity confirmation must have populated this cache first. Do not
            # spend a second identity call from the research stage.
            try:
                registration = self._call_cached_tool(
                    "get_company_registration_info",
                    {"searchKey": normalized_code},
                )
            except _TianyanchaCacheMiss as error:
                raise TianyanchaProviderError(
                    "confirmed identity cache is required before company research"
                ) from error
            registration_base = self._registration_base(registration.content)
            returned_code = str(registration_base.get("creditCode") or "").strip().upper()
            if returned_code != normalized_code:
                raise TianyanchaProviderError("research identity context does not match company")

        arguments: dict[str, object] = {"searchKey": normalized_code}
        if module_code in {"company_base", "operation"}:
            arguments.update({"pageNum": 1, "pageSize": 10})
        if module_code == "executive":
            human_name = str((registration_base or {}).get("legalPersonName") or "").strip()
            if not human_name:
                checked_at = registration.fetched_at if registration is not None else self._clock()
                response_hash = (
                    registration.response_hash
                    if registration is not None
                    else _sha256(f"{module_code}:{normalized_code}:missing-person")
                )
                return TianyanchaResearchModuleResult(
                    module_code=module_code,
                    tool_name=tool_name,
                    checked_at=checked_at,
                    response_hash=response_hash,
                    records=[],
                    no_reliable_data=True,
                    warnings=["registration_context_has_no_legal_representative"],
                )
            arguments = {"searchKey": normalized_name, "humanName": human_name}

        result = call_tool(tool_name, arguments)
        returned_codes = self._subject_codes(result.content)
        if returned_codes and normalized_code not in returned_codes:
            raise TianyanchaProviderError("research response credit code conflicts with company")
        checked_at = max(
            result.fetched_at,
            registration.fetched_at if registration is not None else result.fetched_at,
        )
        response_hash = (
            _sha256(f"{registration.response_hash}:{result.response_hash}")
            if registration is not None
            else result.response_hash
        )
        canonical_url = (
            f"https://www.tianyancha.com/company/{provider_company_id}"
            if provider_company_id
            else "https://www.tianyancha.com/"
        )
        record = self._module_record(
            module_code=module_code,
            content=result.content,
            response_hash=response_hash,
            canonical_url=canonical_url,
            registration_base=registration_base,
        )
        warnings = self._research_warnings(result.content)
        return TianyanchaResearchModuleResult(
            module_code=module_code,
            tool_name=tool_name,
            checked_at=checked_at,
            response_hash=response_hash,
            records=[record] if record is not None else [],
            no_reliable_data=record is None,
            warnings=warnings,
        )

    def lookup_cached_research_module(
        self,
        *,
        module_code: TianyanchaResearchModuleCode,
        legal_name: str,
        credit_code: str,
        provider_company_id: str | None,
    ) -> TianyanchaResearchModuleResult | None:
        cache_hits_before = self.cache_hits
        try:
            return self._lookup_research_module_with(
                module_code=module_code,
                legal_name=legal_name,
                credit_code=credit_code,
                provider_company_id=provider_company_id,
                call_tool=self._call_cached_tool,
            )
        except _TianyanchaCacheMiss:
            self.cache_hits = cache_hits_before
            return None

    def lookup_research_module(
        self,
        *,
        module_code: TianyanchaResearchModuleCode,
        legal_name: str,
        credit_code: str,
        provider_company_id: str | None,
    ) -> TianyanchaResearchModuleResult:
        return self._lookup_research_module_with(
            module_code=module_code,
            legal_name=legal_name,
            credit_code=credit_code,
            provider_company_id=provider_company_id,
            call_tool=self._call_tool,
        )

    def _record_for_query(self, query: TianyanchaIdentityQuery) -> OfficialIdentityRecord:
        search = self._call_tool(
            "search_companies",
            {"searchKey": query.legal_name, "pageNum": 1, "pageSize": 20},
        )
        candidate, candidate_count = self._exact_candidate(search.content, query.credit_code)
        company_id = candidate.get("id")
        if not isinstance(company_id, (int, str)) or not str(company_id).strip():
            raise TianyanchaProviderError("exact company candidate is missing provider company ID")
        registration = self._call_tool(
            "get_company_registration_info",
            {"searchKey": query.credit_code},
        )
        base = self._registration_base(registration.content)
        returned_code = base.get("creditCode")
        if returned_code != query.credit_code:
            raise TianyanchaProviderError("registration credit code does not match the query")
        legal_name = base.get("name")
        status = base.get("regStatus")
        if not isinstance(legal_name, str) or not legal_name.strip():
            raise TianyanchaProviderError("registration response is missing legal name")
        if not isinstance(status, str) or not status.strip():
            raise TianyanchaProviderError("registration response is missing registration status")
        checked_at = max(search.fetched_at, registration.fetched_at)
        data_updated_at = self._data_updated_at(base.get("updateTimes"))
        registration_authority = str(base.get("regInstitute") or "").strip() or None
        provider_id = str(company_id).strip()
        return OfficialIdentityRecord(
            external_record_id=f"tianyancha:{provider_id}:{registration.response_hash[:16]}",
            query_text=query.legal_name,
            legal_name=legal_name.strip(),
            credit_code=query.credit_code,
            registered_region=self._region(base),
            registration_status=status.strip(),
            canonical_url=f"https://www.tianyancha.com/company/{provider_id}",
            checked_at=checked_at,
            registration_authority=registration_authority,
            data_updated_at=data_updated_at,
            provider_metadata={
                "candidate_count": candidate_count,
                "provider_company_id": provider_id,
                "search_response_hash": search.response_hash,
                "registration_response_hash": registration.response_hash,
            },
        )

    def load(self) -> LoadedOfficialIdentityImport:
        if self.manifest is None or self.manifest_path is None:
            raise RuntimeError("manifest-backed load requires a manifest path")
        try:
            records = [self._record_for_query(query) for query in self.manifest.queries]
            queried_at = max(record.checked_at for record in records)
            content_fingerprint = _sha256(
                _canonical_json([record.model_dump(mode="json") for record in records])
            )
            batch = OfficialIdentityImportBatch(
                schema_version="1.0",
                batch_id=f"tyc-{self.manifest.request_id}-{content_fingerprint[:12]}",
                queried_at=queried_at,
                source=OfficialIdentitySource(
                    code="tianyancha_licensed_business_data",
                    name="天眼查授权工商数据",
                    base_url="https://www.tianyancha.com/",
                ),
                original_query=f"天眼查授权工商身份批次：{self.manifest.request_id}",
                target_company_hint=f"{len(records)} 家公司（首家公司：{records[0].query_text}）",
                verification_basis="licensed_business_data",
                license_status="permission_confirmed",
                records=records,
            )
            normalized = _canonical_json(batch.model_dump(mode="json"))
            return LoadedOfficialIdentityImport(
                batch=batch,
                file_hash=_sha256(normalized),
                source_filename=self.manifest_path.name,
            )
        finally:
            self.close()
