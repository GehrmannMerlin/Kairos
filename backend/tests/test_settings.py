"""Settings loading sanity."""

from __future__ import annotations

from app.config import Settings


def test_settings_defaults_are_dev_safe() -> None:
    settings = Settings()
    assert settings.env == "dev"
    assert settings.s3_secret_key  # present, but dev-only placeholder


def test_settings_reads_environment() -> None:
    settings = Settings(database_url="postgresql+psycopg://x:y@host:1/db", s3_bucket="test-bucket")
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.s3_bucket == "test-bucket"
