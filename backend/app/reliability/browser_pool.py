"""M-16 Browser 资源池门控（低并发 + 进程生命周期安全，D-071 / §46-48）。

BrowserProcessRegistry 进程内登记 active browser 渲染；run_with_browser_slot 先占
pool slot 再执行 work，finally 释放——进程数永不超限，正常/超时/异常都回收。
真实 Playwright 渲染器的 pool slot 由 execute_safe_unit 的 Level 3 admission
先于进程创建拦截；这里是第二道防线 + 测试 seam。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class BrowserProcessRegistry:
    """进程内 active browser 登记 + 超时/orphan 清理钩子。"""

    def __init__(self) -> None:
        self._active: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def open(self, holder_id: str) -> None:
        async with self._lock:
            self._active[holder_id] = self._active.get(holder_id, 0) + 1

    async def close(self, holder_id: str) -> None:
        async with self._lock:
            self._active.pop(holder_id, None)

    def active_count(self) -> int:
        return len(self._active)

    async def close_all(self) -> int:
        """正常/优雅退出时回收所有登记进程（孤儿兜底）。"""
        async with self._lock:
            n = len(self._active)
            self._active.clear()
            return n


class ResourceBusy(Exception):
    """pool slot 无空位（调用方转 WAITING_RESOURCE，非失败）。"""


async def run_with_browser_slot(
    admission,
    holder_id: str,
    work: Callable[[], Awaitable[None]],
    registry: BrowserProcessRegistry | None = None,
) -> None:
    """占 pool slot + registry，执行 work，finally 释放（active 进程数永不超限）。"""
    registry = registry or BrowserProcessRegistry()
    slot = admission.try_acquire_pool_slot(resource_class="browser", holder_id=holder_id)
    if not slot.granted:
        raise ResourceBusy()
    try:
        await registry.open(holder_id)
        await work()
    finally:
        await registry.close(holder_id)
        admission.release_pool_slot(resource_class="browser", holder_id=holder_id)
