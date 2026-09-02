# Kairos Phase 3A — Specified Source Collection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** 在现有唯一 `kairos-agent-v1` durable runtime 上交付 `SPECIFIED_SOURCE` 的真实网页采集闭环：冻结 CollectionSpec，安全抓取指定 seed，保存不可变 PageSnapshot 到 PostgreSQL + MinIO，由同一 Agent 读取 bounded snapshot、提交字段级 Evidence，服务器确定性验证并完成 collection。

**Architecture:** CollectionSpecVersion、CollectionSource、PageSnapshot、ExtractionCommit、Record、FieldEvidence 是 PostgreSQL 业务真相；HTML 与 clean text 只在 Activity/application I/O 层写入 MinIO。`KairosAgentWorkflow` 通过 `load_collection_context` Activity 取得 bounded typed context，继续调用同一个 `kairos-agent-v1`；新的 `kairos-collection-v1` 是构造期静态 toolset，由现有 `TemporalDurability` 自动 Activity 化。Workflow 不直接访问 DB、MinIO 或 HTTP，Completion 由 agent 返回后的 `finalize_collection_run` Activity 查询并决定。

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLAlchemy async + PostgreSQL, Alembic, httpx, boto3/MinIO S3 API, Temporal 1.32.0, Pydantic AI/Harness editable upstream, Vue 3 + TypeScript, pytest/ruff.

**Spec:** User-provided Phase 3A specification pasted in `C:\Users\韩吉衍\.codex\attachments\83271a74-2e2c-4625-bcf8-a9123926feaf\pasted-text.txt`.

## Global Constraints

- Only implement `SPECIFIED_SOURCE`; do not implement SearchProvider, URL discovery/frontier, browser, deduplication, entity resolution, review workflow, artifacts, exports, or deployment changes.
- Keep exactly one main Agent: `kairos-agent-v1`; preserve `fetch_url` and `kairos-web-v1`; add no extractor/collector/parser/validation Agent.
- Register `kairos-collection-v1` statically at Agent construction; do not inject a FunctionToolset from inside the Workflow.
- Workflow input and `KairosAgentDeps` contain IDs and bounded values only; credentials remain in the Worker environment and final reports reveal only `DEEPSEEK_API_KEY_PRESENT=true`.
- All PostgreSQL, MinIO, HTTP, and event persistence I/O runs in Activities or existing Activity-side event handlers; Workflow code remains deterministic.
- Never return or persist raw HTML/full clean text in Temporal results, event payloads, API responses, or failure messages; `inspect_snapshot` returns at most 6000 characters and an Evidence quote is at most 1000 characters.
- All new reads are owner- and task-scoped and return the existing non-inferential 404 semantics for inaccessible IDs.
- `CollectionSpecVersion` is immutable after a run starts; updates before a run create `version + 1`, never mutate an old version.
- No change to `backend/alembic/versions/0001_phase02_foundation.py`; add one forward migration and validate `upgrade head`/`current`.
- Do not push to any remote or modify the three upstream repositories.

## Files and Responsibilities

- Modify `backend/app/domain.py`: collection enums, typed spec/submission/context/progress/result models, bounded domain errors, and task/run terminal statuses.
- Modify `backend/app/models.py`: SQLAlchemy mappings for the six collection tables plus `tasks.spec_version_id`.
- Create `backend/app/url_policy.py`: the single canonicalization, public-address/DNS, redirect, robots, and safe-fetch policy shared by API and collection fetch code.
- Create `backend/app/storage.py`: `ObjectStore` protocol and async `MinioS3ObjectStore` backed by boto3 with `asyncio.to_thread`.
- Create `backend/app/collection.py`: clean-text extraction and deterministic field/evidence validation helpers that do not perform LLM calls.
- Create `backend/alembic/versions/0002_phase3a_collection_foundation.py`: schema migration for collection persistence and indexes.
- Modify `backend/app/config.py` and `backend/pyproject.toml`/`backend/uv.lock`: MinIO settings and the one required S3 dependency, if absent.
- Modify `backend/app/repositories.py`: atomic spec confirmation, owner-scoped source/snapshot/commit/progress/query operations, and legal task-state transitions.
- Modify `backend/app/agent/deps.py`: keep only IDs/bounded run context and make `spec_version_id` required for collection runs when present.
- Modify `backend/app/agent/tools.py`: preserve generic `fetch_url`; add typed `fetch_source`, `inspect_snapshot`, `commit_extraction`, and `get_collection_progress` implementations using collection repositories/storage.
- Modify `backend/app/agent/runtime.py`: statically register `kairos-collection-v1`, configure its Activity policy, and extend instructions without changing Agent identity.
- Modify `backend/app/agent/events.py`: keep existing events and add safe event helpers only if needed by Activity-side collection tools.
- Modify `backend/app/activities.py`: `load_collection_context` and `finalize_collection_run` Activities plus bounded collection event persistence.
- Modify `backend/app/workflows.py`: load bounded collection context, run the same Agent with collection instructions, and call deterministic finalization after a successful Agent result.
- Modify `backend/app/worker.py`: register the two explicit collection lifecycle Activities alongside existing Activities; Pydantic AI generated tool Activities remain auto-registered through the plugin/runtime.
- Modify `backend/app/api.py`: spec confirm/read and progress/records/evidence/snapshot metadata endpoints, collection-aware run creation, and task response fields.
- Modify `frontend/src/api.ts`, `frontend/src/App.vue`, and `frontend/src/style.css`: minimal setup/progress/data/evidence presentation in the existing visual language.
- Create targeted backend tests under `backend/tests/` for domain, URL/robots, storage, repositories, tools, completion, and Temporal registration.
- Create `tests/smoke/specified_source_collection_smoke.py`: one real API-to-Agent smoke using three public static pages, real DeepSeek/Temporal/PostgreSQL/MinIO/HTTP, SSE observation, bounded Temporal history inspection, and final data assertions.

---

### Task 1: Freeze the collection domain and database schema

**Files:**
- Modify: `backend/app/domain.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/repositories.py`
- Create: `backend/alembic/versions/0002_phase3a_collection_foundation.py`
- Test: `backend/tests/test_collection_domain.py`
- Test: `backend/tests/test_collection_repositories.py`

**Interfaces:**
- `CollectionFieldSpec(name: str, type: CollectionFieldType, required: bool, description: str | None)` validates `^[a-z][a-z0-9_]{0,63}$` and rejects duplicate field names at the enclosing spec level.
- `CollectionSpecConfirm(goal: str, fields: list[CollectionFieldSpec], seed_urls: list[str], target_count: int | None, mode: CollectionMode = SPECIFIED_SOURCE)` is the API/domain input.
- `CollectionExecutionContext(spec_version_id: str, mode: CollectionMode, goal: str, fields: list[CollectionFieldSpec], sources: list[CollectionSourceSummary])` is the bounded Activity result.
- `confirm_collection_spec(task_id, owner_id, confirm: CollectionSpecConfirm) -> CollectionSpecVersion` canonicalizes/deduplicates URLs and creates the spec, source rows, and task binding in one transaction.
- `get_collection_context(task_id, task_run_id, owner_id, spec_version_id) -> CollectionExecutionContext` checks all four scopes and never returns ORM rows.

- [ ] **Step 1: Write failing domain tests.** Cover supported field types, identifier rules, duplicate/empty fields, empty/duplicate seed URLs, `SPECIFIED_SOURCE` only, and serialized bounded context values.

```python
def test_collection_spec_rejects_duplicate_field_names():
    with pytest.raises(ValidationError):
        CollectionSpecConfirm(
            goal="extract",
            fields=[
                CollectionFieldSpec(name="title", type=CollectionFieldType.STRING, required=True),
                CollectionFieldSpec(name="title", type=CollectionFieldType.STRING, required=False),
            ],
            seed_urls=["https://example.com"],
        )
```

- [ ] **Step 2: Run the domain tests and verify the missing collection types fail.**

Run: `uv run --project backend pytest backend/tests/test_collection_domain.py -q`

Expected: FAIL because the new collection domain types do not exist yet.

- [ ] **Step 3: Add enums, typed Pydantic models, and collection-specific bounded errors.** Add `CollectionMode` values `SPECIFIED_SOURCE`, `EXPLORATORY`, `HYBRID` for forward compatibility while making confirm validation accept only `SPECIFIED_SOURCE`; add `CollectionSourceStatus`, `RecordStatus`, `CollectionFieldType`, `CollectionSourceSummary`, `CollectionProgress`, `FetchSourceResult`, `InspectSnapshotResult`, `EvidenceSubmission`, `RecordSubmission`, `CommitExtractionInput`, `CommitExtractionResult`, `CompletionResult`; add `TaskStatus.PARTIALLY_COMPLETED` and `TaskRunStatus.PARTIALLY_COMPLETED`.

- [ ] **Step 4: Add SQLAlchemy models and the forward migration.** Add `Task.spec_version_id` nullable FK to `collection_spec_versions.id`; persist typed field and seed arrays as JSON; add owner/task/spec/source/run FKs, immutable snapshot metadata, extraction commit hash, record JSON/status/issues, and evidence metadata. Add unique constraints `(task_id, version)`, `(spec_version_id, canonical_url)`, `(task_run_id, snapshot_id)` and only the task/owner/spec/snapshot/canonical/status indexes needed by the queries.

- [ ] **Step 5: Add repository transaction boundaries and write repository tests.** Implement spec confirmation in one `session.begin()` transaction; derive the next version from the task’s current spec; never update an existing version. Add owner-scoped lookup helpers and a single `finalize_collection_run` repository operation that transitions Task/TaskRun through the domain entry point, not scattered status assignments.

- [ ] **Step 6: Run migration and focused tests.**

Run: `uv run --project backend alembic -c backend/alembic.ini upgrade head`

Run: `uv run --project backend alembic -c backend/alembic.ini current`

Run: `uv run --project backend pytest backend/tests/test_collection_domain.py backend/tests/test_collection_repositories.py -q`

Expected: `0002_phase3a_collection` is current and focused tests pass against the local PostgreSQL.

- [ ] **Step 7: Commit the domain/schema unit.**

```powershell
git add backend/app/domain.py backend/app/models.py backend/app/repositories.py backend/alembic/versions/0002_phase3a_collection_foundation.py backend/tests/test_collection_domain.py backend/tests/test_collection_repositories.py
git commit -m "feat(spec): add specified-source collection domain"

实现 CollectionSpec 版本冻结、指定来源状态与字段证据的数据基础，并通过单事务和唯一约束保护后续采集幂等性。
```

### Task 2: Implement one URL, robots, and MinIO boundary

**Files:**
- Create: `backend/app/url_policy.py`
- Create: `backend/app/storage.py`
- Modify: `backend/app/config.py`
- Modify: `backend/pyproject.toml` and `backend/uv.lock` only if `boto3` is absent
- Test: `backend/tests/test_url_policy.py`
- Test: `backend/tests/test_storage.py`

**Interfaces:**
- `canonicalize_url(url: str) -> str` lowercases scheme/hostname, removes fragments, normalizes default ports, and preserves query parameters.
- `validate_public_http_url(url: str) -> str` returns the canonical URL or raises `CollectionError("INVALID_SOURCE_URL" | "PRIVATE_ADDRESS_BLOCKED")`; it rejects non-http(s), localhost, loopback, private, link-local, multicast, and reserved addresses after DNS resolution.
- `RobotsPolicy.check(url: str) -> None` uses `KairosBot/0.1`; 404/410 allows, 401/403 blocks, disallow blocks, transport/5xx raises a retryable fetch error.
- `ObjectStore.put_bytes(key: str, data: bytes, content_type: str) -> None`, `get_bytes(key: str) -> bytes`, and `exists(key: str) -> bool` are the only upper-layer storage contract.
- `MinioS3ObjectStore` reads endpoint/access/secret/bucket only from settings/Worker environment and wraps blocking boto3 calls with `asyncio.to_thread`.

- [ ] **Step 1: Write failing URL/robots/storage tests.** Test canonicalization/dedupe, unsupported schemes, localhost/private DNS via an injected resolver, redirect policy with a public-to-loopback `Location`, robots 404/403/disallow/5xx, deterministic keys, and fake ObjectStore behavior.

```python
def test_canonicalize_url_normalizes_host_fragment_and_default_port():
    assert canonicalize_url("HTTPS://Example.COM:443/path?q=1#section") == "https://example.com/path?q=1"
```

- [ ] **Step 2: Run focused tests and verify they fail for missing modules.**

Run: `uv run --project backend pytest backend/tests/test_url_policy.py backend/tests/test_storage.py -q`

Expected: FAIL because the policy and storage boundary do not exist yet.

- [ ] **Step 3: Implement canonicalization and DNS/IP validation.** Use `urllib.parse`, `ipaddress`, and `socket.getaddrinfo`; make DNS resolver injection available to tests without weakening production validation. Reject credentials in URLs, missing hosts, invalid ports, and all non-public resolved addresses.

- [ ] **Step 4: Implement bounded safe HTTP/robots helpers.** Use `httpx.AsyncClient(follow_redirects=False)`; resolve each relative/absolute `Location`, canonicalize and revalidate every hop, enforce at most five redirects, and keep the fixed User-Agent. Limit robots response bytes and distinguish allow, blocked, and retryable outcomes without bypass behavior.

- [ ] **Step 5: Implement the ObjectStore and settings.** Add `minio_endpoint`, `minio_access_key`, `minio_secret_key`, `minio_bucket="kairos-snapshots"`, `collection_http_max_bytes=5_242_880`, and `robots_timeout_seconds` settings. Keep credential values out of tracked files and log/event payloads. Add `boto3` only if lock inspection confirms no S3-compatible client is available.

- [ ] **Step 6: Run URL/storage tests and lint.**

Run: `uv run --project backend pytest backend/tests/test_url_policy.py backend/tests/test_storage.py -q`

Run: `uv run --project backend ruff check backend/app/url_policy.py backend/app/storage.py backend/tests/test_url_policy.py backend/tests/test_storage.py`

Expected: all focused tests pass with no ruff errors.

- [ ] **Step 7: Commit the boundary unit.**

```powershell
git add backend/app/url_policy.py backend/app/storage.py backend/app/config.py backend/pyproject.toml backend/uv.lock backend/tests/test_url_policy.py backend/tests/test_storage.py
git commit -m "feat(crawl): add public URL and snapshot storage boundaries"

统一 URL、DNS、robots 与 redirect 安全策略，并提供只在 Activity I/O 层使用的 MinIO 对象存储抽象，避免 SSRF 和大内容进入工作流。
```

### Task 3: Add fetch_source, snapshots, and bounded inspect

**Files:**
- Modify: `backend/app/agent/tools.py`
- Modify: `backend/app/repositories.py`
- Modify: `backend/app/agent/events.py`
- Test: `backend/tests/test_collection_fetch.py`
- Test: `backend/tests/test_collection_inspect.py`

**Interfaces:**
- `async def fetch_source(ctx: RunContext[KairosAgentDeps], url: str) -> FetchSourceResult` only accepts the current run’s canonical CollectionSource; it returns snapshot metadata, a preview at most 2000 characters, and bounded failure metadata, never raw/full text.
- `async def inspect_snapshot(ctx: RunContext[KairosAgentDeps], snapshot_id: str, offset: int = 0, limit: int = 4000) -> InspectSnapshotResult` enforces owner/task scope and `0 <= limit <= 6000`, then reads only clean text from MinIO.
- `_clean_html(body: bytes, content_type: str) -> tuple[str, str | None]` removes `script`, `style`, and `noscript`, extracts title, and normalizes whitespace with the standard library parser.
- Snapshot storage keys are `snapshots/{owner_id}/{task_id}/{content_hash}/raw.html` and `snapshots/{owner_id}/{task_id}/{content_hash}/text.txt`.

- [ ] **Step 1: Write failing fetch/inspect tests.** Cover out-of-scope URL rejection, real HTTP response handling through an injectable HTTP transport, body/content-type limits, source state changes, deterministic key writes, retry idempotency, owner isolation, and inspect max-size enforcement. Assert serialized `FetchSourceResult` has no `raw_html`/`full_text` fields.

- [ ] **Step 2: Run focused tests and confirm the collection tools are absent.**

Run: `uv run --project backend pytest backend/tests/test_collection_fetch.py backend/tests/test_collection_inspect.py -q`

Expected: FAIL because the collection tool functions and persistence helpers are not implemented.

- [ ] **Step 3: Implement bounded HTML/text extraction and snapshot metadata.** Hash raw bytes with SHA-256; write raw/text through `ObjectStore`; create immutable `PageSnapshot` metadata; update the source from `PENDING` to `FETCHED`; emit `snapshot.created` with only IDs, counts, hash, and content type.

- [ ] **Step 4: Implement source-scope, robots, HTTP, and failure handling.** Look up `(owner_id, task_id, task_run_id, spec_version_id, canonical_url)` before any network request. Reuse `validate_public_http_url`, `RobotsPolicy`, and manual redirect checks. Mark irrecoverable blocked requests `BLOCKED`, exhausted fetch failures `FAILED`, keep messages bounded, and let Temporal retry only transport/5xx errors.

- [ ] **Step 5: Implement fetch idempotency and inspect.** If the scoped source is `FETCHED`/`PROCESSED` with a snapshot, return existing metadata without a second snapshot. Inspect reads only the text key and returns `next_offset`, `has_more`, title, URL, and the requested bounded chunk.

- [ ] **Step 6: Run focused tests and the existing generic HTTP regression.**

Run: `uv run --project backend pytest backend/tests/test_collection_fetch.py backend/tests/test_collection_inspect.py backend/tests/test_http_tool.py -q`

Expected: new collection tests and the existing real `example.com` generic `fetch_url` test pass; no generic tool identity is removed.

- [ ] **Step 7: Commit snapshot behavior.**

```powershell
git add backend/app/agent/tools.py backend/app/repositories.py backend/app/agent/events.py backend/tests/test_collection_fetch.py backend/tests/test_collection_inspect.py
git commit -m "feat(crawl): persist immutable page snapshots"

实现指定来源的真实 HTTP 抓取、MinIO raw/text 快照、source 状态与 bounded inspect，并保留旧 generic fetch_url 合约。
```

### Task 4: Add deterministic extraction, Evidence, progress, and completion Activities

**Files:**
- Create: `backend/app/collection.py`
- Modify: `backend/app/agent/tools.py`
- Modify: `backend/app/repositories.py`
- Modify: `backend/app/activities.py`
- Modify: `backend/app/agent/events.py`
- Test: `backend/tests/test_collection_extraction.py`
- Test: `backend/tests/test_collection_completion.py`

**Interfaces:**
- `validate_record_submission(spec: CollectionSpecVersion, snapshot_text: str, submission: RecordSubmission) -> ValidatedRecord` performs unknown-field, strict type, required-field, populated-field, and normalized quote matching checks without calling an LLM.
- `async def commit_extraction(ctx: RunContext[KairosAgentDeps], input: CommitExtractionInput) -> CommitExtractionResult` atomically creates `ExtractionCommit`, `Record`, `FieldEvidence`, and source `PROCESSED`.
- `async def get_collection_progress(ctx: RunContext[KairosAgentDeps]) -> CollectionProgress` returns all counts from PostgreSQL, including `remaining_sources = pending + fetched`.
- `@activity.defn(name="kairos.load_collection_context") async def load_collection_context(input: CollectionContextInput) -> CollectionExecutionContext`.
- `@activity.defn(name="kairos.finalize_collection_run") async def finalize_collection_run(input: FinalizeCollectionInput) -> CompletionResult` deterministically maps source states to `COMPLETED`, `PARTIALLY_COMPLETED`, or `FAILED/INCOMPLETE_COLLECTION` through the repository state transition.

- [ ] **Step 1: Write failing validation/idempotency/completion tests.** Include unknown fields, strict integer/number/bool/date/URL types, missing required fields, missing/false/true evidence matches, empty submissions, same/different payload hashes, `records=[]`, progress counts, all-processed completion, terminal failed/blocked partial completion, and pending/fetched incomplete completion.

```python
def test_false_evidence_quote_requires_review():
    result = validate_record_submission(spec, "Title is Kairos", RecordSubmission(
        fields={"title": "Kairos"},
        evidence={"title": EvidenceSubmission(quote="not in snapshot")},
    ))
    assert result.status is RecordStatus.NEEDS_REVIEW
    assert "EVIDENCE_QUOTE_NOT_FOUND" in result.validation_issues
```

- [ ] **Step 2: Run tests and verify they fail before implementation.**

Run: `uv run --project backend pytest backend/tests/test_collection_extraction.py backend/tests/test_collection_completion.py -q`

Expected: FAIL because deterministic validation, extraction commit, progress, and completion functions are absent.

- [ ] **Step 3: Implement normalized deterministic validation.** Normalize whitespace in the complete clean text and quote; cap quote length at 1000; set `verified` only from server substring matching; derive Evidence `source_url` from the stored snapshot; classify `PASSED`, `NEEDS_REVIEW`, or `REJECTED` exactly per Phase 3A rules.

- [ ] **Step 4: Implement atomic extraction commit and idempotency.** Stable-serialize the request with sorted keys, calculate SHA-256, lock/query `(task_run_id, snapshot_id)`, return the existing result for the same hash, and raise bounded `ALREADY_COMMITTED_DIFFERENT_PAYLOAD` for a different hash. Do not delete or overwrite prior records. On first commit, write all records/evidence and source `PROCESSED` in the same transaction; permit `records=[]`.

- [ ] **Step 5: Implement progress and lifecycle Activities.** `load_collection_context` returns goal/fields/spec/mode and source summaries only. `get_collection_progress` queries owner/task/run/spec counts. `finalize_collection_run` refuses to mark success when `PENDING` or `FETCHED` remain, marks the run/task failed with `INCOMPLETE_COLLECTION`, maps all-processed to `COMPLETED`, and maps all-terminal with failed/blocked sources to `PARTIALLY_COMPLETED`; `NEEDS_REVIEW` records do not alter completion.

- [ ] **Step 6: Emit bounded domain events from Activity-side paths.** Add `collection.started`, `snapshot.created`, `extraction.committed`, `collection.progress`, `collection.completed`, and `collection.partially_completed` only where useful. Payloads contain IDs, statuses, counts, hashes, and bounded error codes, never HTML/full text/credentials.

- [ ] **Step 7: Run focused tests and the workspace regression.**

Run: `uv run --project backend pytest backend/tests/test_collection_extraction.py backend/tests/test_collection_completion.py backend/tests/test_workspace.py -q`

Expected: all extraction/completion tests and the existing workspace write/read isolation tests pass.

- [ ] **Step 8: Commit extraction and completion behavior.**

```powershell
git add backend/app/collection.py backend/app/agent/tools.py backend/app/repositories.py backend/app/activities.py backend/app/agent/events.py backend/tests/test_collection_extraction.py backend/tests/test_collection_completion.py
git commit -m "feat(record): add collection extraction evidence and completion"

由唯一 Kairos Agent 提交结构化字段，服务器完成类型与 Evidence 校验、提取幂等、进度统计及确定性业务完成判定。
```

### Task 5: Wire the single durable Agent and Workflow lifecycle

**Files:**
- Modify: `backend/app/agent/deps.py`
- Modify: `backend/app/agent/runtime.py`
- Modify: `backend/app/workflows.py`
- Modify: `backend/app/worker.py`
- Modify: `backend/app/api.py`
- Modify: `backend/app/repositories.py`
- Modify: `backend/tests/test_temporal_agent.py`
- Create: `backend/tests/test_collection_runtime.py`

**Interfaces:**
- Static `_collection_toolset = FunctionToolset([fetch_source, inspect_snapshot, commit_extraction, get_collection_progress], id="kairos-collection-v1", metadata={"temporal": COLLECTION_TOOL_ACTIVITY_CONFIG})` is added to the existing Agent construction.
- `build_collection_instruction(context: CollectionExecutionContext) -> str` explicitly directs the existing Agent to process only current seeds, inspect bounded chunks, quote real snapshot text, commit every source including `records=[]`, and continue until PostgreSQL progress reports zero remaining sources.
- The collection branch of `KairosAgentWorkflow.run` executes `load_collection_context`, emits `collection.started`, calls `kairos_agent.run` once, then executes `finalize_collection_run` before terminal task/run events.
- Generic runs with `spec_version_id is None` keep the existing `fetch_url`/workspace behavior.

- [ ] **Step 1: Write failing registration/workflow tests.** Assert the Agent still has name `kairos-agent-v1`, keeps `kairos-web-v1` and `kairos-workspace-v1`, adds exactly `kairos-collection-v1`, exposes the four collection tools, and has no second `Agent`. Assert collection run input carries only IDs and bounded values and the workflow registration includes the new lifecycle Activities.

- [ ] **Step 2: Run the registration tests and verify the collection toolset is absent.**

Run: `uv run --project backend pytest backend/tests/test_collection_runtime.py backend/tests/test_temporal_agent.py -q`

Expected: the new registration assertions fail before wiring.

- [ ] **Step 3: Register the static collection toolset and configure TemporalDurability.** Add a collection toolset Activity config with bounded timeout/retry policy; keep the stable existing toolset IDs and Agent name unchanged. Ensure every collection function is async and no runtime injection occurs.

- [ ] **Step 4: Add bounded collection instructions and lifecycle branch.** Use Activity result context, not DB/MinIO access, to build the instruction. Add collection finalization after the Agent’s answer; generic runs still use the existing terminal update path. Keep all event persistence in Activities and keep full content out of all Workflow-visible values.

- [ ] **Step 5: Require a configured spec for the collection UI/API path and preserve generic regression.** `start_run` binds the current task `spec_version_id` into `KairosAgentDeps`; a task without a spec remains allowed for the previous generic path. Register `load_collection_context` and `finalize_collection_run` with the Worker.

- [ ] **Step 6: Run targeted Temporal registration and full backend unit tests.**

Run: `uv run --project backend pytest backend/tests/test_collection_runtime.py backend/tests/test_temporal_agent.py backend/tests/test_http_tool.py backend/tests/test_workspace.py -q`

Expected: collection registration plus the two Phase 0–2 regression capabilities pass; integration tests remain skipped unless explicitly enabled.

- [ ] **Step 7: Commit the durable wiring.**

```powershell
git add backend/app/agent/deps.py backend/app/agent/runtime.py backend/app/workflows.py backend/app/worker.py backend/app/api.py backend/app/repositories.py backend/tests/test_temporal_agent.py backend/tests/test_collection_runtime.py
git commit -m "feat(agent): wire specified-source collection into durable runtime"

将采集工具作为静态 toolset 接入现有 kairos-agent-v1，并通过 bounded context Activity 与确定性 finalization 接通 Temporal 生命周期。
```

### Task 6: Add owner-scoped API queries and the minimal Vue loop

**Files:**
- Modify: `backend/app/api.py`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/style.css`
- Test: `backend/tests/test_collection_api.py`

**Interfaces:**
- `POST /api/tasks/{task_id}/collection-spec/confirm` accepts goal, typed fields, seed URLs, target count, and fixed `SPECIFIED_SOURCE`; it returns the current immutable spec version.
- `GET /api/tasks/{task_id}/collection-spec` returns the owner-scoped current spec and source summaries without internal storage keys.
- `GET /api/tasks/{task_id}/collection-progress` returns typed source/record counts.
- `GET /api/tasks/{task_id}/records` returns owner/task-scoped record rows with spec field values, status, and validation issues, but no raw storage keys.
- `GET /api/tasks/{task_id}/records/{record_id}/evidence` returns field value, source URL, quote, verified, confidence, and field name after task/owner scoping.
- `GET /api/tasks/{task_id}/snapshots/{snapshot_id}` returns metadata only: URL, status/content type/title/hash/counts/preview/captured time; it never returns raw/text keys or content.
- Frontend state includes `CollectionSpec`, `CollectionProgress`, `CollectionRecord`, and `FieldEvidence`; SSE listeners include the bounded collection event names.

- [ ] **Step 1: Write failing API tests.** Cover confirm/versioning, invalid spec, owner/task 404 isolation for spec/progress/records/evidence/snapshot, no storage key leakage, progress and query response shape, and run starts with the current spec.

- [ ] **Step 2: Run API tests and verify missing routes fail.**

Run: `uv run --project backend pytest backend/tests/test_collection_api.py -q`

Expected: FAIL because collection routes and serializers are absent.

- [ ] **Step 3: Add response models and owner-scoped routes.** Reuse `_owner_id`, repository query helpers, and existing 404 semantics; set the current task spec only inside the confirm transaction; reject confirmation after a run has started; do not expose internal MinIO keys or credentials.

- [ ] **Step 4: Add the setup/progress/data/evidence UI without changing the visual system.** Add Goal, one-URL-per-line Seeds, field rows with name/type/required/description, Confirm button disabled once a run starts, progress counters, a simple record table, and a selected-record evidence panel with clickable source URL. Preserve the current workspace and Agent conversation panels.

- [ ] **Step 5: Connect API refresh and SSE.** Load spec/progress/records after bootstrap and after a run; refresh progress/data when `collection.progress`, `snapshot.created`, `extraction.committed`, `collection.completed`, or `collection.partially_completed` arrives; stop the stream on any terminal run event.

- [ ] **Step 6: Run backend API tests and frontend typecheck.**

Run: `uv run --project backend pytest backend/tests/test_collection_api.py -q`

Run: `npm --prefix frontend run typecheck`

Expected: API tests pass and TypeScript compilation completes without generated tracked artifacts.

- [ ] **Step 7: Commit the vertical UI/API surface.**

```powershell
git add backend/app/api.py backend/tests/test_collection_api.py frontend/src/api.ts frontend/src/App.vue frontend/src/style.css
git commit -m "feat(web): add collection setup progress and evidence views"

为现有 Task 页面增加最小指定来源配置、进度、数据与字段证据读取闭环，所有 API 查询保持 owner scoped。
```

### Task 7: Execute the real Phase 3A vertical smoke and close the gates

**Files:**
- Create: `tests/smoke/specified_source_collection_smoke.py`
- Modify: `docs/operations/local-run.md` only for exact Phase 3A command/config notes if needed
- Test artifacts: no tracked credentials, dumps, MinIO data, or full Temporal history

**Interfaces:**
- Smoke uses only API actions to confirm one `SPECIFIED_SOURCE` spec with three stable public pages; it never manually calls collection tools.
- Smoke prints only bounded IDs/counts/statuses, `DEEPSEEK_API_KEY_PRESENT=true`, workflow ID, short event/tool sequence, and Temporal payload-size/content safety results.
- Smoke reads PostgreSQL through owner-scoped API or a bounded verification query and checks MinIO object existence/non-empty content through the configured S3 client without printing secret values/content.

- [ ] **Step 1: Add the smoke script with preflight checks.** Verify `/health`, three public URLs return HTTP 200, robots allow them, and `DEEPSEEK_API_KEY` is present without printing its value. Use `example.com` plus two other stable static public pages selected by the smoke fixture; retain the fixture only as test input.

- [ ] **Step 2: Run the script in a deliberate blocked state check only if services/credential are unavailable.**

Run: `uv run --project backend python tests/smoke/specified_source_collection_smoke.py`

Expected when infrastructure or credential is absent: bounded `BLOCKED` reason; never substitute TestModel or fake MinIO/HTTP. When ready, proceed to the real run.

- [ ] **Step 3: Run the real API-to-Agent smoke.** POST the spec, start a run with a collection instruction, stream SSE until terminal, and record the observed `run.started`, `collection.started`, four collection tool names, bounded domain events, and terminal run event. Do not invoke `fetch_source`, `inspect_snapshot`, `commit_extraction`, or `get_collection_progress` directly from the script.

- [ ] **Step 4: Inspect the real Temporal history without printing it.** Fetch the workflow history by workflow ID, assert the names `kairos-agent-v1`, `kairos-collection-v1`, `fetch_source`, `inspect_snapshot`, `commit_extraction`, `get_collection_progress`, and the related Activities are represented, then scan serialized payloads for raw HTML/full clean text and assert bounded result sizes.

- [ ] **Step 5: Verify PostgreSQL, MinIO, API, and SSE acceptance data.** Assert one spec version, three sources (or actual fixture count), a snapshot and non-empty raw/text objects for each successful page, terminal source states, records/evidence rows, required Evidence verified for every `PASSED` record, progress `remaining_sources == 0`, and no API response exposes `raw_storage_key`/`text_storage_key`/credentials.

- [ ] **Step 6: Run the final targeted regression suite and frontend check.**

Run: `uv run --project backend alembic -c backend/alembic.ini current`

Run: `uv run --project backend pytest backend/tests/test_collection_domain.py backend/tests/test_collection_repositories.py backend/tests/test_url_policy.py backend/tests/test_storage.py backend/tests/test_collection_fetch.py backend/tests/test_collection_inspect.py backend/tests/test_collection_extraction.py backend/tests/test_collection_completion.py backend/tests/test_collection_runtime.py backend/tests/test_collection_api.py backend/tests/test_http_tool.py backend/tests/test_workspace.py -q`

Run: `npm --prefix frontend run typecheck`

Run: `uv run --project backend ruff check backend/app backend/tests`

Expected: migration is at `0002_phase3a_collection`, targeted tests pass, frontend typecheck passes, and ruff reports no errors. The real smoke is separately recorded with its workflow/data/SSE evidence.

- [ ] **Step 7: Perform the required verification-before-completion review.** Re-read this plan and the user specification line by line; inspect `git diff --check`, `git status`, and commit history; confirm no second Agent, no Workflow I/O, no full content in Temporal, idempotency on both boundaries, current-spec scope enforcement, deterministic completion, no Phase 3B/4/7 work, and no upstream changes. Only after fresh command evidence report the six P3A gates.

- [ ] **Step 8: Commit the real smoke test if it contains no secret or unbounded artifact.**

```powershell
git add tests/smoke/specified_source_collection_smoke.py docs/operations/local-run.md
git commit -m "test(crawl): verify specified-source collection smoke"

通过真实 DeepSeek、Temporal、PostgreSQL、MinIO、HTTP、SSE 与 API 数据验收固定来源采集闭环，同时保留不含凭据和整页内容的可审计 smoke 入口。
```

## Plan Self-Review

- **Requirement coverage:** Tasks 1–2 cover all six domain objects, migration, version immutability, canonicalization, DNS/SSRF, redirects, robots, MinIO, and bounded storage. Tasks 3–4 cover source scope, snapshots, inspect, deterministic extraction/evidence, idempotency, progress, events, and completion. Tasks 5–6 cover the single Agent, Workflow Activities, owner-scoped API/SSE, and Vue. Task 7 covers real infrastructure, DeepSeek, three-source Agent-driven loop, Temporal history safety, PostgreSQL/MinIO data, SSE, and regression checks.
- **Second-Agent check:** The plan creates no Agent object other than the existing `kairos_agent`; extraction is a tool call into deterministic persistence, not another model loop.
- **Workflow-I/O check:** All DB/MinIO/HTTP operations are explicitly assigned to Activities, TemporalDurability tool Activities, or the existing Activity-side event handler. Workflow values are IDs, metadata, and bounded context only.
- **Temporal-content check:** Fetch result contains metadata/preview only, inspect is capped at 6000, quotes are capped at 1000, API hides storage keys, and smoke scans history payloads for full content.
- **Idempotency check:** Fetch reuses an existing source snapshot; extraction uses stable payload hashes and the unique run/snapshot boundary; conflicting payloads are rejected without overwrite.
- **Scope/evidence check:** Fetch resolves the current owner/task/run/spec source before network access; Evidence URL comes from PageSnapshot; verified comes only from normalized server matching.
- **Completion check:** LLM output never sets terminal business status; finalization queries source states and maps processed/all-terminal/incomplete deterministically.
- **Scope-expansion check:** Search, frontier, browser, dedupe, merge, approval/review workflow, memory, artifact/export, and deployment work have no tasks.
- **Completeness check:** Every implementation step names files, interfaces, test command, expected result, and commit boundary; none is left vague or incomplete.
