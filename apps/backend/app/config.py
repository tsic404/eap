"""Environment-backed application configuration (pydantic-settings)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings sourced from environment variables and ``.env``.

    Compose injects every value explicitly; the defaults below mirror
    ``.env.example`` but use localhost so the app boots for local development
    outside the compose network.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application
    app_env: str = "development"
    app_name: str = "EAP Backend"
    app_version: str = "0.1.0"
    backend_port: int = 3001

    # Infrastructure — local-dev defaults only. Production MUST inject every
    # value below via environment (compose/.env); never ship these defaults,
    # which include a plaintext database password.
    database_url: str = "postgresql+asyncpg://eap:eap_password@localhost:5432/eap"
    redis_url: str = "redis://localhost:6379/0"
    dify_api_base_url: str = "http://localhost:5001"
    dify_api_key: str = ""
    # Dify Console API (admin/owner) credentials — used by DifyConsoleClient to
    # obtain a session cookie for app / dataset / model / API-key management.
    dify_console_email: str = ""
    dify_console_password: str = ""

    # OIDC / SSO
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_redirect_uri: str = "http://localhost/api/auth/callback"

    # JWT (RS256 key material; empty until OIDC/SSO is configured)
    jwt_private_key: str = ""
    jwt_public_key: str = ""

    # Refresh-token rotation (architecture §34.1). Access tokens always live 15
    # minutes (the OIDC spec constant); refresh tokens default to 30 days.
    refresh_token_ttl_seconds: int = 2592000
    # The refresh-token cookie is Secure only when the app runs behind HTTPS
    # (production). Local HTTP development leaves it unset so browsers accept it.
    cookie_secure: bool = False

    # CORS whitelist
    cors_allowed_origins: list[str] = [
        "http://localhost",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # Rate limiting (Redis token bucket with in-memory fallback). The global
    # limit is a token bucket: ``rate_limit_requests`` is
    # the burst capacity and ``rate_limit_window_seconds`` the refill window,
    # so the default (100 / 1s) is 100 requests per second per client IP.
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 100
    rate_limit_window_seconds: int = 1
    # Per-route limits (tokens per minute).
    rate_limit_login_per_minute: int = 10
    rate_limit_conversation_per_minute: int = 20
    rate_limit_upload_per_minute: int = 10
    # Number of trusted reverse proxies in front of the app. When > 0 the rate
    # limiter reads the client IP from X-Forwarded-For at that hop depth instead
    # of trusting the header's leftmost (spoofable) value.
    trusted_proxy_count: int = 0

    # Logging
    log_level: str = "INFO"
    log_json: bool = True


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached settings instance."""
    return Settings()
