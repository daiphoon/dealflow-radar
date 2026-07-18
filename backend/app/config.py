from __future__ import annotations

import os
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
    user_agent: str = "DealflowRadarSourceMonitor/1.0 (controlled low-frequency monitoring)"

    def __post_init__(self) -> None:
        for name, value in (
            ("SOURCE_MONITOR_MAX_REQUESTS", self.max_requests_per_run),
            ("SOURCE_MONITOR_MAX_DOWNLOAD_BYTES", self.max_download_bytes_per_run),
            ("SOURCE_MONITOR_MAX_RESPONSE_BYTES", self.max_response_bytes),
            ("SOURCE_MONITOR_TIMEOUT_SECONDS", self.timeout_seconds),
            ("SOURCE_MONITOR_MAX_REDIRECTS", self.max_redirects),
            ("SOURCE_MONITOR_WORKER_LEASE_SECONDS", self.worker_lease_seconds),
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


@dataclass(frozen=True)
class Settings:
    database_url: str
    app_mode: str
    external_calls_enabled: bool
    paid_api_calls_enabled: bool
    auto_refresh_enabled: bool
    trusted_source_calls_enabled: bool = False
    review_workbench_enabled: bool = False
    refresh_policy: RefreshPolicy = field(default_factory=RefreshPolicy)
    publication_policy: PublicationPolicy = field(default_factory=PublicationPolicy)
    identity_policy: IdentityPolicy = field(default_factory=IdentityPolicy)
    source_monitoring_policy: SourceMonitoringPolicy = field(default_factory=SourceMonitoringPolicy)

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
            review_workbench_enabled=_as_bool(os.getenv("REVIEW_WORKBENCH_ENABLED", "false")),
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
                user_agent=os.getenv(
                    "SOURCE_MONITOR_USER_AGENT",
                    "DealflowRadarSourceMonitor/1.0 (controlled low-frequency monitoring)",
                ),
            ),
        )
