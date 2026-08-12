"""M-07 crash/restart 测试专用 worker（独立进程入口）。

python -m tests.integration.fixture_worker
只注册 TaskWorkflow + lifecycle + fixture 执行单元；绝不在 Production 使用。
"""

from __future__ import annotations

import asyncio

from app.activities.task_execution import (
    commit_checkpoint,
    complete_run,
    ensure_run_started,
    mark_cancelled,
    mark_paused,
)

# Register every ORM table in the shared Base.metadata before any Activity flushes
# a row. Domain models reference ``ForeignKey("users.id")`` and SQLAlchemy must
# resolve the ``users`` table — same requirement as the production worker
# (app/worker.py). The fixture worker runs as a subprocess, so it needs the
# explicit import; the test runner's own process gets it via the API/auth modules.
from app.auth.models import User  # noqa: F401
from app.config import get_settings
from app.infra.temporal import create_temporal_client
from app.reliability.pools import all_role_queues
from app.workflows.task_workflow import TaskWorkflow
from temporalio.worker import Worker
from tests.fixtures.execution_adapter import execute_safe_unit, fetch_next_execution_unit

_ACTIVITIES = [
    ensure_run_started,
    mark_paused,
    mark_cancelled,
    complete_run,
    commit_checkpoint,
    fetch_next_execution_unit,
    execute_safe_unit,
]


async def run(queue: str) -> None:
    settings = get_settings()
    client = await create_temporal_client(settings)
    queues = all_role_queues() if queue == settings.temporal_task_queue else [queue]
    workers = [
        Worker(client, task_queue=q, workflows=[TaskWorkflow], activities=_ACTIVITIES)
        for q in queues
    ]
    await asyncio.gather(*(w.run() for w in workers))


def main() -> None:
    import sys

    queue = sys.argv[1] if len(sys.argv) > 1 else settings_queue_default()
    asyncio.run(run(queue))


def settings_queue_default() -> str:
    from app.config import get_settings

    return get_settings().temporal_task_queue


if __name__ == "__main__":
    main()
