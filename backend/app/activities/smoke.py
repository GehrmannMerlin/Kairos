"""M-01 smoke activity.

Proves that a side-effecting Activity can write to PostgreSQL and object
storage. Real crawling / LLM / network side effects arrive in later modules.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from temporalio import activity

from app.infra.deps import get_object_storage, get_session_factory
from app.storage.smoke_repo import create_smoke_probe

SMOKE_OBJECT_PREFIX = "smoke"


@dataclass
class SmokeActivityInput:
    message: str


@dataclass
class SmokeActivityResult:
    record_id: int
    object_key: str
    message: str


@activity.defn
async def write_smoke_record(inp: SmokeActivityInput) -> SmokeActivityResult:
    info = activity.info()
    session_factory = get_session_factory()
    storage = get_object_storage()

    session = session_factory()
    try:
        probe = await asyncio.to_thread(
            create_smoke_probe, session, info.workflow_id or "", inp.message
        )
    finally:
        session.close()

    object_key = f"{SMOKE_OBJECT_PREFIX}/{info.workflow_id}.txt"
    await storage.ensure_bucket()
    await storage.put(object_key, f"kairos-smoke:{inp.message}".encode(), content_type="text/plain")

    return SmokeActivityResult(record_id=probe.id, object_key=object_key, message=inp.message)
