"""M-16 CapacityConfig：部署/运维容量配置（D-071），禁止进入 CollectionSpec。

跨字段校验在启动时执行（Settings 实例化 → capacity_from_settings → validator），
避免「配置错了运行半天才发现」。browser 上限写死安全范围（>2 视为不安全）。
并发数字属于 Deployment Config，不是用户 CollectionSpec。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

_KNOWN_CLASSES = ("core", "http", "browser", "llm_search")
_BROWSER_SAFE_MAX = 2


class CapacityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    global_active_tasks: int = 4
    per_user_active_tasks: int = 2
    pool_concurrency: dict[str, int] = {
        "core": 4,
        "http": 4,
        "browser": 1,
        "llm_search": 2,
    }
    lease_ttl_seconds: int = 120
    lease_heartbeat_seconds: int = 30
    lease_reap_interval_seconds: int = 30
    domain_breaker_threshold: int = 5
    domain_breaker_cooldown_seconds: int = 60
    default_retry_max_attempts: int = 3
    provider_throttle_min_interval_seconds: float = 0.2
    provider_throttle_max_burst: int = 1

    @model_validator(mode="after")
    def _validate(self) -> CapacityConfig:
        if self.global_active_tasks <= 0:
            raise ValueError("global_active_tasks must be > 0")
        if self.per_user_active_tasks <= 0:
            raise ValueError("per_user_active_tasks must be > 0")
        if self.per_user_active_tasks > self.global_active_tasks:
            raise ValueError("per_user_active_tasks must be <= global_active_tasks")
        for key, value in self.pool_concurrency.items():
            if key not in _KNOWN_CLASSES:
                raise ValueError(f"unknown resource class in pool_concurrency: {key}")
            if value <= 0:
                raise ValueError(f"pool_concurrency[{key}] must be > 0")
        if self.pool_concurrency["browser"] > _BROWSER_SAFE_MAX:
            raise ValueError("browser pool_concurrency exceeds deployment safe range")
        for key in (
            "lease_ttl_seconds",
            "domain_breaker_threshold",
            "default_retry_max_attempts",
        ):
            if getattr(self, key) <= 0:
                raise ValueError(f"{key} must be > 0")
        return self

    def pool_limit(self, resource_class: str) -> int:
        return self.pool_concurrency.get(resource_class, 1)


def capacity_from_settings(settings) -> CapacityConfig:
    """从 Settings 环境配置构建 CapacityConfig（D-071 并发来自部署配置）。"""
    return CapacityConfig(
        global_active_tasks=settings.capacity_global_active_tasks,
        per_user_active_tasks=settings.capacity_per_user_active_tasks,
        pool_concurrency={
            "core": settings.capacity_core_concurrency,
            "http": settings.capacity_http_concurrency,
            "browser": settings.capacity_browser_concurrency,
            "llm_search": settings.capacity_llm_search_concurrency,
        },
        lease_ttl_seconds=settings.capacity_lease_ttl_seconds,
        lease_heartbeat_seconds=settings.capacity_lease_heartbeat_seconds,
        lease_reap_interval_seconds=settings.capacity_lease_reap_interval_seconds,
        domain_breaker_threshold=settings.capacity_domain_breaker_threshold,
        domain_breaker_cooldown_seconds=settings.capacity_domain_breaker_cooldown_seconds,
        default_retry_max_attempts=settings.capacity_default_retry_max_attempts,
        provider_throttle_min_interval_seconds=settings.provider_throttle_min_interval_seconds,
        provider_throttle_max_burst=settings.provider_throttle_max_burst,
    )
