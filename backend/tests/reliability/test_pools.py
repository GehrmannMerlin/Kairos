"""M-16 scoped 测试：ResourceClass → TaskQueue 确定性路由 + Worker 角色。"""

from __future__ import annotations

from app.config import get_settings
from app.plan.nodes import ResourceClass
from app.reliability.pools import (
    BROWSER_QUEUE,
    HTTP_QUEUE,
    WorkerRole,
    role_task_queues,
    task_queue_for,
    workflow_queue_override,
)


def test_every_resource_class_resolves_deterministic_queue() -> None:
    for rc in ResourceClass:
        q = task_queue_for(rc)
        assert q


def test_core_queue_is_the_orchestration_queue() -> None:
    # CORE 类走 workflow 自身队列（settings.temporal_task_queue），workflow 不覆盖
    assert task_queue_for(ResourceClass.CORE) == get_settings().temporal_task_queue
    assert workflow_queue_override(ResourceClass.CORE.value) is None


def test_non_core_queues_are_fixed_constants() -> None:
    assert task_queue_for(ResourceClass.HTTP) == HTTP_QUEUE
    assert task_queue_for(ResourceClass.BROWSER) == BROWSER_QUEUE
    assert task_queue_for(ResourceClass.LLM_SEARCH) != task_queue_for(ResourceClass.HTTP)
    assert workflow_queue_override(ResourceClass.HTTP.value) == HTTP_QUEUE


def test_roles_expand_to_queues() -> None:
    assert set(role_task_queues(WorkerRole.ALL)) >= {HTTP_QUEUE, BROWSER_QUEUE}
    assert role_task_queues(WorkerRole.BROWSER) == [BROWSER_QUEUE]
    assert get_settings().temporal_task_queue in role_task_queues(WorkerRole.CORE)


def test_role_queues_are_disjoint_for_roles() -> None:
    http = set(role_task_queues(WorkerRole.HTTP))
    browser = set(role_task_queues(WorkerRole.BROWSER))
    assert http.isdisjoint(browser)
