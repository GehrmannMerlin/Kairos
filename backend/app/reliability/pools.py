"""M-16 ResourceClass → TaskQueue 确定性路由 + Worker 角色。

确定性规则（D-026/I-001 §26）：
- CORE 类不覆盖 queue → 走 workflow 自身队列（settings.temporal_task_queue）。
- HTTP/BROWSER/LLM_SEARCH 用固定代码常量 queue（workflow 覆盖用同一常量，replay 确定）。
- Worker 角色只决定「poll 哪些 queue + 每 queue 并发」，不复制工程；不读 env 进 workflow。
"""

from __future__ import annotations

from enum import StrEnum

from app.config import get_settings
from app.plan.nodes import ResourceClass

HTTP_QUEUE = "kairos-http"
BROWSER_QUEUE = "kairos-browser"
LLM_SEARCH_QUEUE = "kairos-llm-search"

# 非 CORE 固定常量映射（workflow 覆盖 + worker poll 共用）。
RESOURCE_QUEUE_MAP: dict[ResourceClass, str] = {
    ResourceClass.HTTP: HTTP_QUEUE,
    ResourceClass.BROWSER: BROWSER_QUEUE,
    ResourceClass.LLM_SEARCH: LLM_SEARCH_QUEUE,
}


def _core_queue() -> str:
    return get_settings().temporal_task_queue


def task_queue_for(resource_class: ResourceClass | str) -> str:
    key = resource_class.value if isinstance(resource_class, ResourceClass) else resource_class
    if key == ResourceClass.CORE.value:
        return _core_queue()
    return RESOURCE_QUEUE_MAP[ResourceClass(key)]


def workflow_queue_override(resource_class: str) -> str | None:
    """Workflow 内确定性覆盖：CORE → None（默认 queue），其余 → 固定常量。"""
    if resource_class == ResourceClass.CORE.value:
        return None
    return RESOURCE_QUEUE_MAP[ResourceClass(resource_class)]


class WorkerRole(StrEnum):
    ALL = "all"
    CORE = "core"
    HTTP = "http"
    BROWSER = "browser"
    LLM_SEARCH = "llm_search"


def parse_worker_roles(raw: str) -> list[WorkerRole]:
    if raw.strip() == "all":
        return [WorkerRole.ALL]
    return [WorkerRole(part.strip()) for part in raw.split(",") if part.strip()]


def role_task_queues(role: WorkerRole) -> list[str]:
    """Worker 该角色需要 poll 的 queue 列表（运行时配置；不影响 workflow replay）。"""
    if role is WorkerRole.ALL:
        return sorted({_core_queue(), HTTP_QUEUE, BROWSER_QUEUE, LLM_SEARCH_QUEUE})
    if role is WorkerRole.CORE:
        return [_core_queue()]
    if role is WorkerRole.HTTP:
        return [HTTP_QUEUE]
    if role is WorkerRole.BROWSER:
        return [BROWSER_QUEUE]
    return [LLM_SEARCH_QUEUE]


def all_role_queues() -> list[str]:
    return role_task_queues(WorkerRole.ALL)


def capacity_pool_for_queue(queue: str, capacity) -> int:
    """该 queue 对应的并发上限（Worker runtime config，非 workflow 决策）。"""
    by_queue = {
        _core_queue(): capacity.pool_concurrency[ResourceClass.CORE.value],
        HTTP_QUEUE: capacity.pool_concurrency[ResourceClass.HTTP.value],
        BROWSER_QUEUE: capacity.pool_concurrency[ResourceClass.BROWSER.value],
        LLM_SEARCH_QUEUE: capacity.pool_concurrency[ResourceClass.LLM_SEARCH.value],
    }
    return by_queue.get(queue, capacity.pool_concurrency[ResourceClass.CORE.value])
