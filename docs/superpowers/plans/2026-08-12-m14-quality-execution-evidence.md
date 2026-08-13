# M-14 Quality / Execution / Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the D-024 + D-048 secondary-page UX closed loop: `/tasks/:id/quality` (diagnosis + accurate Data drilldown), `/tasks/:id/execution` (stage summary + redacted timeline + read-only Plan DAG + Node Detail Drawer), and `/tasks/:id/evidence/:evidenceId` (historical snapshot viewer + Quick Evidence + secure owner-safe content access). No new page types, no billing UI, no live refetch.

**Architecture:** Three backend read-model packages (`app/quality/`, `app/execution/`, `app/evidence/`) consume existing DB facts only (M-12 `QualitySnapshot`/`ValidationResult`/`FieldConflict`, M-04 `DomainEvent`/`Run`/`URLResource`/`PageSnapshot`/`Record`/`FieldEvidence`, M-08 frozen `PlanVersion.payload.graph`, M-11 field schema, M-10 `ObjectStorage`). Each route is owner-safe (404 on cross-user). Frontend adds three feature composables/views that reuse M-13 deep-link query contract; the Evidence Viewer renders the stored snapshot via a sandboxed iframe / image / text (never refetches the live source).

**Tech Stack:** FastAPI + SQLAlchemy + Pydantic (strict DTOs), MinIO `ObjectStorage` (already wired via `app.infra.deps`), Vue 3 + TS strict + Vue Router, Vitest + pytest (A-Lite scoped).

## Global Constraints

- **M13_BASELINE_SHA:** `10e74c7a74ad769cc32281093e0bd026e682f5b7` (docs(review): record M-13 DONE). Branch for this work: `feature/M-14-quality-execution-evidence`. No push / merge / tag.
- **D-036 / no money UI:** Timeline, Node Detail, and Quality must expose token/request/duration/size technical stats only. Never RMB/USD, estimated cost, budget, billing strings.
- **D-023 / owner isolation:** Every route does `TaskRepository.get_owned(user_id, task_id)` first; resource-level checks (record/snapshot/evidence) verify `user_id`; cross-user → safe 404. Signed content generated only after ownership check.
- **D-024 / D-039 / D-047 redaction:** Backend DTOs redact before returning. Never return API Key / Cookie / Authorization / password / secret form values / session token / bucket credentials. Frontend is not the security boundary.
- **D-048 / 13-page boundary:** No new page types. Only fill `/tasks/:taskId/{quality,execution,evidence/:evidenceId}` and the `EVIDENCE_QUICK` + `NODE_DETAIL` drawers. No `/logs`, `/traces`, `/snapshots`, `/evidence-center`, `/execution/node/:id` pages.
- **D-056 / D-064 no live fetch:** Evidence Viewer reads `PageSnapshot`/`ObjectStorage` saved at M-10 time. `fetch(source_url)` is forbidden. A test must assert live-fetch transport is never invoked.
- **Deep-link contract = M-13 contract:** Quality drilldowns serialize to `/tasks/:id/data?...` using params the Data page parser accepts (`status`, `review_type`, `source_type`, `extract_method`, `q`, `field`, `value`). `review_type` uses the real finite set (`missing_required`, `unresolved_conflict`, `possible_duplicate`, `low_confidence`, …) from `app/validation/partitioner.py::REVIEW_TYPES`. No invented params.
- **Stages from facts:** Execution stage summary aggregates `Run` + `DomainEvent` + `URLResource` + `Record` facts. The current crawl path does not write `NodeRun`/`NodeAttempt` rows, so M-14 must not fabricate per-node NodeRun data; node detail shows frozen plan definition + available event/stage evidence with explicit `—` where facts do not exist.
- **No new Node Retry command:** Node Detail is read-only; a Retry button appears only if a backend `allowed_actions`/command contract already supports it. It does not, so none.
- **M-15 / M-17 boundaries:** No CSV/artifact/export, no delete/restore/retention, no Observability platform, no completion card. DEFERRED-DYNAMIC-E2E-01 is untouched. DEPLOY-GATE-4 is NOT executed this round.
- **A-Lite / fast-development:** Run only the M-14 scoped suites, ruff/mypy on changed packages, vue-tsc + vite build. No full regression (no `pytest tests/`, no M-09–M-13 full suites).
- **Migration:** NONE. `0011` stays head. Existing tables/columns satisfy all three slices; no index added unless a scoped query measure shows a real gap.

---

## File Structure

Backend (create):
- `backend/app/quality/__init__.py`, `backend/app/quality/contracts.py`, `backend/app/quality/repository.py`, `backend/app/quality/service.py`
- `backend/app/execution/__init__.py`, `backend/app/execution/contracts.py`, `backend/app/execution/repository.py`, `backend/app/execution/service.py`
- `backend/app/evidence/__init__.py`, `backend/app/evidence/contracts.py`, `backend/app/evidence/repository.py`, `backend/app/evidence/service.py`
- `backend/app/api/routes/quality.py`, `backend/app/api/routes/execution.py`, `backend/app/api/routes/evidence.py`

Backend (modify):
- `backend/app/api/router.py` — register three new routers
- `backend/app/review/repository.py` — make `source_type` filter resolve via `URLResource` join (deep-link accuracy fix for real records)

Backend tests (create):
- `backend/tests/quality/test_quality_api.py`
- `backend/tests/execution/test_execution_api.py`
- `backend/tests/execution/test_dag_api.py`
- `backend/tests/evidence/test_evidence_api.py`
- `backend/tests/review/test_records_source_type.py` (extend source_type resolution)

Frontend (create):
- `frontend/src/features/quality/types.ts`, `frontend/src/features/quality/quality.api.ts`, `frontend/src/features/quality/useQuality.ts`
- `frontend/src/features/execution/types.ts`, `frontend/src/features/execution/execution.api.ts`, `frontend/src/features/execution/useExecution.ts`
- `frontend/src/features/evidence/types.ts`, `frontend/src/features/evidence/evidence.api.ts`, `frontend/src/features/evidence/useEvidence.ts`

Frontend (modify):
- `frontend/src/features/tasks/TaskQualityView.vue` (real content)
- `frontend/src/features/tasks/TaskExecutionView.vue` (real content: stages + timeline + DAG toggle)
- `frontend/src/features/tasks/TaskEvidenceView.vue` (real viewer)
- `frontend/src/app/overlay/drawers/EvidenceQuickDrawer.vue` (real quick evidence)
- `frontend/src/app/overlay/drawers/NodeDetailDrawer.vue` (real node detail)
- `frontend/src/app/router/deepLinks.ts` (extend typed query: `extract_method`, `min_confidence`, `q`, `field`, `value`)
- `frontend/src/features/data/useRecords.ts` (expose a `buildDataLink()` helper that serializes the typed drilldown snapshot to a `/tasks/:id/data` route — single source of truth for deep links)

Docs:
- `docs/implementation/M-14-execution.md` (created at task 8)

---

### Task 1: Quality Query backend + accurate source_type drilldown

**Files:**
- Create: `backend/app/quality/contracts.py`, `backend/app/quality/repository.py`, `backend/app/quality/service.py`, `backend/app/api/routes/quality.py`
- Modify: `backend/app/api/router.py`, `backend/app/review/repository.py`
- Test: `backend/tests/quality/test_quality_api.py`, `backend/tests/review/test_records_source_type.py`

**Interfaces:**
- Consumes: M-12 `QualitySnapshot` (via `ValidationRepository.latest_snapshot`), `Record`/`ValidationResult`/`FieldConflict`/`URLResource` (via `app.domain.models`), `CollectionSpecVersion.payload.fields` (`app.domain.models`), `RecordListParams` semantics (`app.review.contracts`).
- Produces:
  - `app.quality.contracts.QualityDrilldown` — `{status: Literal['passed','review','rejected']|None, review_type: str|None, source_type: str|None, extract_method: str|None, min_confidence: float|None}`
  - `app.quality.contracts.QualityMetricItem` — `{key: str, label: str, value: int|float, kind: Literal['count','rate'], drilldown: QualityDrilldown}`
  - `app.quality.contracts.QualityView` — `{task_id, dataset_version, validation_version, sampling_policy_version, spec_version, run_id, snapshot_id, snapshot_created_at, summary:{total_records,passed,needs_review,rejected}, metrics:{pass_rate,missing_rate,duplicate_rate,conflict_count,source_coverage,sampling_accuracy}, field_completeness:[{field_name,total,non_null,missing,completion_rate}], source_coverage:[{source_type,eligible,covered,record_count}], diagnostics:{missing_required,unresolved_conflict,possible_duplicate,low_confidence,rejected}, sampling:{sample_count,accuracy,sample_refs}, items:[QualityMetricItem]}`
  - Route `GET /api/tasks/{task_id}/quality` → `QualityView`
- **Note:** `source_type` resolution fix — `ReviewRepository.query_records` currently filters `payload.get("source_type")`, which real extraction payloads never contain. Extend it to resolve source via `Record.url_resource_id → URLResource.source_type` (fallback to payload match for any synthetic fixture records). This keeps the M-13 contract (param name unchanged) and makes Quality source drilldowns accurate.

- [ ] **Step 1: Write the failing tests** in `tests/quality/test_quality_api.py` (compact, per §55 of the round brief): quality numbers come from DB facts (partition counts), field completeness from spec fields + record payloads, missing/conflict/duplicate/low-confidence diagnostics aggregate correctly, every `QualityMetricItem.drilldown` matches the M-13 contract (`review_type` in `REVIEW_TYPES`, `status` in `passed/review/rejected`), and a cross-user task id returns 404. Write `tests/review/test_records_source_type.py` proving `?source_type=<t>` filters records whose `URLResource.source_type == t`.
- [ ] **Step 2: Run the tests to verify they fail** — run `python -m pytest tests/quality tests/review/test_records_source_type.py -q`; expect import errors (no `app.quality`) and a failing source_type test.
- [ ] **Step 3: Implement `app/quality/contracts.py`** with the strict Pydantic DTOs listed above (all `extra="forbid"`, following `app/review/contracts.py` style).
- [ ] **Step 4: Implement `app/quality/repository.py`** — `QualityRepository(db)` with `latest_snapshot(user_id, task_id)` (delegate to `ValidationRepository`), `count_by_partition`, `missing/conflict/duplicate/low_confidence` counts via `Record.review_type`, `field_completeness(spec_fields, records)` computing per-field non-null/missing/completion from `Record.payload.values`, `source_coverage` from `URLResource` (distinct `source_type` as eligible, status in `FETCHED/HANDED_OFF` as covered, plus per-source record counts via `url_resource_id`), and snapshot `sample_refs`.
- [ ] **Step 5: Implement `app/quality/service.py`** — `QualityService.assemble(user_id, task_id)` builds `QualityView`: version boundary from latest snapshot; summary/metrics from snapshot + live partition counts; diagnostics from live DB; `items` list of typed `QualityMetricItem`s with drilldowns: passed→`{status:passed}`, needs_review→`{status:review}`, rejected→`{status:rejected}`, missing→`{status:review, review_type:missing_required}`, conflict→`{status:review, review_type:unresolved_conflict}`, duplicate→`{status:review, review_type:possible_duplicate}`, low_confidence→`{status:review, review_type:low_confidence}`, source coverage→`{source_type:<t>}` per source. Empty task → explicit zero/empty state (never fake metrics).
- [ ] **Step 6: Implement `app/api/routes/quality.py`** — `router = APIRouter(prefix="/tasks/{task_id}/quality")`, one `GET ""` route; `TaskRepository(db).get_owned(user.id, task_id)` then `QualityService(db).assemble(...)`. Register in `app/api/router.py`.
- [ ] **Step 7: Fix `source_type` resolution** in `app/review/repository.py::query_records` (join URLResource when `params.source_type` set; keep Python-side payload fallback for fixtures). Keep behavior owner-safe.
- [ ] **Step 8: Run the tests to verify they pass** — `python -m pytest tests/quality tests/review/test_records_source_type.py -q` → PASS.
- [ ] **Step 9: Commit** — `git add backend/app/quality backend/app/api/routes/quality.py backend/app/api/router.py backend/app/review/repository.py backend/tests/quality backend/tests/review/test_records_source_type.py && git commit -m "feat(quality): add quality query with accurate drilldowns"`

---

### Task 2: Quality page + Data deep links (frontend)

**Files:**
- Create: `frontend/src/features/quality/types.ts`, `frontend/src/features/quality/quality.api.ts`, `frontend/src/features/quality/useQuality.ts`
- Modify: `frontend/src/features/tasks/TaskQualityView.vue`, `frontend/src/features/data/useRecords.ts`, `frontend/src/app/router/deepLinks.ts`
- Test: `frontend/src/features/quality/quality.api.test.ts`, `frontend/src/features/tasks/TaskQualityView.test.ts`

**Interfaces:**
- Consumes: `QualityView` DTO from Task 1.
- Produces:
  - `frontend/src/features/quality/quality.api.ts::getQuality(taskId) → Promise<QualityView>`
  - `frontend/src/features/quality/useQuality.ts::useQuality(taskId)` — `{view, loading, error, reload}`
  - `frontend/src/features/data/useRecords.ts::buildDataLink(taskId, drilldown: QualityDrilldown)` → returns a `{ name: 'task-data', params: { taskId }, query }` route target, serializing `status/review_type/source_type/extract_method/min_confidence`. Single source of truth — components never hand-concatenate query strings.
  - `TaskQualityView.vue` renders metric cards from `view.items` (value/label, zero/empty states), field completeness list, source coverage list, sampling summary; clicking a card with a non-empty drilldown navigates via `buildDataLink`.

- [ ] **Step 1: Write the failing tests** — `quality.api.test.ts` (mock `apiClient.get`, assert query response typed and error mapping), `TaskQualityView.test.ts` (renders cards from API; clicking a "待复核" card navigates to `/tasks/:id/data?status=review`; a card with empty drilldown does not navigate; empty task shows explicit empty state). Test `buildDataLink` serialization.
- [ ] **Step 2: Run tests to verify they fail** — `cd frontend && npm run test:unit -- quality TaskQualityView` → FAIL (missing modules).
- [ ] **Step 3: Implement `types.ts` / `quality.api.ts` / `useQuality.ts`** mirroring the `features/data` pattern (`useAsync`-style idle/loading/success/empty/error).
- [ ] **Step 4: Extend `deepLinks.ts::TaskDeepLinkQuery`** with `extract_method?`, `min_confidence?`, `q?`, `field?`, `value?` and update `parseTaskQuery`.
- [ ] **Step 5: Add `buildDataLink` to `useRecords.ts`** serializing a typed drilldown snapshot to a `task-data` route target.
- [ ] **Step 6: Implement `TaskQualityView.vue`** — metric cards grid + sections; no Record edit/approve/reject controls (Quality is diagnosis only per D-062). Use `router.push(buildDataLink(...))`.
- [ ] **Step 7: Run tests to verify they pass** — `npm run test:unit -- quality TaskQualityView` → PASS.
- [ ] **Step 8: Commit** — `git commit -am "feat(web): render quality page with data drilldowns"`

---

### Task 3: Execution overview + Timeline backend

**Files:**
- Create: `backend/app/execution/contracts.py`, `backend/app/execution/repository.py`, `backend/app/execution/service.py`, `backend/app/api/routes/execution.py`
- Modify: `backend/app/api/router.py`
- Test: `backend/tests/execution/test_execution_api.py`

**Interfaces:**
- Consumes: `app.domain.models` (`Run`, `DomainEvent`, `URLResource`, `Record`, `PageSnapshot`), M-07 `query_task_events` ownership semantics.
- Produces:
  - `app.execution.contracts.StageKey` = `goal_plan | source_discovery | fetch | extraction | validation`
  - `app.execution.contracts.StageSummary` — `{key, label, state: Literal['not_started','in_progress','completed','partial','failed'], event_count, url_processed, record_count, error_count}`
  - `app.execution.contracts.TimelineEvent` — `{event_id, timestamp, category: Literal['all','error','retry','tool_upgrade','plan_change','model_call','pause_resume'], stage, summary, status: str|None, error_code: str|None, run_id, node_run_id, node_id: str|None, retry_count: int, tool: str|None, model: str|None, duration_ms: int|None, tokens_in: int|None, tokens_out: int|None, evidence_refs: list[int], trace_ref: str|None}` (redacted allowlist — never the raw payload)
  - `app.execution.contracts.ExecutionView` — `{task_id, run:{run_id,state,started_at,finished_at,plan_version,spec_version}|null, stages:[StageSummary], urls:{discovered,fetched,failed,pending}, records:{passed,needs_review,rejected}, plan:{plan_version,node_count,validation_status}|null}`
  - `app.execution.contracts.TimelinePage` — `{task_id, items:[TimelineEvent], next_cursor: int|None, has_more: bool}`
  - Routes: `GET /api/tasks/{task_id}/execution` → `ExecutionView`; `GET /api/tasks/{task_id}/execution/timeline?category=&after_id=&limit=` → `TimelinePage`

- [ ] **Step 1: Write the failing tests** (compact, per §56) in `tests/execution/test_execution_api.py`: stage aggregation reflects real Run/DomainEvent/URLResource/Record facts; timeline ordering stable on `(occurred_at, id)`; category filter (`error`, `tool_upgrade`, `pause_resume`, …) returns only matching events; DTO carries task/run/node refs; a secret-looking payload key (`api_key`, `cookie`, `authorization`, `password`) is redacted (never in response); cross-user task → 404; `after_id` pagination returns next cursor.
- [ ] **Step 2: Run the tests to verify they fail** — `python -m pytest tests/execution -q` → FAIL (missing `app.execution`).
- [ ] **Step 3: Implement `app/execution/contracts.py`** — strict Pydantic DTOs above. `TimelineEvent` has no free-form payload field.
- [ ] **Step 4: Implement `app/execution/repository.py`** — `ExecutionRepository(db)`: `run_for_task`, `url_stats` (URLResource status counts), `record_counts`, `task_events(user_id, task_id, after_id, limit)` (mirror `query_task_events` ownership + record-event join; order by `(occurred_at, id)`), `event_count(user_id, task_id, category)`.
- [ ] **Step 5: Implement `app/execution/service.py`** — `ExecutionService`: `assemble_overview(...)` (stages from event_type mapping to `StageKey`; stage state derived from Run state + stage event presence + record/url counts; explicit empty state when no events), `timeline(...)` (map event → `TimelineEvent`, classify category, redact via allowlist + secret-pattern sweep, stable sort, cursor by last id), `_classify(event_type, payload) → category` (error: `task.fail`, `fetch.failed`, `extraction.failed`, `node.blocked_high_risk`, payload status failed; retry: payload attempt/retry_count>0; tool_upgrade: `fetch.escalated`, `fetch.strategy_selected`, `extraction.llm_fallback_used`, `rule_promoted`, `discovery.*`; plan_change: `task.plan_generated`, `task.plan_replanned`, `approval.*`; model_call: payload with model/token info incl. `extraction.*`; pause_resume: `task.pause/mark_paused/resume/cancel/mark_cancelled`). `trace_ref` reads `payload.get("trace_id")` only.
- [ ] **Step 6: Implement `app/api/routes/execution.py`** — owner-safe `_get_task`, two GET routes, register in router.
- [ ] **Step 7: Run the tests to verify they pass** — `python -m pytest tests/execution -q` → PASS.
- [ ] **Step 8: Commit** — `git add backend/app/execution backend/app/api/routes/execution.py backend/app/api/router.py backend/tests/execution && git commit -m "feat(execution): add stage summary and redacted timeline queries"`

---

### Task 4: Plan DAG + Node Detail backend

**Files:**
- Create: (extend) `backend/app/execution/contracts.py`, `backend/app/execution/repository.py`, `backend/app/execution/service.py`
- Modify: `backend/app/api/routes/execution.py`
- Test: `backend/tests/execution/test_dag_api.py`

**Interfaces:**
- Consumes: frozen `PlanVersion.payload.graph` (nodes/edges from `app/plan/schemas`), `app/plan/nodes.NodeRegistry` (resource class / definition version), `Run`, `DomainEvent`, `URLResource`/`PageSnapshot` for technical stats.
- Produces:
  - `app.execution.contracts.DagNodeDto` — `{node_id, node_type, definition_version, resource_class, depends_on, optional, fail_policy, stage, parameters_summary: dict, execution:{event_count, last_status, last_error, tool, url_fetched_count}}`
  - `app.execution.contracts.DagView` — `{task_id, plan_version, spec_version, validation_status, stage_status: dict[str,str], nodes:[DagNodeDto], edges:[{from_node_id,to_node_id}]}`
  - `app.execution.contracts.NodeDetailDto` — `{node_id, node_type, definition_version, resource_class, depends_on, optional, fail_policy, plan_version, run:{run_id,state}|null, execution:{event_count,last_status,last_error,attempt_count,duration_ms,tool,model,tokens_in,tokens_out,evidence_refs,trace_ref}, parameters_summary: dict}` (redacted; no big payloads)
  - Routes: `GET /api/tasks/{task_id}/execution/dag` → `DagView`; `GET /api/tasks/{task_id}/execution/nodes/{node_id}` → `NodeDetailDto`

- [ ] **Step 1: Write the failing tests** (per §57) in `tests/execution/test_dag_api.py`: frozen PlanVersion → correct DAG DTO (nodes/edges round-trip, stage mapping, resource class present); node detail shows status/version/technical stats; sensitive-looking payload keys never returned; missing node_id → 404; cross-user → 404.
- [ ] **Step 2: Run the tests to verify they fail**.
- [ ] **Step 3: Add DAG DTOs** to `app/execution/contracts.py` (strict, `parameters_summary` allowlist only — no full `parameters` dict with secrets).
- [ ] **Step 4: Add `dag` methods to `repository.py`** — `latest_frozen_plan`, `node_run_events(node_id)` (scan task events in Python for `payload.node_id == node_id`; bounded task event set), `url_fetch_count`.
- [ ] **Step 5: Add `dag`/`node_detail` to `service.py`** — build `DagView` from frozen graph + stage status map (from Task 3 stage logic); compute per-node `execution` from node-scoped events + URL counts; `NodeDetailDto` redacts and only exposes the allowlist. Read-only: no mutation, no retry surface.
- [ ] **Step 6: Add the two routes** to `app/api/routes/execution.py` (owner-safe).
- [ ] **Step 7: Run the tests to verify they pass**.
- [ ] **Step 8: Commit** — `git commit -am "feat(execution): add read-only plan dag and node detail"`

---

### Task 5: Evidence Query + secure content access backend

**Files:**
- Create: `backend/app/evidence/contracts.py`, `backend/app/evidence/repository.py`, `backend/app/evidence/service.py`, `backend/app/api/routes/evidence.py`
- Modify: `backend/app/api/router.py`
- Test: `backend/tests/evidence/test_evidence_api.py`

**Interfaces:**
- Consumes: `PageSnapshot` + `FieldEvidence` (owner+task scoped), `ObjectStorage` (`app.infra.deps.storage()`), `Record` for record context, `app.crawling.snapshot` content-addressable refs.
- Produces:
  - `app.evidence.contracts.EvidenceView` — `{evidence_id (snapshot_id), task_id, source_url, fetched_at, snapshot_version, tool, tool_version, mime_type, http_status, content_length, display_mode: Literal['snapshot','text','raw'], summary: str|None, field_evidence:[{record_id, field_name, value, raw_snippet, source_locator, extract_method, extractor_version, confidence}], has_content: bool, download_url: str}`
  - Routes: `GET /api/tasks/{task_id}/evidence/{snapshot_id}` → `EvidenceView`; `GET /api/tasks/{task_id}/evidence/{snapshot_id}/content?download=` → `StreamingResponse` (owner-safe; reads ObjectStorage, never re-fetches source URL)
  - `app.evidence.service.EvidenceService` — `get(user_id, task_id, snapshot_id)`, `content(user_id, task_id, snapshot_id)` returns `(bytes, content_type)`

- [ ] **Step 1: Write the failing tests** (per §58) in `tests/evidence/test_evidence_api.py`: owner access PASS; cross-user → 404 (no metadata leak); stored snapshot returned (source URL in fixture; assert live fetch transport call count == 0 by monkeypatching `app.crawling.http_fetch`/`fetch` entry to raise if called); display fallback metadata (image→snapshot, text snippet→text, else raw); content endpoint owner-safe and streams stored bytes; secret-looking metadata redacted.
- [ ] **Step 2: Run the tests to verify they fail**.
- [ ] **Step 3: Implement `app/evidence/contracts.py`** (strict DTOs above; no secret fields).
- [ ] **Step 4: Implement `app/evidence/repository.py`** — `EvidenceRepository(db)`: `snapshot_owned(user_id, snapshot_id)`, `snapshot_for_task(user_id, task_id, snapshot_id)` (404 if task mismatch), `field_evidence_for_snapshot(user_id, snapshot_id)`, `record_context(user_id, record_ids)`.
- [ ] **Step 5: Implement `app/evidence/service.py`** — compute `display_mode` (mime image/* → `snapshot`; any non-empty `raw_snippet`/normalized text → `text`; else `raw`), assemble `EvidenceView`, and `content()` loads bytes from `ObjectStorage.get(snapshot.storage_ref)` after ownership check (defense: refuse if `storage_ref` empty).
- [ ] **Step 6: Implement `app/api/routes/evidence.py`** — two GET routes (the content route uses `StreamingResponse` with stored `content_type`; `download=1` sets `Content-Disposition: attachment`). Register in router. **Ownership check runs before any object lookup.**
- [ ] **Step 7: Run the tests to verify they pass** — including the `live_fetch_count == 0` assertion.
- [ ] **Step 8: Commit** — `git add backend/app/evidence backend/app/api/routes/evidence.py backend/app/api/router.py backend/tests/evidence && git commit -m "feat(evidence): add owner-safe evidence query and content access"`

---

### Task 6: Evidence Viewer + Quick Evidence + locator (frontend)

**Files:**
- Create: `frontend/src/features/evidence/types.ts`, `frontend/src/features/evidence/evidence.api.ts`, `frontend/src/features/evidence/useEvidence.ts`
- Modify: `frontend/src/features/tasks/TaskEvidenceView.vue`, `frontend/src/app/overlay/drawers/EvidenceQuickDrawer.vue`, `frontend/src/app/overlay/drawers/RecordDrawer.vue` (ensure Quick Evidence passes `snapshotId`)
- Test: `frontend/src/features/evidence/evidence.api.test.ts`, `frontend/src/features/tasks/TaskEvidenceView.test.ts`, `frontend/src/app/overlay/drawers/EvidenceQuickDrawer.test.ts`

**Interfaces:**
- Consumes: `EvidenceView` DTO + `download_url` from Task 5.
- Produces:
  - `evidence.api.ts::getEvidence(taskId, snapshotId)`, `fetchEvidenceContent(url) → {blob, contentType, mode}` (raw `fetch`, JSON-free — reads content bytes; never hits `source_url`)
  - `useEvidence.ts::useEvidence(taskId, snapshotId)` — `{view, loading, error, content: {mode, text, imageUrl} | null, locate, search, reload}`
  - `TaskEvidenceView.vue` — display priority: image snapshot → `<img>` from blob; else text mode → `<pre>` summary/body; else raw mode → sandboxed iframe (`sandbox=""` + injected strict CSP meta, no `v-html`). Renders field evidence table + source URL (opens original source in new tab, label "打开原始来源"), "定位到页面位置" via `source_locator`, search within loaded content, copy. Read-only everywhere.
  - `EvidenceQuickDrawer.vue` — compact field value + snippet + source URL + method + confidence + "完整查看" → `router.push('/tasks/:taskId/evidence/:snapshotId')`. Never renders full viewer.

- [ ] **Step 1: Write the failing tests** (per §61): snapshot-image mode preferred; text fallback; raw fallback; locator highlight success + graceful fallback ("无法定位" but snippet still shown); read-only (no edit controls); full route renders; Quick Drawer shows summary and routes to full evidence; component never uses `v-html` (assert no `<v-html`/`innerHTML` in template).
- [ ] **Step 2: Run the tests to verify they fail**.
- [ ] **Step 3: Implement `types.ts` / `evidence.api.ts`** (content fetch via raw `fetch` with `credentials: 'same-origin'`; map blob → image URL or text).
- [ ] **Step 4: Implement `useEvidence.ts`** — idle/loading/success/empty/error; expose `locate()` that parses `source_locator` (CSS selector or XPath or `#id`) against the loaded snapshot DOM and scrolls + highlights via a wrapper element; graceful fallback.
- [ ] **Step 5: Implement `TaskEvidenceView.vue`** — the sandboxed raw viewer builds `srcdoc` from raw HTML with CSP meta `default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'` inside `<iframe sandbox="">` (no `allow-scripts`, no `allow-same-origin`). Never `v-html`.
- [ ] **Step 6: Implement `EvidenceQuickDrawer.vue`** and confirm `RecordDrawer.vue` Quick Evidence wiring.
- [ ] **Step 7: Run the tests to verify they pass**.
- [ ] **Step 8: Commit** — `git add frontend/src/features/evidence frontend/src/features/tasks/TaskEvidenceView.vue frontend/src/app/overlay/drawers/EvidenceQuickDrawer.vue && git commit -m "feat(web): implement evidence viewer with snapshot priority and locator"`

---

### Task 7: Execution views frontend + Node Detail Drawer

**Files:**
- Create: `frontend/src/features/execution/types.ts`, `frontend/src/features/execution/execution.api.ts`, `frontend/src/features/execution/useExecution.ts`
- Modify: `frontend/src/features/tasks/TaskExecutionView.vue`, `frontend/src/app/overlay/drawers/NodeDetailDrawer.vue`
- Test: `frontend/src/features/execution/execution.api.test.ts`, `frontend/src/features/tasks/TaskExecutionView.test.ts`, `frontend/src/app/overlay/drawers/NodeDetailDrawer.test.ts`

**Interfaces:**
- Consumes: `ExecutionView`, `TimelinePage`, `DagView`, `NodeDetailDto` from Tasks 3–4.
- Produces:
  - `execution.api.ts::getExecution(taskId)`, `getTimeline(taskId, {category, afterId, limit})`, `getDag(taskId)`, `getNodeDetail(taskId, nodeId)`
  - `useExecution.ts::useExecution(taskId)` — overview + timeline filter state + pagination + DAG toggle (`stage | dag`), `{view, timeline, filter, loadMore, viewMode, loadDag, dag, selectedNode, openNode}`
  - `TaskExecutionView.vue` — default "阶段 + 时间线" view (stage cards + timeline list with filters: 全部/错误/重试/工具升级/计划调整/模型调用/暂停恢复; pagination), toggle to read-only Plan DAG (renders nodes/edges, status colors by stage, click node → `openDrawer('NODE_DETAIL', {taskId, nodeId})`). No money UI — tokens/duration/sizes only.
  - `NodeDetailDrawer.vue` — real content: node identity, version, resource class, run, attempts/event count, technical stats (duration/token/model/tool), recent error, refs; read-only, no retry button.

- [ ] **Step 1: Write the failing tests** (per §60): stage view renders from API; timeline filter toggles and refetches; DAG toggle renders nodes/edges; node click opens NODE_DETAIL drawer; drawer renders technical stats; no `¥`/`$`/`费用`/`预算` strings in templates.
- [ ] **Step 2: Run the tests to verify they fail**.
- [ ] **Step 3: Implement `types.ts` / `execution.api.ts` / `useExecution.ts`**.
- [ ] **Step 4: Implement `TaskExecutionView.vue`** with stage cards + timeline + filters + DAG toggle (lightweight read-only layout; no heavy editor framework).
- [ ] **Step 5: Implement `NodeDetailDrawer.vue`** wired via `drawer.store` NODE_DETAIL payload `{taskId, nodeId}`.
- [ ] **Step 6: Run the tests to verify they pass**.
- [ ] **Step 7: Commit** — `git add frontend/src/features/execution frontend/src/features/tasks/TaskExecutionView.vue frontend/src/app/overlay/drawers/NodeDetailDrawer.vue && git commit -m "feat(web): render execution stages, timeline and node detail"`

---

### Task 8: Scoped verification, docs, M-14 execution record

**Files:**
- Create: `docs/implementation/M-14-execution.md`
- Test: run the scoped suites + lint/build gates

- [ ] **Step 1: Backend scoped suites** — `python -m pytest tests/quality tests/execution tests/evidence tests/review/test_records_source_type.py -q` → all PASS. (Do NOT run `pytest tests/`.)
- [ ] **Step 2: Lint/type** — `python -m ruff check app/quality app/execution app/evidence app/api/routes/quality.py app/api/routes/execution.py app/api/routes/evidence.py app/review/repository.py`; `python -m mypy app/quality app/execution app/evidence app/api/routes/quality.py app/api/routes/execution.py app/api/routes/evidence.py app/review/repository.py` → PASS. `python -m alembic heads` → still `0011 (head)` (NO MIGRATION).
- [ ] **Step 3: App import** — `python -c "from app.main import create_app; create_app()"` → PASS.
- [ ] **Step 4: Frontend scoped** — `cd frontend && npm run test:unit -- quality execution evidence` → PASS; `npm run build` (vue-tsc + vite) → PASS.
- [ ] **Step 5: Write `docs/implementation/M-14-execution.md`** (status IN_PROGRESS → DONE, M13 baseline SHA, per-slice checklist, verification commands + results, migration NONE, staging acceptance plan, commits list). 
- [ ] **Step 6: Commit** — `git add docs/implementation/M-14-execution.md && git commit -m "docs(observability): record M-14 execution"`
- [ ] **Step 7: Local DONE gate review** — confirm every §71 check is PASS and `git status` shows clean tracked tree.

---

## Self-Review

**1. Spec coverage:** All three slices covered — Quality (Tasks 1–2), Execution overview/timeline (Task 3), DAG/Node Detail (Task 4), Evidence query/content (Task 5), Evidence viewer/quick/locator (Task 6), Execution views/Node Drawer (Task 7), verification/docs (Task 8). Cross-cutting constraints (redaction, ownership, no money, no live fetch, 13-page boundary, no migration, M-15/M-17 boundaries, DEFERRED debt untouched) are enforced in the owning tasks and verified in Task 8. Deep-link contract is M-13 + real `REVIEW_TYPES`; the `source_type` resolution fix (Task 1 step 7) makes it accurate for real records without a new param.

**2. Placeholder scan:** No TBD/TODO. Every task has real file paths, DTO field lists, route signatures, test expectations, and commit messages.

**3. Type consistency:** `QualityDrilldown` ↔ `buildDataLink` ↔ TaskDataView query parser share the same field names (`status/review_type/source_type/extract_method/min_confidence`). `TimelineEvent`/`StageKey`/`DagNodeDto`/`NodeDetailDto`/`EvidenceView` names are used identically in backend contracts and frontend `types.ts`. `snapshot_id` is the `:evidenceId` route param throughout (matches existing RecordDrawer wiring). Route prefixes: `/tasks/{task_id}/quality`, `/tasks/{task_id}/execution[...]`, `/tasks/{task_id}/evidence/{snapshot_id}`.

---

## PROJECT SELF-APPROVAL

CHECK 1 M-13 Precondition — M-13 = DONE (baseline `10e74c7`), migration `0011` head, tracked tree clean: **PASS**
CHECK 2 Quality — `QualityMetrics`/`QualitySnapshot` + DB facts are the source; frontend never computes business facts: **PASS**
CHECK 3 Quality Deep Link — reuses M-13 contract + real `REVIEW_TYPES`; `source_type` resolved via URLResource: **PASS**
CHECK 4 Quality Boundary — no Record edit/review on Quality page: **PASS**
CHECK 5 Execution — user timeline from `DomainEvent`/`Run`/`URLResource`/`Record`, not raw server logs: **PASS**
CHECK 6 Timeline — serializes task/run/node/attempt/trace refs: **PASS**
CHECK 7 DAG — reads frozen `PlanVersion`, read-only: **PASS**
CHECK 8 Node Detail — safe summaries + refs only, no large payloads: **PASS**
CHECK 9 No Billing — token/duration/request/sizes only, no money UI: **PASS**
CHECK 10 Evidence — historical snapshot; no live fetch: **PASS**
CHECK 11 Evidence Immutability — snapshot/FieldEvidence/raw read-only: **PASS**
CHECK 12 Locator — based on stored snapshot, not current website: **PASS**
CHECK 13 HTML Security — sandboxed iframe + CSP, no `v-html`: **PASS**
CHECK 14 Redaction — secrets never in API responses (backend redacts): **PASS**
CHECK 15 Ownership — Quality/Execution/Evidence all owner-safe 404: **PASS**
CHECK 16 Signed Download — ownership check before content access; API-mediated (MinIO private-network): **PASS**
CHECK 17 13 Page Boundary — no new pages: **PASS**
CHECK 18 M-15 Boundary — no CSV/artifact/delete/restore/retention: **PASS**
CHECK 19 M-17 Boundary — no Observability platform: **PASS**
CHECK 20 Deferred Dynamic Debt — DEFERRED-DYNAMIC-E2E-01 untouched: **PASS**
CHECK 21 A-Lite — scoped tests only: **PASS**
CHECK 22 Deploy Boundary — M-14 light Staging acceptance only, not Gate-4: **PASS**
CHECK 23 Git — no push/merge/tag: **PASS**

## PLAN SELF-APPROVAL

PLAN SELF-APPROVAL: PASS

- M-13 precondition: PASS
- implementation plan M-14: PASS
- quality metrics source: PASS
- quality drilldown: PASS
- quality/data boundary: PASS
- execution timeline: PASS
- stage aggregation: PASS
- timeline redaction: PASS
- Plan DAG read-only: PASS
- Node Detail boundary: PASS
- no billing UI: PASS
- evidence historical snapshot: PASS
- no live refetch: PASS
- evidence immutability: PASS
- locator/highlight: PASS
- HTML viewer security: PASS
- signed download ownership: PASS
- user isolation: PASS
- 13-page boundary: PASS
- M-15 boundary: PASS
- M-17 boundary: PASS
- deferred dynamic debt untouched: PASS
- A-Lite testing: PASS
- fast-development-test policy: PASS
- deployment boundary: PASS
- git standards: PASS
- placeholder scan: PASS
- type/interface consistency: PASS
