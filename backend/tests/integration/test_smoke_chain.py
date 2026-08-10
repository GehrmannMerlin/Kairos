"""End-to-end Temporal smoke chain against live services.

script → SmokeWorkflow → write_smoke_record Activity
       → PostgreSQL row + MinIO object
       → read both back and verify.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from app.config import get_settings
from app.infra.deps import get_object_storage, get_session_factory
from app.infra.temporal import create_temporal_client
from app.storage.smoke_repo import get_smoke_probe
from app.workflows.smoke import SmokeWorkflowInput, SmokeWorkflowResult

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_smoke_workflow_full_chain() -> None:
    settings = get_settings()
    message = f"integration-{uuid4().hex[:8]}"

    client = await create_temporal_client(settings)
    handle = await client.start_workflow(
        "smoke_workflow",
        arg=SmokeWorkflowInput(message=message),
        id=f"smoke-{uuid4().hex[:8]}",
        task_queue=settings.temporal_smoke_task_queue,
        result_type=SmokeWorkflowResult,
    )
    result = await handle.result(rpc_timeout=timedelta(seconds=90))

    # PostgreSQL read-back
    session = get_session_factory()()
    try:
        probe = get_smoke_probe(session, result.record_id)
    finally:
        session.close()
    assert probe is not None
    assert probe.message == message

    # MinIO read-back
    storage = get_object_storage()
    content = await storage.get(result.object_key)
    assert content == f"kairos-smoke:{message}".encode()
