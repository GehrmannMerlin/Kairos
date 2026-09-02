from __future__ import annotations

import html
import json
from datetime import timedelta
from hashlib import sha256
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry
from temporalio.common import RetryPolicy

from app.agent.deps import KairosAgentDeps
from app.collection import validate_record_submission
from app.config import get_settings
from app.domain import (
    CollectionError,
    CollectionProgress,
    CollectionSourceStatus,
    CommitExtractionInput,
    CommitExtractionResult,
    EventEnvelope,
    FetchSourceResult,
    InspectSnapshotResult,
    bounded_payload,
)
from app.models import PageSnapshot
from app.repositories import (
    get_collection_progress as get_collection_progress_record,
)
from app.repositories import (
    get_collection_source_for_run,
    get_collection_spec_for_run,
    get_extraction_commit_for_scope,
    get_snapshot_for_scope,
    insert_agent_event,
    mark_collection_source_failure,
    persist_extraction_commit,
    persist_page_snapshot,
)
from app.storage import MinioS3ObjectStore, ObjectStore, snapshot_object_keys
from app.url_policy import (
    KAIROS_USER_AGENT,
    RetryableFetchError,
    RobotsPolicy,
    request_with_safe_redirects,
    validate_public_http_url,
)


class HttpToolInputError(ValueError):
    """A caller/model error that must not be retried by Temporal."""


class TransientHttpError(RuntimeError):
    """A transport or server response that is reasonable to retry."""


class FetchUrlResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    status_code: int
    content_type: str
    title: str | None
    text_preview: str = Field(max_length=4000)
    bytes_read: int


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.parts.append(data)


class _CleanTextParser(HTMLParser):
    _ignored_tags = {"script", "style", "noscript"}

    def __init__(self) -> None:
        super().__init__()
        self._ignored_depth = 0
        self._title_depth = 0
        self._parts: list[str] = []
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        normalized = tag.lower()
        if normalized in self._ignored_tags:
            self._ignored_depth += 1
        elif normalized == "title":
            self._title_depth += 1

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        if normalized in self._ignored_tags and self._ignored_depth:
            self._ignored_depth -= 1
        elif normalized == "title" and self._title_depth:
            self._title_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        self._parts.append(data)
        if self._title_depth:
            self._title_parts.append(data)


def _clean_text(body: bytes, content_type: str) -> tuple[str, str | None]:
    text = body.decode("utf-8", errors="replace")
    if "html" not in content_type.lower():
        return " ".join(text.split()), None
    parser = _CleanTextParser()
    parser.feed(text)
    clean = " ".join(" ".join(parser._parts).split())
    title = " ".join(" ".join(parser._title_parts).split()) or None
    return clean, title


def _text_preview(body: bytes, content_type: str) -> tuple[str, str | None]:
    text, title = _clean_text(body, content_type)
    return html.unescape(text)[:4000], title


def create_public_http_client() -> httpx.AsyncClient:
    settings = get_settings()
    return httpx.AsyncClient(
        follow_redirects=False,
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": KAIROS_USER_AGENT},
    )


def get_object_store() -> ObjectStore:
    return MinioS3ObjectStore()


async def _read_limited_body(response: httpx.Response, limit: int) -> bytes:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > limit:
            raise CollectionError("BODY_TOO_LARGE", "response body exceeds the collection limit")
    return bytes(body)


def _fetch_source_result(snapshot: PageSnapshot, source_id: str) -> FetchSourceResult:
    return FetchSourceResult(
        snapshot_id=snapshot.snapshot_id,
        source_id=source_id,
        url=snapshot.url,
        final_url=snapshot.url,
        status=CollectionSourceStatus.FETCHED,
        status_code=snapshot.status_code,
        content_type=snapshot.content_type,
        title=snapshot.title,
        content_hash=snapshot.content_hash,
        bytes_read=snapshot.bytes_read,
        text_chars=snapshot.text_chars,
        text_preview=snapshot.text_preview,
    )


async def _persist_collection_event(
    deps: KairosAgentDeps, event_type: str, summary: str, payload: dict[str, object]
) -> None:
    await insert_agent_event(
        EventEnvelope(
            event_type=event_type,
            task_id=deps.task_id,
            task_run_id=deps.task_run_id,
            owner_id=deps.user_id,
            agent_name="kairos-agent-v1",
            summary=summary,
            payload=bounded_payload(payload),
        )
    )


async def fetch_source(ctx: RunContext[KairosAgentDeps], url: str) -> FetchSourceResult:
    deps = ctx.deps  # type: ignore[attr-defined]
    if deps.spec_version_id is None:
        raise CollectionError("COLLECTION_SPEC_REQUIRED", "collection spec is required")
    from app.url_policy import canonicalize_url

    canonical = canonicalize_url(url)
    source = await get_collection_source_for_run(
        deps.task_id, deps.task_run_id, deps.user_id, deps.spec_version_id, canonical
    )
    if source is None:
        raise CollectionError("SOURCE_OUT_OF_SCOPE", "URL is not in the current collection spec")
    validate_public_http_url(canonical)
    if (
        source.status
        in {
            CollectionSourceStatus.FETCHED.value,
            CollectionSourceStatus.PROCESSED.value,
        }
        and source.snapshot_id
    ):
        snapshot = await get_snapshot_for_scope(
            source.snapshot_id,
            deps.task_id,
            deps.task_run_id,
            deps.user_id,
            deps.spec_version_id,
        )
        if snapshot is not None:
            return _fetch_source_result(snapshot, source.source_id)

    settings = get_settings()
    try:
        policy = RobotsPolicy(
            timeout_seconds=settings.robots_timeout_seconds,
            client_factory=create_public_http_client,
        )
        await policy.check(canonical)
        async with create_public_http_client() as client:
            response, final_url = await request_with_safe_redirects(client, canonical)
            if response.status_code == 429 or response.status_code >= 500:
                raise RetryableFetchError(f"source returned transient HTTP status {response.status_code}")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
                raise CollectionError("UNSUPPORTED_CONTENT_TYPE", "source content type is not supported")
            body = await _read_limited_body(response, settings.collection_http_max_bytes)
    except CollectionError as exc:
        blocked = exc.code in {"PRIVATE_ADDRESS_BLOCKED", "ROBOTS_BLOCKED", "REDIRECT_LIMIT"}
        await mark_collection_source_failure(
            source.source_id,
            deps.task_id,
            deps.task_run_id,
            deps.user_id,
            CollectionSourceStatus.BLOCKED if blocked else CollectionSourceStatus.FAILED,
            exc.code,
            exc.message,
        )
        return FetchSourceResult(
            source_id=source.source_id,
            url=source.url,
            status=CollectionSourceStatus.BLOCKED if blocked else CollectionSourceStatus.FAILED,
            failure_code=exc.code,
            failure_message=exc.message,
        )
    except (RetryableFetchError, httpx.TimeoutException, httpx.TransportError) as exc:
        await mark_collection_source_failure(
            source.source_id,
            deps.task_id,
            deps.task_run_id,
            deps.user_id,
            CollectionSourceStatus.FAILED,
            "FETCH_FAILED",
            type(exc).__name__,
        )
        raise RetryableFetchError(f"collection fetch failed: {type(exc).__name__}") from exc

    clean_text, title = _clean_text(body, content_type)
    content_hash = sha256(body).hexdigest()
    keys = snapshot_object_keys(deps.user_id, deps.task_id, content_hash)
    store = get_object_store()
    await store.put_bytes(keys.raw, body, content_type)
    await store.put_bytes(keys.text, clean_text.encode("utf-8"), "text/plain; charset=utf-8")
    snapshot = await persist_page_snapshot(
        source_id=source.source_id,
        owner_id=deps.user_id,
        task_id=deps.task_id,
        task_run_id=deps.task_run_id,
        url=final_url,
        canonical_url=validate_public_http_url(final_url),
        status_code=response.status_code,
        content_type=content_type,
        title=title,
        content_hash=content_hash,
        raw_storage_key=keys.raw,
        text_storage_key=keys.text,
        bytes_read=len(body),
        text_chars=len(clean_text),
        text_preview=clean_text[:2000],
    )
    await _persist_collection_event(
        deps,
        "snapshot.created",
        "Collection snapshot created",
        {
            "snapshot_id": snapshot.snapshot_id,
            "source_id": source.source_id,
            "status_code": snapshot.status_code,
            "content_hash": snapshot.content_hash,
            "bytes_read": snapshot.bytes_read,
            "text_chars": snapshot.text_chars,
        },
    )
    return _fetch_source_result(snapshot, source.source_id)


async def inspect_snapshot(
    ctx: RunContext[KairosAgentDeps], snapshot_id: str, offset: int = 0, limit: int = 4000
) -> InspectSnapshotResult:
    deps = ctx.deps  # type: ignore[attr-defined]
    if deps.spec_version_id is None:
        raise CollectionError("COLLECTION_SPEC_REQUIRED", "collection spec is required")
    if offset < 0:
        raise CollectionError("SNAPSHOT_OFFSET", "snapshot offset must not be negative")
    if limit < 0 or limit > 6000:
        raise CollectionError("SNAPSHOT_LIMIT", "snapshot limit must be between 0 and 6000")
    snapshot = await get_snapshot_for_scope(
        snapshot_id,
        deps.task_id,
        deps.task_run_id,
        deps.user_id,
        deps.spec_version_id,
    )
    if snapshot is None:
        raise CollectionError("SNAPSHOT_NOT_FOUND", "snapshot not found")
    content = (await get_object_store().get_bytes(snapshot.text_storage_key)).decode(
        "utf-8", errors="replace"
    )
    chunk = content[offset : offset + limit]
    next_offset = offset + len(chunk)
    return InspectSnapshotResult(
        snapshot_id=snapshot.snapshot_id,
        url=snapshot.url,
        title=snapshot.title,
        offset=offset,
        content=chunk,
        next_offset=next_offset,
        has_more=next_offset < len(content),
    )


def _extraction_payload_hash(input_data: CommitExtractionInput) -> str:
    payload = input_data.model_dump(mode="json")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


async def commit_extraction(
    ctx: RunContext[KairosAgentDeps], input_data: CommitExtractionInput
) -> CommitExtractionResult:
    deps = ctx.deps  # type: ignore[attr-defined]
    if deps.spec_version_id is None:
        raise CollectionError("COLLECTION_SPEC_REQUIRED", "collection spec is required")

    payload_hash = _extraction_payload_hash(input_data)
    existing = await get_extraction_commit_for_scope(
        input_data.snapshot_id,
        deps.task_id,
        deps.task_run_id,
        deps.user_id,
        deps.spec_version_id,
    )
    if existing is not None and existing.payload_hash == payload_hash:
        return await persist_extraction_commit(
            snapshot_id=input_data.snapshot_id,
            task_id=deps.task_id,
            task_run_id=deps.task_run_id,
            owner_id=deps.user_id,
            spec_version_id=deps.spec_version_id,
            payload_hash=payload_hash,
            validated_records=[],
            source_complete=input_data.source_complete,
        )

    snapshot = await get_snapshot_for_scope(
        input_data.snapshot_id,
        deps.task_id,
        deps.task_run_id,
        deps.user_id,
        deps.spec_version_id,
    )
    spec = await get_collection_spec_for_run(
        deps.task_id,
        deps.task_run_id,
        deps.user_id,
        deps.spec_version_id,
    )
    if snapshot is None or spec is None:
        raise CollectionError("SNAPSHOT_NOT_FOUND", "snapshot not found")
    snapshot_text = (await get_object_store().get_bytes(snapshot.text_storage_key)).decode(
        "utf-8", errors="replace"
    )
    validated_records = []
    for submission in input_data.records:
        try:
            validated_records.append(validate_record_submission(spec, snapshot_text, submission))
        except CollectionError as exc:
            if exc.code == "UNKNOWN_FIELD":
                raise ModelRetry(str(exc)) from exc
            raise
    result = await persist_extraction_commit(
        snapshot_id=input_data.snapshot_id,
        task_id=deps.task_id,
        task_run_id=deps.task_run_id,
        owner_id=deps.user_id,
        spec_version_id=deps.spec_version_id,
        payload_hash=payload_hash,
        validated_records=validated_records,
        source_complete=input_data.source_complete,
    )
    await _persist_collection_event(
        deps,
        "extraction.committed",
        "Collection extraction committed",
        {
            "snapshot_id": result.snapshot_id,
            "extraction_commit_id": result.extraction_commit_id,
            "record_count": result.record_count,
            "idempotent": result.idempotent,
        },
    )
    progress = await get_collection_progress_record(
        deps.task_id,
        deps.task_run_id,
        deps.user_id,
        deps.spec_version_id,
    )
    await _persist_collection_event(
        deps,
        "collection.progress",
        "Collection progress updated",
        progress.model_dump(mode="json"),
    )
    return result


async def get_collection_progress(ctx: RunContext[KairosAgentDeps]) -> CollectionProgress:
    deps = ctx.deps  # type: ignore[attr-defined]
    if deps.spec_version_id is None:
        raise CollectionError("COLLECTION_SPEC_REQUIRED", "collection spec is required")
    progress = await get_collection_progress_record(
        deps.task_id,
        deps.task_run_id,
        deps.user_id,
        deps.spec_version_id,
    )
    await _persist_collection_event(
        deps,
        "collection.progress",
        "Collection progress read",
        progress.model_dump(mode="json"),
    )
    return progress


async def fetch_url(url: str) -> FetchUrlResult:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HttpToolInputError("url must be an absolute http or https URL")

    settings = get_settings()
    body = bytearray()
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=settings.http_timeout_seconds,
            headers={"User-Agent": "Kairos/0.1 durable-agent"},
        ) as client:
            async with client.stream("GET", url) as response:
                if response.status_code == 429 or response.status_code >= 500:
                    raise TransientHttpError(f"transient HTTP status {response.status_code}")
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > settings.http_max_bytes:
                        raise HttpToolInputError("response body exceeds the configured bounded HTTP limit")
                content_type = response.headers.get("content-type", "application/octet-stream")
                text_preview, title = _text_preview(bytes(body), content_type)
                return FetchUrlResult(
                    url=str(response.url),
                    status_code=response.status_code,
                    content_type=content_type.split(";", 1)[0].strip(),
                    title=title,
                    text_preview=text_preview,
                    bytes_read=len(body),
                )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise TransientHttpError(f"HTTP transport failed: {type(exc).__name__}") from exc


WEB_TOOL_ACTIVITY_CONFIG = {
    "start_to_close_timeout": timedelta(seconds=45),
    "retry_policy": RetryPolicy(
        maximum_attempts=3,
        initial_interval=timedelta(seconds=1),
        maximum_interval=timedelta(seconds=10),
        non_retryable_error_types=[HttpToolInputError.__name__],
    ),
}

COLLECTION_TOOL_ACTIVITY_CONFIG = {
    "start_to_close_timeout": timedelta(seconds=90),
    "retry_policy": RetryPolicy(
        maximum_attempts=3,
        initial_interval=timedelta(seconds=1),
        maximum_interval=timedelta(seconds=10),
        non_retryable_error_types=[CollectionError.__name__, ModelRetry.__name__],
    ),
}
