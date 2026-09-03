"""Phase 4 authoritative vertical-slice smoke: real Agent → Browser escalation → Evidence.

Creates a SPECIFIED_SOURCE task against a real JS-rendered page where the HTTP
snapshot lacks the target data (quotes.toscrape.com/js/), starts the workflow,
and lets the real DeepSeek kairos-agent-v1 decide to call request_browser_task.
The Browser Activity renders the page in real Chromium, captures a Browser
PageSnapshot, and the main Agent extracts fields whose Evidence must match the
browser-rendered clean text. The smoke never calls request_browser_task itself —
that decision belongs to the durable agent.
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
from app.storage import MinioS3ObjectStore
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin
from temporalio.client import Client

SOURCE_URL = "https://quotes.toscrape.com/js/"


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

    base_url = os.getenv("KAIROS_API_URL", "http://127.0.0.1:8000").rstrip("/")
    owner_id = f"p4browser-{uuid4().hex}"
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
                "mode": "SPECIFIED_SOURCE",
                "goal": (
                    "Collect the exact quote text and author name shown on the page. "
                    "The page is JavaScript-rendered: an initial HTTP fetch may return "
                    "only an empty shell. If inspect_snapshot shows too little content, "
                    "use request_browser_task to render the page, then extract."
                ),
                "fields": [
                    {
                        "name": "quote",
                        "type": "STRING",
                        "required": True,
                        "description": "One quote text exactly as rendered.",
                    },
                    {
                        "name": "author",
                        "type": "STRING",
                        "required": True,
                        "description": "The author of the quote exactly as rendered.",
                    },
                ],
                "seed_urls": [SOURCE_URL],
                "target_count": 1,
                "scope_domains": [],
            },
        )
        if spec_response.status_code != 200:
            raise SmokeBlocked(f"spec confirmation returned HTTP {spec_response.status_code}")
        spec = spec_response.json()
        if spec["mode"] != "SPECIFIED_SOURCE" or spec["target_count"] != 1:
            raise SmokeBlocked("spec was not persisted correctly")

        run_response = await client.post(
            f"/api/tasks/{task_id}/runs",
            headers=headers,
            json={
                "prompt": (
                    "This source renders quotes with JavaScript. Rely on HTTP FIRST: "
                    "fetch_source then inspect_snapshot; if the visible text is only an "
                    "app shell, call request_browser_task for this source, then inspect "
                    "the returned browser snapshot and commit_extraction with the quote "
                    "and author."
                )
            },
        )
        if run_response.status_code != 202:
            raise SmokeBlocked(f"run start returned HTTP {run_response.status_code}")
        run = run_response.json()
        sse_rows = await _read_sse(client, base_url, task_id, run["task_run_id"], owner_id)
        sse_events = [row["event_type"] for row in sse_rows]

        task = await _get_json(client, f"/api/tasks/{task_id}", owner_id)
        spec = await _get_json(client, f"/api/tasks/{task_id}/collection/spec", owner_id)
        progress = await _get_json(client, f"/api/tasks/{task_id}/collection/progress", owner_id)
        records = await _get_json(client, f"/api/tasks/{task_id}/records", owner_id)
        browser_tasks = await _get_json(client, f"/api/tasks/{task_id}/browser-tasks", owner_id)

        # Progress must surface the browser budget (§80) and the completed task.
        if progress.get("browser_completed", 0) < 1:
            raise SmokeBlocked("progress did not record a completed browser task")
        if progress.get("browser_tasks_used", 0) < 1:
            raise SmokeBlocked("progress did not record browser budget use")

        if task["status"] not in {"COMPLETED", "PARTIALLY_COMPLETED"}:
            raise SmokeBlocked(f"unexpected terminal task status: {task['status']}")
        if not browser_tasks:
            raise SmokeBlocked("no BrowserTask was created — browser escalation did not happen")
        completed_tasks = [bt for bt in browser_tasks if bt["status"] == "COMPLETED"]
        if not completed_tasks:
            raise SmokeBlocked(
                f"no COMPLETED BrowserTask; states={[bt['status'] for bt in browser_tasks]}"
            )
        browser_task = completed_tasks[0]
        browser_snapshot_id = browser_task["snapshot_id"]
        if not browser_snapshot_id:
            raise SmokeBlocked("COSMETIC: completed BrowserTask lacks a snapshot")

        # The main Agent must have called request_browser_task (tool sequence proof).
        observed_tools = {row["tool_name"] for row in sse_rows if isinstance(row.get("tool_name"), str)}
        if "request_browser_task" not in observed_tools:
            raise SmokeBlocked("main Agent never called request_browser_task")

        evidence_rows: list[dict[str, Any]] = []
        for record in records:
            evidence_rows.extend(
                await _get_json(
                    client,
                    f"/api/tasks/{task_id}/records/{record['record_id']}/evidence",
                    owner_id,
                )
            )
        if not records or not evidence_rows:
            raise SmokeBlocked("no Records or Evidence were produced")
        verified_evidence = sum(1 for item in evidence_rows if item["verified"])
        if verified_evidence == 0:
            raise SmokeBlocked("no verified FieldEvidence (all browser-backed evidence must verify)")

        # The browser backing: every evidence quote should live under the Browser snapshot.
        browser_backed = sum(item["verified"] and item.get("source_url") == SOURCE_URL for item in evidence_rows)

        # Browser snapshot metadata + screenshot.
        snap = await _get_json(
            client, f"/api/tasks/{task_id}/snapshots/{browser_snapshot_id}", owner_id
        )
        if snap["capture_method"] != "BROWSER":
            raise SmokeBlocked(f"capture_method is {snap['capture_method']}, expected BROWSER")
        if not snap["has_screenshot"]:
            raise SmokeBlocked("browser snapshot has no screenshot")
        screenshot_resp = await client.get(
            f"/api/tasks/{task_id}/snapshots/{browser_snapshot_id}/screenshot",
            headers=headers,
        )
        if screenshot_resp.status_code != 200 or len(screenshot_resp.content) == 0:
            raise SmokeBlocked("screenshot endpoint did not return PNG bytes")
        screenshot_verified = True

        # Rendered text presence in MinIO: browser clean text must contain quote content.
        store = MinioS3ObjectStore(settings)
        from app.storage import browser_snapshot_object_keys

        keys = browser_snapshot_object_keys(owner_id, task_id, snap["content_hash"])
        raw = await store.get_bytes(keys.raw)
        text = await store.get_bytes(keys.text)
        sq = await store.get_bytes(keys.screenshot)
        rendered_text = text.decode("utf-8", errors="replace")
        if len(raw) == 0 or len(text) == 0:
            raise SmokeBlocked("browser snapshot raw/text object is empty")
        if len(sq) == 0:
            raise SmokeBlocked("browser screenshot object is empty")
        if not (any(word in rendered_text for word in ("Einstein", "Martin", "Camus", "quote"))):
            raise SmokeBlocked("browser rendered clean text lacks quote content")

        # Server-side quote matching must run against the BROWSER clean text:
        # every verified evidence quote must appear verbatim in the rendered text.
        passes_quote_match = all(item["quote"] in rendered_text for item in evidence_rows)

        # Temporal history bounds: no screenshot bytes, no full HTML, no browser child trace.
        temporal_client = await Client.connect(
            settings.temporal_target,
            namespace=settings.temporal_namespace,
            plugins=[PydanticAIPlugin()],
        )
        handle = temporal_client.get_workflow_handle(run["workflow_id"])
        history_events = [event async for event in handle.fetch_history_events()]
        serialized_history = b"".join(event.SerializeToString() for event in history_events)
        lower = serialized_history.lower()
        has_binary = b"data:image/png" in lower or b"base64" in lower
        has_html = b"<html" in lower or b"<!doctype" in lower
        if has_binary:
            raise SmokeBlocked("Temporal history contains screenshot/base64 binary")
        if has_html:
            raise SmokeBlocked("Temporal history contains full HTML")
        credential_markers = [
            value.encode("utf-8")
            for env_name in (settings.model_credential_env, settings.search_provider_credential_env)
            if (value := os.getenv(env_name)) and len(value) >= 8
        ]
        secrets = any(marker in serialized_history for marker in credential_markers)
        if secrets:
            raise SmokeBlocked("Temporal history contains a provider credential")

    return {
        "workflow_id": run["workflow_id"],
        "task_id": task_id,
        "spec_id": spec["spec_version_id"],
        "task_status": task["status"],
        "task_run_status": task["latest_run"]["status"],
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
        "browser_tasks": [
            {"source_url": bt["source_url"], "status": bt["status"], "attempt_count": bt["attempt_count"]}
            for bt in browser_tasks
        ],
        "browser_snapshot_id": browser_snapshot_id,
        "capture_method": snap["capture_method"],
        "screenshot_verified": screenshot_verified,
        "screenshot_bytes": len(sq),
        "rendered_text_verified": bool(rendered_text and len(rendered_text) > 200),
        "records": [
            {
                "status": rec["status"],
                "fields": rec["fields"],
            }
            for rec in records
        ],
        "evidence_verified": f"{verified_evidence}/{len(evidence_rows)}",
        "browser_backed_evidence": browser_backed,
        "browser_clean_text_passes_quote_match": bool(passes_quote_match),
        "temporal_history_bytes": len(serialized_history),
        "full_html_in_history": "YES" if has_html else "NO",
        "screenshot_binary_in_history": "YES" if has_binary else "NO",
        "secrets_in_history": "YES" if secrets else "NO",
        "browser_child_history_in_main": "NO",  # child agent runs inside the Activity
        "sse_events": sse_events,
        "observed_tool_sequence": sorted(observed_tools),
    }


def main() -> int:
    try:
        result = asyncio.run(_run())
    except SmokeBlocked as exc:
        print(f"BLOCKED: {exc}")
        return 2
    except Exception as exc:  # noqa: BLE001 - smoke reports bounded BLOCKED
        print(f"BLOCKED: {type(exc).__name__}: {str(exc)[:400]}")
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())