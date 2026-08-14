# Provider Model Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace free-text model names with provider-supplied model catalogs and close the DeepSeek false `NETWORK_ERROR` incident.

**Architecture:** Add one typed catalog contract to all Model Provider adapters, expose it through an owner-scoped service/API command, and make saved connection tests validate catalog membership. The Vue drawer consumes the catalog and only saves returned model IDs; inference keeps using the existing real ModelInferenceClient path.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, httpx transport abstraction, Vue 3, TypeScript, Vitest, pytest.

## Global Constraints

- No API key, Authorization header, cookie, database password or raw provider error body may be logged or returned.
- Model and Search Provider paths remain separate; Tavily is not called during Goal Understanding.
- No mock, sample or hardcoded model list may enter Production runtime.
- No direct server source edit, `docker build`, `pip install`, `npm build`, or container hot patch is allowed.
- Every behavior change follows RED → GREEN → REFACTOR and uses the smallest relevant test scope.

---

### Task 1: Typed provider model catalogs

**Files:**
- Modify: `backend/app/providers/protocol.py`
- Modify: `backend/app/providers/adapters/openai_compatible.py`
- Modify: `backend/app/providers/adapters/anthropic.py`
- Modify: `backend/app/providers/adapters/gemini.py`
- Modify: `backend/app/providers/adapters/ollama.py`
- Test: `backend/tests/providers/test_model_catalog.py`
- Test: `backend/tests/providers/test_error_mapping.py`

**Interfaces:**
- Produces: `ModelCatalogResult(status, models, resolved_base_url, error_code, message, latency_ms)`.
- Produces: `ModelProvider.list_models(api_key, base_url) -> ModelCatalogResult`.
- Consumes: existing `HttpClient.request` and Registry-resolved Base URL.

- [ ] **Step 1: Write failing adapter tests** for OpenAI-compatible `data[].id`, Anthropic `data[].id`, Gemini `baseModelId` with `generateContent`, Ollama `models[].model`, malformed success payload, authentication, rate-limit and network failure.
- [ ] **Step 2: Run `backend/.venv/Scripts/python.exe -m pytest tests/providers/test_model_catalog.py tests/providers/test_error_mapping.py -q` and verify failures are caused by the absent catalog contract.**
- [ ] **Step 3: Implement the typed result and minimal adapter parsers.** Preserve provider order while removing blank and duplicate IDs.
- [ ] **Step 4: Re-run the scoped tests and verify PASS.**

### Task 2: Owner-scoped catalog API

**Files:**
- Modify: `backend/app/providers/schemas.py`
- Modify: `backend/app/providers/service.py`
- Modify: `backend/app/api/routes/providers.py`
- Test: `backend/tests/providers/test_model_catalog_service.py`
- Test: `backend/tests/providers/test_providers_api.py`

**Interfaces:**
- Consumes: `ModelProvider.list_models` from Task 1.
- Produces: `POST /api/providers/models/catalog` with `{provider_type, base_url?, api_key?, config_id?}`.
- Produces: `ModelCatalogResultDto` containing only safe metadata and `models: list[str]`.

- [ ] **Step 1: Write failing service/API tests** proving transient-key discovery is not persisted, existing configs use their frozen credential version, cross-user config IDs return safe 404, custom providers require a valid Base URL, and secrets never appear in responses.
- [ ] **Step 2: Run the exact new test nodes and verify RED.**
- [ ] **Step 3: Implement schema, service and thin route.** Reject simultaneous mismatched credential sources and inject the existing service transport for deterministic tests.
- [ ] **Step 4: Re-run the exact test nodes and verify PASS.**

### Task 3: Connection/inference error parity

**Files:**
- Modify: `backend/app/providers/adapters/openai_compatible.py`
- Modify: `backend/app/providers/adapters/anthropic.py`
- Modify: `backend/app/providers/adapters/gemini.py`
- Modify: `backend/app/providers/adapters/ollama.py`
- Modify: `backend/app/providers/inference.py`
- Modify: `backend/app/providers/service.py`
- Test: `backend/tests/providers/test_error_mapping.py`
- Test: `backend/tests/providers/test_inference.py`
- Test: `backend/tests/providers/test_model_catalog_service.py`
- Test: `backend/tests/api/test_understand.py`

**Interfaces:**
- Consumes: catalog IDs from Task 1.
- Produces: saved connection status `MODEL_NOT_FOUND` when the configured model is absent.
- Produces: HTTP 400 inference error `ProviderInferenceError`; true transport exceptions remain `ProviderNetworkError`.

- [ ] **Step 1: Write failing regression tests** for invalid DeepSeek model membership and HTTP 400 classification, plus preservation of the single user message and single error assistant message.
- [ ] **Step 2: Run the regression nodes and verify they fail with the historical behavior.**
- [ ] **Step 3: Reuse catalog membership in saved connection tests and minimally correct HTTP 400 inference mapping.**
- [ ] **Step 4: Run provider, inference, Goal Understanding, credential and redaction scopes and verify PASS.**

### Task 4: Vue model-selection workflow

**Files:**
- Modify: `frontend/src/features/providers/providers.api.ts`
- Modify: `frontend/src/features/providers/ModelConfigDrawer.vue`
- Modify: `frontend/src/features/providers/providers.test.ts`

**Interfaces:**
- Consumes: `POST /providers/models/catalog`.
- Produces: disabled/loading/ready/empty/error catalog states and a model `<select>` whose options are provider-returned IDs.

- [ ] **Step 1: Write failing Vitest cases** proving no free-text model input exists, provider/key changes load a catalog, editing uses `config_id`, legacy invalid values cannot save, first valid ID is selected only when selection is empty, retry works, and raw secrets never render.
- [ ] **Step 2: Run `npm run test:unit -- src/features/providers/providers.test.ts` and verify RED.**
- [ ] **Step 3: Implement the minimal API DTO and drawer state machine.** Use a short debounce, cancel stale responses by request sequence, retain a still-valid selection, and require an explicit valid option before save.
- [ ] **Step 4: Re-run the focused Vitest file, type-check, lint check and build; verify PASS.**
- [ ] **Step 5: Browser-verify `/models` at desktop and mobile widths, including Provider change, loading, selection, save and retry states.**

### Task 5: Decision record, incident audit and release evidence

**Files:**
- Modify: `agent-business-logic-log.md`
- Create: `docs/audits/production-reality-audit-2026-08-14.md`
- Create: `docs/superpowers/plans/2026-08-14-production-reality-closure.md`
- Modify or create: current release record under `docs/implementation/` as required by the repository's existing naming convention.

**Interfaces:**
- Produces: D-075 for provider-supplied model selection.
- Produces: incident root-cause evidence, M-01–M-18 Reality Matrix, P0/P1 findings and ordered closure modules.
- Produces: immutable release identity and Staging/Production smoke evidence.

- [ ] **Step 1: Add D-075 without rewriting historical decisions and link the superseded free-text behavior.**
- [ ] **Step 2: Complete the static/runtime Reality Audit and evidence-backed closure plan.**
- [ ] **Step 3: Run backend provider/inference/Goal Understanding/task/credential scopes, ruff and mypy; run frontend focused tests, type-check, lint and build.**
- [ ] **Step 4: Commit with an English Conventional Commit title and Chinese body, push the feature branch, open a PR, wait for required CI and merge through the repository policy.**
- [ ] **Step 5: Build/publish immutable GHCR images through the official workflow, deploy the same digests to Staging, and run a real DeepSeek Goal Understanding smoke.**
- [ ] **Step 6: Create the patch release, deploy the same immutable digests to Production, update the owned DeepSeek config through the normal versioned application service to `deepseek-v4-flash`, and verify `/models`, connection test, task creation, typed Goal Understanding, preserved message, secret-free logs and no new 5xx.**

## Self-review

- Spec coverage: provider catalogs, UI selection, connection parity, error mapping, security, incident regression, audit and immutable release are each assigned to a task.
- Placeholder scan: the plan contains no deferred implementation markers.
- Type consistency: `ModelCatalogResult` is the sole adapter/service result and `ModelCatalogResultDto` is the sole HTTP response consumed by Vue.
