"""M-01 integration smoke.

Chain exercised (see docs/operations/local-run.md):

    script → Temporal SmokeWorkflow → write_smoke_record Activity
           → PostgreSQL row + MinIO object
           → read both back and verify

Exit code 0 on success, 1 on failure.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.infra.deps import get_object_storage, get_session_factory  # noqa: E402
from app.infra.temporal import create_temporal_client  # noqa: E402
from app.storage.smoke_repo import get_smoke_probe  # noqa: E402
from app.workflows.smoke import SmokeWorkflowInput, SmokeWorkflowResult  # noqa: E402


async def run() -> int:
    settings = get_settings()
    message = f"kairos-smoke-{uuid4().hex[:8]}"
    client = await create_temporal_client(settings)

    print(f"-> start smoke_workflow (queue={settings.temporal_smoke_task_queue})")
    handle = await client.start_workflow(
        "smoke_workflow",
        arg=SmokeWorkflowInput(message=message),
        id=f"smoke-{uuid4().hex[:8]}",
        task_queue=settings.temporal_smoke_task_queue,
        result_type=SmokeWorkflowResult,
    )
    result: SmokeWorkflowResult = await handle.result(rpc_timeout=timedelta(seconds=90))
    print(f"-> workflow completed: record_id={result.record_id} object_key={result.object_key}")

    session = get_session_factory()()
    try:
        probe = get_smoke_probe(session, result.record_id)
    finally:
        session.close()
    if probe is None or probe.message != message:
        print(f"FAIL: postgres read-back mismatch (probe={probe})")
        return 1
    print(f"-> postgres read-back OK (id={probe.id}, workflow={probe.workflow_id})")

    storage = get_object_storage()
    content = await storage.get(result.object_key)
    if content != f"kairos-smoke:{message}".encode():
        print("FAIL: minio read-back content mismatch")
        return 1
    print(f"-> minio read-back OK ({result.object_key})")

    print("SMOKE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
