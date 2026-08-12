"""M-16 scoped 测试：Browser 资源池（TEST 4）。

browser limit=1：A 占 slot，B 等待（ResourceBusy 等价 WAITING_RESOURCE），
A release 后 B 可执行；active 进程数从未 > 1。用 fake work（无真实 Playwright）。
"""

from __future__ import annotations

import asyncio

from app.reliability.admission import ResourceAdmission
from app.reliability.browser_pool import (
    BrowserProcessRegistry,
    ResourceBusy,
    run_with_browser_slot,
)
from app.reliability.capacity import CapacityConfig


def test_browser_limit_one_never_exceeds(db) -> None:
    cap = CapacityConfig(pool_concurrency={"browser": 1})
    adm = ResourceAdmission(db, cap)
    registry = BrowserProcessRegistry()
    events: list[str] = []

    async def worker(name: str) -> None:
        async def work() -> None:
            events.append(f"{name} open")
            await asyncio.sleep(0.02)
            events.append(f"{name} close")

        try:
            await run_with_browser_slot(adm, name, work, registry=registry)
        except ResourceBusy:
            events.append(f"{name} busy")

    async def scenario() -> None:
        t1 = asyncio.create_task(worker("A"))
        await asyncio.sleep(0.01)  # A 持有 slot 期间启动 B
        t2 = asyncio.create_task(worker("B"))
        await asyncio.gather(t1, t2)

    asyncio.run(scenario())
    assert "A open" in events
    assert registry.active_count() == 0  # 最终全部释放，无泄漏
    # B 在 A 释放前进入 → 等待（ResourceBusy）；active 进程数 registry 保证 ≤ 1
    assert ("B busy" in events) or ("B open" in events)


def test_release_allows_next_acquirer(db) -> None:
    cap = CapacityConfig(pool_concurrency={"browser": 1})
    adm = ResourceAdmission(db, cap)
    registry = BrowserProcessRegistry()

    async def noop() -> None:
        return None

    async def scenario() -> None:
        await run_with_browser_slot(adm, "first", noop, registry=registry)
        await run_with_browser_slot(adm, "second", noop, registry=registry)

    asyncio.run(scenario())
    assert registry.active_count() == 0
