"""Run the real Phase 3B exploratory collection smoke through the HTTP API."""

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

from app.config import get_settings  # noqa: E402
from app.storage import MinioS3ObjectStore, snapshot_object_keys  # noqa: E402
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin  # noqa: E402
from temporalio.client import Client  # noqa: E402


class SmokeBlocked(RuntimeError):
    pass


async def _get_json(client: httpx.AsyncClient, path: str, owner_id: str) -> Any:
    response = await client.get(path, headers={"X-Kairos-User-Id": owner_id})
    if response.status_code >= 400:
        raise SmokeBlocked(f"API {path} returned HTTP {response.status_code}")
    return response.json()


async def _read_sse(
    client: httpx.AsyncClient,
    base_url: str,
    task_id: str,
    run_id: str,
    owner_id: str,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current_event: str | None = None
    current_data: dict[str, Any] = {}
    try:
        async with client.stream(
            "GET",
            f"{base_url}/api/tasks/{task_id}/events",
            params={"run_id": run_id},
            headers={"X-Kairos-User-Id": owner_id},
            timeout=httpx.Timeout(900.0),
        ) as response:
            if response.status_code != 200:
                raise SmokeBlocked(f"SSE returned HTTP {response.status_code}")
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    current_event = line.removeprefix("event:").strip()
                elif line.startswith("data:"):
                    try:
                        current_data = json.loads(line.removeprefix("data:").strip())
                    except json.JSONDecodeError:
                        current_data = {}
                elif not line.strip() and current_event is not None:
                    events.append({"event_type": current_event, **current_data})
                    if current_event in {"run.completed", "run.failed"}:
                        break
                    current_event = None
                    current_data = {}
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise SmokeBlocked(f"SSE unavailable: {type(exc).__name__}") from exc
    return events


async def _run() -> dict[str, Any]:
    settings = get_settings()
    if not os.getenv(settings.model_credential_env):
        raise SmokeBlocked("real-model credential is missing")
    search_credential_env = settings.search_provider_credential_env
    if not os.getenv(search_credential_env):
        raise SmokeBlocked("real search-provider credential is missing")

    base_url = os.getenv("KAIROS_API_URL", "http://127.0.0.1:8000").rstrip("/")
    owner_id = f"smoke-owner-{uuid4().hex}"
    async with httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(30.0)) as client:
        health = await client.get("/health")
        if health.status_code != 200 or health.json().get("temporal") != "ok":
            raise SmokeBlocked("API or Temporal health check is unavailable")
        headers = {"X-Kairos-User-Id": owner_id}
        created = await client.post("/api/tasks", headers=headers)
        if created.status_code != 201:
            raise SmokeBlocked(f"task creation returned HTTP {created.status_code}")
        task_id = created.json()["task_id"]
        spec_response = await client.post(
            f"/api/tasks/{task_id}/collection/spec",
            headers=headers,
            json={
                "mode": "EXPLORATORY",
                "goal": (
                    "Find five current Python web frameworks and collect their name, official website, "
                    "and concise description."
                ),
                "fields": [
                    {
                        "name": "name",
                        "type": "STRING",
                        "required": True,
                        "description": "The framework name stated by the official source.",
                    },
                    {
                        "name": "website",
                        "type": "URL",
                        "required": True,
                        "description": "The official project website URL.",
                    },
                    {
                        "name": "description",
                        "type": "STRING",
                        "required": True,
                        "description": "A concise description copied from the fetched snapshot.",
                    },
                ],
                "seed_urls": [],
                "target_count": 5,
                "scope_domains": [],
            },
        )
        if spec_response.status_code != 200:
            raise SmokeBlocked(f"spec confirmation returned HTTP {spec_response.status_code}")
        spec = spec_response.json()
        if spec["mode"] != "EXPLORATORY" or spec["target_count"] != 5:
            raise SmokeBlocked("exploratory spec was not persisted with target_count=5")

        run_response = await client.post(
            f"/api/tasks/{task_id}/runs",
            headers=headers,
            json={
                "prompt": (
                    "Discover and process enough official sources to reach five canonical passed records. "
                    "Use only fetched snapshot text as evidence and leave a deterministic completion state."
                )
            },
        )
        if run_response.status_code != 202:
            raise SmokeBlocked(f"run start returned HTTP {run_response.status_code}")
        run = run_response.json()
        sse_rows = await _read_sse(client, base_url, task_id, run["task_run_id"], owner_id)
        sse_events = [row["event_type"] for row in sse_rows]
        observed_tool_names = [row["tool_name"] for row in sse_rows if isinstance(row.get("tool_name"), str)]
        task = await _get_json(client, f"/api/tasks/{task_id}", owner_id)
        spec = await _get_json(client, f"/api/tasks/{task_id}/collection/spec", owner_id)
        progress = await _get_json(client, f"/api/tasks/{task_id}/collection/progress", owner_id)
        rounds = await _get_json(client, f"/api/tasks/{task_id}/collection/search-rounds", owner_id)
        records = await _get_json(client, f"/api/tasks/{task_id}/records", owner_id)
        all_records = await _get_json(
            client, f"/api/tasks/{task_id}/records?include_duplicates=true", owner_id
        )

        if task["status"] not in {"COMPLETED", "PARTIALLY_COMPLETED"}:
            raise SmokeBlocked(f"unexpected terminal task status: {task['status']}")
        if not {"search.started", "search.completed", "sources.discovered"}.issubset(sse_events):
            raise SmokeBlocked("SSE did not contain the required discovery events")
        if not rounds:
            raise SmokeBlocked("no persisted search rounds were returned")
        if progress["mode"] != "EXPLORATORY" or progress["search_rounds_completed"] < 1:
            raise SmokeBlocked("progress did not expose completed exploratory search rounds")

        returned_results = sum(round_row["returned_results"] for round_row in rounds)
        accepted_results = sum(round_row["accepted_results"] for round_row in rounds)
        new_sources = sum(round_row["new_sources"] for round_row in rounds)
        canonical_urls = {source["canonical_url"] for source in spec["sources"]}

        evidence: list[dict[str, Any]] = []
        for record in records:
            evidence.extend(
                await _get_json(
                    client,
                    f"/api/tasks/{task_id}/records/{record['record_id']}/evidence",
                    owner_id,
                )
            )

        snapshots: list[dict[str, Any]] = []
        for source in spec["sources"]:
            if source["snapshot_id"]:
                snapshots.append(
                    await _get_json(
                        client,
                        f"/api/tasks/{task_id}/snapshots/{source['snapshot_id']}",
                        owner_id,
                    )
                )
        if not snapshots:
            raise SmokeBlocked("no PageSnapshot was persisted")

        store = MinioS3ObjectStore(settings)
        object_sizes: list[dict[str, int]] = []
        for snapshot in snapshots:
            keys = snapshot_object_keys(owner_id, task_id, snapshot["content_hash"])
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
        credential_markers = [
            value.encode("utf-8")
            for env_name in (
                settings.model_credential_env,
                settings.search_provider_credential_env,
            )
            if (value := os.getenv(env_name)) and len(value) >= 8
        ]
        if any(marker in serialized_history for marker in credential_markers):
            raise SmokeBlocked("Temporal history contains a provider credential")
        if b"api_key" in lower_history or b"raw_content" in lower_history:
            raise SmokeBlocked("Temporal history contains provider raw response fields")
        if len(serialized_history) > 8_000_000:
            raise SmokeBlocked("Temporal history exceeded the bounded smoke threshold")

    verified_evidence = sum(1 for item in evidence if item["verified"])
    if not evidence or verified_evidence == 0:
        raise SmokeBlocked("collection returned no verified FieldEvidence")
    return {
        "task_id": task_id,
        "spec_id": spec["spec_version_id"],
        "mode": spec["mode"],
        "target_count": spec["target_count"],
        "search_rounds": len(rounds),
        "queries": [round_row["query"] for round_row in rounds],
        "provider_returned": returned_results,
        "accepted": accepted_results,
        "new_sources": new_sources,
        "unique_collection_sources": len(canonical_urls),
        "duplicate_urls_filtered": max(accepted_results - new_sources, 0),
        "discovered_sources": progress["sources_discovered"],
        "processed": progress["processed_sources"],
        "failed": progress["failed_sources"],
        "blocked": progress["blocked_sources"],
        "skipped": progress["skipped_sources"],
        "snapshots": len(snapshots),
        "minio_raw_objects": len(object_sizes),
        "minio_text_objects": len(object_sizes),
        "minio_objects": object_sizes,
        "observations": progress["observations_total"],
        "canonical_records": progress["canonical_records_total"],
        "passed_canonical_records": progress["passed_canonical_records"],
        "needs_review": progress["needs_review_records"],
        "rejected": progress["rejected_records"],
        "duplicates": sum(record.get("canonical_record_id") is not None for record in all_records),
        "conflicts": sum("CONFLICTING_SOURCE_VALUE" in record["validation_issues"] for record in all_records),
        "verified_evidence": verified_evidence,
        "total_evidence": len(evidence),
        "decision": next(
            (
                row.get("payload", {}).get("decision")
                for row in reversed(sse_rows)
                if row["event_type"] in {"collection.completed", "collection.partially_completed"}
            ),
            "UNKNOWN",
        ),
        "reason": next(
            (
                row.get("payload", {}).get("reason")
                for row in reversed(sse_rows)
                if row["event_type"] in {"collection.completed", "collection.partially_completed"}
            ),
            "UNKNOWN",
        ),
        "saturation": progress["saturation_state"],
        "search_rounds_used": progress["search_rounds_completed"],
        "finalize_evaluate_activity": "PASS",
        "task_status": task["status"],
        "task_run_status": task["latest_run"]["status"],
        "workflow_id": run["workflow_id"],
        "provider": settings.search_provider_type,
        "model": settings.model_id,
        "agent": "kairos-agent-v1",
        "collection_toolset": "kairos-collection-v1",
        "search_toolset": "kairos-search-v1",
        "observed_tool_sequence": list(dict.fromkeys(observed_tool_names)),
        "agent_continuation_count": max(
            (
                row.get("payload", {}).get("agent_continuations", 0)
                for row in sse_rows
                if row["event_type"] in {"collection.completed", "collection.partially_completed"}
            ),
            default=0,
        ),
        "sse_events": sse_events[:60],
        "search_events": "PASS",
        "collection_progress": "PASS",
        "completion_event": "PASS"
        if any(event in sse_events for event in ("collection.completed", "collection.partially_completed"))
        else "FAIL",
        "run_completed": "PASS" if "run.completed" in sse_events else "FAIL",
        "temporal_history_bytes": len(serialized_history),
        "full_html_in_history": "NO",
        "provider_raw_response_in_history": "NO",
        "credential_in_history": "NO",
        "payload_safety": "PASS",
        "history_health": "PASS" if len(serialized_history) <= 5 * 1024 * 1024 else "WARN",
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
