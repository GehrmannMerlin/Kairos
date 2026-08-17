"""M-16 资源 lease reaper 运行时：把 `LeaseReaper` 真正接入 Worker 生命周期。

不新增独立微服务 / 消息队列 / 调度系统（§7.1）。复用当前 Worker runtime：
worker 启动后启动一个受控 periodic maintenance task，按 `lease_reap_interval_seconds`
周期有界分批回收过期 lease；worker 关闭时优雅取消。单次 tick 短暂 DB 错误只记录并
backoff，不因此把 API readiness 判为不可用（§15）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from app.reliability.admission import ResourceAdmission
from app.reliability.capacity import capacity_from_settings

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger("kairos.worker.reaper")

# 单个 tick 内最多 sweep 的批次数（防止无界循环阻塞 worker 事件循环）。
_MAX_BATCHES_PER_SWEEP = 100
# 单批回收上限（与 ResourceAdmission.reap 默认一致，历史累积按批 commit）。
_REAP_BATCH_SIZE = 500


async def _lease_reaper_loop(settings: Settings) -> None:
    from app.infra.deps import get_session_factory

    capacity = capacity_from_settings(settings)
    interval = capacity.lease_reap_interval_seconds
    session_factory = get_session_factory()
    while True:
        await asyncio.sleep(interval)
        try:
            reaped = 0
            for _ in range(_MAX_BATCHES_PER_SWEEP):
                session = session_factory()
                try:
                    n = ResourceAdmission(session, capacity).reap(limit=_REAP_BATCH_SIZE)
                finally:
                    session.close()
                reaped += n
                if n == 0:
                    break
            if reaped:
                logger.info("reaped_expired_leases count=%d", reaped)
        except asyncio.CancelledError:
            raise
        except Exception:
            # 单次维护 tick 失败：记录后下一周期恢复，不拖垮 worker（§15）。
            logger.warning("lease_reaper_tick_failed", exc_info=True)


def start_lease_reaper(settings: Settings) -> asyncio.Task:
    """在 Worker 生命周期内启动 reaper 后台任务；调用方负责在关闭时 cancel + await。"""
    return asyncio.create_task(_lease_reaper_loop(settings))


async def stop_lease_reaper(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
