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


def _as_non_negative_decimal(name: str, default: str) -> Decimal:
    raw_value = os.getenv(name, default)
    try:
        value = Decimal(raw_value)
    except InvalidOperation as error:
        raise ValueError(f"{name} must be a non-negative decimal") from error
    if value < 0:
        raise ValueError(f"{name} must be a non-negative decimal")
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
    max_pdf_pages: int = 40
    max_pdf_text_chars: int = 100_000
    pdf_parse_timeout_seconds: int = 5
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
            ("SOURCE_MONITOR_MAX_PDF_PAGES", self.max_pdf_pages),
            ("SOURCE_MONITOR_MAX_PDF_TEXT_CHARS", self.max_pdf_text_chars),
            ("SOURCE_MONITOR_PDF_PARSE_TIMEOUT_SECONDS", self.pdf_parse_timeout_seconds),
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


@dataclass(frozen=True)
class WebResearchPolicy:
    version: str = "bounded-web-v3"
    primary_provider: str = "baidu"
    fallback_provider: str = "bocha"
    search_cache_ttl_days: int = 14
    document_cache_ttl_days: int = 14
    max_search_calls_per_job: int = 4
    max_results_per_search: int = 10
    max_candidate_urls: int = 12
    max_documents_per_job: int = 3
    max_fetch_requests_per_job: int = 8
    identity_max_search_calls: int = 6
    identity_max_fetch_requests: int = 24
    identity_max_download_bytes: int = 4_000_000
    max_download_bytes_per_job: int = 2_000_000
    max_response_bytes: int = 500_000
    max_pdf_pages: int = 40
    max_pdf_text_chars: int = 100_000
    pdf_parse_timeout_seconds: int = 5
    max_elapsed_seconds: int = 180
    daily_search_call_limit: int = 50
    monthly_search_call_limit: int = 1_500
    timeout_seconds: int = 15
    worker_lease_seconds: int = 300
    fallback_min_subject_results: int = 1
    recent_change_window_days: int = 365
    user_agent: str = "DealflowRadarWebResearch/1.0 (bounded evidence research)"

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("WEB_RESEARCH_POLICY_VERSION must not be empty")
        if self.primary_provider not in {"baidu", "bocha"}:
            raise ValueError("WEB_RESEARCH_PRIMARY_PROVIDER must be baidu or bocha")
        if self.fallback_provider not in {"baidu", "bocha"}:
            raise ValueError("WEB_RESEARCH_FALLBACK_PROVIDER must be baidu or bocha")
        if self.primary_provider == self.fallback_provider:
            raise ValueError("web research primary and fallback providers must differ")
        for name, value in (
            ("WEB_RESEARCH_SEARCH_CACHE_TTL_DAYS", self.search_cache_ttl_days),
            ("WEB_RESEARCH_DOCUMENT_CACHE_TTL_DAYS", self.document_cache_ttl_days),
            ("WEB_RESEARCH_MAX_SEARCH_CALLS_PER_JOB", self.max_search_calls_per_job),
            ("WEB_RESEARCH_MAX_RESULTS_PER_SEARCH", self.max_results_per_search),
            ("WEB_RESEARCH_MAX_CANDIDATE_URLS", self.max_candidate_urls),
            ("WEB_RESEARCH_MAX_DOCUMENTS_PER_JOB", self.max_documents_per_job),
            ("WEB_RESEARCH_MAX_FETCH_REQUESTS_PER_JOB", self.max_fetch_requests_per_job),
            ("IDENTITY_MAX_SEARCH_CALLS", self.identity_max_search_calls),
            ("IDENTITY_MAX_FETCH_REQUESTS", self.identity_max_fetch_requests),
            ("IDENTITY_MAX_DOWNLOAD_BYTES", self.identity_max_download_bytes),
            ("WEB_RESEARCH_MAX_DOWNLOAD_BYTES_PER_JOB", self.max_download_bytes_per_job),
            ("WEB_RESEARCH_MAX_RESPONSE_BYTES", self.max_response_bytes),
            ("WEB_RESEARCH_MAX_PDF_PAGES", self.max_pdf_pages),
            ("WEB_RESEARCH_MAX_PDF_TEXT_CHARS", self.max_pdf_text_chars),
            ("WEB_RESEARCH_PDF_PARSE_TIMEOUT_SECONDS", self.pdf_parse_timeout_seconds),
            ("WEB_RESEARCH_MAX_ELAPSED_SECONDS", self.max_elapsed_seconds),
            ("WEB_RESEARCH_DAILY_SEARCH_CALL_LIMIT", self.daily_search_call_limit),
            ("WEB_RESEARCH_MONTHLY_SEARCH_CALL_LIMIT", self.monthly_search_call_limit),
            ("WEB_RESEARCH_TIMEOUT_SECONDS", self.timeout_seconds),
            ("WEB_RESEARCH_WORKER_LEASE_SECONDS", self.worker_lease_seconds),
            ("WEB_RESEARCH_FALLBACK_MIN_SUBJECT_RESULTS", self.fallback_min_subject_results),
            ("WEB_RESEARCH_RECENT_CHANGE_WINDOW_DAYS", self.recent_change_window_days),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_documents_per_job > self.max_candidate_urls:
            raise ValueError("WEB_RESEARCH_MAX_DOCUMENTS_PER_JOB cannot exceed candidate URLs")
        if self.max_results_per_search > 10:
            raise ValueError("WEB_RESEARCH_MAX_RESULTS_PER_SEARCH cannot exceed 10 in V1")
        if self.recent_change_window_days > 365:
            raise ValueError("WEB_RESEARCH_RECENT_CHANGE_WINDOW_DAYS cannot exceed 365 in V1")
        if self.max_search_calls_per_job < 2:
            raise ValueError("WEB_RESEARCH_MAX_SEARCH_CALLS_PER_JOB must allow the fixed plan")
        if self.max_fetch_requests_per_job < self.max_documents_per_job:
            raise ValueError("web research fetch request budget is too small")
        if self.monthly_search_call_limit < self.daily_search_call_limit:
            raise ValueError("monthly web research search limit must not be below daily limit")
        if not self.user_agent.strip():
            raise ValueError("WEB_RESEARCH_USER_AGENT must not be empty")


_CLOUDBASE_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")


@dataclass(frozen=True)
class CloudBaseAuthPolicy:
    env_id: str = ""
    client_id: str = ""
    timeout_seconds: int = 5
    max_response_bytes: int = 64_000
    user_agent: str = "DealflowRadarAuth/1.0"
    phone_login_enabled: bool = False
    phone_code_cooldown_seconds: int = 60
    phone_daily_limit: int = 5
    phone_environment_daily_limit: int = 50

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
        for name, value in (
            ("AUTH_PHONE_CODE_COOLDOWN_SECONDS", self.phone_code_cooldown_seconds),
            ("AUTH_PHONE_DAILY_LIMIT", self.phone_daily_limit),
            ("AUTH_PHONE_ENV_DAILY_LIMIT", self.phone_environment_daily_limit),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.phone_environment_daily_limit < self.phone_daily_limit:
            raise ValueError("AUTH_PHONE_ENV_DAILY_LIMIT must not be below per-phone limit")


@dataclass(frozen=True)
class PersonalEntitlementPolicy:
    monthly_search_limit: int = 100
    watchlist_company_limit: int = 20
    monthly_report_limit: int = 10
    daily_request_limit: int = 10
    monthly_request_limit: int = 30
    request_cooldown_hours: int = 24

    def __post_init__(self) -> None:
        for name, value in (
            ("PERSONAL_MONTHLY_SEARCH_LIMIT", self.monthly_search_limit),
            ("PERSONAL_WATCHLIST_COMPANY_LIMIT", self.watchlist_company_limit),
            ("PERSONAL_MONTHLY_REPORT_LIMIT", self.monthly_report_limit),
            ("PERSONAL_DAILY_REQUEST_LIMIT", self.daily_request_limit),
            ("PERSONAL_MONTHLY_REQUEST_LIMIT", self.monthly_request_limit),
            ("PERSONAL_REQUEST_COOLDOWN_HOURS", self.request_cooldown_hours),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be a positive integer")


DEEPSEEK_CHAT_COMPLETIONS_ENDPOINT = "https://api.deepseek.com/chat/completions"


@dataclass(frozen=True)
class InvestorAnalysisPolicy:
    version: str = "investor-analysis-v1"
    provider: str = "deepseek"
    model: str = "deepseek-v4-flash"
    endpoint_url: str = DEEPSEEK_CHAT_COMPLETIONS_ENDPOINT
    min_materiality_score: int = 60
    max_input_characters: int = 12_000
    max_output_tokens: int = 1_200
    monthly_token_limit: int = 500_000
    timeout_seconds: int = 30
    max_response_bytes: int = 128_000
    retry_limit: int = 1
    worker_lease_seconds: int = 300
    input_cost_per_million_tokens: Decimal = Decimal("0")
    output_cost_per_million_tokens: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("INVESTOR_ANALYSIS_POLICY_VERSION must not be empty")
        if self.provider != "deepseek":
            raise ValueError("INVESTOR_ANALYSIS_PROVIDER must be deepseek in V1")
        if not self.model.strip():
            raise ValueError("INVESTOR_ANALYSIS_MODEL must not be empty")
        if self.endpoint_url != DEEPSEEK_CHAT_COMPLETIONS_ENDPOINT:
            raise ValueError("INVESTOR_ANALYSIS_ENDPOINT must use the approved endpoint")
        if not 0 <= self.min_materiality_score <= 100:
            raise ValueError("INVESTOR_ANALYSIS_MIN_MATERIALITY must be between 0 and 100")
        for name, value in (
            ("INVESTOR_ANALYSIS_MAX_INPUT_CHARACTERS", self.max_input_characters),
            ("INVESTOR_ANALYSIS_MAX_OUTPUT_TOKENS", self.max_output_tokens),
            ("INVESTOR_ANALYSIS_MONTHLY_TOKEN_LIMIT", self.monthly_token_limit),
            ("INVESTOR_ANALYSIS_TIMEOUT_SECONDS", self.timeout_seconds),
            ("INVESTOR_ANALYSIS_MAX_RESPONSE_BYTES", self.max_response_bytes),
            ("INVESTOR_ANALYSIS_WORKER_LEASE_SECONDS", self.worker_lease_seconds),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.retry_limit < 0 or self.retry_limit > 1:
            raise ValueError("INVESTOR_ANALYSIS_RETRY_LIMIT must be 0 or 1")
        if self.input_cost_per_million_tokens < 0 or self.output_cost_per_million_tokens < 0:
            raise ValueError("investor analysis token costs must be non-negative")


@dataclass(frozen=True)
class Settings:
    database_url: str
    app_mode: str
    external_calls_enabled: bool
    paid_api_calls_enabled: bool
    auto_refresh_enabled: bool
    trusted_source_calls_enabled: bool = False
    source_monitor_scheduler_enabled: bool = False
    web_research_enabled: bool = False
    web_research_calls_enabled: bool = False
    investor_analysis_enabled: bool = False
    review_workbench_enabled: bool = False
    auth_provider: str = "demo"
    refresh_policy: RefreshPolicy = field(default_factory=RefreshPolicy)
    publication_policy: PublicationPolicy = field(default_factory=PublicationPolicy)
    identity_policy: IdentityPolicy = field(default_factory=IdentityPolicy)
    source_monitoring_policy: SourceMonitoringPolicy = field(default_factory=SourceMonitoringPolicy)
    web_research_policy: WebResearchPolicy = field(default_factory=WebResearchPolicy)
    cloudbase_auth_policy: CloudBaseAuthPolicy = field(default_factory=CloudBaseAuthPolicy)
    personal_entitlement_policy: PersonalEntitlementPolicy = field(
        default_factory=PersonalEntitlementPolicy
    )
    investor_analysis_policy: InvestorAnalysisPolicy = field(default_factory=InvestorAnalysisPolicy)

    def __post_init__(self) -> None:
        if self.app_mode not in {"demo", "production"}:
            raise ValueError("APP_MODE must be demo or production")
        if self.auth_provider not in {"demo", "cloudbase"}:
            raise ValueError("AUTH_PROVIDER must be demo or cloudbase")
        if self.auth_provider == "cloudbase" and not self.cloudbase_auth_policy.env_id:
            raise ValueError("CLOUDBASE_ENV_ID is required when AUTH_PROVIDER=cloudbase")
        if self.app_mode == "production":
            if self.auth_provider != "cloudbase":
                raise ValueError("production mode requires AUTH_PROVIDER=cloudbase")
            if not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
                raise ValueError("production mode requires PostgreSQL DATABASE_URL")

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            database_url=os.getenv(
                "DATABASE_URL",
                "postgresql+psycopg://equity_app:replace_app_password@localhost:5432/equity_radar",
            ),
            app_mode=os.getenv("APP_MODE", "demo").strip().lower(),
            external_calls_enabled=_as_bool(os.getenv("EXTERNAL_CALLS_ENABLED", "false")),
            paid_api_calls_enabled=_as_bool(os.getenv("PAID_API_CALLS_ENABLED", "false")),
            auto_refresh_enabled=_as_bool(os.getenv("AUTO_REFRESH_ENABLED", "false")),
            trusted_source_calls_enabled=_as_bool(
                os.getenv("TRUSTED_SOURCE_CALLS_ENABLED", "false")
            ),
            source_monitor_scheduler_enabled=_as_bool(
                os.getenv("SOURCE_MONITOR_SCHEDULER_ENABLED", "false")
            ),
            web_research_enabled=_as_bool(os.getenv("WEB_RESEARCH_ENABLED", "false")),
            web_research_calls_enabled=_as_bool(os.getenv("WEB_RESEARCH_CALLS_ENABLED", "false")),
            investor_analysis_enabled=_as_bool(os.getenv("INVESTOR_ANALYSIS_ENABLED", "false")),
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
            source_monitoring_policy=SourceMonitoringPolicy(
                version=os.getenv("SOURCE_MONITOR_POLICY_VERSION", "trusted-source-v1"),
                max_requests_per_run=_as_positive_int("SOURCE_MONITOR_MAX_REQUESTS", 10),
                max_download_bytes_per_run=_as_positive_int(
                    "SOURCE_MONITOR_MAX_DOWNLOAD_BYTES", 5_000_000
                ),
                max_response_bytes=_as_positive_int("SOURCE_MONITOR_MAX_RESPONSE_BYTES", 1_000_000),
                max_pdf_pages=_as_positive_int("SOURCE_MONITOR_MAX_PDF_PAGES", 40),
                max_pdf_text_chars=_as_positive_int("SOURCE_MONITOR_MAX_PDF_TEXT_CHARS", 100_000),
                pdf_parse_timeout_seconds=_as_positive_int(
                    "SOURCE_MONITOR_PDF_PARSE_TIMEOUT_SECONDS", 5
                ),
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
            web_research_policy=WebResearchPolicy(
                version=os.getenv("WEB_RESEARCH_POLICY_VERSION", "bounded-web-v3"),
                primary_provider=os.getenv("WEB_RESEARCH_PRIMARY_PROVIDER", "baidu")
                .strip()
                .lower(),
                fallback_provider=os.getenv("WEB_RESEARCH_FALLBACK_PROVIDER", "bocha")
                .strip()
                .lower(),
                search_cache_ttl_days=_as_positive_int("WEB_RESEARCH_SEARCH_CACHE_TTL_DAYS", 14),
                document_cache_ttl_days=_as_positive_int(
                    "WEB_RESEARCH_DOCUMENT_CACHE_TTL_DAYS", 14
                ),
                max_search_calls_per_job=_as_positive_int(
                    "WEB_RESEARCH_MAX_SEARCH_CALLS_PER_JOB", 4
                ),
                max_results_per_search=_as_positive_int("WEB_RESEARCH_MAX_RESULTS_PER_SEARCH", 10),
                max_candidate_urls=_as_positive_int("WEB_RESEARCH_MAX_CANDIDATE_URLS", 12),
                max_documents_per_job=_as_positive_int("WEB_RESEARCH_MAX_DOCUMENTS_PER_JOB", 3),
                max_fetch_requests_per_job=_as_positive_int(
                    "WEB_RESEARCH_MAX_FETCH_REQUESTS_PER_JOB", 8
                ),
                identity_max_search_calls=_as_positive_int("IDENTITY_MAX_SEARCH_CALLS", 6),
                identity_max_fetch_requests=_as_positive_int("IDENTITY_MAX_FETCH_REQUESTS", 24),
                identity_max_download_bytes=_as_positive_int(
                    "IDENTITY_MAX_DOWNLOAD_BYTES", 4_000_000
                ),
                max_download_bytes_per_job=_as_positive_int(
                    "WEB_RESEARCH_MAX_DOWNLOAD_BYTES_PER_JOB", 2_000_000
                ),
                max_response_bytes=_as_positive_int("WEB_RESEARCH_MAX_RESPONSE_BYTES", 500_000),
                max_pdf_pages=_as_positive_int("WEB_RESEARCH_MAX_PDF_PAGES", 40),
                max_pdf_text_chars=_as_positive_int("WEB_RESEARCH_MAX_PDF_TEXT_CHARS", 100_000),
                pdf_parse_timeout_seconds=_as_positive_int(
                    "WEB_RESEARCH_PDF_PARSE_TIMEOUT_SECONDS", 5
                ),
                max_elapsed_seconds=_as_positive_int("WEB_RESEARCH_MAX_ELAPSED_SECONDS", 180),
                daily_search_call_limit=_as_positive_int(
                    "WEB_RESEARCH_DAILY_SEARCH_CALL_LIMIT", 50
                ),
                monthly_search_call_limit=_as_positive_int(
                    "WEB_RESEARCH_MONTHLY_SEARCH_CALL_LIMIT", 1_500
                ),
                timeout_seconds=_as_positive_int("WEB_RESEARCH_TIMEOUT_SECONDS", 15),
                worker_lease_seconds=_as_positive_int("WEB_RESEARCH_WORKER_LEASE_SECONDS", 300),
                fallback_min_subject_results=_as_positive_int(
                    "WEB_RESEARCH_FALLBACK_MIN_SUBJECT_RESULTS", 1
                ),
                recent_change_window_days=_as_positive_int(
                    "WEB_RESEARCH_RECENT_CHANGE_WINDOW_DAYS", 365
                ),
                user_agent=os.getenv(
                    "WEB_RESEARCH_USER_AGENT",
                    "DealflowRadarWebResearch/1.0 (bounded evidence research)",
                ),
            ),
            cloudbase_auth_policy=CloudBaseAuthPolicy(
                env_id=os.getenv("CLOUDBASE_ENV_ID", "").strip(),
                client_id=os.getenv("CLOUDBASE_CLIENT_ID", "").strip(),
                timeout_seconds=_as_positive_int("CLOUDBASE_AUTH_TIMEOUT_SECONDS", 5),
                max_response_bytes=_as_positive_int("CLOUDBASE_AUTH_MAX_RESPONSE_BYTES", 64_000),
                user_agent=os.getenv("CLOUDBASE_AUTH_USER_AGENT", "DealflowRadarAuth/1.0"),
                phone_login_enabled=_as_bool(os.getenv("PHONE_LOGIN_ENABLED", "false")),
                phone_code_cooldown_seconds=_as_positive_int(
                    "AUTH_PHONE_CODE_COOLDOWN_SECONDS", 60
                ),
                phone_daily_limit=_as_positive_int("AUTH_PHONE_DAILY_LIMIT", 5),
                phone_environment_daily_limit=_as_positive_int("AUTH_PHONE_ENV_DAILY_LIMIT", 50),
            ),
            personal_entitlement_policy=PersonalEntitlementPolicy(
                monthly_search_limit=_as_positive_int("PERSONAL_MONTHLY_SEARCH_LIMIT", 100),
                watchlist_company_limit=_as_positive_int("PERSONAL_WATCHLIST_COMPANY_LIMIT", 20),
                monthly_report_limit=_as_positive_int("PERSONAL_MONTHLY_REPORT_LIMIT", 10),
                daily_request_limit=_as_positive_int("PERSONAL_DAILY_REQUEST_LIMIT", 10),
                monthly_request_limit=_as_positive_int("PERSONAL_MONTHLY_REQUEST_LIMIT", 30),
                request_cooldown_hours=_as_positive_int("PERSONAL_REQUEST_COOLDOWN_HOURS", 24),
            ),
            investor_analysis_policy=InvestorAnalysisPolicy(
                version=os.getenv(
                    "INVESTOR_ANALYSIS_POLICY_VERSION",
                    "investor-analysis-v1",
                ),
                provider=os.getenv("INVESTOR_ANALYSIS_PROVIDER", "deepseek").strip().lower(),
                model=os.getenv("INVESTOR_ANALYSIS_MODEL", "deepseek-v4-flash").strip(),
                endpoint_url=os.getenv(
                    "INVESTOR_ANALYSIS_ENDPOINT",
                    DEEPSEEK_CHAT_COMPLETIONS_ENDPOINT,
                ).strip(),
                min_materiality_score=_as_non_negative_int(
                    "INVESTOR_ANALYSIS_MIN_MATERIALITY",
                    60,
                ),
                max_input_characters=_as_positive_int(
                    "INVESTOR_ANALYSIS_MAX_INPUT_CHARACTERS",
                    12_000,
                ),
                max_output_tokens=_as_positive_int(
                    "INVESTOR_ANALYSIS_MAX_OUTPUT_TOKENS",
                    1_200,
                ),
                monthly_token_limit=_as_positive_int(
                    "INVESTOR_ANALYSIS_MONTHLY_TOKEN_LIMIT",
                    500_000,
                ),
                timeout_seconds=_as_positive_int(
                    "INVESTOR_ANALYSIS_TIMEOUT_SECONDS",
                    30,
                ),
                max_response_bytes=_as_positive_int(
                    "INVESTOR_ANALYSIS_MAX_RESPONSE_BYTES",
                    128_000,
                ),
                retry_limit=_as_non_negative_int(
                    "INVESTOR_ANALYSIS_RETRY_LIMIT",
                    1,
                ),
                worker_lease_seconds=_as_positive_int(
                    "INVESTOR_ANALYSIS_WORKER_LEASE_SECONDS",
                    300,
                ),
                input_cost_per_million_tokens=_as_non_negative_decimal(
                    "INVESTOR_ANALYSIS_INPUT_COST_PER_MILLION_TOKENS",
                    "0",
                ),
                output_cost_per_million_tokens=_as_non_negative_decimal(
                    "INVESTOR_ANALYSIS_OUTPUT_COST_PER_MILLION_TOKENS",
                    "0",
                ),
            ),
        )
