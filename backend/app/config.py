"""Application settings.

All configuration is injected through environment variables (see `.env.example`).
No real secrets are ever committed to the repository.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KAIROS_",
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Runtime environment: dev / staging / production.
    env: str = "dev"
    app_name: str = "kairos-api"

    # --- PostgreSQL ---
    # Host 5434: local PostgreSQL holds 5432, another project's container holds 5433.
    database_url: str = "postgresql+psycopg://kairos:kairos_dev_password@localhost:5434/kairos"

    # --- Temporal ---
    # Host 8233 because Windows excludes the 7178-7277 range (Hyper-V/WSL).
    temporal_address: str = "localhost:8233"
    temporal_namespace: str = "default"
    temporal_smoke_task_queue: str = "kairos-smoke"

    # --- Object storage (S3-compatible) ---
    s3_endpoint: str = "localhost:9000"
    s3_access_key: str = "kairos_minio"
    s3_secret_key: str = "kairos_minio_secret"
    s3_bucket: str = "kairos-dev"
    s3_secure: bool = False

    # --- OpenTelemetry ---
    otel_enabled: bool = True
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    service_name: str = "kairos-api"

    # --- API / CORS ---
    api_port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> object:
        # Allow JSON array strings from .env (e.g. ["http://localhost:5173"]).
        if isinstance(value, str):
            import json

            return json.loads(value)
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
