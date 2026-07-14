from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    app_mode: str
    external_calls_enabled: bool
    paid_api_calls_enabled: bool
    auto_refresh_enabled: bool

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
        )
