from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
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

    def __init__(
        self,
        manifest_path: str | Path,
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
        if tool_name not in {"search_companies", "get_company_registration_info"}:
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
            if self._owns_client:
                self._client.close()
