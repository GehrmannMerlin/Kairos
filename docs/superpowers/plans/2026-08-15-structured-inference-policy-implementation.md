# Structured Inference Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make plan generation use an explicit, capability-driven inference policy so DeepSeek structured plans complete within the agreed budget, failures retain actionable semantics, repair is bounded and contract-aware, and persisted plans/workflow starts recover safely across ambiguous client or infrastructure failures.

**Architecture:** Introduce an inference intent and provider-capability registry, resolve those inputs into a pure immutable request policy, and require every agent to obtain its inference client through one shared factory. Keep transport, retry, and error translation below that policy boundary; keep plan validation, repair, persistence, workflow start, and browser reconciliation as separate lifecycle stages with explicit deadlines and durable state.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLAlchemy, httpx, Temporal, pytest, Vue 3, TypeScript, Vitest, Playwright, nginx, Docker Compose, GitHub Actions, GHCR.

## Global implementation constraints

- Use exactly these inference intents: `PLAN_STRUCTURED`, `GOAL_EXTRACTION`, and `CUSTOM_AGENT`.
- Only DeepSeek plus `PLAN_STRUCTURED` disables thinking, requires JSON-object output, and sets `max_tokens=4096`. Goal extraction behavior must remain unchanged, and custom agents must not inherit plan-specific flags.
- Use four nested timeout layers: 45 seconds for one logical provider operation including its allowed retry and backoff, 105 seconds for the complete backend plan lifecycle, 120 seconds for nginx upstream read/send, and no frontend request timeout for plan generation.
- A connect timeout may make one retry, for two transport attempts total. A read timeout never retries. HTTP 429 remains bounded by existing retry policy. Authentication and model errors never retry.
- A plan lifecycle may make at most two model calls: initial generation and one repair. The deterministic validator remains authoritative after repair.
- Persist a generated plan exactly once. A workflow-start failure must not regenerate or create another plan version, and retrying start must not create another active run.
- Logs and API errors may contain provider type, model, intent, timeout phase, attempt, elapsed time, status, plan version, validator issue codes, and request identifiers. They must not contain credentials, authorization headers, prompts, model responses, full plan payloads, or environment secrets.
- Preserve the seven user-owned untracked scripts in the original workspace. Perform all implementation in `D:\Develop\Vue\Kairos\.worktrees\structured-plan-inference` on `fix/structured-plan-inference`.
- Follow `agent-git-standards.md` for commits, pull request, and merge. Follow `agent-production-deployment-standards.md` for Staging and Production. Do not deploy a local build to Production; Production must pull the exact GHCR images built from the merged commit.
- Do not mark a task complete until its red test was observed, the focused test is green, and the task verification command is green.

## Planned file map

### New backend files

- `backend/app/providers/inference_policy.py`: intents, capabilities, immutable request policy, pure resolver.
- `backend/app/providers/inference_factory.py`: settings-aware shared client construction.
- `backend/app/providers/inference_telemetry.py`: safe allowlisted structured events.
- `backend/app/agents/plan_repair.py`: contract-aware repair context builder.
- `backend/tests/providers/test_inference_policy.py`
- `backend/tests/providers/test_inference_factory.py`
- `backend/tests/providers/test_inference_telemetry.py`
- `backend/tests/agents/test_plan_repair.py`

### Modified backend files

- `backend/app/providers/protocol.py`
- `backend/app/providers/registry.py`
- `backend/app/providers/inference.py`
- `backend/app/providers/transport.py`
- `backend/app/providers/errors.py`
- `backend/app/reliability/errors.py`
- `backend/app/reliability/retry.py`
- `backend/app/reliability/provider_limit.py`
- `backend/app/agents/plan_generator.py`
- `backend/app/agents/plan_service.py`
- `backend/app/agents/goal_understanding.py`
- `backend/app/api/routes/plans.py`
- `backend/app/plan/service.py`
- `backend/app/plan/validator.py`
- `backend/app/workflows/starter.py`
- `backend/app/domain/repository.py`
- `backend/app/config.py`
- Focused tests under `backend/tests/providers`, `backend/tests/reliability`, `backend/tests/agents`, `backend/tests/api`, `backend/tests/plan`, `backend/tests/domain`, and `backend/tests/integration`.

### Modified frontend files

- `frontend/src/features/tasks/plans.api.ts`
- `frontend/src/app/error/apiErrorMapper.ts`
- `frontend/src/features/tasks/TaskChatView.vue`
- Associated Vitest files for API contracts, error mapping, and task chat reconciliation.

### Modified delivery files

- `infra/reverse-proxy/zz-kairos-staging-tls.conf`
- `infra/reverse-proxy/zz-kairos-production-tls.conf`
- `infra/compose/compose.staging.yml`
- `infra/compose/compose.production.yml`
- `.github/workflows/ci-build-push.yml`
- New focused tests or static assertions under `backend/tests/infra` or the repository's existing infrastructure-test location.
- `infra/scripts/structured-plan-staging-acceptance.py`
- `frontend/e2e/structured-plan-production.spec.ts`

## Task 1: Define inference intents, provider capabilities, and a pure policy resolver

**Files:**

- Create: `backend/app/providers/inference_policy.py`
- Create: `backend/tests/providers/test_inference_policy.py`
- Modify: `backend/app/providers/protocol.py`
- Modify: `backend/app/providers/registry.py`

- [ ] **Step 1: Write the failing policy matrix tests**

Cover exact equality, not loose membership:

```python
def test_deepseek_structured_plan_policy_disables_thinking() -> None:
    policy = resolve_inference_policy(
        intent=InferenceIntent.PLAN_STRUCTURED,
        capability=DEEPSEEK_CAPABILITY,
    )

    assert policy == InferenceRequestPolicy(
        response_format={"type": "json_object"},
        thinking={"type": "disabled"},
        max_tokens=4096,
    )


def test_deepseek_goal_extraction_does_not_inherit_plan_flags() -> None:
    policy = resolve_inference_policy(
        intent=InferenceIntent.GOAL_EXTRACTION,
        capability=DEEPSEEK_CAPABILITY,
    )

    assert policy.thinking is None
    assert policy.max_tokens is None


def test_custom_agent_does_not_inherit_plan_flags() -> None:
    policy = resolve_inference_policy(
        intent=InferenceIntent.CUSTOM_AGENT,
        capability=DEEPSEEK_CAPABILITY,
    )

    assert policy.thinking is None
    assert policy.max_tokens is None
```

Also cover non-DeepSeek OpenAI-compatible providers, the immutability of the returned dataclass, and registry declarations for every provider type.

- [ ] **Step 2: Run the focused tests and observe the expected import or assertion failure**

Run:

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest tests/providers/test_inference_policy.py -q
```

Expected: FAIL because the intent, capability declaration, and resolver do not yet exist.

- [ ] **Step 3: Implement the smallest pure policy model**

Use frozen dataclasses and enums. Keep the resolver independent of settings, credentials, registry lookups, clocks, and networking:

```python
class InferenceIntent(StrEnum):
    PLAN_STRUCTURED = "PLAN_STRUCTURED"
    GOAL_EXTRACTION = "GOAL_EXTRACTION"
    CUSTOM_AGENT = "CUSTOM_AGENT"


@dataclass(frozen=True)
class ProviderCapability:
    supports_json_object: bool
    supports_thinking_control: bool
    plan_thinking_mode: Literal["disabled"] | None = None


@dataclass(frozen=True)
class InferenceRequestPolicy:
    response_format: dict[str, str] | None = None
    thinking: dict[str, str] | None = None
    max_tokens: int | None = None
```

Add a capability field to `ProviderDefinition`. Declare DeepSeek's thinking control explicitly in the registry; declare other providers without that capability. Resolve the DeepSeek plan exception only when both intent and capability match.

- [ ] **Step 4: Add the output-token budget assertion**

Create a representative complete 10-node Plan JSON fixture matching current schema. Assert its conservative token estimate is at most 2048 tokens using `ceil(len(serialized_utf8) / 3)`, and document in the test that `4096` is a two-times output allowance rather than an arbitrary constant.

- [ ] **Step 5: Run focused and adjacent provider tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/providers/test_inference_policy.py tests/providers/test_registry.py tests/providers/test_inference.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit the policy seam**

```powershell
git add backend/app/providers/inference_policy.py backend/app/providers/protocol.py backend/app/providers/registry.py backend/tests/providers/test_inference_policy.py
git commit -m "feat(provider): add intent capability policy" -m "引入显式推理意图与供应商能力声明。`n`n通过纯策略解析器限定 DeepSeek 结构化计划的 thinking 与输出预算。" -m "Modules: provider-registry, inference-policy"
```

## Task 2: Route all agents through one settings-aware inference factory

**Files:**

- Create: `backend/app/providers/inference_factory.py`
- Create: `backend/tests/providers/test_inference_factory.py`
- Modify: `backend/app/providers/inference.py`
- Modify: `backend/app/agents/plan_generator.py`
- Modify: `backend/app/agents/plan_service.py`
- Modify: `backend/app/agents/goal_understanding.py`
- Modify: agent fakes and fixtures under `backend/tests/agents` and `backend/tests/plan`

- [ ] **Step 1: Write failing request-body and factory tests**

Use the existing fake transport to capture the outgoing body. Assert:

```python
assert request.json["response_format"] == {"type": "json_object"}
assert request.json["thinking"] == {"type": "disabled"}
assert request.json["max_tokens"] == 4096
```

for DeepSeek `PLAN_STRUCTURED`, and assert all plan-only keys are absent for `GOAL_EXTRACTION` and `CUSTOM_AGENT`. Add a test proving a custom `Settings` object supplied to `PlanService` reaches the factory instead of silently falling back to global defaults.

- [ ] **Step 2: Run the focused tests and observe failure**

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest tests/providers/test_inference_factory.py tests/providers/test_inference.py tests/plan/test_plan_generator.py tests/agents/test_goal_understanding.py -q
```

Expected: FAIL because `ModelInferenceClient` has no intent and `PlanService` constructs its own default client.

- [ ] **Step 3: Implement the shared factory**

Expose one constructor seam with dependency-injection overrides for tests:

```python
def build_inference_client(
    *,
    intent: InferenceIntent,
    settings: Settings,
    http: HttpClient | None = None,
    definition_resolver: Callable[[str], ProviderDefinition] = get_model_definition,
) -> ModelInferenceClient:
    ...
```

Require `ModelInferenceClient` to receive an intent. Resolve the provider definition and policy before composing the request body. Only add optional keys when their policy value is not `None`.

- [ ] **Step 4: Assign fixed intent at each agent boundary**

- `PlanGeneratorAgent` always uses `PLAN_STRUCTURED`.
- `GoalExtractorAgent` always uses `GOAL_EXTRACTION`.
- Generic/custom agent creation always uses `CUSTOM_AGENT`.
- `PlanService` accepts a constructed plan generator or constructs it through the shared factory with its own `Settings`; remove direct `ModelInferenceClient()` construction.
- Update fake inference call signatures so tests fail if an agent bypasses the intent contract.

- [ ] **Step 5: Run focused and adjacent tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/providers/test_inference_factory.py tests/providers/test_inference.py tests/plan/test_plan_generator.py tests/plan/test_plan_service.py tests/agents/test_goal_understanding.py -q
```

Expected: PASS, including unchanged Goal extraction request snapshots.

- [ ] **Step 6: Commit the shared construction path**

```powershell
git add backend/app/providers/inference_factory.py backend/app/providers/inference.py backend/app/agents backend/tests/providers/test_inference_factory.py backend/tests/providers/test_inference.py backend/tests/agents
git commit -m "feat(agent): route inference through shared intent factory" -m "统一 Agent 的推理客户端构造入口并显式绑定推理意图。`n`n保留 Goal 行为，避免 Plan 专属参数泄漏到其他调用。" -m "Modules: agents, inference-provider"
```

## Task 3: Preserve timeout phases and enforce the retry matrix

**Files:**

- Modify: `backend/app/providers/errors.py`
- Modify: `backend/app/providers/transport.py`
- Modify: `backend/app/providers/inference.py`
- Modify: `backend/app/reliability/errors.py`
- Modify: `backend/app/reliability/retry.py`
- Modify: `backend/app/reliability/provider_limit.py`
- Modify: `backend/tests/providers/test_error_mapping.py`
- Modify: `backend/tests/providers/test_inference.py`
- Modify: retry matrix tests under `backend/tests/reliability`

- [ ] **Step 1: Write the failing phase-preservation tests**

Cover these exact outcomes:

| Input | Public provider error | Retry count |
|---|---|---:|
| `httpx.ConnectTimeout` | `ProviderTimeoutError(CONNECT)` | 2 total attempts |
| `httpx.ReadTimeout` | `ProviderTimeoutError(READ)` | 1 total attempt |
| logical 45-second expiry | `ProviderTimeoutError(OVERALL)` | no outer retry |
| `httpx.ConnectError` | `ProviderNetworkError` | existing bounded network policy |
| HTTP 401/403 | authentication error | 1 |
| HTTP 404 model endpoint | model error | 1 |
| HTTP 429 | rate-limit error with safe retry metadata | existing bounded policy |
| unexpected `TypeError` | unchanged programming error | 1 and API 500 |

Use an injected clock or zero-delay retry policy so tests do not sleep.

- [ ] **Step 2: Run the focused tests and observe misclassification**

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest tests/providers/test_error_mapping.py tests/providers/test_inference.py tests/reliability -q
```

Expected: FAIL because the current generic exception handler collapses timeout phases into `ProviderNetworkError` and treats them as retryable `NETWORK_TIMEOUT`.

- [ ] **Step 3: Introduce typed timeout semantics**

```python
class TimeoutPhase(StrEnum):
    CONNECT = "connect"
    READ = "read"
    OVERALL = "overall"


class ProviderTimeoutError(ProviderError):
    status_code = 504

    def __init__(self, *, phase: TimeoutPhase) -> None:
        self.phase = phase
        super().__init__(f"provider timeout during {phase.value}")
```

Translate only known `httpx` exceptions at the transport/inference boundary. Remove the catch-all translation; unexpected programming failures must retain traceback and reach the existing internal-error handler.

- [ ] **Step 4: Preserve safe response metadata for rate limits**

Extend `HttpResponse` with a normalized, allowlisted header view sufficient for `Retry-After` and request identifiers. Never expose or log request headers. Parse `Retry-After` defensively and retain the current configured maximum backoff.

- [ ] **Step 5: Encode the retry classifier and logical deadline**

Add separate retry classes for connect and read timeouts. Configure connect timeout for one retry, read timeout for none, and authentication/model errors for none. Wrap the complete provider limiter operation—including semaphore wait, transport attempts, and retry delay—in `asyncio.timeout(settings.provider_request_timeout_seconds)`, default `45`; translate only that expiry to phase `OVERALL`.

- [ ] **Step 6: Run focused and complete retry tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/providers/test_error_mapping.py tests/providers/test_inference.py tests/reliability -q
```

Expected: PASS with exact attempt counts.

- [ ] **Step 7: Commit error and retry behavior**

```powershell
git add backend/app/providers backend/app/reliability backend/tests/providers backend/tests/reliability
git commit -m "fix(provider): classify timeout phases before retry" -m "保留连接、读取与整体超时语义，并按阶段执行有限重试。`n`n未知编程异常不再被错误包装为供应商网络故障。" -m "Modules: provider-transport, reliability"
```

## Task 4: Make plan repair contract-aware and bounded to one extra model call

**Files:**

- Create: `backend/app/agents/plan_repair.py`
- Create: `backend/tests/plan/test_plan_repair.py`
- Modify: `backend/app/agents/plan_generator.py`
- Modify: `backend/app/agents/plan_service.py`
- Modify: `backend/app/plan/validator.py`
- Create: `backend/tests/plan/test_plan_service.py`

- [ ] **Step 1: Write failing repair-context tests**

Construct an invalid first plan that has both an incompatible edge and an invalid parameter. Assert the repair input contains:

- The original graph JSON.
- Every validator issue code, node identifier, edge endpoints, and parameter path.
- The source node output contract and target node input contract for the incompatible edge.
- The invalid node's parameter schema, required fields, and actual invalid value.
- A strict instruction to return only a complete replacement graph.

Also assert a permanently invalid plan causes exactly two inference calls total and is never persisted.

- [ ] **Step 2: Run the focused tests and observe failure**

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest tests/plan/test_plan_repair.py tests/plan/test_plan_service.py tests/plan/test_plan_generator.py -q
```

Expected: FAIL because current repair context only appends human-readable issue text to execution constraints and the Pydantic agent can retry internally.

- [ ] **Step 3: Enrich validator issues without weakening validation**

Add structured fields needed for repair to the existing issue model: edge source and target, parameter path, expected schema, and safe actual-value summary. Populate those fields for `RESOURCE_EDGE_INCOMPATIBLE` and `PARAMETER_SCHEMA_INVALID`. Do not change which graphs pass validation.

- [ ] **Step 4: Implement the pure repair-context builder**

Make `build_plan_repair_context(original_graph, issues, node_registry)` deterministic and unit-testable. Resolve node input/output and parameter contracts from the frozen `NodeRegistry` planning metadata. Serialize only data required to correct the graph.

- [ ] **Step 5: Bound model invocations explicitly**

Set the plan generator's internal model retry count to zero. Treat malformed model JSON as a typed inference failure. In `PlanService`, execute:

```python
first = await generator.generate(initial_context)
issues = validator.validate(first)
if issues:
    repaired = await generator.generate(build_plan_repair_context(first, issues, registry))
    issues = validator.validate(repaired)
```

Raise the existing plan validation failure with final structured issues if the second result is invalid. Record call and validation durations for later telemetry, but do not persist from this service.

- [ ] **Step 6: Run the repair and plan suites**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/plan/test_plan_repair.py tests/plan/test_plan_generator.py tests/plan/test_plan_service.py tests/plan -q
```

Expected: PASS; tests prove one call for a valid first graph and two calls, never three, when repair is required.

- [ ] **Step 7: Commit bounded repair**

```powershell
git add backend/app/agents/plan_repair.py backend/app/agents/plan_generator.py backend/app/agents/plan_service.py backend/app/plan/validator.py backend/tests/plan
git commit -m "fix(plan): provide contract-aware bounded repair" -m "向修复调用提供原始图、结构化校验问题与节点契约。`n`n计划生成最多执行一次初始调用和一次修复调用。" -m "Modules: plan-agent, plan-validator"
```

## Task 5: Persist once and recover workflow start idempotently

**Files:**

- Modify: `backend/app/api/routes/plans.py`
- Modify: `backend/app/plan/service.py`
- Modify: `backend/app/workflows/starter.py`
- Modify: `backend/app/domain/repository.py`
- Modify: `backend/tests/api/test_plan_api.py`
- Modify: plan service, repository, and starter tests

- [ ] **Step 1: Re-read the high-risk standards immediately before coding**

Run:

```powershell
Get-Content agent-git-standards.md
Get-Content agent-production-deployment-standards.md
```

Confirm that this task changes only local code and tests; no environment mutation is authorized yet.

- [ ] **Step 2: Write failing persistence and start-recovery tests**

Cover these exact scenarios:

1. Temporal connection failure after a valid plan: one `PlanVersion`, one pending `Run`, API error `PLAN_START_FAILED` with HTTP 503.
2. Retry `POST /tasks/{task_id}/plans/{plan_version}/start`: reuse the existing active run and deterministic workflow identity.
3. Temporal `WorkflowAlreadyStarted`: treat the matching workflow as successfully started and return the same run.
4. Two concurrent start requests: serialize on the task row and create at most one active run.
5. Complete plan lifecycle exceeding 105 seconds: API error `PLAN_GENERATION_TIMEOUT` with HTTP 504 and no duplicate plan persistence.
6. Unexpected programming error: HTTP 500, no conversion to provider/network timeout.

Do not add a global uniqueness constraint that would forbid legitimate reruns after a terminal run. Enforce uniqueness for the active-start operation through the task lock plus repository lookup.

- [ ] **Step 3: Observe the existing duplicate-persistence failure**

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest tests/api/test_plan_api.py tests/plan tests/integration/test_plan_workflow.py -q
```

Expected: FAIL because the route currently catches Temporal and persistence failures together and invokes fallback persistence a second time.

- [ ] **Step 4: Separate generate, persist, and start phases**

Refactor the route into explicit stages:

```python
async with asyncio.timeout(settings.plan_lifecycle_timeout_seconds):
    generated = await plan_service.generate(...)
    persisted = await plan_store.persist_once(...)
    started = await workflow_starter.start_persisted_plan(...)
```

Default `plan_lifecycle_timeout_seconds` to `105`. Persist validator issue summaries with the plan response for UI display. Remove `_NoopStarter` fallback and every second call to `persist_plan`.

- [ ] **Step 5: Add durable start recovery**

- Acquire a row lock for the owned task before active-run lookup and creation.
- Store or deterministically derive `workflow_id = task-workflow-{task_id}` for the start attempt.
- Let `WorkflowStarter` accept the pre-created run identity instead of creating a second run internally.
- On `WorkflowAlreadyStarted`, verify the workflow identity matches and return the existing run.
- On Temporal connectivity failure, retain the run in the existing recoverable `pending` state and surface `PLAN_START_FAILED` without rolling back the plan version.
- Add `POST /tasks/{task_id}/plans/{plan_version}/start` to retry only the start phase.

- [ ] **Step 6: Run focused backend lifecycle tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/api/test_plan_api.py tests/plan tests/domain tests/integration/test_plan_workflow.py -q
```

Expected: PASS with one plan version and one active run in every ambiguous-start test.

- [ ] **Step 7: Commit the durable lifecycle**

```powershell
git add backend/app/api/routes/plans.py backend/app/plan backend/app/workflows/starter.py backend/app/domain/repository.py backend/tests/api/test_plan_api.py backend/tests/plan backend/tests/domain backend/tests/integration/test_plan_workflow.py
git commit -m "fix(plan): persist once and recover workflow start" -m "拆分生成、持久化与工作流启动阶段，消除异常回退造成的重复版本。`n`n通过任务锁与确定性工作流标识恢复模糊启动结果。" -m "Modules: plan-api, workflow-start"
```

## Task 6: Reconcile ambiguous plan results in the frontend

**Files:**

- Modify: `frontend/src/features/tasks/plans.api.ts`
- Modify: `frontend/src/app/error/apiErrorMapper.ts`
- Modify: `frontend/src/features/tasks/TaskChatView.vue`
- Modify: `frontend/src/features/tasks/plans.api.test.ts`
- Modify: `frontend/src/app/error/apiErrorMapper.test.ts`
- Modify: `frontend/src/features/tasks/TaskChatView.test.ts`

- [ ] **Step 1: Write failing frontend contract tests**

Assert:

- Plan generation passes `{ timeoutMs: null }`; unrelated API methods retain their existing timeout.
- `PLAN_GENERATION_TIMEOUT` maps to a retryable plan-generation message.
- `PROVIDER_TIMEOUT` includes a safe phase-specific message and refreshes server state once before offering regeneration.
- `PLAN_START_FAILED` displays the persisted plan and a “重试启动” action that calls the start-only endpoint.
- An ambiguous browser/network failure polls server state every three seconds for up to 45 checks, stops when plan version advances or a run appears, and never automatically regenerates.
- Component unmount aborts polling and pending requests.

- [ ] **Step 2: Run focused Vitest files and observe failure**

```powershell
Set-Location frontend
npm run test:unit -- src/features/tasks/plans.api.test.ts src/app/error/apiErrorMapper.test.ts src/features/tasks/TaskChatView.test.ts
```

Expected: FAIL because plan generation currently inherits `AI_REQUEST_TIMEOUT_MS`, performs only one refresh, and has no start-only recovery path.

- [ ] **Step 3: Implement the plan API boundary**

Set only `generatePlan` to `timeoutMs: null`. Add a typed `startPlan(taskId, planVersion)` method. Extend response types to include plan version, validator issue summaries, run state, and recoverable-start state without weakening current type checks.

- [ ] **Step 4: Implement lifecycle-specific reconciliation**

Create plan-specific state and methods inside `TaskChatView.vue` or a small colocated composable:

- Snapshot plan version and active run before generation.
- On successful response, render persisted plan and run state.
- On typed provider timeout, fetch server state once; if no advancement exists, offer explicit retry.
- On an ambiguous client/network error, poll every three seconds for no more than 135 seconds. Stop on version advancement, active-run appearance, abort, or limit.
- On `PLAN_START_FAILED`, preserve and render the plan, display validator issue summaries, and call only `startPlan` from the retry action.
- Never infer failure solely from the browser request ending, and never automatically submit a second generation request.

- [ ] **Step 5: Run focused and full frontend tests**

```powershell
npm run test:unit -- src/features/tasks/plans.api.test.ts src/app/error/apiErrorMapper.test.ts src/features/tasks/TaskChatView.test.ts
npm run test:unit
```

Expected: PASS with fake timers proving interval count, termination, and cleanup.

- [ ] **Step 6: Commit frontend reconciliation**

```powershell
git add frontend/src/features/tasks/plans.api.ts frontend/src/features/tasks/plans.api.test.ts frontend/src/features/tasks/TaskChatView.vue frontend/src/features/tasks/TaskChatView.test.ts frontend/src/app/error/apiErrorMapper.ts frontend/src/app/error/apiErrorMapper.test.ts
git commit -m "fix(web): reconcile plan generation with server state" -m "计划生成取消浏览器侧超时，并根据服务端版本与运行状态处理模糊结果。`n`n启动失败仅重试工作流启动，不重复生成计划。" -m "Modules: web-plan, web-errors"
```

## Task 7: Add safe inference and plan lifecycle telemetry

**Files:**

- Create: `backend/app/providers/inference_telemetry.py`
- Create: `backend/tests/providers/test_inference_telemetry.py`
- Modify: `backend/app/providers/inference.py`
- Modify: `backend/app/agents/plan_service.py`
- Modify: `backend/app/api/routes/plans.py`

- [ ] **Step 1: Write failing telemetry allowlist tests**

Capture structured log records and assert exact event names:

- `inference.started`
- `inference.attempt_finished`
- `inference.failed`
- `plan.validation_finished`
- `plan.persisted`
- `plan.workflow_start_finished`

Assert allowed fields are present and recursively assert forbidden key fragments and known secret values are absent: `authorization`, `api_key`, `credential`, `prompt`, `messages`, `response_body`, `graph`, and test secret values.

- [ ] **Step 2: Run the test and observe failure**

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest tests/providers/test_inference_telemetry.py -q
```

Expected: FAIL because no centralized safe telemetry adapter exists.

- [ ] **Step 3: Implement an allowlist-based event helper**

The helper must reject unknown fields rather than redact after logging. Permit only provider type, model, intent, timeout phase, attempt number, elapsed milliseconds, response status, plan version, issue codes, run state, and request/correlation identifiers.

- [ ] **Step 4: Emit lifecycle events at stage boundaries**

Instrument the provider attempt loop and the plan validate/persist/start stages. Measure durations with a monotonic clock. Do not log prompts, response text, serialized plans, request bodies, Vault references, or exception repr strings that may embed a URL credential.

- [ ] **Step 5: Run telemetry and adjacent suites**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/providers/test_inference_telemetry.py tests/providers/test_inference.py tests/plan/test_plan_service.py tests/api/test_plan_api.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit telemetry**

```powershell
git add backend/app/providers/inference_telemetry.py backend/app/providers/inference.py backend/app/agents/plan_service.py backend/app/api/routes/plans.py backend/tests/providers/test_inference_telemetry.py
git commit -m "feat(provider): log safe inference lifecycle events" -m "通过字段白名单记录推理与计划生命周期的阶段耗时和结果。`n`n禁止提示词、响应正文、凭据与完整计划进入日志。" -m "Modules: inference-observability, plan-api"
```

## Task 8: Align application, proxy, Compose, and OCI metadata

**Files:**

- Modify: `backend/app/config.py`
- Modify: config tests under `backend/tests`
- Modify: `infra/reverse-proxy/zz-kairos-staging-tls.conf`
- Modify: `infra/reverse-proxy/zz-kairos-production-tls.conf`
- Modify: `infra/compose/compose.staging.yml`
- Modify: `infra/compose/compose.production.yml`
- Modify: `.github/workflows/ci-build-push.yml`
- Create or modify focused infrastructure assertions under the existing test convention

- [ ] **Step 1: Write failing static budget and metadata assertions**

Test that parsed configuration preserves this strict ordering:

```text
provider logical operation = 45 seconds
backend plan lifecycle = 105 seconds
nginx proxy_read_timeout = 120 seconds
nginx proxy_send_timeout = 120 seconds
frontend plan timeout = null
```

Inspect all three image build steps in `.github/workflows/ci-build-push.yml` and assert they emit OCI labels for source repository, exact Git revision, and release/image version.

- [ ] **Step 2: Observe the configuration mismatch**

Run the repository's current config/infra test command, plus:

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest tests -q -k "config or compose or nginx or workflow"
```

Expected: FAIL because nginx currently uses 90 seconds and image builds have no OCI provenance labels.

- [ ] **Step 3: Add typed settings and explicit environment wiring**

Add `provider_request_timeout_seconds: int = 45` and `plan_lifecycle_timeout_seconds: int = 105` with positive-value validation. Wire both values explicitly into Staging and Production API/worker environment blocks so deployment does not depend on an undocumented default.

- [ ] **Step 4: Raise only the plan-compatible proxy bounds**

Set `proxy_read_timeout 120s` and `proxy_send_timeout 120s` on the API location serving plan generation, preserving existing connect timeout and unrelated static/UI behavior.

- [ ] **Step 5: Add immutable OCI provenance labels**

For web, api, and worker build steps, add:

```yaml
labels: |
  org.opencontainers.image.source=https://github.com/${{ github.repository }}
  org.opencontainers.image.revision=${{ github.sha }}
  org.opencontainers.image.version=${{ steps.tag.outputs.tag }}
```

- [ ] **Step 6: Run focused configuration checks**

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest tests -q -k "config or compose or nginx or workflow"
```

Then validate Compose rendering without printing secrets:

```powershell
Set-Location ..
$env:POSTGRES_DB = 'kairos_config_check'
$env:POSTGRES_USER = 'kairos_config_check'
$env:POSTGRES_PASSWORD = 'config_check_only'
$env:KAIROS_TEMPORAL_NAMESPACE = 'kairos-config-check'
$env:MINIO_ACCESS_KEY = 'config_check_only'
$env:MINIO_SECRET_KEY = 'config_check_only'
$env:KAIROS_S3_BUCKET = 'kairos-config-check'
$env:KAIROS_CREDENTIAL_MASTER_KEY = 'config_check_only'
$env:KAIROS_API_IMAGE = 'ghcr.io/gehrmannmerlin/kairos-api:config-check'
$env:KAIROS_WORKER_IMAGE = 'ghcr.io/gehrmannmerlin/kairos-worker:config-check'
$env:KAIROS_WEB_IMAGE = 'ghcr.io/gehrmannmerlin/kairos-web:config-check'
docker compose -f infra/compose/compose.base.yml -f infra/compose/compose.staging.yml config --quiet
docker compose -f infra/compose/compose.base.yml -f infra/compose/compose.production.yml config --quiet
```

Expected: all commands exit zero.

- [ ] **Step 7: Commit aligned budgets and provenance**

```powershell
git add backend/app/config.py backend/tests infra/reverse-proxy/zz-kairos-staging-tls.conf infra/reverse-proxy/zz-kairos-production-tls.conf infra/compose/compose.staging.yml infra/compose/compose.production.yml .github/workflows/ci-build-push.yml
git commit -m "fix(infra): align plan lifecycle timeout bounds" -m "统一供应商、后端生命周期与反向代理的分层超时预算。`n`n为 GHCR 镜像补充源码、提交与版本 OCI 标签。" -m "Modules: config, nginx, compose, ci"
```

## Task 9: Add release-gate harnesses for real DeepSeek and browser verification

**Files:**

- Create: `infra/scripts/structured-plan-staging-acceptance.py`
- Create: `backend/tests/ops/test_structured_plan_acceptance_contract.py`
- Create: `frontend/e2e/structured-plan-production.spec.ts`
- Modify: `frontend/playwright.config.ts` only if an opt-in external base URL is required

- [ ] **Step 1: Write failing contract tests for the Staging harness**

Statically and behaviorally verify that the harness:

- Runs the full Goal Understanding → CollectionSpec → Confirm → Plan Generation → Validator → PlanVersion → workflow auto-start chain.
- Uses a real DeepSeek model configuration and the existing CredentialVault execution path; no key literal or key CLI argument is accepted.
- Executes Test A with `采集山东省人民政府官网发布的最近一个月的干部任前公示信息`.
- Executes Test B with `采集上海市人民政府官网最近一个月的任前公示信息`.
- Records `goal_ms`, `plan_model_1_ms`, `repair_used`, `plan_model_2_ms`, `plan_total_ms`, `validation_result`, `plan_version`, `run_id`, and `workflow_id` for both tests.
- Fails on a 45-second read timeout, inference failure, duplicate plan version, missing run, or secret leakage.
- Exercises Test C as a controlled Staging-only `RESOURCE_EDGE_INCOMPATIBLE` repair fixture only when the real A/B executions both produce valid first plans. The normal plan calls must still use real DeepSeek.

- [ ] **Step 2: Write the failing Playwright contract**

The test must be opt-in through environment variables and never commit credentials. It must visit the supplied external base URL, log in, create a fresh task with the exact Shandong prompt, complete Goal Understanding, confirm the Spec, wait for Plan Generation, assert Plan Summary and workflow start are visible, and assert `推理请求失败` is absent.

- [ ] **Step 3: Run focused tests and observe failure**

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest tests/ops/test_structured_plan_acceptance_contract.py -q
Set-Location ..\frontend
npm run test:e2e -- --list
```

Expected: the backend contract test fails until the harness exists; Playwright list must include the new production smoke once created.

- [ ] **Step 4: Implement the Staging acceptance harness**

Reuse existing API schemas and smoke helpers where possible. Emit one JSON result per test with only safe identifiers and duration fields. Fail fast on any required field or lifecycle stage. Query server state after generation to prove one PlanVersion and one Run instead of trusting only the HTTP response.

- [ ] **Step 5: Implement the opt-in production Playwright smoke**

Read `KAIROS_E2E_BASE_URL`, `KAIROS_E2E_EMAIL`, and `KAIROS_E2E_PASSWORD` from the process environment. Skip with an explicit message when external-smoke mode is not enabled so the ordinary local E2E suite remains deterministic. Configure trace-on-failure without storing session cookies in Git.

- [ ] **Step 6: Run contract, unit, and Playwright discovery checks**

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest tests/ops/test_structured_plan_acceptance_contract.py tests/ops/test_redaction.py -q
Set-Location ..\frontend
npm run test:unit
npm run test:e2e -- --list
```

Expected: PASS; this step does not claim real provider or public-site acceptance yet.

- [ ] **Step 7: Commit release gates**

```powershell
Set-Location ..
git add infra/scripts/structured-plan-staging-acceptance.py backend/tests/ops/test_structured_plan_acceptance_contract.py frontend/e2e/structured-plan-production.spec.ts frontend/playwright.config.ts
git commit -m "test(deploy): add structured plan release gates" -m "增加真实 DeepSeek 三轮 Staging 验收与外部浏览器 Production Smoke。`n`n所有凭据仅从既有 Vault 或运行环境读取，仓库不保存秘密。" -m "Modules: staging-acceptance, browser-smoke"
```

## Task 10: Complete local verification, open the PR, pass CI, and merge

**Files:**

- Review: every changed file
- Modify: only files required to fix verification or review findings

- [ ] **Step 1: Invoke the verification skill and run fresh local gates**

Use `superpowers:verification-before-completion`, then run from the worktree:

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m ruff format --check app tests
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m mypy app tests
.\.venv\Scripts\python.exe -m pytest tests/providers tests/reliability tests/agents tests/plan tests/api/test_plan_api.py tests/domain tests/ops -q
.\.venv\Scripts\python.exe -m pytest -q
Set-Location ..\frontend
npm run lint:check
npm run format:check
npm run type-check
npm run test:unit
npm run build
npm run test:e2e -- --list
```

Expected: every command exits zero. If a formatter changes files, rerun its check and the affected tests before committing the correction.

- [ ] **Step 2: Inspect scope, secrets, and commit quality**

```powershell
Set-Location ..
git status --short --branch
git diff --check origin/main...HEAD
git diff --stat origin/main...HEAD
git log --format=fuller origin/main..HEAD
bash ./infra/scripts/secret-scan.sh
```

Confirm no original-workspace untracked script appears in the branch, no secret or generated environment file is tracked, each commit has an English Conventional Commit title and Chinese body, and no required P0/P1 item is deferred.

- [ ] **Step 3: Re-read the Git standard immediately before remote writes**

```powershell
Get-Content agent-git-standards.md
$expected = 'https://github.com/GehrmannMerlin/Kairos.git'
if ((git remote get-url origin) -ne $expected) { throw 'origin fetch URL mismatch' }
if ((git remote get-url --push origin) -ne $expected) { throw 'origin push URL mismatch' }
git branch --show-current
```

Expected: both URLs exactly match and branch is `fix/structured-plan-inference`.

- [ ] **Step 4: Push the feature branch and create the PR**

```powershell
git push -u origin fix/structured-plan-inference
gh pr create --repo GehrmannMerlin/Kairos --base main --head fix/structured-plan-inference --title "fix(plan): enforce structured inference lifecycle" --body "## 变更说明`n- 为 Agent 增加推理意图、Provider 能力与统一客户端工厂`n- 修复 DeepSeek 结构化计划 thinking、超时分类和有限重试`n- 增强 Plan repair、单次持久化与工作流启动恢复`n- 增加前端对账、遥测、分层超时与部署验收门禁`n`n## 验证`n- 后端 Ruff、mypy、pytest`n- 前端 lint、format、type-check、Vitest、build、Playwright discovery`n- Compose config 与 secret scan"
```

- [ ] **Step 5: Wait for CI and request an independent code review**

```powershell
$pr = gh pr view --repo GehrmannMerlin/Kairos --json number --jq '.number'
gh pr checks $pr --repo GehrmannMerlin/Kairos --watch
```

Invoke `superpowers:requesting-code-review` against `origin/main...HEAD`. Address every P0/P1 finding with a focused test and commit, push again, and wait for a fresh green CI run. Do not merge while any required check is pending or failed.

- [ ] **Step 6: Rebase-merge and capture the immutable merged identity**

```powershell
gh pr merge $pr --repo GehrmannMerlin/Kairos --rebase --delete-branch=false
gh pr view $pr --repo GehrmannMerlin/Kairos --json state,mergedAt,mergeCommit,url
git fetch origin main --tags
$mergeSha = gh pr view $pr --repo GehrmannMerlin/Kairos --json mergeCommit --jq '.mergeCommit.oid'
if ((git rev-parse origin/main) -ne $mergeSha) { throw 'origin/main does not match merged PR' }
```

Expected: PR state `MERGED`, remote feature branch still exists, and `$mergeSha` equals `origin/main` at the time of deployment preparation.

## Task 11: Deploy the merged SHA to Staging and pass the real DeepSeek gate

**Files:**

- Read: `agent-production-deployment-standards.md`
- Execute: existing deployment/smoke scripts and the new acceptance harness
- Do not edit Production state in this task

- [ ] **Step 1: Wait for the merged-main GHCR build and record provenance**

```powershell
Set-Location D:\Develop\Vue\Kairos\.worktrees\structured-plan-inference
Get-Content agent-production-deployment-standards.md
$mergeSha = gh pr view $pr --repo GehrmannMerlin/Kairos --json mergeCommit --jq '.mergeCommit.oid'
$sha12 = $mergeSha.Substring(0, 12)
gh run list --repo GehrmannMerlin/Kairos --workflow ci-build-push.yml --commit $mergeSha --limit 1
$runId = gh run list --repo GehrmannMerlin/Kairos --workflow ci-build-push.yml --commit $mergeSha --limit 1 --json databaseId --jq '.[0].databaseId'
gh run watch $runId --repo GehrmannMerlin/Kairos --exit-status
docker buildx imagetools inspect "ghcr.io/gehrmannmerlin/kairos-web:$sha12"
docker buildx imagetools inspect "ghcr.io/gehrmannmerlin/kairos-api:$sha12"
docker buildx imagetools inspect "ghcr.io/gehrmannmerlin/kairos-worker:$sha12"
```

Record the three digests and verify each image's `org.opencontainers.image.revision` equals the 40-character merged SHA.

- [ ] **Step 2: Print and verify the Staging preflight identity**

Record a PASS/FAIL table containing target `47.238.145.24`, release tag `$sha12`, all three digests, current Staging release, and exact rollback tag/digests. Stop as `BLOCKED` if any field is missing.

- [ ] **Step 3: Deploy only the immutable merged-main images**

From PowerShell, pass environment variables to the existing standard script:

```powershell
$env:REGISTRY = 'ghcr.io'
$env:NAMESPACE = 'gehrmannmerlin'
$env:RELEASE_TAG = $sha12
$env:DEPLOY_HOST = '47.238.145.24'
bash ./infra/scripts/deploy-staging.sh
bash ./infra/scripts/smoke-staging.sh
```

Expected: deployment and standard Staging smoke both exit zero.

- [ ] **Step 4: Execute the real DeepSeek A/B/C acceptance inside Staging**

Inject the committed harness into the Staging API container exactly as existing smoke scripts inject their drivers, and execute it there. The harness must use the server's existing smoke credential and persist it only through the normal CredentialVault path. Never print or copy the key back to the workstation.

Expected evidence:

- Test A Shandong full chain PASS.
- Test B Shanghai full chain PASS.
- Test C `RESOURCE_EDGE_INCOMPATIBLE` repair path PASS, controlled only if A/B did not naturally repair.
- Each real normal plan has model-call timings, total timing, validator result, one plan version, run ID, and workflow ID.
- No 45-second ReadTimeout, generic `推理请求失败`, duplicate PlanVersion, or secret leak.

- [ ] **Step 5: Verify Staging server truth**

Use read-only SSH and API checks to confirm the running container image references/digests match `$sha12`, API live/ready is healthy, worker/Temporal/PostgreSQL/MinIO are healthy, and logs for the acceptance correlation IDs contain safe lifecycle fields but no prompt, response body, authorization value, or credential.

Any failure in Steps 1–5 is a hard gate: report `BLOCKED`, preserve evidence, and do not tag or deploy Production.

## Task 12: Create the patch release, deploy Production, and verify in a real browser

**Files:**

- Read: `agent-git-standards.md`
- Read: `agent-production-deployment-standards.md`
- Execute: release tag, GHCR workflow, backup, standard Production deploy, health, smoke, browser, and release-identity checks

- [ ] **Step 1: Re-read both standards and calculate the patch version**

```powershell
Get-Content agent-git-standards.md
Get-Content agent-production-deployment-standards.md
git fetch origin main --tags
$mergeSha = git rev-parse origin/main
git tag --points-at $mergeSha
git tag --list --sort=-version:refname | Select-Object -First 10
```

The expected next tag is `v0.1.6` because current Production is `v0.1.5` at `a3df2245c0a09e539b0b4294742543abd6cd7a0c`. If another release has appeared, recompute the next patch tag from current repository and Production truth, document the changed identity, and never overwrite an existing tag.

- [ ] **Step 2: Create and push an annotated patch tag on the merged commit**

For the expected identity:

```powershell
git tag -a v0.1.6 $mergeSha -m "release: v0.1.6 structured plan inference fix"
git push origin v0.1.6
```

Wait for the tag-triggered `ci-build-push.yml` run to pass. Set `$releaseTag` to `v0.1.6-` plus the first 12 characters of `$mergeSha`. Inspect web/api/worker with `docker buildx imagetools inspect`, record all three digests, and verify OCI source, revision, and version labels.

- [ ] **Step 3: Record rollback identity and create the pre-release backup**

The known previous identity is release `v0.1.5`, Git SHA `a3df2245c0a09e539b0b4294742543abd6cd7a0c`, and image tag `v0.1.5-a3df2245c0a0`; re-query Production container digests before trusting those values.

Run the standard server-side backup and parse its explicit result without printing secrets:

```powershell
$backupOutput = ssh -i "$HOME/.ssh/kairos_staging_deploy_rsa" -o BatchMode=yes deploy@47.238.145.24 "ENV=production BACKUP_DIR=/srv/kairos/backups bash /srv/kairos/scripts/backup.sh"
$backupId = ([regex]::Match(($backupOutput -join "`n"), 'BACKUP_DONE backup_id=([^\s]+)')).Groups[1].Value
if (-not $backupId) { throw 'Production backup did not return BACKUP_DONE' }
```

Validate the backup manifest and checksums according to the deployment standard. Record `previous_release`, previous three digests, `$backupId`, migration head, and rollback target before changing Production.

- [ ] **Step 4: Print the Production preflight table and deploy immutable GHCR images**

The table must show PASS for server `47.238.145.24`, public URL `https://app.kairos.ac.cn/`, merged SHA, release tag, three new digests, backup ID, migration head, previous release/digests, and rollback target.

```powershell
$env:REGISTRY = 'ghcr.io'
$env:NAMESPACE = 'gehrmannmerlin'
$env:RELEASE_TAG = $releaseTag
$env:DEPLOY_HOST = '47.238.145.24'
$env:BACKUP_ID = $backupId
$env:PREVIOUS_RELEASE = 'v0.1.5-a3df2245c0a0'
$env:ROLLBACK_TARGET = 'v0.1.5-a3df2245c0a0'
bash ./infra/scripts/deploy-production.sh
```

If the previous Production identity changed during preflight, replace the two previous-release variables with the verified actual immutable tag. The Production server must not run `git pull`, `docker build`, `pip install`, `npm build`, source copy/sync, or `docker exec` hot patches.

- [ ] **Step 5: Run Production health and golden-path smoke**

```powershell
$env:DEPLOY_HOST = '47.238.145.24'
$env:PROD_DOMAIN = 'app.kairos.ac.cn'
bash ./infra/scripts/smoke-production.sh
```

Additionally verify web, API live, API ready, worker, PostgreSQL, Temporal, and MinIO health. Scan correlated deployment/smoke logs for secrets and the forbidden generic plan failure.

- [ ] **Step 6: Run the real public browser acceptance**

Load the smoke account credentials into process environment through the approved secret source, never command history or Git, then run:

```powershell
Set-Location frontend
$env:KAIROS_E2E_EXTERNAL = '1'
$env:KAIROS_E2E_BASE_URL = 'https://app.kairos.ac.cn/'
npm run test:e2e -- structured-plan-production.spec.ts
```

The browser must log in, create the exact Shandong task, pass Goal Understanding, confirm Spec, pass Plan Generation and Validator, display Plan Summary, and show workflow start. It must not show `推理请求失败`.

- [ ] **Step 7: Prove the browser loaded the new release**

Compare Production web/api/worker container image digests with the release manifest and GHCR. Verify reverse-proxy upstream, `index.html` cache headers, hashed asset URLs, and a clean browser context. Record the visible asset hash and confirm it belongs to the new web image.

- [ ] **Step 8: Perform final verification and branch cleanup decision**

Invoke `superpowers:verification-before-completion` against fresh Production evidence. Only after all gates pass, invoke `superpowers:finishing-a-development-branch` to verify the PR is merged, the remote feature branch is preserved, the original workspace can be fast-forwarded to `origin/main`, and the isolated local worktree/branch can be removed without touching the user's seven untracked scripts.

The final status may be `DEPLOYED` only when code, CI, real DeepSeek Staging, Production deployment, health, public browser verification, release identity, and rollback readiness all pass. Otherwise report `BLOCKED` with the exact failed gate; never relabel incomplete Production work as a follow-up.

## Plan self-review checklist

- [x] Every approved design section maps to at least one red test, implementation step, verification command, and commit.
- [x] No plan-specific DeepSeek flag can reach Goal Understanding or custom providers.
- [x] Timeout and retry attempt counts are numerically explicit at every layer.
- [x] Repair makes no more than one additional model call and deterministic validation remains final authority.
- [x] Persistence and workflow start are independently recoverable with no automatic regeneration.
- [x] Real Staging and Production tests use secrets only through approved runtime channels.
- [x] PR, CI, immutable image, backup, rollback, Production health, and real browser gates cannot be skipped.
- [x] All referenced repository paths and package-script names exist or are explicitly created by an earlier task.
