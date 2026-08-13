"""Application settings.

All configuration is injected through environment variables (see `.env.example`).
No real secrets are ever committed to the repository.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

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

    # --- Temporal task execution (M-07) ---
    temporal_task_queue: str = "kairos-task"
    task_pause_timeout_seconds: int = 300
    task_cancel_timeout_seconds: int = 300

    # --- Plan fixture harness (M-08, Staging/test only) ---
    # 默认关闭；只有显式开启的 Staging/测试环境才注册 fixture Node Executor。
    # Production 强制关闭（部署规范覆盖）。无真实外部网络/第三方写入/凭据外传。
    plan_fixture_mode: bool = False

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

    # --- Session cookie (M-02) ---
    session_cookie_name: str = "kairos_session"
    session_cookie_httponly: bool = True
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    session_cookie_secure: bool = False  # dev; must be True for staging/production
    session_cookie_path: str = "/"
    session_cookie_max_age_seconds: int = 60 * 60 * 24 * 7  # 7 days

    # --- Auth rate limiting (M-02, in-memory) ---
    auth_login_max_attempts: int = 5
    auth_login_window_seconds: int = 15 * 60

    # --- Credential master key (M-03, envelope encryption) ---
    # 32-byte hex (64 chars). Never committed; set in .env (see .env.example).
    credential_master_key: str | None = None
    credential_key_version: str = "k1"
    provider_test_timeout_seconds: float = 15.0

    # --- API / CORS ---
    api_port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173"]

    # --- M-10 crawling / fetch（集中配置，禁止散落 magic number）---
    fetch_timeout_seconds: float = 30.0
    fetch_max_download_bytes: int = 5_000_000
    fetch_max_redirects: int = 5
    fetch_internal_retries: int = 2  # 网络/5xx/429 有界内部重试
    fetch_internal_retry_base_seconds: float = 1.0
    site_strategy_ttl_seconds: int = 86400
    browser_render_timeout_seconds: float = 60.0

    # --- M-15 retention（D-072，部署配置；测试用独立短值 fixture）---
    retention_heavy_days: int = 90

    # --- M-16 capacity / worker roles（D-071 部署配置，禁止进入 CollectionSpec）---
    capacity_global_active_tasks: int = 4
    capacity_per_user_active_tasks: int = 2
    capacity_core_concurrency: int = 4
    capacity_http_concurrency: int = 4
    capacity_browser_concurrency: int = 1
    capacity_llm_search_concurrency: int = 2
    capacity_lease_ttl_seconds: int = 120
    capacity_lease_heartbeat_seconds: int = 30
    capacity_lease_reap_interval_seconds: int = 30
    capacity_domain_breaker_threshold: int = 5
    capacity_domain_breaker_cooldown_seconds: int = 60
    capacity_default_retry_max_attempts: int = 3
    provider_throttle_min_interval_seconds: float = 0.2
    provider_throttle_max_burst: int = 1
    worker_roles: str = "all"  # all | core,http,browser,llm_search（逗号分隔）


    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> object:
        # Allow JSON array strings from .env (e.g. ["http://localhost:5173"]).
        if isinstance(value, str):
            import json

            return json.loads(value)
        return value

    # --- M-17：production 上线门禁校验（部署配置错误必须启动即失败）---
    _DEV_CORS_HOSTS = ("localhost", "127.0.0.1")
    _DEV_DB_HOSTS = ("localhost", "127.0.0.1")

    def production_validation_errors(self) -> list[str]:
        """返回 production 环境下配置违规列表；空列表表示可上线。"""
        if self.env != "production":
            return []
        errors: list[str] = []
        if not self.session_cookie_secure:
            errors.append("production: KAIROS_SESSION_COOKIE_SECURE must be true")
        if self.cors_origins == ["*"] or any(
            any(h in o for h in self._DEV_CORS_HOSTS) for o in self.cors_origins
        ):
            errors.append(
                "production: KAIROS_CORS_ORIGINS must be the real product origin only"
            )
        if not self.credential_master_key:
            errors.append("production: KAIROS_CREDENTIAL_MASTER_KEY is required")
        elif len(self.credential_master_key) != 64:
            errors.append(
                "production: KAIROS_CREDENTIAL_MASTER_KEY must be 64 hex chars"
            )
        if self.database_url and any(
            h in self.database_url for h in self._DEV_DB_HOSTS
        ):
            errors.append(
                "production: KAIROS_DATABASE_URL must point to the production DB host"
            )
        if self.s3_bucket.endswith("-dev") or "kairos-staging" in self.s3_bucket:
            errors.append("production: KAIROS_S3_BUCKET must be the production bucket")
        if self.temporal_namespace in ("default", "kairos-staging"):
            errors.append(
                "production: KAIROS_TEMPORAL_NAMESPACE must be production-isolated"
            )
        return errors

    def validate_runtime(self) -> None:
        errors = self.production_validation_errors()
        if errors:
            raise RuntimeError("production config invalid: " + "; ".join(errors))


@lru_cache
def get_settings() -> Settings:
    return Settings()
