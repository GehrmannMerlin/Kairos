"""M-16 scoped 测试：CapacityConfig 默认安全值 + 启动校验（TEST 2 前置）。"""

from __future__ import annotations

import pytest
from app.reliability.capacity import CapacityConfig
from pydantic import ValidationError


def test_defaults_are_safe() -> None:
    c = CapacityConfig()
    assert c.global_active_tasks >= c.per_user_active_tasks
    assert c.pool_concurrency["browser"] == 1


def test_zero_global_rejected() -> None:
    with pytest.raises(ValidationError):
        CapacityConfig(global_active_tasks=0)


def test_per_user_exceeds_global_rejected() -> None:
    with pytest.raises(ValidationError):
        CapacityConfig(global_active_tasks=2, per_user_active_tasks=3)


def test_unknown_resource_class_rejected() -> None:
    with pytest.raises(ValidationError):
        CapacityConfig(pool_concurrency={"nope": 1})


def test_browser_above_safe_rejected() -> None:
    with pytest.raises(ValidationError):
        CapacityConfig(pool_concurrency={"browser": 8})


def test_pool_limit_helper() -> None:
    c = CapacityConfig()
    assert c.pool_limit("browser") == 1
    assert c.pool_limit("unknown") == 1
