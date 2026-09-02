from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from app.agent.deps import KairosAgentDeps
from app.agent.tools import fetch_source
from app.domain import (
    CollectionError,
    CollectionFieldSpec,
    CollectionFieldType,
    CollectionSourceStatus,
    CollectionSpecConfirm,
)
from app.repositories import confirm_collection_spec, create_task_run, insert_task
from app.storage import InMemoryObjectStore
from app.url_policy import KAIROS_USER_AGENT
from pydantic_ai import RunContext


async def _run_context() -> tuple[RunContext[KairosAgentDeps], InMemoryObjectStore]:
    owner_id = f"owner-{uuid4().hex}"
    task_id = f"task-{uuid4().hex}"
    run_id = f"run-{uuid4().hex}"
    workflow_id = f"workflow-{uuid4().hex}"
    await insert_task(task_id, owner_id)
    await confirm_collection_spec(
        task_id,
        owner_id,
        CollectionSpecConfirm(
            goal="extract page",
            fields=[CollectionFieldSpec(name="title", type=CollectionFieldType.STRING, required=True)],
            seed_urls=["https://example.com/page"],
        ),
    )
    from app.repositories import get_collection_spec

    spec = await get_collection_spec(task_id, owner_id)
    assert spec is not None
    await create_task_run(task_id, owner_id, run_id, workflow_id, "collect")
    deps = KairosAgentDeps(
        user_id=owner_id,
        task_id=task_id,
        task_run_id=run_id,
        spec_version_id=spec.spec_version_id,
        model_config_id="test-model",
    )
    return RunContext[object](deps=deps, model=None, usage=None, run_id=run_id), InMemoryObjectStore()


@pytest.mark.asyncio
async def test_fetch_source_rejects_url_outside_current_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, _ = await _run_context()
    monkeypatch.setattr("app.agent.tools.get_object_store", lambda: InMemoryObjectStore())

    with pytest.raises(CollectionError, match="SOURCE_OUT_OF_SCOPE"):
        await fetch_source(ctx, "https://attacker.example/not-in-spec")


@pytest.mark.asyncio
async def test_fetch_source_persists_raw_and_text_and_returns_bounded_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, store = await _run_context()
    body = (
        b"<html><head><title>Example Page</title></head><body>"
        b"<h1>Visible title</h1><script>secret()</script></body></html>"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"] == KAIROS_USER_AGENT
        if request.url.path == "/robots.txt":
            return httpx.Response(404, request=request)
        return httpx.Response(
            200, content=body, headers={"content-type": "text/html; charset=utf-8"}, request=request
        )

    monkeypatch.setattr("app.agent.tools.get_object_store", lambda: store)
    monkeypatch.setattr(
        "app.agent.tools.create_public_http_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
            headers={"User-Agent": KAIROS_USER_AGENT},
        ),
    )

    result = await fetch_source(ctx, "https://example.com/page")

    assert result.status is CollectionSourceStatus.FETCHED
    assert result.snapshot_id is not None
    assert result.title == "Example Page"
    assert result.bytes_read == len(body)
    assert "Visible title" in result.text_preview
    assert "secret()" not in result.text_preview
    assert "raw_html" not in result.model_dump()
    assert "full_text" not in result.model_dump()
    assert len(result.text_preview) <= 2000
    assert len(store.objects) == 2
    assert all(value for value in store.objects.values())


@pytest.mark.asyncio
async def test_fetch_source_is_idempotent_for_a_fetched_source(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, store = await _run_context()
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.url.path == "/robots.txt":
            return httpx.Response(404, request=request)
        return httpx.Response(
            200, content=b"<title>Once</title>Page", headers={"content-type": "text/html"}, request=request
        )

    monkeypatch.setattr("app.agent.tools.get_object_store", lambda: store)
    monkeypatch.setattr(
        "app.agent.tools.create_public_http_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
            headers={"User-Agent": KAIROS_USER_AGENT},
        ),
    )

    first = await fetch_source(ctx, "https://example.com/page")
    second = await fetch_source(ctx, "https://example.com/page")

    assert first.snapshot_id == second.snapshot_id
    assert calls == 2
