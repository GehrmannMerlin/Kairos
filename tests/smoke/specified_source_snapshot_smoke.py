"""Verify the real HTTP → MinIO → PostgreSQL snapshot boundary without an LLM."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

from pydantic_ai import RunContext

APP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP_ROOT / "backend"))

from app.agent.deps import KairosAgentDeps
from app.agent.tools import fetch_source, get_object_store
from app.domain import (
    CollectionFieldSpec,
    CollectionFieldType,
    CollectionSpecConfirm,
)
from app.repositories import (
    confirm_collection_spec,
    create_task_run,
    get_collection_spec,
    insert_task,
)
from app.storage import snapshot_object_keys


async def main() -> int:
    owner_id = f"snapshot-smoke-owner-{uuid4().hex}"
    task_id = f"snapshot-smoke-task-{uuid4().hex}"
    run_id = f"snapshot-smoke-run-{uuid4().hex}"
    await insert_task(task_id, owner_id)
    await confirm_collection_spec(
        task_id,
        owner_id,
        CollectionSpecConfirm(
            goal="snapshot",
            fields=[CollectionFieldSpec(name="title", type=CollectionFieldType.STRING, required=True)],
            seed_urls=["https://example.com"],
        ),
    )
    spec = await get_collection_spec(task_id, owner_id)
    if spec is None:
        raise RuntimeError("snapshot smoke spec was not persisted")
    await create_task_run(task_id, owner_id, run_id, f"snapshot-smoke-workflow-{uuid4().hex}", "snapshot")
    context = RunContext[object](
        deps=KairosAgentDeps(
            user_id=owner_id,
            task_id=task_id,
            task_run_id=run_id,
            spec_version_id=spec.spec_version_id,
            model_config_id="local-model",
        ),
        model=None,
        usage=None,
        run_id=run_id,
    )
    result = await fetch_source(context, "https://example.com")
    if result.content_hash is None or result.snapshot_id is None:
        raise RuntimeError("snapshot smoke did not return a snapshot")
    store = get_object_store()
    keys = snapshot_object_keys(owner_id, task_id, result.content_hash)
    raw = await store.get_bytes(keys.raw)
    text = await store.get_bytes(keys.text)
    if not raw or not text:
        raise RuntimeError("snapshot smoke found an empty MinIO object")
    print(
        {
            "status": result.status.value,
            "snapshot_id": result.snapshot_id,
            "raw_bytes": len(raw),
            "text_bytes": len(text),
            "preview_chars": len(result.text_preview),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
