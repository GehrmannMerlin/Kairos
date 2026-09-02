from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "kairos"
    app_env: str = "local"
    local_mode: bool = False
    database_url: str = "postgresql+asyncpg://kairos:kairos@127.0.0.1:5434/kairos"
    temporal_target: str = "127.0.0.1:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "kairos-agent-task-queue"
    temporal_ui_url: str = "http://127.0.0.1:8088"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    frontend_origin: str = "http://127.0.0.1:5173"
    model_config_id: str = "local-model"
    model_id: str = "deepseek:deepseek-chat"
    model_credential_env: str = "DEEPSEEK_API_KEY"
    model_api_key: SecretStr | None = None
    http_max_bytes: int = 1_000_000
    http_timeout_seconds: float = 20.0
    collection_http_max_bytes: int = 5_242_880
    robots_timeout_seconds: float = 10.0
    minio_endpoint: str = "http://127.0.0.1:9000"
    # Matches the credentials in the local-only compose file; production deployments must
    # override both values through the process environment.
    minio_access_key: str | None = "kairos"
    minio_secret_key: SecretStr | None = SecretStr("kairos-local-minio")
    minio_bucket: str = "kairos-snapshots"
    event_poll_seconds: float = 0.5
    native_folder_picker_enabled: bool = False

    @property
    def model_is_configured(self) -> bool:
        return bool(self.model_api_key and self.model_api_key.get_secret_value())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
