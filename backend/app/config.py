from __future__ import annotations

import os
from dataclasses import dataclass, field


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


@dataclass(frozen=True)
class RefreshPolicy:
    version: str = "demo-v1"
    recent_query_ttl_days: int = 14
    request_cooldown_hours: int = 24

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("REFRESH_POLICY_VERSION must not be empty")
        if self.recent_query_ttl_days <= 0:
            raise ValueError("RECENT_QUERY_TTL_DAYS must be a positive integer")
        if self.request_cooldown_hours <= 0:
            raise ValueError("REFRESH_REQUEST_COOLDOWN_HOURS must be a positive integer")


@dataclass(frozen=True)
class Settings:
    database_url: str
    app_mode: str
    external_calls_enabled: bool
    paid_api_calls_enabled: bool
    auto_refresh_enabled: bool
    refresh_policy: RefreshPolicy = field(default_factory=RefreshPolicy)

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
            refresh_policy=RefreshPolicy(
                version=os.getenv("REFRESH_POLICY_VERSION", "demo-v1"),
                recent_query_ttl_days=_as_positive_int("RECENT_QUERY_TTL_DAYS", 14),
                request_cooldown_hours=_as_positive_int("REFRESH_REQUEST_COOLDOWN_HOURS", 24),
            ),
        )
