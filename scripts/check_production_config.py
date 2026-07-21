from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

from sqlalchemy.engine import URL, make_url

from backend.app.config import Settings

INITIAL_DISABLED_SWITCHES = (
    "EXTERNAL_CALLS_ENABLED",
    "PAID_API_CALLS_ENABLED",
    "AUTO_REFRESH_ENABLED",
    "AUTO_PUBLISH_ENABLED",
    "TRUSTED_SOURCE_CALLS_ENABLED",
    "SOURCE_MONITOR_SCHEDULER_ENABLED",
    "TIANYANCHA_IDENTITY_CALLS_ENABLED",
    "REVIEW_WORKBENCH_ENABLED",
)
PLACEHOLDER_MARKERS = ("replace_", "replace-", "changeme", "example.com", "example.invalid")


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    if any(marker in value.lower() for marker in PLACEHOLDER_MARKERS):
        raise RuntimeError(f"{name} still contains a placeholder")
    return value


def _postgres_url(name: str, value: str, *, libpq_only: bool = False) -> URL:
    try:
        url = make_url(value)
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"{name} must be a valid PostgreSQL URL") from error
    allowed_drivers = {"postgresql"} if libpq_only else {"postgresql", "postgresql+psycopg"}
    if url.drivername not in allowed_drivers:
        raise RuntimeError(f"{name} must be a PostgreSQL URL")
    if not url.host or not url.database or not url.username or url.password is None:
        raise RuntimeError(f"{name} must include host, database, user, and password")
    return url


def _url_identity(url: URL) -> tuple[str, int, str, str]:
    return (
        str(url.host).lower().rstrip("."),
        url.port or 5432,
        str(url.database),
        str(url.username),
    )


def _check_site_address(value: str) -> str:
    explicit_scheme = "://" in value
    parsed = urlsplit(value if explicit_scheme else f"https://{value}")
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("SITE_ADDRESS must be a bare HTTPS host without credentials or paths")
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    allow_local = os.getenv("ALLOW_INSECURE_LOCALHOST", "false").strip().lower() == "true"
    if parsed.hostname in local_hosts and not allow_local:
        raise RuntimeError("localhost SITE_ADDRESS requires explicit local test authorization")
    if parsed.scheme == "http":
        if parsed.hostname not in local_hosts or not allow_local:
            raise RuntimeError("SITE_ADDRESS must use HTTPS outside an explicit localhost test")
    return parsed.scheme


def check_production_config() -> dict[str, object]:
    settings = Settings.from_env()
    if settings.app_mode != "production":
        raise RuntimeError("APP_MODE must be production")
    _required("CLOUDBASE_ENV_ID")

    site_scheme = _check_site_address(_required("SITE_ADDRESS"))
    for name in INITIAL_DISABLED_SWITCHES:
        if os.getenv(name, "false").strip().lower() != "false":
            raise RuntimeError(f"{name} must be false for the initial external environment")

    admin_url = _postgres_url("DATABASE_ADMIN_URL", _required("DATABASE_ADMIN_URL"))
    app_url = _postgres_url("DATABASE_URL", _required("DATABASE_URL"))
    backup_url = _postgres_url(
        "BACKUP_DATABASE_URL",
        _required("BACKUP_DATABASE_URL"),
        libpq_only=True,
    )
    app_user = _required("APP_DATABASE_USER")
    app_password = _required("APP_DATABASE_PASSWORD")

    if admin_url.username == app_url.username:
        raise RuntimeError("migration and application database users must differ")
    if str(admin_url.password) == str(app_url.password):
        raise RuntimeError("migration and application database passwords must differ")
    if app_url.username != app_user or str(app_url.password) != app_password:
        raise RuntimeError("application database credentials do not match DATABASE_URL")
    if _url_identity(backup_url) != _url_identity(admin_url):
        raise RuntimeError("BACKUP_DATABASE_URL must target the migration database identity")
    if str(backup_url.password) != str(admin_url.password):
        raise RuntimeError("BACKUP_DATABASE_URL password must match DATABASE_ADMIN_URL")

    return {
        "status": "ok",
        "app_mode": settings.app_mode,
        "auth_provider": settings.auth_provider,
        "database": "postgresql",
        "site_scheme": site_scheme,
        "initial_safety_switches": "closed",
    }


def main() -> None:
    print(json.dumps(check_production_config(), ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
