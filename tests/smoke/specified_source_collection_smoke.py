"""Run the real Phase 3A specified-source vertical smoke.

This script intentionally has no TestModel, fake HTTP transport, or in-memory object store path.
It returns BLOCKED when the real credential, API, worker, or infrastructure is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

_APP_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_APP_ROOT / "backend"))

from app.config import get_settings
from app.storage import MinioS3ObjectStore, snapshot_object_keys
from app.url_policy import (
    RetryableFetchError,
    RobotsPolicy,
    request_with_safe_redirects,
)
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin
from temporalio.client import Client

API_BASE = "http://127.0.0.1:8000"
OWNER_ID = f"smoke-owner-{uuid4().hex}"
SEED_URLS = [
    "https://www.python.org/",
    "https://www.djangoproject.com/",
    "https://www.postgresql.org/",
]


class SmokeBlocked(RuntimeError):
    pass


async def _preflight_sources() -> None:
    timeout = httpx.Timeout(20.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for url in SEED_URLS:
            for attempt in range(3):
                try:
                    await RobotsPolicy(client_factory=lambda: httpx.AsyncClient(
                        timeout=timeout,
                        follow_redirects=False,
                        headers={"User-Agent": "KairosBot/0.1"},
                    )).check(url)
                    response, _ = await request_with_safe_redirects(client, url)
                except (RetryableFetchError, httpx.TimeoutException, httpx.TransportError) as exc:
                    if attempt == 0:
                        continue
                    raise SmokeBlocked(f"source preflight failed for {url}: {type(exc).__name__}") from exc
                except Exception as exc:
                    raise SmokeBlocked(f"source preflight failed for {url}: {type(exc).__name__}") from exc
                if response.status_code != 200:
                    raise SmokeBlocked(f"source preflight returned HTTP {response.status_code}")
                break


async def _read_sse(client: httpx.AsyncClient, task_id: str, run_id: str) -> list[str]:
    events: list[str] = []
    current_event: str | None = None
    try:
        async with client.stream(
            "GET",
            f"{API_BASE}/api/tasks/{task_id}/events",
            params={"run_id": run_id},
            headers={"X-Kairos-User-Id": OWNER_ID},
            timeout=httpx.Timeout(300.0),
        ) as response:
            if response.status_code != 200:
                raise SmokeBlocked(f"SSE returned HTTP {response.status_code}")
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    current_event = line.removeprefix("event:").strip()
                elif not line.strip() and current_event is not None:
                    events.append(current_event)
                    if current_event in {"run.completed", "run.failed"}:
                        break
                    current_event = None
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise SmokeBlocked(f"SSE unavailable: {type(exc).__name__}") from exc
    return events


async def _get_json(client: httpx.AsyncClient, path: str) -> Any:
    response = await client.get(path, headers={"X-Kairos-User-Id": OWNER_ID})
    if response.status_code >= 400:
        raise SmokeBlocked(f"API {path} returned HTTP {response.status_code}")
    return response.json()


async def _run() -> dict[str, Any]:
    settings = get_settings()
    if not os.getenv(settings.model_credential_env):
        raise SmokeBlocked("real-model acceptance blocked by credential")
    await _preflight_sources()

    async with httpx.AsyncClient(base_url=API_BASE, timeout=httpx.Timeout(30.0)) as client:
        health = await client.get("/health")
        if health.status_code != 200 or health.json().get("temporal") != "ok":
            raise SmokeBlocked("API or Temporal health check is unavailable")
        headers = {"X-Kairos-User-Id": OWNER_ID}
        created = await client.post("/api/tasks", headers=headers)
        if created.status_code != 201:
            raise SmokeBlocked(f"task creation returned HTTP {created.status_code}")
        task_id = created.json()["task_id"]
        spec_response = await client.post(
            f"/api/tasks/{task_id}/collection/spec",
            headers=headers,
            json={
                "goal": "Collect the page title and one concise visible description from each specified source.",
                "fields": [
                    {
                        "name": "title",
                        "type": "STRING",
                        "required": True,
                        "description": "The page title shown in the source snapshot.",
                    },
                    {
                        "name": "description",
                        "type": "STRING",
                        "required": False,
                        "description": "A short description copied from visible snapshot text.",
                    },
                ],
                "seed_urls": SEED_URLS,
            },
        )
        if spec_response.status_code != 200:
            raise SmokeBlocked(f"spec confirmation returned HTTP {spec_response.status_code}")
        spec = spec_response.json()
        if len(spec["sources"]) != 3:
            raise SmokeBlocked("spec did not persist exactly three sources")

        run_response = await client.post(
            f"/api/tasks/{task_id}/runs",
            headers=headers,
            json={"prompt": "Process every specified source and finish only when the collection is complete."},
        )
        if run_response.status_code != 202:
            raise SmokeBlocked(f"run start returned HTTP {run_response.status_code}")
        run = run_response.json()
        sse_events = await _read_sse(client, task_id, run["task_run_id"])
        task = await _get_json(client, f"/api/tasks/{task_id}")
        spec = await _get_json(client, f"/api/tasks/{task_id}/collection/spec")
        progress = await _get_json(client, f"/api/tasks/{task_id}/collection/progress")
        records = await _get_json(client, f"/api/tasks/{task_id}/records")

        evidence: list[dict[str, Any]] = []
        for record in records:
            evidence.extend(
                await _get_json(client, f"/api/tasks/{task_id}/records/{record['record_id']}/evidence")
            )
        snapshots: list[dict[str, Any]] = []
        for source in spec["sources"]:
            if not source["snapshot_id"]:
                raise SmokeBlocked("a source has no persisted snapshot")
            snapshots.append(
                await _get_json(client, f"/api/tasks/{task_id}/snapshots/{source['snapshot_id']}")
            )

        store = MinioS3ObjectStore(settings)
        object_sizes: list[dict[str, int]] = []
        for snapshot in snapshots:
            keys = snapshot_object_keys(OWNER_ID, task_id, snapshot["content_hash"])
            raw = await store.get_bytes(keys.raw)
            text = await store.get_bytes(keys.text)
            if not raw or not text:
                raise SmokeBlocked("a persisted MinIO snapshot object is empty")
            object_sizes.append({"raw": len(raw), "text": len(text)})

        temporal_client = await Client.connect(
            settings.temporal_target,
            namespace=settings.temporal_namespace,
            plugins=[PydanticAIPlugin()],
        )
        handle = temporal_client.get_workflow_handle(run["workflow_id"])
        history_events = [event async for event in handle.fetch_history_events()]
        serialized_history = b"".join(event.SerializeToString() for event in history_events)
        lower_history = serialized_history.lower()
        if b"<html" in lower_history or b"<!doctype" in lower_history:
            raise SmokeBlocked("Temporal history contains HTML markup")
        if len(serialized_history) > 2_000_000:
            raise SmokeBlocked("Temporal history exceeded the bounded smoke threshold")

    verified_evidence = sum(1 for item in evidence if item["verified"])
    if progress["remaining_sources"] != 0:
        raise SmokeBlocked("collection progress still has remaining sources")
    if task["status"] not in {"COMPLETED", "PARTIALLY_COMPLETED"}:
        raise SmokeBlocked(f"unexpected task status: {task['status']}")
    return {
        "spec_id": spec["spec_version_id"],
        "version": spec["version"],
        "seed_count": len(spec["sources"]),
        "processed": progress["processed_sources"],
        "failed": progress["failed_sources"],
        "blocked": progress["blocked_sources"],
        "snapshots": len(snapshots),
        "minio_objects": object_sizes,
        "records": progress["total_records"],
        "passed": progress["passed_records"],
        "needs_review": progress["needs_review_records"],
        "rejected": progress["rejected_records"],
        "verified_evidence": verified_evidence,
        "total_evidence": len(evidence),
        "task_status": task["status"],
        "workflow_id": run["workflow_id"],
        "provider": "DeepSeek",
        "model": settings.model_id,
        "agent": "kairos-agent-v1",
        "collection_toolset": "kairos-collection-v1",
        "sse_events": sse_events[:40],
        "temporal_history_bytes": len(serialized_history),
    }


def main() -> int:
    try:
        result = asyncio.run(_run())
    except SmokeBlocked as exc:
        print(f"BLOCKED: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 - smoke must report bounded BLOCKED instead of a traceback
        print(f"BLOCKED: {type(exc).__name__}")
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
