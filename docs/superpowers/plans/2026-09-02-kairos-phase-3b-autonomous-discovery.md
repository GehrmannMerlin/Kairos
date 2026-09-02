# Kairos Phase 3B Autonomous Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Kairos 从只处理用户提供 Seed URL 的 Phase 3A，升级为由同一个 `kairos-agent-v1` 自主搜索、发现、抓取、提取、去重并按确定性门禁完成的 EXPLORATORY/HYBRID 多来源采集链路。

**Architecture:** 在现有 `CollectionSource` 上扩展来源 frontier，不建立第二张 URL 队列表；新增独立的 `SearchProvider` 协议和 `SearchRound` 业务记录。`search_sources` 是唯一暴露给 Agent 的搜索工具，Provider 凭据只在 Worker/Activity 运行时解析；Temporal 只负责编排 Agent run、确定性 completion gate 和有上限的 continuation，Pydantic AI 继续负责单次自适应工具循环。

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy async, Alembic, PostgreSQL, Temporal 1.32, Pydantic AI `TemporalDurability`, HTTPX, MinIO, Vue 3 + TypeScript strict, pytest/ruff/vue-tsc。

**Spec:** `C:\Users\韩吉衍\.codex\attachments\f1f04c8e-4f18-4918-b2b1-85f93a8ab3a1\pasted-text.txt`

**Execution status (2026-09-02):** Implementation and deterministic verification complete. Real credential-dependent smoke gates remain `BLOCKED` because this environment has no `DEEPSEEK_API_KEY` or `TAVILY_API_KEY`; no mock fallback was used.

## Global Constraints

- 只有一个主 Agent：`kairos-agent-v1`；不创建 Search/Discovery/Crawler/Dedup/Supervisor Agent 或第二套 Agent Loop。
- `SPECIFIED_SOURCE` 保持 Phase 3A 语义且不要求 Search Provider；`EXPLORATORY` 要求 `goal + fields + target_count + Search Provider`；`HYBRID` 还要求 Seed 或 `scope_domains`，新发现结果必须域名匹配。
- 搜索工具固定为 `search_sources(query, max_results=None)`，Agent 不控制 Provider 名称；返回的 snippet 最多 500 字符，不能直接形成 Record/Evidence。
- Search Provider 原始响应、凭据、完整 HTML、MinIO 内容不能进入 Temporal History/Event/SSE/DTO；PageSnapshot 仍由现有 fetch/inspect/commit 链路产生正式 Evidence。
- 使用公开 HTTP URL 校验和 canonicalization；拒绝 private/localhost/非 http(s) URL；hostname scope 必须采用边界匹配。
- Search limits 采用服务端默认与硬上限：rounds 5/10，results per round 10/20，discovered 50/100，processed 30/100。
- 记录 deterministic normalization、SHA-256 fingerprint、URL 优先 identity；保留 duplicate observation 与 Evidence，canonical target count 只统计 PASSED 且 `canonical_record_id IS NULL`。
- 只实现 Phase 3B 所需 HTTP 路径；不实现 Playwright、递归 crawler、Continue-As-New、Artifact、导出、复杂 conflict resolver 或 UI redesign。
- Migration 只新增 `0003_phase3b_discovery`，不得修改 `0001`/`0002`；迁移必须在真实 PostgreSQL 上 `alembic upgrade head` 通过。
- 不向 upstream 仓库提交；不 push；按 focused Conventional Commit 提交英文标题 + 中文正文。

## Current State and Exact Existing Reuse

现有实现位于 `backend/app/{domain.py,models.py,repositories.py,collection.py,agent/tools.py,agent/runtime.py,workflows.py,activities.py,api.py}`，已提供：CollectionSpec 版本冻结、CollectionSource Seed 表、公共 URL/SSRF 规则、HTTP→PageSnapshot/MinIO、bounded `inspect_snapshot`、服务器校验 Evidence 的 `commit_extraction`、进度、事件和单一 Temporal/Pydantic AI Agent。Phase 3B 只扩展这些契约；既有 `fetch_source`、`inspect_snapshot`、`commit_extraction` 和 `SPECIFIED_SOURCE` targeted tests 必须继续通过。

### Task 1: Search Provider protocol and bounded Tavily adapter

**Files:**
- Create: `backend/app/search.py`
- Modify: `backend/app/config.py`
- Modify: `backend/app/agent/deps.py`
- Create: `backend/tests/test_search_provider.py`
- Create: `tests/smoke/search_provider_preflight.py`

**Interfaces:**
- Produces `SearchProvider`, `NormalizedSearchResult`, `SearchProviderResponse`, `SearchProviderError`, `resolve_search_provider()` and an environment-backed Tavily adapter.
- Consumes only `query`, `max_results`, and Worker environment configuration; never puts credentials in DTOs or `KairosAgentDeps`.

- [ ] Step 1: Write failing provider tests for Tavily payload normalization, result cap, 500-character snippet truncation, auth/rate-limit/network error mapping, and DTO serialization without credential fields.
- [ ] Step 2: Run `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_search_provider.py -q`; confirm failure is due to missing protocol/adapter.
- [ ] Step 3: Implement the small protocol and HTTPX Tavily adapter. Read `KAIROS_SEARCH_PROVIDER` (default `tavily`) and `TAVILY_API_KEY` only inside `resolve_search_provider`; use bounded retry for 429/5xx/timeout and safe error codes for auth/network/provider failures.
- [ ] Step 4: Re-run the provider module and `backend\.venv\Scripts\python.exe -m ruff check backend/app/search.py backend/tests/test_search_provider.py`; expect PASS.
- [ ] Step 5: Add the credential-safe preflight helper with output restricted to `provider=<name>`, `results=<count>`, `PASS`; run it only when `TAVILY_API_KEY` exists and otherwise report the specified blocker.

### Task 2: CollectionSpec, SearchRound, Source frontier schema and migration

**Files:**
- Modify: `backend/app/domain.py`
- Modify: `backend/app/models.py`
- Create: `backend/alembic/versions/0003_phase3b_discovery.py`
- Create: `backend/tests/test_phase3b_domain.py`

**Interfaces:**
- Produces typed `SearchLimits`, `SearchRoundStatus`, `SearchRound`, `SearchSourceOrigin`, expanded `CollectionSpecConfirm/Version`, `CollectionSourceSummary`, `CollectionProgress`, `CollectionCompletionDecision`, `SearchSourcesResult` and `SearchSourceResult` DTOs.
- Database adds `search_rounds`, CollectionSource discovery metadata, Record normalization/identity metadata, and `SKIPPED` source status.

- [ ] Step 1: Write failing domain tests for EXPLORATORY/HYBRID validation, empty Seed acceptance for EXPLORATORY, HYBRID Seed-or-scope requirement, hard limits, bounded snippets and `SKIPPED` status.
- [ ] Step 2: Run `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_phase3b_domain.py -q`; confirm the expected validation/attribute failures.
- [ ] Step 3: Add typed defaults and validators without changing valid `SPECIFIED_SOURCE` construction; extend Progress with mode/target/canonical counts/round/saturation/actionable source fields and keep compatible defaults only where required for existing tests.
- [ ] Step 4: Add SQLAlchemy fields, indexes and constraints: `search_rounds` with `(task_run_id, round_number)` unique; CollectionSource `search_round_id`, query/rank/score/snippet/timestamps/attempt count; Record `normalized_data_json`, `record_fingerprint`, `identity_key`, `canonical_record_id`; `SKIPPED` status support.
- [ ] Step 5: Write migration `0003_phase3b_discovery`, run `backend\.venv\Scripts\python.exe -m alembic -c backend/alembic.ini upgrade head` against local PostgreSQL, inspect the resulting revision/schema, then run the domain tests again.

### Task 3: Persist search rounds and canonical Source discovery

**Files:**
- Modify: `backend/app/repositories.py`
- Modify: `backend/app/agent/tools.py`
- Create: `backend/tests/test_search_sources.py`

**Interfaces:**
- Produces owner-scoped `create_search_round`, `complete_search_round`, `get_completed_search_round_by_hash`, `persist_search_sources`, `list_search_rounds`, and Agent tool `search_sources(ctx, query, max_results=None) -> SearchSourcesResult`.
- Consumes `resolve_search_provider`, URL policy helpers and current `CollectionSpec`; all persistence remains PostgreSQL fact.

- [ ] Step 1: Write failing tests for one-call duplicate URL collapse, cross-round duplicate collapse, fragment/hostname canonicalization, unsafe/private rejection, EXPLORATORY acceptance, HYBRID out-of-scope filtering, SEED vs SEARCH origin, query idempotency, ownership, and bounded output.
- [ ] Step 2: Run `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_search_sources.py -q`; confirm the missing tool/repository behavior.
- [ ] Step 3: Implement canonical scope matching with hostname boundary semantics and call `validate_public_http_url` for every provider URL; normalize provider data before persistence and never return provider raw payload.
- [ ] Step 4: Implement round lifecycle in transactions: allocate round number, persist RUNNING, call provider, deduplicate accepted URLs against current Spec Source frontier, attach discovery metadata, complete the round with returned/accepted/new counts; retry only failed prior rounds and reuse completed query hashes.
- [ ] Step 5: Emit bounded `search.started`, `search.completed`, `sources.discovered`, and `dedup.completed` events through the existing event path; rerun the search-source tests and relevant collection repository tests.

### Task 4: Deterministic normalization, record deduplication, conflicts and evidence aggregation

**Files:**
- Modify: `backend/app/collection.py`
- Modify: `backend/app/repositories.py`
- Modify: `backend/app/domain.py`
- Create: `backend/tests/test_collection_dedup.py`
- Modify: `backend/tests/test_collection_extraction.py`

**Interfaces:**
- Produces `normalize_record_data`, `stable_record_fingerprint`, `record_identity_key`, and deterministic canonical mapping inside `persist_extraction_commit`; no new Agent dedup tool.
- Consumes CollectionSpec field order/types and validated records; preserves every source-specific Record and FieldEvidence.

- [ ] Step 1: Write failing tests for string whitespace/Unicode, URL/date canonical values, typed number/boolean preservation, exact fingerprint duplicates, same stable URL identity, duplicate observations/evidence retention, canonical progress count, and same identity with conflicting normalized values becoming `NEEDS_REVIEW` plus `CONFLICTING_SOURCE_VALUE`.
- [ ] Step 2: Run `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_collection_dedup.py backend/tests/test_collection_extraction.py -q`; confirm failures are caused by missing normalized fields/mapping.
- [ ] Step 3: Implement normalization and SHA-256 stable serialization; set `normalized_data_json`, `record_fingerprint`, and `identity_key` on every new Record. Use URL-field identity first, required-field tuple second, exact payload fingerprint third.
- [ ] Step 4: In the existing extraction transaction, lock/find the earliest canonical observation, set later duplicate `canonical_record_id`, preserve Evidence, and add conflict issues/status without overwriting either source value. Reject any commit whose snapshot is missing, so search snippets can never create Records.
- [ ] Step 5: Add default canonical-only repository/API behavior and optional duplicate evidence aggregation; rerun dedup, extraction, API, and Phase 3A regression tests.

### Task 5: Progress, saturation and deterministic completion evaluator

**Files:**
- Modify: `backend/app/domain.py`
- Modify: `backend/app/repositories.py`
- Modify: `backend/app/activities.py`
- Create: `backend/tests/test_collection_completion_phase3b.py`

**Interfaces:**
- Produces `evaluate_collection_completion(...) -> CollectionCompletionDecision`, deterministic `SaturationEvaluator`, SearchRound terminal accounting, and `mark_target_reached_sources_skipped` for only EXPLORATORY/HYBRID.
- Consumes canonical PASSED count, terminal Source states, search round history, spec limits, and the workflow continuation count.

- [ ] Step 1: Write failing deterministic tests for target reached → COMPLETED, two zero-new-pass rounds → PARTIALLY_COMPLETED/SEARCH_SATURATED, search/discovered/processed limits → corresponding partial reason, remaining budget → CONTINUE, LLM “finished” not overriding CONTINUE, and technical fatal failure → FAILED.
- [ ] Step 2: Run `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_collection_completion_phase3b.py -q`; confirm evaluator symbols/fields are absent.
- [ ] Step 3: Implement evaluator as a pure/domain function plus owner-scoped DB activity. Count `PASSED AND canonical_record_id IS NULL`; set saturation only from two completed rounds with zero new canonical passes; keep runtime limit and saturation mutually deterministic.
- [ ] Step 4: Extend progress and round completion calculations. For target reached, mark only exploratory search-discovered pending sources `SKIPPED` with `TARGET_REACHED`; keep all specified seeds eligible and processed.
- [ ] Step 5: Run new completion tests plus existing `test_collection_completion.py` and API progress tests; verify no Continue-As-New code is introduced.

### Task 6: One-agent exploratory workflow continuation and stable search toolset

**Files:**
- Modify: `backend/app/agent/runtime.py`
- Modify: `backend/app/workflows.py`
- Modify: `backend/app/activities.py`
- Modify: `backend/app/worker.py`
- Create: `backend/tests/test_exploratory_workflow.py`

**Interfaces:**
- Produces fixed `kairos-search-v1` toolset registered at Agent construction, mode-specific instructions, `evaluate_collection_completion_activity`, and a max-three continuation loop that re-enters the same `kairos_agent`.
- Consumes bounded `CollectionExecutionContext` and `CollectionCompletionDecision`; Workflow never selects queries or directly performs source operations.

- [ ] Step 1: Write deterministic TestModel/integration tests for an early final answer followed by CONTINUE and a second invocation that reaches target, asserting the same Agent identity and bounded continuation count; also cover fatal failure.
- [ ] Step 2: Run `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_exploratory_workflow.py -q`; confirm the current single-finalize workflow cannot continue.
- [ ] Step 3: Add `kairos-search-v1` to the existing durable toolset metadata and worker activity registration; include EXPLORATORY/HYBRID instructions to read progress, search only when needed, process actionable sources with fetch→inspect→commit, and never use snippets as evidence.
- [ ] Step 4: Replace the unconditional specified-source finalize call with evaluate gate + bounded continuation prompt; finalize only with COMPLETED/PARTIALLY_COMPLETED/FAILED and persist matching events. Keep SPECIFIED_SOURCE path behavior compatible.
- [ ] Step 5: Run workflow, temporal-agent, toolset, completion and extraction targeted tests; inspect toolset IDs to ensure there is still one main Agent.

### Task 7: API progress/search-rounds/spec and canonical records

**Files:**
- Modify: `backend/app/api.py`
- Modify: `backend/tests/test_collection_api.py`
- Create: `backend/tests/test_phase3b_api.py`

**Interfaces:**
- Produces EXPLORATORY/HYBRID spec creation, server-side Search Provider availability validation, owner-scoped `GET /api/tasks/{task_id}/collection/search-rounds`, upgraded progress response, and canonical-only records with optional `include_duplicates=true`.
- Consumes the same repository services as Agent tools; API never calls SearchProvider directly.

- [ ] Step 1: Write failing API tests for exploratory spec creation without Seed URLs when provider is configured, rejection when no provider is available, HYBRID scope validation, owner isolation for progress/rounds/records, and canonical record defaults.
- [ ] Step 2: Run `backend\.venv\Scripts\python.exe -m pytest backend/tests/test_phase3b_api.py -q`; confirm current request model/API lacks fields and route.
- [ ] Step 3: Add request/response models and route mappings, using a stable safe `search_provider` availability DTO with configured/missing + display name only; preserve `SPECIFIED_SOURCE` no-provider behavior.
- [ ] Step 4: Wire `start_run` to pass only the existing provider config identifier/owner context and block exploratory/hybrid runs before Temporal if unavailable; update records/evidence query ownership and canonical filtering.
- [ ] Step 5: Run phase3b API plus all collection API/repository tests and migration check.

### Task 8: Minimal Vue mode/spec/progress surface

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/style.css`

**Interfaces:**
- Produces mode selector, target count, Hybrid scope domains, safe Search Provider configured/missing indicator, and text metrics for canonical passed/target, rounds, discovered/processed sources, latest round deltas and saturation.
- Consumes the existing API/SSE only; no secret/config ID rendering, charts, redesign or browser E2E.

- [ ] Step 1: Update TypeScript DTOs and write the smallest type-level/UI behavior changes needed for new fields; keep current Data/Evidence table.
- [ ] Step 2: Run `npm --prefix frontend run typecheck`; confirm any expected type failures before template/state changes.
- [ ] Step 3: Add conditional form state and payload fields for three modes; require Seed only for specified, target/provider for exploratory, and Seed-or-scope for hybrid.
- [ ] Step 4: Add bounded progress metrics and SSE listeners for search/discovery/dedup/saturation events; show only provider display name and Configured/Missing.
- [ ] Step 5: Run `npm --prefix frontend run typecheck` and `npm --prefix frontend run build` if template structure changes require it.

### Task 9: Real exploratory smoke and final gate evidence

**Files:**
- Create: `tests/smoke/exploratory_collection_smoke.py`
- Modify: `tests/smoke/__init__.py` only if required for import discovery
- Modify: `README.md` or `docs/operations/local-run.md` with the Phase 3B commands and credential boundary

**Interfaces:**
- Produces a real-only smoke that creates one EXPLORATORY target=5 task, starts the existing API/Temporal workflow, waits for SSE completion, queries SearchRound/Source/Progress/Record/Evidence, measures Temporal History bytes and reports bounded gate evidence.
- Consumes real DeepSeek, real configured SearchProvider, PostgreSQL, MinIO, HTTP and Temporal; it never calls Provider/fetch/extract itself and never injects URLs into the Agent.

- [ ] Step 1: Add a smoke preflight that checks `DEEPSEEK_API_KEY`, the selected Search Provider credential and `/health`; absent credentials return `BLOCKED` without mock fallback.
- [ ] Step 2: Implement the autonomous smoke goal “Find 5 well-established Python web frameworks” with required `name`, `website`, `description` fields and official-source guidance, target 5 and bounded server limits.
- [ ] Step 3: Collect only bounded SSE event names/metadata and query facts after completion; verify at least one SearchRound, SEARCH CollectionSources are canonical-unique, every formal field has verified PageSnapshot Evidence, and canonical count drives completion.
- [ ] Step 4: Fetch Temporal history metadata only, serialize events for byte count and scan for HTML/provider raw response/credential markers; report `PASS/WARN` health and fail only on unbounded payloads.
- [ ] Step 5: Run at most two real exploratory smoke attempts if credentials exist; otherwise preserve a truthful BLOCKED report. Run final affected backend tests, frontend checks, `alembic upgrade head`, and ruff before the final report.

## Self-Review Checklist

- [ ] No task creates a second Agent, second URL frontier table, browser path, or Continue-As-New implementation.
- [ ] Existing Phase 3A fetch/inspect/commit and `SPECIFIED_SOURCE` behavior remains covered by targeted regression tests.
- [ ] Search credentials are only resolved inside the provider adapter/Worker runtime and cannot enter `SearchSourcesResult`, events, SSE, DB plaintext or Temporal input.
- [ ] Search snippets are bounded discovery metadata and the missing-snapshot negative test prevents them from becoming Evidence/Records.
- [ ] Completion is decided by the deterministic evaluator, never by the LLM final message or Agent-selected counters.
- [ ] Full HTML, raw Provider payload and full MinIO content remain outside Temporal History.
- [ ] Real A/C/F gates are marked BLOCKED when `DEEPSEEK_API_KEY` or Search Provider credentials are absent; deterministic tests are not presented as real smoke passes.
