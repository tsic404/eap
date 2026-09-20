"""Configuration (pydantic-settings) tests."""

from app.config import Settings


def test_settings_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.app_env == "development"
    assert settings.app_version == "0.1.0"
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.redis_url.startswith("redis://")
    assert settings.log_json is True


def test_settings_read_environment_variables(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pw@db:5432/app")
    monkeypatch.setenv("REDIS_URL", "redis://cache:6379/1")
    monkeypatch.setenv("OIDC_ISSUER", "https://sso.example.com")
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "42")

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+asyncpg://user:pw@db:5432/app"
    assert settings.redis_url == "redis://cache:6379/1"
    assert settings.oidc_issuer == "https://sso.example.com"
    assert settings.rate_limit_requests == 42


def test_settings_parse_cors_list(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", '["http://a.example", "http://b.example"]')
    settings = Settings(_env_file=None)
    assert settings.cors_allowed_origins == ["http://a.example", "http://b.example"]


def test_settings_load_jwt_keys_from_files(tmp_path) -> None:  # type: ignore[no-untyped-def]
    private_file = tmp_path / "private.pem"
    public_file = tmp_path / "public.pem"
    private_file.write_text("PRIVATE_KEY")
    public_file.write_text("PUBLIC_KEY")

    settings = Settings(
        _env_file=None,
        jwt_private_key_file=str(private_file),
        jwt_public_key_file=str(public_file),
    )

    assert settings.jwt_private_key == "PRIVATE_KEY"
    assert settings.jwt_public_key == "PUBLIC_KEY"


def test_settings_inline_jwt_key_wins_over_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    key_file = tmp_path / "private.pem"
    key_file.write_text("FROM_FILE")

    settings = Settings(
        _env_file=None,
        jwt_private_key="FROM_ENV",
        jwt_private_key_file=str(key_file),
    )

    assert settings.jwt_private_key == "FROM_ENV"
