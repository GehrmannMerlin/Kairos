# Execution Readiness and Observable Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 阻止不可执行的采集计划进入 Temporal，补齐真实 artifact executor、准确终态和可回放的节点执行进度，使 Task Chat 能解释每个真实执行阶段。

**Architecture:** 在 Goal Understanding 与 Spec Confirm 边界增加确定性 source contract，在冻结 Plan 与 Workflow start 之间增加持久化 `ExecutionPreflight`。Temporal 继续负责编排；`execute_safe_unit` 通过现有 `NodeRun`、`NodeAttempt`、`Checkpoint` 和 `DomainEvent` 写入幂等执行事实，现有 Execution Query 与 Task SSE 投影这些事实，Task Chat 使用 snapshot + SSE delta 展示进度。

**Tech Stack:** Python 3.11、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic、Temporal Python SDK、PostgreSQL/SQLite tests、Vue 3、TypeScript 5.7、Vitest、Playwright、GHCR。

## Global Constraints

- 保留 Temporal 为唯一 durable orchestration engine；不得把 Workflow 替换成同步循环或浏览器轮询执行器。
- 复用 `DomainEvent + SSE + Last-Event-ID/after_id`；不得新增 Kafka、Redis pub/sub、WebSocket 或第二张 execution event 表。
- `SPECIFIED_SOURCE` 只有在规范化后至少存在一个 literal HTTP(S) URL 时才可确认和启动。
- named source + available search 必须确定性变为 `HYBRID`，搜索范围固定为命名来源解析，不得扩张用户地域、机构、主题或时间范围。
- named source + no search 必须返回 `SOURCE_RESOLUTION_REQUIRED`，不得自动启动。
- `PlanValidationResult.VALID` 与 `ExecutionPreflightStatus.READY` 是两个独立门禁；只有二者同时满足才能 start Workflow。
- `NODE_EXECUTOR_UNAVAILABLE` 在运行中必须进入 `FAILED`，不得与审批拒绝合并或继续到 completion。
- `eligible_urls=0 && terminal_urls=0` 不得产生 `PARTIALLY_COMPLETED`。
- 不新增全局 TaskState；复用现有 `FAILED`、`COMPLETED`、`PARTIALLY_COMPLETED`，细分结果使用 stable reason/completion type。
- 不展示或持久化 chain-of-thought、credential、authorization header、cookie、完整 provider request/response、页面正文或未经脱敏的 exception。
- Production 禁止源码热改和服务器构建；只允许 PR 合并后的 immutable GHCR image digest 经 Staging 晋级。
- 保留用户现有未跟踪 `infra/scripts/_*.py` 文件，不修改、不暂存、不提交。

---

## File Map

### New backend files

- `backend/app/domain/source_contract.py`：纯函数 source mode/URL/clarification 归一化。
- `backend/app/plan/capabilities.py`：Production executor capability manifest 与 runtime registry assertion。
- `backend/app/plan/preflight.py`：Execution Preflight contracts、规则和持久化 service。
- `backend/app/plan/preflight_repository.py`：owner-scoped、幂等 preflight result repository。
- `backend/app/artifacts/executor.py`：复用 `ArtifactService` 的 `generate_artifact` executor。
- `backend/app/execution/lifecycle.py`：NodeRun/NodeAttempt/DomainEvent 幂等记录器与 payload allowlist。
- `backend/app/observability/execution_metrics.py`：低基数 Preflight/Run/Node/SSE/invariant OpenTelemetry metrics。
- `backend/alembic/versions/0016_execution_readiness_progress.py`：preflight result 与 NodeRun identity migration。

### New frontend files

- `frontend/src/features/execution/ExecutionProgressPanel.vue`：Task Chat 内嵌的 snapshot + live delta 进度面板。
- `frontend/src/features/execution/ExecutionProgressPanel.test.ts`：历史、增量、去重、重连和中文语义测试。

### Existing files changed

- Source/Spec：`backend/app/domain/spec.py`、`backend/app/agents/service.py`、`backend/app/agents/goal_understanding.py`、`backend/app/providers/service.py`、`backend/app/domain/service.py`、`backend/app/domain/task_draft.py`。
- Plan/start：`backend/app/agents/plan_generator.py`、`backend/app/plan/validator.py`、`backend/app/api/routes/plans.py`、`backend/app/api/schemas.py`、`backend/app/domain/errors.py`。
- Runtime/artifact：`backend/app/plan/executors.py`、各 `*/executors.py` installer、`backend/app/worker.py`、`backend/app/activities/plan_execution.py`、`backend/app/activities/execution_seam.py`、`backend/app/workflows/task_workflow.py`、`backend/app/discovery/source_search.py`。
- Completion/events/read model：`backend/app/validation/completion.py`、`backend/app/activities/completion.py`、`backend/app/api/events.py`、`backend/app/execution/contracts.py`、`backend/app/execution/repository.py`、`backend/app/execution/service.py`。
- Frontend：`frontend/src/features/tasks/events.api.ts`、`frontend/src/features/tasks/useTaskEvents.ts`、`frontend/src/features/execution/types.ts`、`frontend/src/features/execution/useExecution.ts`、`frontend/src/features/tasks/TaskChatView.vue`、`frontend/src/app/error/apiErrorMapper.ts`。
- Docs/ops：`agent-business-logic-log.md`、`agent-project-implementation-plan.md`、必要的 Staging acceptance script 与 release evidence document。

---

### Task 1: Deterministic Source Contract

**Files:**
- Create: `backend/app/domain/source_contract.py`
- Modify: `backend/app/domain/spec.py`
- Modify: `backend/app/agents/service.py`
- Modify: `backend/app/agents/goal_understanding.py`
- Modify: `backend/app/providers/service.py`
- Modify: `backend/app/domain/service.py`
- Modify: `backend/app/domain/task_draft.py`
- Test: `backend/tests/domain/test_source_contract.py`
- Test: `backend/tests/agents/test_goal_understanding.py`
- Test: `backend/tests/domain/test_spec_confirm.py`

**Interfaces:**
- Consumes: `TaskType`, `SourceScope`, `GoalUnderstandingResult`, `canonical_url()` and owner-scoped `SearchConfigRepository.list_current()`.
- Produces: `SourceContractResult`, `normalize_source_contract(*, task_type, source_scope, search_available, explicit_texts)`, `validate_confirmable_spec_payload(payload)`, and `ProviderService.has_available_search_config(user) -> bool`.

- [ ] **Step 1: Write source contract unit tests**

Create tests with these exact cases:

```python
def test_explicit_url_becomes_specified_source() -> None:
    result = normalize_source_contract(
        task_type=TaskType.HYBRID,
        source_scope=SourceScope(
            mode=TaskType.HYBRID,
            seed_urls=["HTTPS://Example.COM/a/../notice#top"],
            source_hints=["示例官网"],
        ),
        search_available=False,
        explicit_texts=(),
    )
    assert result.ready is True
    assert result.task_type is TaskType.SPECIFIED_SOURCE
    assert result.source_scope.seed_urls == ["https://example.com/notice"]
    assert result.issue_code is None


def test_named_source_with_search_becomes_scoped_hybrid() -> None:
    result = normalize_source_contract(
        task_type=TaskType.SPECIFIED_SOURCE,
        source_scope=SourceScope(
            mode=TaskType.SPECIFIED_SOURCE,
            seed_urls=[],
            source_hints=["山东省人民政府官网"],
        ),
        search_available=True,
        explicit_texts=(),
    )
    assert result.ready is True
    assert result.task_type is TaskType.HYBRID
    assert result.resolution_scope == "NAMED_SOURCE_ONLY"


def test_named_source_without_search_requires_url() -> None:
    result = normalize_source_contract(
        task_type=TaskType.SPECIFIED_SOURCE,
        source_scope=SourceScope(
            mode=TaskType.SPECIFIED_SOURCE,
            seed_urls=[],
            source_hints=["山东省人民政府官网"],
        ),
        search_available=False,
        explicit_texts=(),
    )
    assert result.ready is False
    assert result.issue_code == "SOURCE_RESOLUTION_REQUIRED"
    assert result.clarification_question == "请提供该网站的完整网址，或先配置可用的搜索服务。"


def test_literal_url_in_user_text_survives_model_omission() -> None:
    result = normalize_source_contract(
        task_type=TaskType.SPECIFIED_SOURCE,
        source_scope=SourceScope(
            mode=TaskType.SPECIFIED_SOURCE,
            seed_urls=[],
            source_hints=["山东省人民政府官网"],
        ),
        search_available=False,
        explicit_texts=("请采集 https://www.shandong.gov.cn/ 的公示",),
    )
    assert result.ready is True
    assert result.task_type is TaskType.SPECIFIED_SOURCE
    assert result.source_scope.seed_urls == ["https://www.shandong.gov.cn/"]


def test_confirm_rejects_specified_source_without_seed(db, service, user, task) -> None:
    payload = _payload()
    payload["task_type"] = "SPECIFIED_SOURCE"
    payload["source_scope"] = {
        "mode": "SPECIFIED_SOURCE",
        "seed_urls": [],
        "source_hints": ["官网"],
    }
    with pytest.raises(SpecValidationError, match="完整网址"):
        service.confirm_spec(
            user_id=user.id,
            task_id=task.id,
            expected_version=task.version,
            spec_payload=payload,
        )
```

Add unsafe URL cases for `ftp://`, credential-in-URL and duplicate canonical URLs. The contract performs syntactic canonicalization only; DNS/SSRF checks remain in the existing discovery/fetch boundary.

- [ ] **Step 2: Run the tests and verify the contract is absent**

Run:

```powershell
Set-Location backend
python -m pytest tests/domain/test_source_contract.py tests/domain/test_spec_confirm.py -q
```

Expected: collection/import failure for `app.domain.source_contract` or assertion failure because empty specified sources are accepted.

- [ ] **Step 3: Implement the pure source contract**

Create these stable types and signature:

```python
class SourceResolutionScope(StrEnum):
    NAMED_SOURCE_ONLY = "NAMED_SOURCE_ONLY"


class SourceContractResult(BaseModel):
    ready: bool
    task_type: TaskType
    source_scope: SourceScope
    resolution_scope: SourceResolutionScope | None = None
    issue_code: str | None = None
    clarification_question: str | None = None


def normalize_source_contract(
    *,
    task_type: TaskType,
    source_scope: SourceScope,
    search_available: bool,
    explicit_texts: Sequence[str] = (),
) -> SourceContractResult:
    explicit_urls = [
        match.rstrip("，。；、)]}>")
        for text in explicit_texts
        for match in re.findall(r"https?://[^\s<>'\"]+", text, flags=re.IGNORECASE)
    ]
    seed_urls = list(
        dict.fromkeys(canonical_url(raw) for raw in [*source_scope.seed_urls, *explicit_urls])
    )
    source_hints = list(dict.fromkeys(h.strip() for h in source_scope.source_hints if h.strip()))
    if seed_urls:
        return SourceContractResult(
            ready=True,
            task_type=TaskType.SPECIFIED_SOURCE,
            source_scope=SourceScope(
                mode=TaskType.SPECIFIED_SOURCE,
                seed_urls=seed_urls,
                source_hints=source_hints,
            ),
        )
    if source_hints and search_available:
        return SourceContractResult(
            ready=True,
            task_type=TaskType.HYBRID,
            source_scope=SourceScope(
                mode=TaskType.HYBRID,
                seed_urls=[],
                source_hints=source_hints,
                resolution_scope=SourceResolutionScope.NAMED_SOURCE_ONLY,
            ),
            resolution_scope=SourceResolutionScope.NAMED_SOURCE_ONLY,
        )
    if source_hints:
        return SourceContractResult(
            ready=False,
            task_type=TaskType.HYBRID,
            source_scope=SourceScope(
                mode=TaskType.HYBRID,
                seed_urls=[],
                source_hints=source_hints,
                resolution_scope=SourceResolutionScope.NAMED_SOURCE_ONLY,
            ),
            resolution_scope=SourceResolutionScope.NAMED_SOURCE_ONLY,
            issue_code="SOURCE_RESOLUTION_REQUIRED",
            clarification_question="请提供该网站的完整网址，或先配置可用的搜索服务。",
        )
    return SourceContractResult(
        ready=True,
        task_type=TaskType.EXPLORATORY,
        source_scope=SourceScope(mode=TaskType.EXPLORATORY),
    )
```

Add `resolution_scope: Literal["NAMED_SOURCE_ONLY"] | None = None` to `SourceScope`. Implementation rules are exhaustive:

- extract only literal `http://`/`https://` tokens from explicit user messages, combine them with model/draft `seed_urls`, then canonicalize/dedupe with `app.discovery.url.canonical_url`;
- if any canonical URL exists, return ready `SPECIFIED_SOURCE` and retain hints;
- if no URL but hints exist and search is available, return ready `HYBRID` with `NAMED_SOURCE_ONLY`;
- if no URL but hints exist and search is unavailable, return not-ready with `SOURCE_RESOLUTION_REQUIRED` and the exact Chinese question above;
- if neither URL nor hint exists, preserve `EXPLORATORY`; its search requirement is enforced by Plan validation/preflight.

In `domain/spec.py`, add `validate_confirmable_spec_payload(payload)` which calls the existing draft validator and raises `SpecValidationError` when `mode == SPECIFIED_SOURCE and not seed_urls`. Keep `validate_spec_payload()` permissive for editable drafts.

- [ ] **Step 4: Apply the contract to Goal Understanding and manual confirmation**

Add:

```python
def has_available_search_config(self, user: Any) -> bool:
    return any(c.connection_status == "available" for c in self._search_configs.list_current(user.id))
```

Immediately after `GoalUnderstandingAgent.understand()` returns, normalize its scope and pass `explicit_texts=tuple(user_texts)` so a model omission cannot discard a user-supplied URL. Use `model_copy(update={"task_type": contract.task_type, "source_scope": contract.source_scope, "clarification_required": not contract.ready, "clarification_question": contract.clarification_question})`; save only the normalized result. Update the system prompt to say a named site without a literal URL is `HYBRID`, literal URL input is `SPECIFIED_SOURCE`, and the first `source_hints` item for a named-source request must be the named institution/site rather than a topic keyword.

Make `DomainService.confirm_spec()` call `validate_confirmable_spec_payload()`. Make `TaskDraftService.add_seed_url()` call `canonical_url()` so the UI-added URL follows the same identity contract.

- [ ] **Step 5: Run source and Goal Understanding tests**

Run:

```powershell
Set-Location backend
python -m pytest tests/domain/test_source_contract.py tests/domain/test_spec_confirm.py tests/agents/test_goal_understanding.py tests/api/test_understand.py tests/api/test_task_draft.py -q
```

Expected: all selected tests pass; no model call or DNS lookup is made by source contract tests.

- [ ] **Step 6: Commit Task 1**

```powershell
git add backend/app/domain/source_contract.py backend/app/domain/spec.py backend/app/agents/service.py backend/app/agents/goal_understanding.py backend/app/providers/service.py backend/app/domain/service.py backend/app/domain/task_draft.py backend/tests/domain/test_source_contract.py backend/tests/domain/test_spec_confirm.py backend/tests/agents/test_goal_understanding.py
git commit -m "fix(spec): enforce executable source contracts" -m "规范化显式 URL，并将无 URL 的命名来源确定性转换为受限混合搜索或要求用户补充来源。`n`n关联模块：M-06、M-09。"
```

---

### Task 2: Plan Source Invariants and Production Capability Manifest

**Files:**
- Create: `backend/app/plan/capabilities.py`
- Modify: `backend/app/plan/validator.py`
- Modify: `backend/app/agents/plan_generator.py`
- Modify: `backend/app/agents/plan_service.py`
- Modify: `backend/app/api/routes/plans.py`
- Modify: `backend/app/plan/executors.py`
- Modify: `backend/app/discovery/executors.py`
- Modify: `backend/app/crawling/executors.py`
- Modify: `backend/app/extraction/executors.py`
- Modify: `backend/app/validation/executors.py`
- Modify: `backend/app/worker.py`
- Test: `backend/tests/plan/test_source_invariants.py`
- Test: `backend/tests/plan/test_executor_capabilities.py`
- Test: `backend/tests/plan/test_plan_service.py`
- Test: `backend/tests/plan/test_plan_generator.py`
- Test: `backend/tests/discovery/test_executor_binding.py`

**Interfaces:**
- Consumes: `NodeType`, `NodeRegistry`, `ResourceClass`, `register_node_executor()`, actual Task id and frozen Spec version from the plan route.
- Produces: `PlanInput.task_id/spec_version`, `PlanGenerationService.generate_for_task(*, user, task_id, spec_version, spec_payload, task_type)`, `CAPABILITY_MANIFEST_VERSION`, `PRODUCTION_EXECUTOR_CAPABILITIES`, `supported_node_types()`, and `assert_runtime_executor_manifest()`.

- [ ] **Step 1: Write failing Plan invariant tests**

```python
def test_hybrid_plan_requires_source_search() -> None:
    outcome = validate_plan(_hybrid_graph_without_search(), _hybrid_spec(), NodeRegistry())
    assert outcome.result is PlanValidationResult.INVALID
    assert "SOURCE_SEARCH_REQUIRED" in {issue.code for issue in outcome.issues}


def test_specified_plan_rejects_empty_seed_even_for_historical_spec() -> None:
    outcome = validate_plan(_specified_graph(), _specified_spec(seed_urls=[]), NodeRegistry())
    assert outcome.result is PlanValidationResult.INVALID
    assert "EXECUTION_INPUT_UNMATERIALIZABLE" in {issue.code for issue in outcome.issues}


@pytest.mark.asyncio
async def test_plan_identity_is_canonicalized_from_command_context() -> None:
    agent = PlanGeneratorAgent(inference=FakeInference(VALID_PLAN_JSON))
    inp = _input().model_copy(
        update={"task_id": 25, "spec_version": 3, "task_type": TaskType.SPECIFIED_SOURCE}
    )
    graph = await agent.generate(inp, RESOLVED, api_key=None)
    assert graph.task_id == 25
    assert graph.spec_version == 3
    assert graph.task_type is TaskType.SPECIFIED_SOURCE
```

Update the Plan prompt assertion so `HYBRID` and `EXPLORATORY` always start with `source_search`; `SPECIFIED_SOURCE` consumes frozen seeds and does not invent a URL from hints.

- [ ] **Step 2: Write failing capability tests**

```python
def test_manifest_covers_every_generated_node_type() -> None:
    generated = {definition.node_type for definition in NodeRegistry().all()}
    assert generated == supported_node_types()


def test_fixture_registration_does_not_change_production_manifest() -> None:
    before = supported_node_types()
    install_staging_fixture()
    assert supported_node_types() == before


def install_all_real_executors_for_manifest_test() -> None:
    install_discovery_executors()
    install_fetch_executors()
    install_extraction_executors()
    install_validation_executors()

    async def artifact_executor(unit: ExecutionUnit) -> ExecuteUnitResult:
        return ExecuteUnitResult(unit_index=unit.index, committed_refs={}, status="OK")

    register_node_executor(NodeType.GENERATE_ARTIFACT, artifact_executor)


def test_runtime_registry_matches_manifest_after_real_installers() -> None:
    install_all_real_executors_for_manifest_test()
    assert_runtime_executor_manifest()
```

- [ ] **Step 3: Run tests to show current gaps**

Run:

```powershell
Set-Location backend
python -m pytest tests/plan/test_source_invariants.py tests/plan/test_executor_capabilities.py tests/discovery/test_executor_binding.py -q
```

Expected: missing-source-search and missing `generate_artifact` capability assertions fail.

- [ ] **Step 4: Implement the manifest and validator rules**

Define one immutable manifest entry per node:

```python
@dataclass(frozen=True)
class ExecutorCapability:
    node_type: NodeType
    resource_class: ResourceClass | None
    task_queue_role: str
    implementation_id: str


CAPABILITY_MANIFEST_VERSION = "m08-production-v1"
PRODUCTION_EXECUTOR_CAPABILITIES: Sequence[ExecutorCapability] = (
    ExecutorCapability(NodeType.SOURCE_SEARCH, ResourceClass.LLM_SEARCH, "llm_search", "search-service-v1"),
    ExecutorCapability(NodeType.ACCESS_RULES_CHECK, ResourceClass.CORE, "core", "access-rules-v1"),
    ExecutorCapability(NodeType.LINK_DISCOVERY, ResourceClass.CORE, "core", "link-discovery-v1"),
    ExecutorCapability(NodeType.FETCH, ResourceClass.HTTP, "http", "http-fetch-v1"),
    ExecutorCapability(NodeType.BROWSER_RENDER, ResourceClass.BROWSER, "browser", "browser-render-v1"),
    ExecutorCapability(NodeType.EXTRACT, ResourceClass.CORE, "core", "extraction-v1"),
    ExecutorCapability(NodeType.NORMALIZE, ResourceClass.CORE, "core", "normalize-v1"),
    ExecutorCapability(NodeType.DEDUPLICATE, ResourceClass.CORE, "core", "deduplicate-v1"),
    ExecutorCapability(NodeType.VALIDATE, ResourceClass.CORE, "core", "validation-v1"),
    ExecutorCapability(NodeType.GENERATE_ARTIFACT, ResourceClass.CORE, "core", "artifact-export-v1"),
)
```

The manifest must list exactly the ten NodeTypes currently generated: source search, access rules, link discovery, fetch, browser render, extract, normalize, deduplicate, validate and generate artifact. `assert_runtime_executor_manifest()` compares this declared set to `NODE_EXECUTORS` after all real installers run and raises `RuntimeError` with sorted missing/extra node names.

Add validator issues before provider prerequisite handling:

```python
if task_type in ("EXPLORATORY", "HYBRID") and not has_search_node:
    issues.append(
        PlanValidationIssue(
            code="SOURCE_SEARCH_REQUIRED",
            message="探索或混合计划必须先解析来源",
            path="nodes",
        )
    )
if task_type == "SPECIFIED_SOURCE" and not seed_urls:
    issues.append(
        PlanValidationIssue(
            code="EXECUTION_INPUT_UNMATERIALIZABLE",
            message="指定来源计划缺少可物化的种子 URL",
            path="source_scope.seed_urls",
        )
    )
```

Extend `PlanInput` with `task_id: int` and `spec_version: int`. Immediately after typed model output and before deterministic validation, replace model-supplied identity fields with the trusted command context:

```python
graph = graph.model_copy(
    update={
        "task_id": inp.task_id,
        "spec_version": inp.spec_version,
        "task_type": inp.task_type,
    }
)
```

The plan route passes its actual `task_id` and confirmed `cmd.spec_version`. The LLM cannot choose or copy these ownership/version identities from a prompt example.

Keep fixture registration outside the manifest. Call the runtime assertion in `worker.run()` only after all real installers, including Task 5's artifact installer, have run; until Task 5 lands, the test passes by installing a test-local real-shaped artifact function, and the worker assertion call is committed with Task 5.

- [ ] **Step 5: Run Plan/capability tests**

```powershell
Set-Location backend
python -m pytest tests/plan/test_source_invariants.py tests/plan/test_executor_capabilities.py tests/plan/test_plan_generator.py tests/plan/test_plan_service.py tests/plan/test_node_registry.py -q
```

Expected: all tests pass and a graph missing `source_search` is never `VALID`.

- [ ] **Step 6: Commit Task 2**

```powershell
git add backend/app/plan/capabilities.py backend/app/plan/validator.py backend/app/agents/plan_generator.py backend/app/agents/plan_service.py backend/app/api/routes/plans.py backend/app/plan/executors.py backend/app/discovery/executors.py backend/app/crawling/executors.py backend/app/extraction/executors.py backend/app/validation/executors.py backend/tests/plan/test_source_invariants.py backend/tests/plan/test_executor_capabilities.py backend/tests/plan/test_plan_service.py backend/tests/plan/test_plan_generator.py backend/tests/discovery/test_executor_binding.py
git commit -m "fix(plan): require materializable source paths" -m "为探索与混合计划强制来源搜索，并建立与测试 fixture 隔离的 Production executor capability manifest。`n`n关联模块：M-07、M-08、M-09。"
```

---

### Task 3: Persisted Execution Preflight

**Files:**
- Create: `backend/alembic/versions/0016_execution_readiness_progress.py`
- Create: `backend/app/plan/preflight.py`
- Create: `backend/app/plan/preflight_repository.py`
- Modify: `backend/app/domain/models.py`
- Modify: `backend/app/providers/repository.py`
- Test: `backend/tests/plan/test_execution_preflight.py`
- Test: `backend/tests/domain/test_models_roundtrip.py`
- Test: `backend/tests/ops/test_migration_single_head.py`

**Interfaces:**
- Consumes: frozen `CollectionSpecVersion`, `PlanVersion`, `SearchConfig`, `PRODUCTION_EXECUTOR_CAPABILITIES`, `Settings`.
- Produces: `ExecutionPreflightStatus`, `PreflightIssue`, `ExecutionPreflightOutcome`, `ExecutionPreflightService.evaluate(*, user_id, task_id, spec_version, plan_version)`, `ExecutionPreflightRepository.get_or_create(outcome)`, and ORM `ExecutionPreflightResult`.

- [ ] **Step 1: Write failing preflight tests**

Cover these exact outputs:

```python
def test_empty_specified_seed_is_blocked(preflight_case):
    outcome = preflight_case.service.evaluate(
        user_id=preflight_case.user.id,
        task_id=preflight_case.task.id,
        spec_version=preflight_case.spec.version,
        plan_version=preflight_case.plan.version,
    )
    assert outcome.status is ExecutionPreflightStatus.BLOCKED
    assert outcome.issue_codes == ("EXECUTION_INPUT_UNMATERIALIZABLE",)


def test_hybrid_freezes_available_search_config(hybrid_preflight_case):
    outcome = hybrid_preflight_case.service.evaluate(
        user_id=hybrid_preflight_case.user.id,
        task_id=hybrid_preflight_case.task.id,
        spec_version=hybrid_preflight_case.spec.version,
        plan_version=hybrid_preflight_case.plan.version,
    )
    assert outcome.status is ExecutionPreflightStatus.READY
    assert outcome.search_config_id == hybrid_preflight_case.search.config_id
    assert outcome.search_config_version == hybrid_preflight_case.search.version


def test_unsupported_node_is_blocked(preflight_case):
    service = ExecutionPreflightService(
        preflight_case.db,
        settings=preflight_case.settings,
        supported_nodes={NodeType.FETCH},
    )
    outcome = service.evaluate(
        user_id=preflight_case.user.id,
        task_id=preflight_case.task.id,
        spec_version=preflight_case.spec.version,
        plan_version=preflight_case.plan.version,
    )
    assert "EXECUTION_CAPABILITY_UNAVAILABLE" in outcome.issue_codes


def test_preflight_is_idempotent_per_frozen_versions_and_manifest(preflight_case):
    kwargs = {
        "user_id": preflight_case.user.id,
        "task_id": preflight_case.task.id,
        "spec_version": preflight_case.spec.version,
        "plan_version": preflight_case.plan.version,
    }
    first = preflight_case.service.evaluate(**kwargs)
    second = preflight_case.service.evaluate(**kwargs)
    assert first.result_id == second.result_id
    assert first.created is True
    assert second.created is False
    assert preflight_case.db.scalar(select(func.count(ExecutionPreflightResult.id))) == 1
```

Also test `TASK_QUEUE_ROUTE_UNAVAILABLE`, `FROZEN_CONFIG_UNAVAILABLE`, `ARTIFACT_STORAGE_UNAVAILABLE`, `PLAN_CONTEXT_MISMATCH` and non-owner access.

- [ ] **Step 2: Run tests and verify model/service do not exist**

```powershell
Set-Location backend
python -m pytest tests/plan/test_execution_preflight.py tests/domain/test_models_roundtrip.py tests/ops/test_migration_single_head.py -q
```

Expected: import/model failures for Execution Preflight.

- [ ] **Step 3: Add migration and ORM identity**

Migration `0016` has `down_revision = "0015"` and performs:

```text
create execution_preflight_results:
  id bigint/int primary key
  task_id FK tasks.id CASCADE, indexed
  user_id FK users.id CASCADE, indexed
  spec_version int not null
  plan_version int not null
  capability_manifest_version varchar(64) not null
  status varchar(20) not null
  issues JSON not null default []
  search_config_id varchar(32) nullable
  search_config_version int nullable
  created_at timezone datetime server default now
  unique(task_id, plan_version, capability_manifest_version)

alter node_runs:
  add node_id varchar(100) nullable
  unique(run_id, node_id)
```

`node_id` remains nullable for legacy Runs; all new lifecycle rows set it. Downgrade drops the unique constraints/column/table in reverse order.

- [ ] **Step 4: Implement typed preflight and repository**

Use these contracts:

```python
class ExecutionPreflightStatus(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"


class PreflightIssue(BaseModel):
    code: str
    safe_message: str
    remediation: str
    node_id: str | None = None
    field: str | None = None


class ExecutionPreflightOutcome(BaseModel):
    result_id: int
    created: bool
    status: ExecutionPreflightStatus
    task_id: int
    spec_version: int
    plan_version: int
    capability_manifest_version: str
    issues: list[PreflightIssue]
    search_config_id: str | None = None
    search_config_version: int | None = None

    @property
    def issue_codes(self) -> Sequence[str]:
        return tuple(sorted(issue.code for issue in self.issues))
```

`evaluate()` owner-loads Task/Spec/Plan, verifies all frozen identities, selects the first current owner search config with `connection_status == "available"`, checks the node/queue manifest, and checks non-empty S3 endpoint/bucket/access-key settings without reading or emitting the secret value. Repository uniqueness resolves concurrent inserts by rollback/reload of the existing row. `created` is true only for the transaction that inserts the fact; a reused or concurrently recovered row returns false.

- [ ] **Step 5: Run migration and preflight tests**

```powershell
Set-Location backend
python -m alembic upgrade head
python -m pytest tests/plan/test_execution_preflight.py tests/domain/test_models_roundtrip.py tests/ops/test_migration_single_head.py -q
python -m alembic downgrade 0015
python -m alembic upgrade head
```

Expected: tests pass, Alembic has exactly one head, upgrade/downgrade/upgrade succeeds.

- [ ] **Step 6: Commit Task 3**

```powershell
git add backend/alembic/versions/0016_execution_readiness_progress.py backend/app/domain/models.py backend/app/plan/preflight.py backend/app/plan/preflight_repository.py backend/app/providers/repository.py backend/tests/plan/test_execution_preflight.py backend/tests/domain/test_models_roundtrip.py backend/tests/ops/test_migration_single_head.py
git commit -m "feat(execution): add persisted preflight gate" -m "在冻结 Plan 与 Workflow start 之间持久化资源、配置、队列和 Production executor 能力检查。`n`n关联模块：M-07、M-08、M-16。"
```

---

### Task 4: Wire Preflight Into Every Workflow Start

**Files:**
- Modify: `backend/app/domain/errors.py`
- Modify: `backend/app/api/schemas.py`
- Modify: `backend/app/api/routes/plans.py`
- Modify: `backend/app/plan/service.py`
- Modify: `frontend/src/app/error/apiErrorMapper.ts`
- Modify: `frontend/src/features/tasks/plans.api.ts`
- Test: `backend/tests/api/test_plan_api.py`
- Test: `backend/tests/domain/test_plan_start_recovery.py`
- Test: `frontend/src/features/tasks/plans.api.test.ts`
- Test: `frontend/src/features/tasks/TaskChatView.test.ts`

**Interfaces:**
- Consumes: `ExecutionPreflightService.evaluate(*, user_id, task_id, spec_version, plan_version)` and frozen Plan/Spec versions.
- Produces: `ExecutionPreflightBlockedError(code="EXECUTION_PREFLIGHT_BLOCKED", status=409)`, response fields `preflight_status` and `preflight_issues`, and a start path that revalidates the exact READY identity.

- [ ] **Step 1: Write API tests proving Workflow start is gated**

```python
def test_generate_persists_plan_but_does_not_start_when_preflight_blocks(plan_api_case):
    response = plan_api_case.client.post(
        f"/tasks/{plan_api_case.task.id}/plan",
        json=plan_api_case.command,
        headers=plan_api_case.auth,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "EXECUTION_PREFLIGHT_BLOCKED"
    assert response.json()["detail"]["preflight_issues"][0]["code"] == "EXECUTION_INPUT_UNMATERIALIZABLE"
    assert plan_api_case.starter.calls == []
    assert PlanVersionRepository(plan_api_case.db).latest_version(
        plan_api_case.user.id, plan_api_case.task.id
    ) is not None
    assert RunRepository(plan_api_case.db).find_active_for_task(
        plan_api_case.user.id, plan_api_case.task.id
    ) is None


def test_retry_start_rechecks_same_plan_preflight_identity(ready_plan_api_case):
    response = ready_plan_api_case.client.post(
        f"/tasks/{ready_plan_api_case.task.id}/plans/1/start",
        headers=ready_plan_api_case.auth,
    )
    assert response.status_code == 200
    assert response.json()["preflight_status"] == "READY"
    assert ready_plan_api_case.starter.calls == [(ready_plan_api_case.task.id, 1, 1)]
```

Add cases for a changed Plan/Spec version, non-owner, Temporal RPC failure after READY, and repeated retry producing one active Run.

- [ ] **Step 2: Run backend/frontend tests to verify the missing gate**

```powershell
Set-Location backend
python -m pytest tests/api/test_plan_api.py tests/domain/test_plan_start_recovery.py -q
Set-Location ..\frontend
npm run test:unit -- src/features/tasks/plans.api.test.ts src/features/tasks/TaskChatView.test.ts
```

Expected: preflight response fields/code assertions fail and the starter is currently called without a preflight fact.

- [ ] **Step 3: Implement typed API error and response fields**

```python
class ExecutionPreflightBlockedError(DomainError):
    code = "EXECUTION_PREFLIGHT_BLOCKED"
    status_code = 409

    def __init__(self, message: str, *, context: dict[str, Any]) -> None:
        super().__init__(message)
        self.context = context

    def to_dict(self) -> dict[str, Any]:
        return {**super().to_dict(), **self.context}
```

Add to `PlanGenerateResponse` and `PlanSummaryDto`:

```python
preflight_status: str | None = None
preflight_issues: list[dict] = Field(default_factory=list)
```

Frontend `PlanSummaryDto` mirrors these fields. Map `EXECUTION_PREFLIGHT_BLOCKED` to kind `execution_preflight_blocked` and display the first server `safe_message` without exposing raw payload.

- [ ] **Step 4: Gate generated-plan and persisted-plan start paths**

After `persist_plan()` and before `prepare_start()`, call preflight. If BLOCKED and `outcome.created` is true, append `task.execution_preflight_blocked` with plan/spec version, manifest version and allowlisted issues, commit it with the preflight result, then raise the typed 409 without creating a Run. A reused result returns the same 409 without appending a duplicate event.

For READY, append `task.execution_preflight_ready` only when `outcome.created` is true, call `prepare_start()` using the exact checked versions, then dispatch Temporal. `start_persisted_plan()` runs the same service path; no route may call `prepare_start()` before READY.

Extend `PlanService.get_plan_summary()` to owner-load the matching preflight result and return its status/issues. Do not mutate the immutable PlanVersion payload.

- [ ] **Step 5: Run start-gate tests**

```powershell
Set-Location backend
python -m pytest tests/api/test_plan_api.py tests/domain/test_plan_start_recovery.py tests/plan/test_execution_preflight.py -q
Set-Location ..\frontend
npm run test:unit -- src/features/tasks/plans.api.test.ts src/features/tasks/TaskChatView.test.ts
```

Expected: all tests pass; BLOCKED produces no Run and READY still preserves RPC retry recovery.

- [ ] **Step 6: Commit Task 4**

```powershell
git add backend/app/domain/errors.py backend/app/api/schemas.py backend/app/api/routes/plans.py backend/app/plan/service.py backend/tests/api/test_plan_api.py backend/tests/domain/test_plan_start_recovery.py frontend/src/app/error/apiErrorMapper.ts frontend/src/features/tasks/plans.api.ts frontend/src/features/tasks/plans.api.test.ts frontend/src/features/tasks/TaskChatView.test.ts
git commit -m "fix(workflow): gate starts on execution readiness" -m "所有自动与重试启动路径必须复验同一冻结 Spec、Plan 和 capability manifest 的 READY 事实。`n`n关联模块：M-07、M-08。"
```

---

### Task 5: Real Generate Artifact Executor and Frozen Search Config

**Files:**
- Create: `backend/app/artifacts/executor.py`
- Modify: `backend/app/worker.py`
- Modify: `backend/app/discovery/source_search.py`
- Modify: `backend/app/plan/capabilities.py`
- Test: `backend/tests/artifacts/test_artifact_executor.py`
- Test: `backend/tests/discovery/test_source_search.py`
- Test: `backend/tests/plan/test_executor_capabilities.py`

**Interfaces:**
- Consumes: `ArtifactService.export(*, user_id, task_id, request)`, `ExportRequest`, `get_object_storage()`, preflight-frozen search config id/version.
- Produces: `install_artifact_executor()`, real `generate_artifact(unit) -> ExecuteUnitResult`, source search resolution by frozen config version, and `filter_named_source_results(results, source_hint)`.

- [ ] **Step 1: Write artifact executor tests**

```python
@pytest.mark.asyncio
async def test_generate_artifact_uses_real_service_and_is_idempotent(artifact_executor_case):
    unit = artifact_executor_case.unit(
        run_id=artifact_executor_case.run.id,
        node_id="n7",
        node_type="generate_artifact",
    )
    first = await artifact_executor_case.execute(unit)
    second = await artifact_executor_case.execute(unit)
    assert first.status == second.status == "OK"
    assert first.committed_refs["artifact_id"] == second.committed_refs["artifact_id"]
    assert first.committed_refs["row_count"] == 1
    assert len(artifact_executor_case.storage.objects) == 1


@pytest.mark.asyncio
async def test_generate_artifact_binds_output_to_run_owner(artifact_executor_case):
    unit = artifact_executor_case.unit(
        run_id=artifact_executor_case.other_owner_run.id,
        node_id="n7",
        node_type="generate_artifact",
    )
    result = await artifact_executor_case.execute(unit)
    artifact = artifact_executor_case.load_artifact(result.committed_refs["artifact_id"])
    assert artifact.user_id == artifact_executor_case.other_owner_run.user_id
    assert artifact.task_id == artifact_executor_case.other_owner_run.task_id
```

Add a headers-only/zero-record assertion matching current `ArtifactService` behavior, and a storage exception assertion that returns/raises a typed `STORAGE_ERROR` rather than fixed success.

- [ ] **Step 2: Write frozen search config test**

Create search config v1, persist READY preflight referencing v1, rotate current config to v2, execute `source_search`, and assert the provider/base URL/credential reference comes from v1. A missing frozen version must raise `FROZEN_CONFIG_UNAVAILABLE` without falling back to current default.

Add a `NAMED_SOURCE_ONLY` case whose provider returns one result titled `山东省人民政府` and one unrelated commercial site. Assert only the result whose normalized title contains the frozen first source hint is inserted into the frontier. When no result title/snippet contains that hint, assert `candidate_sites=0` and no arbitrary host is inserted.

- [ ] **Step 3: Run tests to verify artifact and frozen config gaps**

```powershell
Set-Location backend
python -m pytest tests/artifacts/test_artifact_executor.py tests/discovery/test_source_search.py tests/plan/test_executor_capabilities.py -q
```

Expected: artifact executor import/registry failure and source search currently selects the current config.

- [ ] **Step 4: Implement and install the real executor**

```python
async def generate_artifact(unit: ExecutionUnit) -> ExecuteUnitResult:
    session = get_session_factory()()
    try:
        run = session.get(Run, unit.run_id)
        if run is None:
            return ExecuteUnitResult(
                unit_index=unit.index,
                status="FAILED",
                error_code="RUN_NOT_FOUND",
                committed_refs={},
            )
        TaskRepository(session).get_owned(run.user_id, run.task_id)
        ref = await ArtifactService(session, get_object_storage()).export(
            user_id=run.user_id,
            task_id=run.task_id,
            request=ExportRequest(export_type=ExportType.FORMAL, scope=ExportScope.ALL),
        )
        return ExecuteUnitResult(
            unit_index=unit.index,
            status="OK",
            committed_refs={
                "task_id": run.task_id,
                "run_id": run.id,
                "node_id": unit.node_id,
                "node_type": unit.node_type,
                "artifact_id": ref.artifact_id,
                "row_count": ref.row_count,
                "content_hash": ref.content_hash,
            },
        )
    finally:
        session.close()


def install_artifact_executor() -> None:
    register_node_executor(NodeType.GENERATE_ARTIFACT, generate_artifact)
```

Use an owner-safe Run repository method instead of accepting an owner from model parameters. Do not place download URL or file bytes in committed refs/events.

Install artifact executor with the other real installers, then call `assert_runtime_executor_manifest()` in worker startup.

- [ ] **Step 5: Resolve SourceSearch from preflight-frozen config**

`SearchService._require_config(run)` loads the READY `ExecutionPreflightResult` for `run.task_id/run.plan_version`, requires non-null search id/version for search nodes, then calls `SearchConfigRepository.get_version(run.user_id, id, version)`. It never calls `_available_search_config()` in a frozen Run.

For a frozen Spec with `resolution_scope="NAMED_SOURCE_ONLY"`, load the first non-empty `source_hints` value as the authoritative named source. Normalize whitespace/punctuation and remove only the terminal generic suffixes `官方网站`, `官网`, `网站`; then keep a search result only when the complete remaining hint is present in its title or snippet. This conservative rule may return no candidate, but it cannot silently expand to an unrelated host. Ordinary `EXPLORATORY` search keeps the existing result behavior.

- [ ] **Step 6: Run executor tests**

```powershell
Set-Location backend
python -m pytest tests/artifacts/test_artifact_executor.py tests/artifacts/test_artifact_service.py tests/discovery/test_source_search.py tests/plan/test_executor_capabilities.py -q
```

Expected: idempotency, ownership, storage error, manifest and frozen-config tests all pass.

- [ ] **Step 7: Commit Task 5**

```powershell
git add backend/app/artifacts/executor.py backend/app/worker.py backend/app/discovery/source_search.py backend/app/plan/capabilities.py backend/tests/artifacts/test_artifact_executor.py backend/tests/discovery/test_source_search.py backend/tests/plan/test_executor_capabilities.py
git commit -m "feat(artifact): execute plan exports for real" -m "将 generate_artifact 绑定到幂等 ArtifactService，并让来源搜索使用 Preflight 冻结的配置版本。`n`n关联模块：M-08、M-09、M-15。"
```

---

### Task 6: Persist NodeRun, NodeAttempt and Lifecycle Events

**Files:**
- Create: `backend/app/execution/lifecycle.py`
- Modify: `backend/app/domain/repository.py`
- Modify: `backend/app/activities/plan_execution.py`
- Modify: `backend/app/activities/execution_seam.py`
- Modify: `backend/app/activities/task_execution.py`
- Test: `backend/tests/execution/test_lifecycle_recorder.py`
- Test: `backend/tests/activities/test_plan_execution.py`
- Test: `backend/tests/activities/test_task_execution.py`

**Interfaces:**
- Consumes: new `NodeRun.node_id`, Temporal `activity.info().attempt`, `ExecuteUnitInput`, `ExecuteUnitResult`, `append_domain_event()`.
- Produces: `ExecutionLifecycleRecorder.start_attempt(run_id, unit, attempt)`, `finish_attempt(run_id, unit, attempt, status, committed_refs, error_code)`, `checkpoint_committed(checkpoint)`, stable lifecycle event payload schema version 1.

- [ ] **Step 1: Write lifecycle recorder tests**

```python
def test_start_attempt_is_idempotent_for_run_node_attempt(lifecycle_case):
    first = lifecycle_case.recorder.start_attempt(
        run_id=lifecycle_case.run.id, unit=lifecycle_case.unit, attempt=1
    )
    second = lifecycle_case.recorder.start_attempt(
        run_id=lifecycle_case.run.id, unit=lifecycle_case.unit, attempt=1
    )
    assert first.node_run_id == second.node_run_id
    assert first.node_attempt_id == second.node_attempt_id
    assert lifecycle_case.event_types() == ["run.node_started"]


def test_finish_attempt_records_allowlisted_counts(lifecycle_case):
    lifecycle_case.recorder.finish_attempt(
        run_id=lifecycle_case.run.id,
        unit=lifecycle_case.unit,
        attempt=1,
        status="SUCCEEDED",
        committed_refs={"fetched": 3, "authorization": "secret", "html": "private"},
        error_code=None,
    )
    event = lifecycle_case.last_event()
    assert event.event_type == "run.node_completed"
    assert event.payload["counts"] == {"fetched": 3}
    assert "authorization" not in str(event.payload)
    assert "private" not in str(event.payload)
```

Add retry attempt 2, failed attempt, duplicate terminal, and checkpoint-event ordering assertions.

- [ ] **Step 2: Run tests to verify real Workflow creates no NodeRun**

```powershell
Set-Location backend
python -m pytest tests/execution/test_lifecycle_recorder.py tests/activities/test_plan_execution.py tests/activities/test_task_execution.py -q
```

Expected: lifecycle module is absent or NodeRun/NodeAttempt/event counts are zero.

- [ ] **Step 3: Implement repository identity and lifecycle contracts**

Add owner-scoped repository methods:

```python
NodeRunRepository.get_or_create(
    *, user_id: int, run_id: int, task_id: int, node_id: str,
    node_type: str, position: int, input_fingerprint: str
) -> NodeRun

NodeAttemptRepository.get_or_create(
    *, user_id: int, node_run_id: int, attempt: int
) -> NodeAttempt
```

`ExecutionLifecycleRecorder` uses one transaction per lifecycle command. Event types and terminal mapping are fixed:

```text
run.node_started
run.node_progress
run.checkpoint_committed
run.node_completed
run.node_blocked
run.node_failed
```

All events use `aggregate_type="task"`, `aggregate_id=task_id`, `actor_type="system"`, and set `run_id/node_run_id`. Payload contains only schema version, task/run/plan, node id/type, attempt, state, timestamps, allowlisted numeric counts, reason code and safe message.

- [ ] **Step 4: Wrap every real `execute_safe_unit` attempt**

After resource admission succeeds, obtain Temporal attempt with a safe fallback for direct unit tests:

```python
try:
    attempt = activity.info().attempt
except RuntimeError:
    attempt = 1
```

Call `start_attempt` before registry lookup. On normal `ExecuteUnitResult`, map `OK` to SUCCEEDED, waiting/approval outcomes to the matching nonterminal/block state, and `FAILED`/`NODE_EXECUTOR_UNAVAILABLE` to FAILED. On exception, record a safe typed failure and re-raise so Temporal retry policy remains authoritative.

Extend `ExecuteUnitResult` with optional `safe_message: str | None = None`; do not add arbitrary raw payload.

- [ ] **Step 5: Emit Run and checkpoint facts**

In `ensure_run_started`, after seed ingestion and Run activation, append idempotent `run.started` with seed count and versions. In `commit_checkpoint`, after the checkpoint row is created, append one `run.checkpoint_committed`; replay returning an existing checkpoint must not append another event.

- [ ] **Step 6: Run lifecycle and secret-scan tests**

```powershell
Set-Location backend
python -m pytest tests/execution/test_lifecycle_recorder.py tests/activities/test_plan_execution.py tests/activities/test_task_execution.py -q
```

Expected: event ordering is deterministic, retries create distinct attempts, semantic events are idempotent, and forbidden payload values are absent.

- [ ] **Step 7: Commit Task 6**

```powershell
git add backend/app/execution/lifecycle.py backend/app/domain/repository.py backend/app/activities/plan_execution.py backend/app/activities/execution_seam.py backend/app/activities/task_execution.py backend/tests/execution/test_lifecycle_recorder.py backend/tests/activities/test_plan_execution.py backend/tests/activities/test_task_execution.py
git commit -m "feat(execution): persist node lifecycle facts" -m "为每个真实节点 attempt、checkpoint 与终态写入幂等 NodeRun、NodeAttempt 和安全 DomainEvent。`n`n关联模块：M-04、M-08、M-14。"
```

---

### Task 7: Correct Runtime Failure and Completion Semantics

**Files:**
- Modify: `backend/app/workflows/task_workflow.py`
- Modify: `backend/app/activities/task_execution.py`
- Modify: `backend/app/validation/completion.py`
- Modify: `backend/app/activities/completion.py`
- Modify: `backend/app/domain/models.py`
- Test: `backend/tests/integration/test_task_workflow.py`
- Test: `backend/tests/validation/test_completion.py`
- Test: `backend/tests/activities/test_completion.py`

**Interfaces:**
- Consumes: `ExecuteUnitResult.status/error_code`, persisted URL/record facts, lifecycle events.
- Produces: runtime `FAILED/NODE_EXECUTOR_UNAVAILABLE`, `NO_MATCHING_PAGES`, `NO_MATCHING_RECORDS`, and strict partial predicate.

- [ ] **Step 1: Write completion decision tests**

```python
def test_zero_eligible_urls_is_empty_success_not_partial() -> None:
    result = decide(task_type="HYBRID", eligible=0, terminal=0, fetched=0, records=0)
    assert result.status == "NORMAL_COMPLETED"
    assert result.completion_type == "NO_MATCHING_PAGES"
    assert result.is_partial is False


def test_processed_pages_without_records_is_empty_record_success() -> None:
    result = decide(task_type="SPECIFIED_SOURCE", eligible=3, terminal=3, fetched=3, records=0)
    assert result.status == "NORMAL_COMPLETED"
    assert result.completion_type == "NO_MATCHING_RECORDS"


def test_zero_over_zero_never_becomes_partial() -> None:
    result = decide(task_type="SPECIFIED_SOURCE", eligible=0, terminal=0, fetched=0, records=0)
    assert result.is_partial is False


def test_real_subset_stopped_by_access_limit_is_partial() -> None:
    result = decide(task_type="SPECIFIED_SOURCE", eligible=5, terminal=2, fetched=2, records=1)
    assert result.status == "PARTIALLY_COMPLETED"
    assert result.completion_type == "access_limited"
```

Add runtime limit and user stop cases that require non-empty completed work before partial.

- [ ] **Step 2: Write Workflow test for executor unavailability**

The fixture returns `ExecuteUnitResult(status="NODE_EXECUTOR_UNAVAILABLE", error_code="NODE_EXECUTOR_UNAVAILABLE")`. Assert Workflow calls `fail_run` once, returns `FAILED`, does not call `block_high_risk_node`, does not call `resolve_completion`, and persists `run.node_failed`/`task.fail` with the typed reason.

- [ ] **Step 3: Run tests to reproduce the incorrect partial behavior**

```powershell
Set-Location backend
python -m pytest tests/validation/test_completion.py tests/activities/test_completion.py tests/integration/test_task_workflow.py -q
```

Expected: zero/zero is currently partial and executor unavailable currently continues.

- [ ] **Step 4: Implement failure-first Workflow semantics**

Replace the `block_high_risk_node` branch for `NODE_EXECUTOR_UNAVAILABLE` with typed failure:

```python
if exec_result.status == "NODE_EXECUTOR_UNAVAILABLE":
    await workflow.execute_activity(
        fail_run,
        FailRunInput(
            task_id=inp.task_id,
            user_id=inp.user_id,
            run_id=inp.run_id,
            error_code="NODE_EXECUTOR_UNAVAILABLE",
        ),
        start_to_close_timeout=timedelta(seconds=60),
    )
    return TaskWorkflowResult(inp.task_id, inp.run_id, "FAILED")
```

Extend `FailRunInput` with `error_code: str | None`; include it only as a stable code/safe message in `task.fail` and `run.failed` facts. Approval rejection remains its existing distinct block/cancel path.

Update the existing terminal Activities so each writes one idempotent task-aggregate lifecycle fact together with its state transition: `complete_run → run.completed`, `mark_partial → run.partially_completed`, `fail_run → run.failed`, and `mark_cancelled → run.cancelled`. Replay that finds the Run already in the same terminal state returns without appending another terminal event.

- [ ] **Step 5: Implement exhaustive completion order**

Pass `fetched_page_count` and `record_count` into `CompletionDecisionService.decide()`. Evaluate in this order:

1. unrecoverable error is handled before completion and never calls decide;
2. `eligible=0 && fetched=0` → `NORMAL_COMPLETED/NO_MATCHING_PAGES`;
3. scope done and `fetched>0 && record_count=0` → `NORMAL_COMPLETED/NO_MATCHING_RECORDS`;
4. actual runtime/user/access limit with completed work > 0 → partial;
5. specified scope fully terminal → normal completed;
6. exploratory/hybrid saturation rules;
7. remaining non-empty incomplete scope → partial.

Persist `CompletionDecision.completion_type` exactly as these stable values. No new TaskState or database column is needed.

- [ ] **Step 6: Run completion and Workflow tests**

```powershell
Set-Location backend
python -m pytest tests/validation/test_completion.py tests/activities/test_completion.py tests/integration/test_task_workflow.py tests/artifacts/test_completion_card.py -q
```

Expected: all tests pass; no `eligible=0 && partial` result remains.

- [ ] **Step 7: Commit Task 7**

```powershell
git add backend/app/workflows/task_workflow.py backend/app/activities/task_execution.py backend/app/validation/completion.py backend/app/activities/completion.py backend/app/domain/models.py backend/tests/integration/test_task_workflow.py backend/tests/validation/test_completion.py backend/tests/activities/test_completion.py
git commit -m "fix(completion): distinguish empty results from failures" -m "执行器缺失立即失败，零候选与零匹配使用完成型空结果，partial 仅表达真实非空子集。`n`n关联模块：M-07、M-08、M-12、M-15。"
```

---

### Task 8: Canonical Execution Events, Query Snapshot and SSE Replay

**Files:**
- Create: `backend/app/observability/execution_metrics.py`
- Modify: `backend/app/api/events.py`
- Modify: `backend/app/api/routes/events.py`
- Modify: `backend/app/plan/preflight.py`
- Modify: `backend/app/execution/lifecycle.py`
- Modify: `backend/app/activities/task_execution.py`
- Modify: `backend/app/execution/contracts.py`
- Modify: `backend/app/execution/repository.py`
- Modify: `backend/app/execution/service.py`
- Modify: `backend/app/api/routes/execution.py`
- Test: `backend/tests/api/test_task_events.py`
- Test: `backend/tests/execution/test_execution_api.py`
- Test: `backend/tests/execution/test_dag_api.py`
- Test: `backend/tests/observability/test_execution_metrics.py`

**Interfaces:**
- Consumes: canonical `run.*` DomainEvents and NodeRun/NodeAttempt facts.
- Produces: expanded `ExecutionView` snapshot, allowlisted `TimelineEvent`, SSE event names for preflight/run/node/checkpoint lifecycle, and low-cardinality execution metrics.

- [ ] **Step 1: Write backend event and snapshot tests**

```python
def test_execution_snapshot_uses_node_facts(execution_case):
    view = execution_case.service.assemble_overview(
        user_id=execution_case.user.id,
        task_id=execution_case.task.id,
    )
    assert view.current_node.node_id == "n3"
    assert view.last_successful_node.node_id == "n2"
    assert view.last_event_id == 17
    assert view.last_activity_at == execution_case.node_attempt.started_at
    assert view.counts.fetched_pages == 4


def test_sse_replays_canonical_node_events_after_cursor(execution_case):
    events = query_task_events(
        execution_case.db,
        execution_case.user.id,
        execution_case.task.id,
        after_id=10,
    )
    mapped = [map_domain_event_to_sse(event) for event in events]
    assert [event.event_type for event in mapped] == [
        "NODE_COMPLETED", "CHECKPOINT_COMMITTED", "NODE_STARTED"
    ]
    assert [event.event_id for event in mapped] == sorted(event.event_id for event in mapped)
```

Add owner isolation, unknown payload field removal, `Last-Event-ID` precedence over `after_id`, and no duplicate replay tests.

Create metric tests with a fake meter and assert only stable labels are accepted:

```python
def test_execution_metrics_use_only_stable_labels(fake_meter) -> None:
    metrics = ExecutionMetrics(fake_meter)
    metrics.record_preflight(status="BLOCKED", issue_codes=["SOURCE_RESOLUTION_REQUIRED"])
    metrics.record_node_terminal(
        node_type="fetch", state="FAILED", reason_code="NETWORK_TIMEOUT"
    )
    assert fake_meter.attribute_keys() <= {"status", "issue_code", "node_type", "state", "reason_code"}
    assert "task_id" not in fake_meter.attribute_keys()
    assert "url" not in fake_meter.attribute_keys()
```

- [ ] **Step 2: Run tests to verify generic TASK_STATE_CHANGED mapping**

```powershell
Set-Location backend
python -m pytest tests/api/test_task_events.py tests/execution/test_execution_api.py tests/execution/test_dag_api.py tests/observability/test_execution_metrics.py -q
```

Expected: canonical run/node names and current-node snapshot fields are absent.

- [ ] **Step 3: Extend typed execution snapshot**

Add:

```python
class ExecutionNodeSummary(BaseModel):
    node_id: str
    node_type: str
    label: str
    state: str
    attempt: int
    safe_message: str | None = None


class ExecutionCounts(BaseModel):
    discovered_pages: int = 0
    fetched_pages: int = 0
    extracted_records: int = 0
    validated_records: int = 0


class ExecutionView(BaseModel):
    task_id: int
    run: RunSummary | None = None
    stages: list[StageSummary] = Field(default_factory=list)
    urls: dict[str, int] = Field(default_factory=dict)
    records: dict[str, int] = Field(default_factory=dict)
    plan: PlanBrief | None = None
    current_node: ExecutionNodeSummary | None = None
    last_successful_node: ExecutionNodeSummary | None = None
    last_activity_at: datetime | None = None
    last_event_id: int = 0
    counts: ExecutionCounts = Field(default_factory=ExecutionCounts)
    waiting_reason_code: str | None = None
    outcome_code: str | None = None
    legacy_execution_facts: bool = False
```

Repository adds owner-scoped latest NodeRun/Attempt and max DomainEvent id queries. Service derives node facts from NodeRun/Attempt first; legacy Runs without node rows set `legacy_execution_facts=true` and keep the existing event/stage projection without inventing nodes.

- [ ] **Step 4: Add canonical SSE mappings and safe payload projection**

Map:

```text
task.execution_preflight_blocked → EXECUTION_PREFLIGHT_BLOCKED
discovery.candidates_found → SOURCE_CANDIDATES_FOUND
discovery.expanded → LINKS_DISCOVERED
run.started → RUN_STARTED
run.node_started → NODE_STARTED
run.node_progress → NODE_PROGRESS
run.checkpoint_committed → CHECKPOINT_COMMITTED
run.node_completed → NODE_COMPLETED
run.node_blocked → NODE_BLOCKED
run.node_failed → NODE_FAILED
run.completed → RUN_COMPLETED
run.partially_completed → RUN_PARTIALLY_COMPLETED
run.failed → RUN_FAILED
run.cancelled → RUN_CANCELLED
```

`map_domain_event_to_sse()` builds a new payload dict from an explicit allowlist; it must not return `ev.payload` wholesale. Existing fetch/extraction/validation/record mappings remain compatible.

- [ ] **Step 5: Implement low-cardinality execution metrics**

Create `ExecutionMetrics` around OpenTelemetry instruments with these exact methods:

```python
record_preflight(*, status: str, issue_codes: Sequence[str]) -> None
record_node_terminal(*, node_type: str, state: str, reason_code: str | None) -> None
record_run_terminal(*, state: str, outcome_code: str | None) -> None
record_sse_replay(*, count: int) -> None
change_sse_connections(*, delta: int) -> None
record_invariant_violation(*, invariant: str) -> None
```

Allowed metric attributes are stable status/issue/node type/state/reason/outcome/invariant only. Task id, user id, title, prompt, provider credential, URL and exception text are forbidden labels. Instrument preflight outcomes, node/run terminal writes, `eligible_zero_partial` invariant detection, SSE replay count and SSE connection open/close. Wrap the SSE generator in `try/finally` so active connection count is decremented on disconnect.

- [ ] **Step 6: Run execution API/SSE/metrics tests**

```powershell
Set-Location backend
python -m pytest tests/api/test_task_events.py tests/execution/test_execution_api.py tests/execution/test_dag_api.py tests/observability/test_execution_metrics.py tests/review/test_record_events_mapping.py -q
```

Expected: snapshot, ordering, replay, ownership and payload redaction tests pass.

- [ ] **Step 7: Commit Task 8**

```powershell
git add backend/app/observability/execution_metrics.py backend/app/api/events.py backend/app/api/routes/events.py backend/app/plan/preflight.py backend/app/execution/lifecycle.py backend/app/activities/task_execution.py backend/app/execution/contracts.py backend/app/execution/repository.py backend/app/execution/service.py backend/app/api/routes/execution.py backend/tests/api/test_task_events.py backend/tests/execution/test_execution_api.py backend/tests/execution/test_dag_api.py backend/tests/observability/test_execution_metrics.py
git commit -m "feat(events): expose replayable execution progress" -m "将持久化 Run/Node 事实投影为安全 Execution snapshot、时间线和 owner-scoped SSE 增量。`n`n关联模块：M-07、M-14。"
```

---

### Task 9: Task Chat Snapshot + SSE Progress Panel

**Files:**
- Create: `frontend/src/features/execution/ExecutionProgressPanel.vue`
- Create: `frontend/src/features/execution/ExecutionProgressPanel.test.ts`
- Modify: `frontend/src/features/execution/types.ts`
- Modify: `frontend/src/features/execution/useExecution.ts`
- Modify: `frontend/src/features/tasks/events.api.ts`
- Modify: `frontend/src/features/tasks/useTaskEvents.ts`
- Modify: `frontend/src/features/tasks/TaskChatView.vue`
- Modify: `frontend/src/features/tasks/TaskChatView.test.ts`
- Test: `frontend/src/features/tasks/taskEvents.test.ts`
- Test: `frontend/src/features/execution/execution.api.test.ts`

**Interfaces:**
- Consumes: `GET /tasks/{id}/execution`, `GET /tasks/{id}/execution/timeline`, Task SSE canonical events.
- Produces: `ExecutionProgressPanel`, `useExecution.refreshSnapshot()`, event-id dedupe/reconcile behavior and Chinese node/outcome labels.

- [ ] **Step 1: Write event subscription tests**

Extend `TaskEventType` and `_EVENT_TYPES` with every canonical name from Task 8 plus the backend event names that already exist but are currently omitted by the frontend: `SOURCE_CANDIDATES_FOUND`, `LINKS_DISCOVERED`, `FETCH_STARTED`, `FETCH_STRATEGY_SELECTED`, `BROWSER_ESCALATION`, `CREDENTIAL_REQUIRED`, `FETCH_COMPLETED`, `FETCH_FAILED`, `EXTRACTION_STARTED`, `EXTRACTION_PROGRESS`, `LLM_FALLBACK_USED`, `RULE_PROMOTED`, `EXTRACTION_COMPLETED`, `EXTRACTION_FAILED`, `NORMALIZE_COMPLETED`, `VALIDATION_STARTED`, `VALIDATION_PROGRESS`, `DEDUPE_COMPLETED`, and `VALIDATION_COMPLETED`. Tests trigger `NODE_STARTED`, `NODE_COMPLETED`, `FETCH_COMPLETED`, `VALIDATION_COMPLETED`, `RUN_FAILED`, and `EXECUTION_PREFLIGHT_BLOCKED`; each must update `latestEvent`. Send the same event id twice and assert the second is ignored. Send a lower id and assert it is ignored.

- [ ] **Step 2: Write progress panel tests**

```typescript
it('先显示历史 snapshot，再合并 SSE 增量且不重复', async () => {
  mocks.execution.current_node = node('n3', 'fetch', 'RUNNING')
  const wrapper = mount(ExecutionProgressPanel, { props: { taskId: '25' } })
  await flushPromises()
  expect(wrapper.text()).toContain('抓取页面')
  expect(wrapper.text()).toContain('已抓取 4')

  mocks.emit(event(18, 'NODE_COMPLETED', { node_id: 'n3', node_type: 'fetch' }))
  mocks.emit(event(18, 'NODE_COMPLETED', { node_id: 'n3', node_type: 'fetch' }))
  await flushPromises()
  expect(mocks.refreshSnapshot).toHaveBeenCalledTimes(1)
})
```

Add tests for last activity, last successful node, waiting reason, `NODE_EXECUTOR_UNAVAILABLE` as system failure, `NO_MATCHING_PAGES`/`NO_MATCHING_RECORDS` as completed empty results, reconnecting → open reconcile, refresh preservation, and absence of percentages/reasoning.

- [ ] **Step 3: Run frontend tests to verify missing event types/panel**

```powershell
Set-Location frontend
npm run test:unit -- src/features/tasks/taskEvents.test.ts src/features/execution/ExecutionProgressPanel.test.ts src/features/tasks/TaskChatView.test.ts
```

Expected: component import and canonical event listener tests fail.

- [ ] **Step 4: Implement monotonic event handling and snapshot refresh**

`useTaskEvents.handleEvent()` returns early when `ev.event_id <= (lastEventId.value ?? 0)`. On open after `reconnecting`, expose a monotonically increasing `reconcileVersion` so consumers refresh exactly once per recovered connection.

Extend `useExecution` with:

```typescript
refreshSnapshot: () => Promise<void>
mergeTimelineEvent: (event: TimelineEvent) => void
```

`mergeTimelineEvent` dedupes by `event_id` and sorts ascending. Snapshot fetch remains authoritative; SSE is a refresh/delta trigger.

- [ ] **Step 5: Implement the compact panel**

Node labels are fixed:

```typescript
const NODE_LABELS: Record<string, string> = {
  source_search: '解析指定来源',
  access_rules_check: '检查访问规则',
  link_discovery: '发现页面链接',
  fetch: '抓取页面',
  browser_render: '渲染动态页面',
  extract: '提取字段',
  normalize: '规范化数据',
  deduplicate: '去重与冲突检查',
  validate: '验证记录',
  generate_artifact: '生成结果文件',
}
```

Render current node, last successful node, `上次活动于…`, four factual counts, waiting/failure/outcome safe message, and recent bounded timeline. Never calculate a percentage. While no backend failure exists, stale time only renders the last-activity phrase.

Connect SSE on mount, refresh snapshot on every relevant canonical event, reconcile once after reconnect open, disconnect on unmount, and reset state when `taskId` changes.

- [ ] **Step 6: Embed below Plan summary in Task Chat**

Replace the bare `运行状态：{{ planSummary.run_state }}` line with:

```vue
<ExecutionProgressPanel
  v-if="planSummary?.run_id || planSummary?.preflight_status === 'BLOCKED'"
  :task-id="taskId"
/>
```

Render preflight issues separately from Validator codes:

```vue
<ul v-if="planSummary?.preflight_issues.length" class="plan-issues">
  <li v-for="issue in planSummary.preflight_issues" :key="`${issue.code}-${issue.node_id ?? ''}`">
    {{ issue.safe_message }}
    <span v-if="issue.remediation"> · {{ issue.remediation }}</span>
  </li>
</ul>
```

Keep Plan validator issues and retry buttons. A preflight-blocked task shows its actionable source/config/capability message without an execution timeline pretending a Run exists.

- [ ] **Step 7: Run frontend unit/type/lint checks**

```powershell
Set-Location frontend
npm run test:unit -- src/features/tasks/taskEvents.test.ts src/features/execution/ExecutionProgressPanel.test.ts src/features/tasks/TaskChatView.test.ts src/features/execution/execution.api.test.ts
npm run type-check
npm run lint:check
npm run format:check
```

Expected: all commands exit 0.

- [ ] **Step 8: Commit Task 9**

```powershell
git add frontend/src/features/execution/ExecutionProgressPanel.vue frontend/src/features/execution/ExecutionProgressPanel.test.ts frontend/src/features/execution/types.ts frontend/src/features/execution/useExecution.ts frontend/src/features/tasks/events.api.ts frontend/src/features/tasks/useTaskEvents.ts frontend/src/features/tasks/TaskChatView.vue frontend/src/features/tasks/TaskChatView.test.ts frontend/src/features/tasks/taskEvents.test.ts frontend/src/features/execution/execution.api.test.ts
git commit -m "feat(ui): show live execution progress in task chat" -m "以 Execution snapshot 为事实源并通过 SSE 增量刷新，展示节点、计数、上次活动和 typed outcome。`n`n关联模块：M-05、M-14。"
```

---

### Task 10: Integrated Regression, Decisions and Release Acceptance

**Files:**
- Modify: `backend/tests/integration/test_plan_workflow.py`
- Modify: `backend/tests/integration/test_m09_discovery_workflow.py`
- Modify: `backend/tests/integration/test_m12_validation_workflow.py`
- Modify: `agent-business-logic-log.md`
- Modify: `agent-project-implementation-plan.md`
- Create: `infra/scripts/execution-readiness-staging-acceptance.py`
- Create: `frontend/e2e/execution-progress.spec.ts`
- Create: `docs/audits/execution-readiness-progress-verification-2026-08-15.md`

**Interfaces:**
- Consumes: all Tasks 1–9 interfaces.
- Produces: a real end-to-end regression, `execution-readiness-staging-acceptance.py --base-url --username-env --password-env --output`, browser progress acceptance, decision record, Staging evidence, PR/CI evidence and Production release manifest evidence.

- [ ] **Step 1: Add an integrated Task 25 regression**

The integration test builds the same semantic input (`山东省人民政府官网` without literal URL), an available fake search adapter that returns one official-site URL, and real discovery/fetch/extract/validate/artifact test infrastructure. Assert:

```python
assert frozen_spec.payload["task_type"] == "HYBRID"
assert graph.nodes[0].node_type == "source_search"
assert preflight.status == "READY"
assert workflow_result.final_state in {"COMPLETED", "PARTIALLY_COMPLETED"}
assert node_run_count == len(graph.nodes)
assert artifact_count == 1
assert not (completion.is_partial and eligible_urls == 0 and terminal_urls == 0)
assert _ordered_events() == sorted(_ordered_events())
```

Add a no-candidate test with `COMPLETED/NO_MATCHING_PAGES` and an executor-manifest drift test blocked before Run creation.

- [ ] **Step 2: Run scoped backend integration tests**

With local PostgreSQL, Temporal and object storage available:

```powershell
Set-Location backend
$env:KAIROS_RUN_INTEGRATION='1'
python -m pytest tests/integration/test_plan_workflow.py tests/integration/test_m09_discovery_workflow.py tests/integration/test_m12_validation_workflow.py -q
Remove-Item Env:KAIROS_RUN_INTEGRATION
```

Expected: real Workflow tests pass and Temporal history contains no secret/page-body payload.

- [ ] **Step 3: Update authoritative decision and implementation logs**

Append one decision entry that records:

- source contract and named-source-only HYBRID resolution;
- Plan VALID versus Preflight READY;
- Production executor manifest and real artifact executor;
- empty-success/failure/partial semantics;
- canonical lifecycle DomainEvents and Task Chat snapshot + SSE;
- no chain-of-thought and owner boundary.

Update relevant M-06–M-18 acceptance rows with factual implementation/test evidence only; do not mark Staging/Production complete before those gates run.

Create the Staging acceptance script with required CLI `--base-url` and `--output`, and credential environment-variable names defaulting to `KAIROS_ACCEPTANCE_USERNAME` / `KAIROS_ACCEPTANCE_PASSWORD`. It logs in through the public API, creates the four scenario tasks, polls only bounded snapshot/terminal endpoints, and writes a secret-free JSON report containing task/run/workflow ids, event ids/types, counts, outcome codes and artifact download status. It exits nonzero on missing progress events, `NODE_EXECUTOR_UNAVAILABLE`, `eligible=0 && partial`, ownership leakage or timeout.

Create `frontend/e2e/execution-progress.spec.ts` against `PLAYWRIGHT_BASE_URL`. It opens the Task Chat for a task id created by the acceptance script, asserts historical current/last node and counts, reloads, disconnects/reconnects the page context, and asserts the same event ids are not duplicated. It verifies `NO_MATCHING_PAGES` and `NO_MATCHING_RECORDS` are rendered as completed empty outcomes and captures screenshots through the existing Playwright output configuration.

- [ ] **Step 4: Run full local verification**

Use the workspace dependency runtime if system executables are unavailable. Run:

```powershell
Set-Location backend
python -m pytest -q
python -m ruff check app tests
python -m mypy app tests
python -m alembic heads
Set-Location ..\frontend
npm run test:unit
npm run type-check
npm run lint:check
npm run format:check
npm run build
Set-Location ..
git diff --check
git status --short --branch
```

Expected: every command exits 0; `alembic heads` prints only `0016 (head)`; status contains no accidentally staged user-owned `infra/scripts/_*.py`.

- [ ] **Step 5: Commit integrated tests and docs**

```powershell
git add backend/tests/integration/test_plan_workflow.py backend/tests/integration/test_m09_discovery_workflow.py backend/tests/integration/test_m12_validation_workflow.py infra/scripts/execution-readiness-staging-acceptance.py frontend/e2e/execution-progress.spec.ts agent-business-logic-log.md agent-project-implementation-plan.md docs/audits/execution-readiness-progress-verification-2026-08-15.md
git commit -m "test(workflow): verify observable execution readiness" -m "覆盖 Task 25 等价输入、空结果语义、executor 漂移、节点事件顺序和 owner-safe UI 投影。`n`n关联模块：M-06～M-18。"
```

- [ ] **Step 6: Run review and PR gates**

Before claiming implementation complete, invoke `superpowers:requesting-code-review` and fix every accepted finding with tests. Push only the feature branch, open a PR, wait for all required checks, and merge through the repository's allowed PR flow. Record PR URL, merge SHA and CI run URL in the verification audit.

- [ ] **Step 7: Re-read deployment standard and state the gate result**

Immediately before the first deployment write, re-read `agent-production-deployment-standards.md` completely and write the exact checkpoint in the working log:

```text
Deployment Standard reread: PASS
Release source verification: local HEAD equals origin/main merge SHA and that SHA is recorded in the release audit
Image policy: immutable GHCR digest only
Staging gate: required before Production
```

Do not run a deployment mutation if any line cannot be proven.

- [ ] **Step 8: Build/publish immutable images and verify Staging**

Follow the repository release workflow to build web/api/worker images from the merged SHA. Record image digests. Deploy those exact digests to Staging, migrate to single head `0016`, then execute four browser scenarios from the design spec:

1. named Shandong government source + search available;
2. explicit official URL;
3. named source + search unavailable;
4. accessible pages with no matching records.

For each, capture Task/Run/Workflow ids, ordered node lifecycle events, refresh/reconnect result, counts, typed outcome and artifact download. Verify no `NODE_EXECUTOR_UNAVAILABLE`, no `eligible=0 && PARTIALLY_COMPLETED`, no secret/reasoning in logs or Temporal history.

- [ ] **Step 9: Deploy and verify Production**

Create the required backup and release manifest, record rollback target, then deploy the exact Staging-approved digests. Verify health, migration head, container digests and public browser behavior. Create a new owner-scoped test Task; do not modify Task 25. Repeat named-source progress, refresh/reconnect, final outcome and artifact download checks.

Only after evidence is written and checked invoke `superpowers:verification-before-completion`. The final status is `DEPLOYED` only if PR, CI, GHCR, Staging, Production, browser, Temporal/DB consistency and rollback evidence all pass; otherwise report the exact earlier state such as `CODE_COMPLETE`, `PR_OPEN`, or `STAGING_VERIFIED`.

---

## Spec Coverage Matrix

| Approved design requirement | Implemented by |
| --- | --- |
| Literal URL / named source / no-search contract | Task 1 |
| HYBRID/EXPLORATORY source-search invariant and trusted Plan identity | Task 2 |
| Materializable input, frozen config, queue, storage and executor preflight | Tasks 3–4 |
| Production capability/runtime registry agreement | Tasks 2 and 5 |
| Real idempotent artifact executor | Task 5 |
| Named-source-only result filtering and frozen search version | Task 5 |
| NodeRun/NodeAttempt/checkpoint and safe lifecycle events | Task 6 |
| Runtime executor failure and mutually exclusive completion outcomes | Task 7 |
| Owner-safe snapshot, timeline, replay, redaction and legacy compatibility | Task 8 |
| Low-cardinality metrics and invariant alarms | Task 8 |
| Task Chat current/last node, counts, last activity, reconnect and Chinese copy | Task 9 |
| Full regression, secret scan, migration, CI, Staging, browser and Production | Task 10 |

No approved design section is deferred outside this plan.

---

## Task Dependency Order

```text
Task 1 Source Contract
  → Task 2 Plan Invariants + Capability Manifest
    → Task 3 Persisted Preflight
      → Task 4 Start Gate
        → Task 5 Artifact + Frozen Search
          → Task 6 Lifecycle Facts
            → Task 7 Completion Semantics
              → Task 8 Query + SSE
                → Task 9 Task Chat UI
                  → Task 10 Integrated Verification + Release
```

The order is intentionally sequential because each later task consumes stable interfaces or persisted facts from the prior task. A task may be reviewed and committed independently, but execution must not skip its dependencies.
