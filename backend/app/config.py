from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_positive_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} must be a positive integer") from error
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _as_non_negative_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ValueError(f"{name} must be a non-negative integer") from error
    if value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _as_rate(name: str, default: str) -> Decimal:
    raw_value = os.getenv(name, default)
    try:
        value = Decimal(raw_value)
    except InvalidOperation as error:
        raise ValueError(f"{name} must be a decimal between 0 and 1") from error
    if value < 0 or value > 1:
        raise ValueError(f"{name} must be a decimal between 0 and 1")
    return value


@dataclass(frozen=True)
class RefreshPolicy:
    version: str = "demo-v1"
    recent_query_ttl_days: int = 14
    request_cooldown_hours: int = 24
    mock_worker_lease_seconds: int = 60

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("REFRESH_POLICY_VERSION must not be empty")
        if self.recent_query_ttl_days <= 0:
            raise ValueError("RECENT_QUERY_TTL_DAYS must be a positive integer")
        if self.request_cooldown_hours <= 0:
            raise ValueError("REFRESH_REQUEST_COOLDOWN_HOURS must be a positive integer")
        if self.mock_worker_lease_seconds <= 0:
            raise ValueError("MOCK_WORKER_LEASE_SECONDS must be a positive integer")


@dataclass(frozen=True)
class PublicationPolicy:
    version: str = "identity-first-v1"
    enabled: bool = False
    min_confidence: Decimal = Decimal("0.80")
    source_url_timeout_seconds: int = 10
    max_source_url_checks_per_import: int = 20

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("PUBLICATION_POLICY_VERSION must not be empty")
        if self.min_confidence < 0 or self.min_confidence > 1:
            raise ValueError("AUTO_PUBLISH_MIN_CONFIDENCE must be between 0 and 1")
        if self.source_url_timeout_seconds <= 0:
            raise ValueError("SOURCE_URL_TIMEOUT_SECONDS must be a positive integer")
        if self.max_source_url_checks_per_import <= 0:
            raise ValueError("SOURCE_URL_MAX_CHECKS_PER_IMPORT must be a positive integer")


@dataclass(frozen=True)
class IdentityPolicy:
    version: str = "official-identity-v1"
    verification_ttl_days: int = 30

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("IDENTITY_POLICY_VERSION must not be empty")
        if self.verification_ttl_days <= 0:
            raise ValueError("IDENTITY_VERIFICATION_TTL_DAYS must be a positive integer")


TIANYANCHA_CORE_ENDPOINT = "https://mcp.tianyancha.com/v1/core/tools/call"


@dataclass(frozen=True)
class TianyanchaIdentityPolicy:
    version: str = "tianyancha-licensed-identity-v1"
    endpoint_url: str = TIANYANCHA_CORE_ENDPOINT
    max_companies_per_run: int = 10
    max_requests_per_run: int = 24
    max_response_bytes: int = 1_000_000
    timeout_seconds: int = 10
    retry_limit: int = 1
    min_request_interval_ms: int = 1_000
    cache_ttl_days: int = 30

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("TIANYANCHA_IDENTITY_POLICY_VERSION must not be empty")
        if self.endpoint_url != TIANYANCHA_CORE_ENDPOINT:
            raise ValueError("TIANYANCHA_IDENTITY_ENDPOINT must use the approved endpoint")
        for name, value in (
            ("TIANYANCHA_IDENTITY_MAX_COMPANIES", self.max_companies_per_run),
            ("TIANYANCHA_IDENTITY_MAX_REQUESTS", self.max_requests_per_run),
            ("TIANYANCHA_IDENTITY_MAX_RESPONSE_BYTES", self.max_response_bytes),
            ("TIANYANCHA_IDENTITY_TIMEOUT_SECONDS", self.timeout_seconds),
            ("TIANYANCHA_IDENTITY_CACHE_TTL_DAYS", self.cache_ttl_days),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.retry_limit < 0:
            raise ValueError("TIANYANCHA_IDENTITY_RETRY_LIMIT must be non-negative")
        if self.min_request_interval_ms < 0:
            raise ValueError("TIANYANCHA_IDENTITY_MIN_REQUEST_INTERVAL_MS must be non-negative")
        minimum_requests = self.max_companies_per_run * 2
        if self.max_requests_per_run < minimum_requests:
            raise ValueError("TIANYANCHA_IDENTITY_MAX_REQUESTS must allow two calls per company")


@dataclass(frozen=True)
class SourceMonitoringPolicy:
    version: str = "trusted-source-v1"
    max_requests_per_run: int = 10
    max_download_bytes_per_run: int = 5_000_000
    max_response_bytes: int = 1_000_000
    timeout_seconds: int = 10
    retry_limit: int = 1
    max_redirects: int = 3
    min_request_interval_ms: int = 1_000
    worker_lease_seconds: int = 300
    scheduler_max_sources_per_run: int = 10
    failure_backoff_max_multiplier: int = 8
    user_agent: str = "DealflowRadarSourceMonitor/1.0 (controlled low-frequency monitoring)"

    def __post_init__(self) -> None:
        for name, value in (
            ("SOURCE_MONITOR_MAX_REQUESTS", self.max_requests_per_run),
            ("SOURCE_MONITOR_MAX_DOWNLOAD_BYTES", self.max_download_bytes_per_run),
            ("SOURCE_MONITOR_MAX_RESPONSE_BYTES", self.max_response_bytes),
            ("SOURCE_MONITOR_TIMEOUT_SECONDS", self.timeout_seconds),
            ("SOURCE_MONITOR_MAX_REDIRECTS", self.max_redirects),
            ("SOURCE_MONITOR_WORKER_LEASE_SECONDS", self.worker_lease_seconds),
            ("SOURCE_MONITOR_SCHEDULER_MAX_SOURCES", self.scheduler_max_sources_per_run),
            (
                "SOURCE_MONITOR_FAILURE_BACKOFF_MAX_MULTIPLIER",
                self.failure_backoff_max_multiplier,
            ),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.retry_limit < 0:
            raise ValueError("SOURCE_MONITOR_RETRY_LIMIT must be a non-negative integer")
        if self.min_request_interval_ms < 0:
            raise ValueError("SOURCE_MONITOR_MIN_REQUEST_INTERVAL_MS must be non-negative")
        if not self.version.strip():
            raise ValueError("SOURCE_MONITOR_POLICY_VERSION must not be empty")
        if not self.user_agent.strip():
            raise ValueError("SOURCE_MONITOR_USER_AGENT must not be empty")


_CLOUDBASE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")


@dataclass(frozen=True)
class CloudBaseAuthPolicy:
    env_id: str = ""
    client_id: str = ""
    timeout_seconds: int = 5
    max_response_bytes: int = 64_000
    user_agent: str = "DealflowRadarAuth/1.0"

    def __post_init__(self) -> None:
        for name, value in (
            ("CLOUDBASE_ENV_ID", self.env_id),
            ("CLOUDBASE_CLIENT_ID", self.client_id),
        ):
            if value and not _CLOUDBASE_IDENTIFIER_PATTERN.fullmatch(value):
                raise ValueError(f"{name} contains unsupported characters")
        if self.timeout_seconds <= 0:
            raise ValueError("CLOUDBASE_AUTH_TIMEOUT_SECONDS must be a positive integer")
        if self.max_response_bytes <= 0:
            raise ValueError("CLOUDBASE_AUTH_MAX_RESPONSE_BYTES must be a positive integer")
        if not self.user_agent.strip():
            raise ValueError("CLOUDBASE_AUTH_USER_AGENT must not be empty")


@dataclass(frozen=True)
class Settings:
    database_url: str
    app_mode: str
    external_calls_enabled: bool
    paid_api_calls_enabled: bool
    auto_refresh_enabled: bool
    trusted_source_calls_enabled: bool = False
    source_monitor_scheduler_enabled: bool = False
    tianyancha_identity_calls_enabled: bool = False
    review_workbench_enabled: bool = False
    auth_provider: str = "demo"
    refresh_policy: RefreshPolicy = field(default_factory=RefreshPolicy)
    publication_policy: PublicationPolicy = field(default_factory=PublicationPolicy)
    identity_policy: IdentityPolicy = field(default_factory=IdentityPolicy)
    tianyancha_identity_policy: TianyanchaIdentityPolicy = field(
        default_factory=TianyanchaIdentityPolicy
    )
    source_monitoring_policy: SourceMonitoringPolicy = field(default_factory=SourceMonitoringPolicy)
    cloudbase_auth_policy: CloudBaseAuthPolicy = field(default_factory=CloudBaseAuthPolicy)

    def __post_init__(self) -> None:
        if self.auth_provider not in {"demo", "cloudbase"}:
            raise ValueError("AUTH_PROVIDER must be demo or cloudbase")
        if self.auth_provider == "cloudbase" and not self.cloudbase_auth_policy.env_id:
            raise ValueError("CLOUDBASE_ENV_ID is required when AUTH_PROVIDER=cloudbase")

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=os.getenv(
                "DATABASE_URL",
                "postgresql+psycopg://equity_app:replace_app_password@localhost:5432/equity_radar",
            ),
            app_mode=os.getenv("APP_MODE", "demo"),
            external_calls_enabled=_as_bool(os.getenv("EXTERNAL_CALLS_ENABLED", "false")),
            paid_api_calls_enabled=_as_bool(os.getenv("PAID_API_CALLS_ENABLED", "false")),
            auto_refresh_enabled=_as_bool(os.getenv("AUTO_REFRESH_ENABLED", "false")),
            trusted_source_calls_enabled=_as_bool(
                os.getenv("TRUSTED_SOURCE_CALLS_ENABLED", "false")
            ),
            source_monitor_scheduler_enabled=_as_bool(
                os.getenv("SOURCE_MONITOR_SCHEDULER_ENABLED", "false")
            ),
            tianyancha_identity_calls_enabled=_as_bool(
                os.getenv("TIANYANCHA_IDENTITY_CALLS_ENABLED", "false")
            ),
            review_workbench_enabled=_as_bool(os.getenv("REVIEW_WORKBENCH_ENABLED", "false")),
            auth_provider=os.getenv("AUTH_PROVIDER", "demo").strip().lower(),
            refresh_policy=RefreshPolicy(
                version=os.getenv("REFRESH_POLICY_VERSION", "demo-v1"),
                recent_query_ttl_days=_as_positive_int("RECENT_QUERY_TTL_DAYS", 14),
                request_cooldown_hours=_as_positive_int("REFRESH_REQUEST_COOLDOWN_HOURS", 24),
                mock_worker_lease_seconds=_as_positive_int("MOCK_WORKER_LEASE_SECONDS", 60),
            ),
            publication_policy=PublicationPolicy(
                version=os.getenv("PUBLICATION_POLICY_VERSION", "identity-first-v1"),
                enabled=_as_bool(os.getenv("AUTO_PUBLISH_ENABLED", "false")),
                min_confidence=_as_rate("AUTO_PUBLISH_MIN_CONFIDENCE", "0.80"),
                source_url_timeout_seconds=_as_positive_int("SOURCE_URL_TIMEOUT_SECONDS", 10),
                max_source_url_checks_per_import=_as_positive_int(
                    "SOURCE_URL_MAX_CHECKS_PER_IMPORT", 20
                ),
            ),
            identity_policy=IdentityPolicy(
                version=os.getenv("IDENTITY_POLICY_VERSION", "official-identity-v1"),
                verification_ttl_days=_as_positive_int("IDENTITY_VERIFICATION_TTL_DAYS", 30),
            ),
            tianyancha_identity_policy=TianyanchaIdentityPolicy(
                version=os.getenv(
                    "TIANYANCHA_IDENTITY_POLICY_VERSION",
                    "tianyancha-licensed-identity-v1",
                ),
                endpoint_url=os.getenv(
                    "TIANYANCHA_IDENTITY_ENDPOINT",
                    TIANYANCHA_CORE_ENDPOINT,
                ),
                max_companies_per_run=_as_positive_int("TIANYANCHA_IDENTITY_MAX_COMPANIES", 10),
                max_requests_per_run=_as_positive_int("TIANYANCHA_IDENTITY_MAX_REQUESTS", 24),
                max_response_bytes=_as_positive_int(
                    "TIANYANCHA_IDENTITY_MAX_RESPONSE_BYTES", 1_000_000
                ),
                timeout_seconds=_as_positive_int("TIANYANCHA_IDENTITY_TIMEOUT_SECONDS", 10),
                retry_limit=_as_non_negative_int("TIANYANCHA_IDENTITY_RETRY_LIMIT", 1),
                min_request_interval_ms=_as_non_negative_int(
                    "TIANYANCHA_IDENTITY_MIN_REQUEST_INTERVAL_MS", 1_000
                ),
                cache_ttl_days=_as_positive_int("TIANYANCHA_IDENTITY_CACHE_TTL_DAYS", 30),
            ),
            source_monitoring_policy=SourceMonitoringPolicy(
                version=os.getenv("SOURCE_MONITOR_POLICY_VERSION", "trusted-source-v1"),
                max_requests_per_run=_as_positive_int("SOURCE_MONITOR_MAX_REQUESTS", 10),
                max_download_bytes_per_run=_as_positive_int(
                    "SOURCE_MONITOR_MAX_DOWNLOAD_BYTES", 5_000_000
                ),
                max_response_bytes=_as_positive_int("SOURCE_MONITOR_MAX_RESPONSE_BYTES", 1_000_000),
                timeout_seconds=_as_positive_int("SOURCE_MONITOR_TIMEOUT_SECONDS", 10),
                retry_limit=_as_non_negative_int("SOURCE_MONITOR_RETRY_LIMIT", 1),
                max_redirects=_as_positive_int("SOURCE_MONITOR_MAX_REDIRECTS", 3),
                min_request_interval_ms=_as_non_negative_int(
                    "SOURCE_MONITOR_MIN_REQUEST_INTERVAL_MS", 1_000
                ),
                worker_lease_seconds=_as_positive_int("SOURCE_MONITOR_WORKER_LEASE_SECONDS", 300),
                scheduler_max_sources_per_run=_as_positive_int(
                    "SOURCE_MONITOR_SCHEDULER_MAX_SOURCES", 10
                ),
                failure_backoff_max_multiplier=_as_positive_int(
                    "SOURCE_MONITOR_FAILURE_BACKOFF_MAX_MULTIPLIER", 8
                ),
                user_agent=os.getenv(
                    "SOURCE_MONITOR_USER_AGENT",
                    "DealflowRadarSourceMonitor/1.0 (controlled low-frequency monitoring)",
                ),
            ),
            cloudbase_auth_policy=CloudBaseAuthPolicy(
                env_id=os.getenv("CLOUDBASE_ENV_ID", "").strip(),
                client_id=os.getenv("CLOUDBASE_CLIENT_ID", "").strip(),
                timeout_seconds=_as_positive_int("CLOUDBASE_AUTH_TIMEOUT_SECONDS", 5),
                max_response_bytes=_as_positive_int("CLOUDBASE_AUTH_MAX_RESPONSE_BYTES", 64_000),
                user_agent=os.getenv("CLOUDBASE_AUTH_USER_AGENT", "DealflowRadarAuth/1.0"),
            ),
        )
