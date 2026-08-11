"""M-08 plan fixture worker（独立进程入口；仅测试/Staging，绝不在 Production）。

注册 TaskWorkflow + lifecycle + plan execution + approval activities + fixture
executor（FETCH）。用于 DEPLOY-GATE-2 与 M-08 Temporal 集成测试。
"""

from __future__ import annotations

import asyncio

from app.activities.approval import (
    block_high_risk_node,
    request_approval,
    resume_from_approval,
)
from app.activities.plan_execution import execute_safe_unit, fetch_next_execution_unit
from app.activities.task_execution import (
    commit_checkpoint,
    complete_run,
    ensure_run_started,
    mark_cancelled,
    mark_paused,
)

# Register every ORM table in the shared Base.metadata before any Activity flushes
# a row — same requirement as the production worker.
from app.auth.models import User  # noqa: F401
from app.config import get_settings
from app.infra.temporal import create_temporal_client
from app.workflows.task_workflow import TaskWorkflow
from temporalio.worker import Worker
from tests.fixtures.plan_fixture import install_fixture_executors


async def run(queue: str) -> None:
    settings = get_settings()
    install_fixture_executors()
    client = await create_temporal_client(settings)
    worker = Worker(
        client,
        task_queue=queue,
        workflows=[TaskWorkflow],
        activities=[
            ensure_run_started,
            mark_paused,
            mark_cancelled,
            complete_run,
            commit_checkpoint,
            fetch_next_execution_unit,
            execute_safe_unit,
            request_approval,
            block_high_risk_node,
            resume_from_approval,
        ],
    )
    await worker.run()


def main() -> None:
    import sys

    queue = sys.argv[1] if len(sys.argv) > 1 else get_settings().temporal_task_queue
    asyncio.run(run(queue))


if __name__ == "__main__":
    main()
