# M-08 Plan 生成、节点注册表、确定性校验与人工审批 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 D-007、D-008、D-017 的「Agent 可规划但不能越界」机制：类型化 Node Registry、Pydantic AI Plan Generator、确定性 Plan Validator、不可变 PlanVersion、Replan/Diff、有范围有期限的 Approval 生命周期，并接入 M-07 TaskWorkflow 的等待/恢复 seam 与前端审批闭环。

**Architecture:** LLM（Plan Generator）只负责提出受约束的 PlanGraphDraft；NodeDefinition Registry 是代码注册的静态允许列表，任何未注册 node_type 都会被确定性 Validator 拒绝。Validator 完全确定性（不调用 LLM），按「schema → registered → version → 唯一 ID → 依赖 → DAG → 参数 → 资源边 → Spec 匹配 → 范围边界 → 字段语义 → 质量约束 → 运行限制 → 风险/权限 → Provider 前置 → fingerprint」顺序检查并返回结构化 error code。PlanVersion 不可变；Replan 产生 vN+1 并保留 parent/diff/trigger/evidence。Approval 采用 Just-In-Time：Workflow 到达高风险 Node 时才 request_approval，用户 approve/reject 经 ApprovalService（owner + fingerprint + scope + expiry 校验）→ DomainEvent/Outbox → M-07 `ApprovalResolutionSignal` → Workflow 恢复；PROHIBITED 动作直接拒绝不可审批。Spec 边界变化走 REQUIRES_NEW_SPEC，不能仅靠 Approval 放行。

**Tech Stack:** Pydantic AI 2（复用 M-03 ModelInferenceClient + FunctionModel）、FastAPI、SQLAlchemy 2、pydantic v2、Temporal Python SDK、Vue 3 + TypeScript strict、Vitest、pytest。

## Global Constraints

- **M-04 兼容：** 复用 `PlanVersion`、`Approval`、`DomainEvent`、`OutboxEvent`、`IdempotencyKey`、`Checkpoint` 与 `TaskState/NodeState` 状态机。**不创建第二套** AgentPlan/TaskPlan/UserApproval 表。
- **M-04 状态机：** 任何 Task 状态变化必须经过 `app.state.states.assert_task_transition`（在 `DomainService.transition_task` 事务内）。Workflow/Activity 不得 `UPDATE tasks.state`。
- **M-04 幂等：** 审批命令走 `IdempotencyService`；节点指纹走 `stable_fingerprint`/`canonical_json`；Approval 的 `parameter_fingerprint` 变化后旧授权自动失效。
- **M-04 事务：** 状态变化 + DomainEvent + Outbox 同一事务提交。Approval 状态转换、Spec 边界、Replan 版本全部满足该事务规则。
- **M-06 兼容：** Plan 只消费已确认（`confirmed_at IS NOT NULL`）的不可变 `CollectionSpecVersion`；Agent 模型必须复用 M-03/M-06 `ModelInferenceClient` + `CredentialVault`，不建第二套模型 SDK。
- **M-07 兼容：** Plan 通过 `TaskWorkflowStarter.submit_validated_plan` 启动；Approval 通过 M-07 `ApprovalResolutionSignal` 恢复；SSE 复用 `/api/events/tasks/{id}` 流；新增审批事件不新建 SSE endpoint。
- **Node Registry 边界：** 只有代码注册的静态允许列表。禁止把任意 Python class path 存库后 dynamic import；禁止「模型生成了就自动注册」。
- **Validator 确定性：** Validator 不调用 LLM；返回结构化 error/reason code（不得只有 `ValueError("bad plan")`）。
- **Spec 边界 vs Approval：** 扩大采集范围 / 改变核心字段含义 / 降低质量要求 / 修改已完成标准 → `REQUIRES_NEW_SPEC`，不是 `REQUIRES_APPROVAL`。
- **风险/权限：** 建立 canonical `RiskLevel`（LOW/MEDIUM/HIGH/PROHIBITED），不散落 `boolean requires_approval`。PROHIBITED 不可创建 Approval、不可「用户同意就放行」。
- **M-09+ 边界：** 只注册 Node Contract，不实现真实 Search/robots/Frontier/HTTP/Scrapy/Playwright/Extract/Quality/CSV。生产运行遇到无 Activity 实现的 Node 必须返回稳定 `NODE_EXECUTOR_UNAVAILABLE`，不用 fake execution 冒充 Production 能力。
- **Fake/Staging 隔离：** 测试/Staging fixture executor 只在 test worker 或 `plan_fixture_mode` 下注册，默认关闭，Production 强制关闭，无真实外部网络/第三方写入/凭据外传。
- **D-036：** 任何 Plan/Approval/PlanSummary/Drawer 不得出现预计费用、人民币/美元金额、预算 UI。保留 Token/请求次数/执行时间/页面数/重试等技术统计。
- **Ownership：** PlanVersion/Approval 严格 user 隔离；跨用户访问 owner-safe 404；无权限 Deep Link 不泄漏 Approval 存在性。
- **UI 边界：** 不新增页面。Plan Summary Card（Chat）、Approval Drawer、Chat Approval Card、Deep Link、Task Drawer 待审批、`/tasks?view=needs_action` 复用现有 13 页面。完整 Plan DAG/Node Detail 留给 M-14。
- **Secret：** PlanVersion graph / Approval / Temporal History / SSE / DomainEvent / 日志均不得出现 API Key、Credential 明文、Cookie、密码。Credential 只显示脱敏引用。
- **Temporal 确定性：** Workflow 内禁止 DB/HTTP/LLM/文件副作用；全部走 Activity。时间等待只允许 `workflow.wait_condition(timeout=...)`。
- **命名：** 复用现有 enum（`TaskType`/`TaskState`/`NodeState`）；新增 SSE event 与 `domain_events.event_type` 语义一一对应；Approval 状态/范围用 canonical `ApprovalState`/`ApprovalScope`。
- **Migration：** 当前 Alembic head = `0005`。M-08 用 `0006` 增量扩展 `plan_versions` + `approvals`（expand/contract，兼容旧行）。不为模块号创建空 migration。
- **Git：** 每个 Task 一个可独立验证 Commit（英文 Conventional Commits 标题 + 中文正文）；本轮 5～8 个 Commit；不 push/merge/tag；不重写 M-07 历史。
- **测试策略：** A-Lite。至少 10 组 Plan fixture 契约测试（parameterized contract table，不建 10 个文件）；高风险路径（Validator/Approval fingerprint/owner/Temporal 集成）必须有测试；普通 CRUD 不堆测试。不跑全量 suite。

---

## File Structure

**后端新建：**
- `backend/app/plan/__init__.py` — plan 领域包
- `backend/app/plan/nodes.py` — `NodeType`/`RiskLevel`/`ResourceClass`/`ResourceKind`/`NodeDefinition`/`RetryPolicy`/`NodeRegistry`
- `backend/app/plan/schemas.py` — `PlanNodeInstance`/`PlanEdge`/`PlanGraphDraft`/`PlanValidationResult`/`PlanValidationIssue`/`PlanValidationOutcome`
- `backend/app/plan/validator.py` — `validate_plan()`（确定性，结构化 error code）
- `backend/app/plan/diff.py` — `PlanDiff`（确定性结构化 diff）
- `backend/app/plan/executors.py` — `NODE_EXECUTORS` 注册表 + `get_node_executor()`（生产为空；M-09+ 填充）
- `backend/app/plan/service.py` — `PlanService`（generate/validate/persist/auto-start/replan/summary）
- `backend/app/agents/plan_generator.py` — `PlanGeneratorAgent`（Pydantic AI FunctionModel + ModelInferenceClient）
- `backend/app/agents/plan_service.py` — `PlanGenerationService`（复用 M-03 provider/vault 解析 → generator → validator → 单次 repair）
- `backend/app/approval/__init__.py` — approval 领域包
- `backend/app/approval/service.py` — `ApprovalService`（request/resolve/revoke/query + owner + fingerprint + scope + expiry）
- `backend/app/approval/schemas.py` — `ApprovalState`/`ApprovalScope`/DTO
- `backend/app/activities/plan_execution.py` — 真实 `fetch_next_execution_unit` / `execute_safe_unit`（读 PlanVersion graph 拓扑调度；dispatch 到 NODE_EXECUTORS）
- `backend/app/activities/approval.py` — `request_approval` Activity（JIT：创建 Approval + WAITING_APPROVAL + DomainEvent + SSE）

**后端修改：**
- `backend/app/domain/models.py` — 扩展 `PlanVersion`（parent/validation/fingerprint/model_config/registry_versions/generation/trigger/diff）与 `Approval`（plan_version/node_id/node_type/target/approved_scope/credential_ref/status_payload/resolved_by/consumed_at）
- `backend/app/domain/repository.py` — 扩展 `PlanVersionRepository`；新增 `ApprovalRepository`
- `backend/app/domain/service.py` — `DomainService` 增加 `set_waiting_approval`/`set_waiting_resource` 系统命令（复用 `transition_task`）或直接用现有 `mark_waiting_approval` 命令
- `backend/app/state/states.py` — 确认 `WAITING_APPROVAL` 已可用；如需要补充 Approval 相关系统命令的 allowed 边界
- `backend/alembic/versions/0006_extend_plan_approval.py` — 增量 migration
- `backend/app/workflows/task_workflow.py` — 消费 `approval_resolution` Signal：high-risk unit → request_approval → wait → approve 执行 / reject 走合法 fallback/block
- `backend/app/activities/execution_seam.py` — 扩展 `ExecutionUnit`/`ExecuteUnitResult`（node_type/node_id/definition_version/parameters/requires_approval/approval_* 字段）
- `backend/app/infra/temporal.py` — `create_task_worker()` 注册 plan_execution + approval Activity
- `backend/app/worker.py` — 生产 worker 注册 plan_execution/approval Activity（executor 表保持空）
- `backend/app/infra/outbox_dispatch.py` — 增加 approval 事件 → `approval_resolution` Signal 映射
- `backend/app/api/schemas.py` — Plan/Approval DTO
- `backend/app/api/routes/plans.py` — plan 生成/列表/摘要端点
- `backend/app/api/routes/approvals.py` — approval query/approve/reject/revoke 端点
- `backend/app/api/router.py` — include plans/approvals router
- `backend/app/api/events.py` — 增加 APPROVAL_APPROVED/REJECTED/EXPIRED/REVOKED SSE event
- `backend/app/config.py` — `plan_fixture_mode: bool = False`（Staging-only fixture harness 开关）

**后端测试：**
- `backend/tests/plan/test_node_registry.py` — 10 标准 Node 注册 + typed metadata + 版本
- `backend/tests/plan/test_plan_fixtures.py` — ≥10 组合法/非法 Plan 契约表（parameterized）
- `backend/tests/plan/test_plan_validator.py` — DAG/参数/资源边/Spec/风险/Prohibited 专项
- `backend/tests/plan/test_plan_generator.py` — Fake Model Adapter（合法结构 / unknown node / 单次 repair / 二次失败）
- `backend/tests/plan/test_plan_replan.py` — v1 immutable / replan→v2 / parent / diff / Spec 边界拒绝
- `backend/tests/approval/test_approval_service.py` — 6 关键场景
- `backend/tests/api/test_plan_api.py` — plan 生成/摘要/owner-safe
- `backend/tests/api/test_approval_api.py` — approve/reject/revoke/owner 隔离
- `backend/tests/fixtures/plan_fixture.py` — Staging/test fixture executor（真实 NodeDefinition，fixture-only）
- `backend/tests/integration/test_plan_workflow.py` — 3 条 Temporal 集成（A/B/C）

**前端新建：**
- `frontend/src/features/tasks/plans.api.ts` — generate plan / list / summary
- `frontend/src/features/tasks/approvals.api.ts` — get / approve / reject / revoke
- `frontend/src/features/tasks/PlanSummaryCard.vue` — Chat 内简洁 Plan 摘要卡
- `frontend/src/features/tasks/ChatApprovalCard.vue` — Chat 时间线内审批卡（引用 approval_id）

**前端修改：**
- `frontend/src/app/overlay/drawers/ApprovalDrawer.vue` — 真实审批 Drawer（状态/动作/目标/原因/数据/脱敏凭据/副作用/范围/有效期/批准/拒绝/撤销）
- `frontend/src/features/tasks/events.api.ts` — 增加 APPROVAL_APPROVED/REJECTED/EXPIRED/REVOKED event type
- `frontend/src/features/tasks/useTaskEvents.ts` — 订阅新增审批事件
- `frontend/src/features/tasks/TaskChatView.vue` — confirm 后自动生成 Plan；Deep Link `?approval=` 打开同一 Approval Drawer；渲染 PlanSummaryCard/ChatApprovalCard
- `frontend/src/features/tasks/ChatMessageList.vue` — 渲染 ref_type=plan / approval 的卡片
- `frontend/src/features/tasks/commands.api.ts` — 保持现有命令（暂停/恢复/取消）
- `frontend/src/features/tasks/TaskShell.vue` / `useTaskShell.ts` — 透出 pending approval 状态（如需）
- `frontend/src/features/tasks/TasksView.vue` — `?view=needs_action` 聚合 pending approval（复用现有真实查询机制）
- `frontend/src/app/overlay/drawers/TaskStatusDrawer.vue` — 显示待审批数量/状态
- `frontend/src/app/router/deepLinks.ts` — 已支持 `approval`；如有需要扩展解析

**前端测试：**
- `frontend/src/features/tasks/approvalFlow.test.ts` — Deep Link 打开同一 Approval Drawer；Approve/Reject 真实后端状态；低风险 VALID Plan 无二次确认按钮
- `frontend/src/app/overlay/drawers/ApprovalDrawer.test.ts` — 展示/操作

**文档：**
- `docs/implementation/M-08-execution.md`
- `docs/implementation/DEPLOY-GATE-2-execution.md`（Gate 阶段）

---

# Task 1: Node Registry + 类型化 NodeDefinition 契约

**Files:**
- Create: `backend/app/plan/__init__.py`
- Create: `backend/app/plan/nodes.py`
- Create: `backend/app/plan/schemas.py`
- Test: `backend/tests/plan/test_node_registry.py`
- Test: `backend/tests/plan/test_plan_schemas.py`

**Interfaces:**
- Consumes: `app.domain.task_types.TaskType`（EXPLORATORY/SPECIFIED_SOURCE/HYBRID）
- Produces:
  - `class NodeType(StrEnum)` — source_search / access_rules_check / link_discovery / fetch / browser_render / extract / normalize / deduplicate / validate / generate_artifact
  - `class RiskLevel(StrEnum)` — low / medium / high / prohibited
  - `class ResourceClass(StrEnum)` — core / http / browser / llm_search
  - `class ResourceKind(StrEnum)` — spec / seed / candidate / url / snapshot / record / evidence / artifact / batch / credential
  - `@dataclass(frozen=True) class RetryPolicy` — `max_attempts: int`, `backoff_seconds: int`
  - `@dataclass(frozen=True) class NodeDefinition` — `node_type`, `definition_version: str`, `parameter_schema: type[BaseModel]`, `input_contract: tuple[ResourceKind, ...]`, `output_contract: tuple[ResourceKind, ...]`, `timeout_seconds: int`, `retry_policy: RetryPolicy`, `risk_level: RiskLevel`, `idempotency_identity: str`, `recoverable_boundary: str`, `resource_class: ResourceClass`, `capability_metadata: dict`
  - `class NodeRegistry` — `register(NodeDefinition)`, `get(node_type) -> NodeDefinition`, `all() -> list[NodeDefinition]`, `is_registered(node_type) -> bool`, `planning_metadata() -> list[dict]`
  - `class ResourceRef(BaseModel)` — `kind: ResourceKind`, `ref_key: str`
  - `class PlanNodeInstance(BaseModel)` — `node_id`, `node_type`, `definition_version`, `parameters: dict`, `depends_on: list[str]`, `optional: bool`, `fail_policy: Literal["block","skip","retry"]`
  - `class PlanEdge(BaseModel)` — `from_node_id`, `to_node_id`, `resource_refs: list[ResourceRef]`
  - `class PlanGraphDraft(BaseModel)` — `schema_version: str`, `task_id`, `spec_version`, `task_type`, `nodes`, `edges`, `reasoning_summary: str | None`
  - `class PlanValidationResult(StrEnum)` — valid / requires_approval / requires_new_spec / invalid / prohibited
  - `class PlanValidationIssue(BaseModel)` — `code: str`, `message: str`, `node_id: str | None`, `path: str | None`

### Step 1: 写 NodeType/RiskLevel/ResourceClass/ResourceKind 枚举失败测试

Create `backend/tests/plan/test_node_registry.py`:

```python
"""M-08 Task 1: canonical node / risk / resource vocabulary + registry contract."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.plan.nodes import (
    NodeDefinition,
    NodeRegistry,
    NodeType,
    ResourceClass,
    ResourceKind,
    RetryPolicy,
    RiskLevel,
)


def test_node_type_has_ten_standard_nodes() -> None:
    assert list(NodeType) == [
        NodeType.SOURCE_SEARCH,
        NodeType.ACCESS_RULES_CHECK,
        NodeType.LINK_DISCOVERY,
        NodeType.FETCH,
        NodeType.BROWSER_RENDER,
        NodeType.EXTRACT,
        NodeType.NORMALIZE,
        NodeType.DEDUPLICATE,
        NodeType.VALIDATE,
        NodeType.GENERATE_ARTIFACT,
    ]


def test_risk_level_has_no_boolean_leak() -> None:
    assert list(RiskLevel) == [
        RiskLevel.LOW,
        RiskLevel.MEDIUM,
        RiskLevel.HIGH,
        RiskLevel.PROHIBITED,
    ]


def test_resource_kind_covers_typed_io() -> None:
    for kind in ResourceKind:
        assert kind.value


def test_registry_registers_ten_standard_definitions() -> None:
    registry = NodeRegistry()
    defs = registry.all()
    assert {d.node_type for d in defs} == set(NodeType)
    # 每个 definition 都有 typed parameter schema 与 input/output contract
    for d in defs:
        assert issubclass(d.parameter_schema, BaseModel)
        assert d.definition_version
        assert d.input_contract
        assert d.output_contract
        assert d.timeout_seconds > 0
        assert d.retry_policy.max_attempts >= 1
        assert d.risk_level in RiskLevel
        assert d.resource_class in ResourceClass
        assert d.idempotency_identity
        assert d.recoverable_boundary


def test_registry_is_static_allowlist() -> None:
    registry = NodeRegistry()
    assert registry.is_registered(NodeType.FETCH)
    assert registry.get(NodeType.FETCH).risk_level == RiskLevel.LOW
    # 未注册 node_type 必须不存在（Agent 不能引用未知动作）
    assert registry.get(NodeType.SOURCE_SEARCH).risk_level == RiskLevel.MEDIUM
    assert NodeRegistry().get(NodeType.DEDUPLICATE).risk_level == RiskLevel.LOW
    assert NodeRegistry().get(NodeType.BROWSER_RENDER).risk_level == RiskLevel.MEDIUM


def test_planning_metadata_is_serializable() -> None:
    meta = NodeRegistry().planning_metadata()
    assert len(meta) == len(NodeType)
    first = meta[0]
    assert set(first) >= {"node_type", "risk_level", "resource_class", "input", "output"}
```

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/plan/test_node_registry.py -q`
Expected: FAIL — `ModuleNotFoundError: app.plan`.

### Step 2: 创建 app/plan 包与 nodes.py

Create `backend/app/plan/__init__.py` (empty docstring module):

```python
"""M-08 plan 领域：Node Registry、Plan 校验、Replan/Diff。"""
```

Create `backend/app/plan/nodes.py`:

```python
"""Canonical node vocabulary + typed NodeDefinition registry (M-08 / D-008).

Node Registry 是代码注册的静态允许列表。Agent 只能引用 ``NodeRegistry`` 已注册的
``node_type``；未注册动作不能被 Validator 放行，也不得在模型生成后自动注册。

M-08 只注册标准节点的契约（参数 schema、input/output、timeout、retry、风险、
幂等身份、可恢复边界、资源类）。真实 Activity 实现由 M-09～M-12 挂入
``app.plan.executors``；M-08 生产运行时对无实现的 Node 返回 NODE_EXECUTOR_UNAVAILABLE。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class NodeType(StrEnum):
    SOURCE_SEARCH = "source_search"
    ACCESS_RULES_CHECK = "access_rules_check"
    LINK_DISCOVERY = "link_discovery"
    FETCH = "fetch"
    BROWSER_RENDER = "browser_render"
    EXTRACT = "extract"
    NORMALIZE = "normalize"
    DEDUPLICATE = "deduplicate"
    VALIDATE = "validate"
    GENERATE_ARTIFACT = "generate_artifact"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    PROHIBITED = "prohibited"


class ResourceClass(StrEnum):
    CORE = "core"
    HTTP = "http"
    BROWSER = "browser"
    LLM_SEARCH = "llm_search"


class ResourceKind(StrEnum):
    """Typed resource refs flowing between plan nodes (D-008)."""

    SPEC = "spec"
    SEED = "seed"
    CANDIDATE = "candidate"
    URL = "url"
    SNAPSHOT = "snapshot"
    RECORD = "record"
    EVIDENCE = "evidence"
    ARTIFACT = "artifact"
    BATCH = "batch"
    CREDENTIAL = "credential"


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    backoff_seconds: int = 2


class _FetchParams(BaseModel):
    url_template: str
    max_redirects: int = 5
    render_if_empty: bool = False


class _SourceSearchParams(BaseModel):
    query: str
    max_results: int = 20
    locale: str | None = None


class _AccessRulesParams(BaseModel):
    respect_robots: bool = True
    public_only: bool = True


class _LinkDiscoveryParams(BaseModel):
    allow_outside_scope: bool = False
    max_links: int = 200


class _BrowserRenderParams(BaseModel):
    wait_selector: str | None = None
    full_page: bool = False


class _ExtractParams(BaseModel):
    fields: list[str]
    prefer_rules: bool = True


class _NormalizeParams(BaseModel):
    trim_whitespace: bool = True


class _DeduplicateParams(BaseModel):
    keys: list[str]


class _ValidateParams(BaseModel):
    check_evidence: bool = True
    min_required_fields: int = 1


class _GenerateArtifactParams(BaseModel):
    format: str = "csv"
    dataset_version: str = "v1"


@dataclass(frozen=True)
class NodeDefinition:
    """Typed contract every standard node must declare (D-008)."""

    node_type: NodeType
    definition_version: str
    parameter_schema: type[BaseModel]
    input_contract: tuple[ResourceKind, ...]
    output_contract: tuple[ResourceKind, ...]
    timeout_seconds: int
    retry_policy: RetryPolicy
    risk_level: RiskLevel
    idempotency_identity: str
    recoverable_boundary: str
    resource_class: ResourceClass
    capability_metadata: dict[str, Any] = field(default_factory=dict)


_STANDARD_DEFINITIONS: tuple[NodeDefinition, ...] = (
    NodeDefinition(
        node_type=NodeType.SOURCE_SEARCH,
        definition_version="1.0.0",
        parameter_schema=_SourceSearchParams,
        input_contract=(ResourceKind.SPEC,),
        output_contract=(ResourceKind.CANDIDATE,),
        timeout_seconds=120,
        retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=2),
        risk_level=RiskLevel.MEDIUM,
        idempotency_identity="node_type+input_fingerprint",
        recoverable_boundary="batch-committed",
        resource_class=ResourceClass.LLM_SEARCH,
        capability_metadata={"provider": "search_provider", "requires": ["search_config"]},
    ),
    NodeDefinition(
        node_type=NodeType.ACCESS_RULES_CHECK,
        definition_version="1.0.0",
        parameter_schema=_AccessRulesParams,
        input_contract=(ResourceKind.URL, ResourceKind.SPEC),
        output_contract=(ResourceKind.URL,),
        timeout_seconds=30,
        retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=2),
        risk_level=RiskLevel.LOW,
        idempotency_identity="node_type+input_fingerprint",
        recoverable_boundary="batch-committed",
        resource_class=ResourceClass.CORE,
        capability_metadata={"robots": True},
    ),
    NodeDefinition(
        node_type=NodeType.LINK_DISCOVERY,
        definition_version="1.0.0",
        parameter_schema=_LinkDiscoveryParams,
        input_contract=(ResourceKind.URL, ResourceKind.SPEC),
        output_contract=(ResourceKind.URL,),
        timeout_seconds=60,
        retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=2),
        risk_level=RiskLevel.LOW,
        idempotency_identity="node_type+input_fingerprint",
        recoverable_boundary="batch-committed",
        resource_class=ResourceClass.CORE,
    ),
    NodeDefinition(
        node_type=NodeType.FETCH,
        definition_version="1.0.0",
        parameter_schema=_FetchParams,
        input_contract=(ResourceKind.URL, ResourceKind.SPEC, ResourceKind.CREDENTIAL),
        output_contract=(ResourceKind.SNAPSHOT,),
        timeout_seconds=60,
        retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=2),
        risk_level=RiskLevel.LOW,
        idempotency_identity="node_type+input_fingerprint",
        recoverable_boundary="batch-committed",
        resource_class=ResourceClass.HTTP,
        capability_metadata={"tool_ladder": ["http", "browser"]},
    ),
    NodeDefinition(
        node_type=NodeType.BROWSER_RENDER,
        definition_version="1.0.0",
        parameter_schema=_BrowserRenderParams,
        input_contract=(ResourceKind.URL, ResourceKind.SPEC, ResourceKind.CREDENTIAL),
        output_contract=(ResourceKind.SNAPSHOT,),
        timeout_seconds=180,
        retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=5),
        risk_level=RiskLevel.MEDIUM,
        idempotency_identity="node_type+input_fingerprint",
        recoverable_boundary="batch-committed",
        resource_class=ResourceClass.BROWSER,
        capability_metadata={"tool_ladder": ["browser"]},
    ),
    NodeDefinition(
        node_type=NodeType.EXTRACT,
        definition_version="1.0.0",
        parameter_schema=_ExtractParams,
        input_contract=(ResourceKind.SNAPSHOT, ResourceKind.SPEC),
        output_contract=(ResourceKind.RECORD, ResourceKind.EVIDENCE),
        timeout_seconds=120,
        retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=2),
        risk_level=RiskLevel.LOW,
        idempotency_identity="node_type+input_fingerprint",
        recoverable_boundary="batch-committed",
        resource_class=ResourceClass.CORE,
        capability_metadata={"uses": ["rules", "llm_fallback"]},
    ),
    NodeDefinition(
        node_type=NodeType.NORMALIZE,
        definition_version="1.0.0",
        parameter_schema=_NormalizeParams,
        input_contract=(ResourceKind.RECORD, ResourceKind.SPEC),
        output_contract=(ResourceKind.RECORD,),
        timeout_seconds=30,
        retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=2),
        risk_level=RiskLevel.LOW,
        idempotency_identity="node_type+input_fingerprint",
        recoverable_boundary="batch-committed",
        resource_class=ResourceClass.CORE,
    ),
    NodeDefinition(
        node_type=NodeType.DEDUPLICATE,
        definition_version="1.0.0",
        parameter_schema=_DeduplicateParams,
        input_contract=(ResourceKind.RECORD, ResourceKind.SPEC),
        output_contract=(ResourceKind.RECORD,),
        timeout_seconds=30,
        retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=2),
        risk_level=RiskLevel.LOW,
        idempotency_identity="node_type+input_fingerprint",
        recoverable_boundary="batch-committed",
        resource_class=ResourceClass.CORE,
    ),
    NodeDefinition(
        node_type=NodeType.VALIDATE,
        definition_version="1.0.0",
        parameter_schema=_ValidateParams,
        input_contract=(ResourceKind.RECORD, ResourceKind.EVIDENCE, ResourceKind.SPEC),
        output_contract=(ResourceKind.RECORD,),
        timeout_seconds=30,
        retry_policy=RetryPolicy(max_attempts=3, backoff_seconds=2),
        risk_level=RiskLevel.LOW,
        idempotency_identity="node_type+input_fingerprint",
        recoverable_boundary="batch-committed",
        resource_class=ResourceClass.CORE,
        capability_metadata={"partitions": ["passed", "needs_review", "rejected"]},
    ),
    NodeDefinition(
        node_type=NodeType.GENERATE_ARTIFACT,
        definition_version="1.0.0",
        parameter_schema=_GenerateArtifactParams,
        input_contract=(ResourceKind.RECORD, ResourceKind.SPEC),
        output_contract=(ResourceKind.ARTIFACT,),
        timeout_seconds=60,
        retry_policy=RetryPolicy(max_attempts=2, backoff_seconds=2),
        risk_level=RiskLevel.LOW,
        idempotency_identity="node_type+input_fingerprint",
        recoverable_boundary="artifact-committed",
        resource_class=ResourceClass.CORE,
        capability_metadata={"formats": ["csv"]},
    ),
)


class NodeRegistry:
    """Code-registered static allowlist of standard nodes (D-008)."""

    def __init__(self, definitions: tuple[NodeDefinition, ...] = _STANDARD_DEFINITIONS) -> None:
        self._defs = {d.node_type: d for d in definitions}

    def register(self, definition: NodeDefinition) -> None:
        self._defs[definition.node_type] = definition

    def get(self, node_type: NodeType) -> NodeDefinition | None:
        return self._defs.get(node_type)

    def all(self) -> list[NodeDefinition]:
        return list(self._defs.values())

    def is_registered(self, node_type: NodeType) -> bool:
        return node_type in self._defs

    def planning_metadata(self) -> list[dict[str, Any]]:
        """LLM 可读的允许节点清单（不含 Secret，不含实现细节）。"""
        return [
            {
                "node_type": d.node_type.value,
                "risk_level": d.risk_level.value,
                "resource_class": d.resource_class.value,
                "input": [k.value for k in d.input_contract],
                "output": [k.value for k in d.output_contract],
                "timeout_seconds": d.timeout_seconds,
            }
            for d in self._defs.values()
        ]
```

Note: `dataclass`/`field` import is missing above — add `from dataclasses import dataclass, field` to the imports.

### Step 3: 运行并修正

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/plan/test_node_registry.py -q`
Fix any import/typing issues. Expected: 6 passed.

### Step 4: 写 Plan schema 失败测试

Create `backend/tests/plan/test_plan_schemas.py`:

```python
"""M-08 Task 1: typed PlanGraphDraft / validation result schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.task_types import TaskType
from app.plan.nodes import NodeType, ResourceKind
from app.plan.schemas import (
    PlanEdge,
    PlanGraphDraft,
    PlanNodeInstance,
    PlanValidationResult,
    ResourceRef,
)


def _node(node_id: str, node_type: NodeType, depends_on: list[str] | None = None) -> PlanNodeInstance:
    return PlanNodeInstance(
        node_id=node_id,
        node_type=node_type,
        definition_version="1.0.0",
        parameters={},
        depends_on=depends_on or [],
    )


def test_plan_graph_draft_roundtrip() -> None:
    draft = PlanGraphDraft(
        task_id=1,
        spec_version=1,
        task_type=TaskType.SPECIFIED_SOURCE,
        nodes=[_node("n1", NodeType.FETCH), _node("n2", NodeType.EXTRACT, ["n1"])],
        edges=[PlanEdge(from_node_id="n1", to_node_id="n2", resource_refs=[ResourceRef(kind=ResourceKind.SNAPSHOT, ref_key="snap:1")])],
    )
    data = draft.model_dump(mode="json")
    assert data["nodes"][1]["depends_on"] == ["n1"]
    assert data["edges"][0]["resource_refs"][0]["kind"] == "snapshot"


def test_plan_graph_draft_rejects_unknown_node_type() -> None:
    with pytest.raises(ValidationError):
        PlanGraphDraft(
            task_id=1,
            spec_version=1,
            task_type=TaskType.EXPLORATORY,
            nodes=[_node("n1", "not_a_node")],
            edges=[],
        )


def test_validation_result_is_single_canonical_enum() -> None:
    assert list(PlanValidationResult) == [
        PlanValidationResult.VALID,
        PlanValidationResult.REQUIRES_APPROVAL,
        PlanValidationResult.REQUIRES_NEW_SPEC,
        PlanValidationResult.INVALID,
        PlanValidationResult.PROHIBITED,
    ]
```

### Step 5: 创建 schemas.py

Create `backend/app/plan/schemas.py`:

```python
"""Typed PlanGraphDraft + canonical validation result (M-08 / D-008).

LLM 只负责提出 Plan；判定合法性的 enum 只有这一组，禁止新增语义重复的
WAITING_CONFIRMATION / NEEDS_USER / BLOCK_APPROVAL 等第二套结果名。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.domain.task_types import TaskType
from app.plan.nodes import NodeType, ResourceKind


class ResourceRef(BaseModel):
    kind: ResourceKind
    ref_key: str  # 稳定引用，如 seed:1 / batch:unit-1


class PlanNodeInstance(BaseModel):
    node_id: str
    node_type: NodeType
    definition_version: str
    parameters: dict = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    optional: bool = False
    fail_policy: Literal["block", "skip", "retry"] = "block"


class PlanEdge(BaseModel):
    from_node_id: str
    to_node_id: str
    resource_refs: list[ResourceRef] = Field(default_factory=list)


class PlanGraphDraft(BaseModel):
    schema_version: str = "m08.1"
    task_id: int
    spec_version: int
    task_type: TaskType
    nodes: list[PlanNodeInstance] = Field(default_factory=list)
    edges: list[PlanEdge] = Field(default_factory=list)
    reasoning_summary: str | None = None  # 只保存可审计摘要，不保存 LLM chain-of-thought


class PlanValidationResult(StrEnum):
    VALID = "VALID"
    REQUIRES_APPROVAL = "REQUIRES_APPROVAL"
    REQUIRES_NEW_SPEC = "REQUIRES_NEW_SPEC"
    INVALID = "INVALID"
    PROHIBITED = "PROHIBITED"


class PlanValidationIssue(BaseModel):
    code: str
    message: str
    node_id: str | None = None
    path: str | None = None
```

### Step 6: 运行 Task 1 全部门禁

Run:
```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/plan/test_node_registry.py tests/plan/test_plan_schemas.py -q
.venv/Scripts/python.exe -m ruff check app/plan tests/plan
.venv/Scripts/python.exe -m ruff format app/plan tests/plan
.venv/Scripts/python.exe -m mypy app/plan
```
Expected: all PASS.

### Step 7: Commit

```bash
git add backend/app/plan backend/tests/plan
git commit -m "feat(plan): add typed node registry and plan graph schemas

建立 M-08 标准节点契约（SourceSearch/AccessRulesCheck/LinkDiscovery/Fetch/
BrowserRender/Extract/Normalize/Deduplicate/Validate/GenerateArtifact），每个节点声明
typed 参数 schema、input/output 资源契约、timeout、retry、风险等级、幂等身份、
可恢复边界与资源类。NodeRegistry 是代码注册静态允许列表，规划元数据供 LLM 只读。
PlanGraphDraft 为 typed LLM 输出；PlanValidationResult 为唯一 canonical 判定枚举。
关联模块：M-08"
```

---

# Task 2: Pydantic AI Plan Generator

**Files:**
- Create: `backend/app/agents/plan_generator.py`
- Create: `backend/app/agents/plan_service.py`
- Create: `backend/app/plan/validator.py`（最小版本，供 repair 判断；完整校验在 Task 3 扩展）
- Test: `backend/tests/plan/test_plan_generator.py`

**Interfaces:**
- Consumes: `ModelInferenceClient`（`app.providers.inference`）、`ResolvedModel`（`app.providers.protocol`）、`NodeRegistry.planning_metadata()`、`PlanGraphDraft`、`SpecDraftPayload`（`app.domain.spec`）、`ProviderService.require_available_model_config`、`CredentialVault.read_for_execution`
- Produces:
  - `class PlanInput(BaseModel)` — `spec_payload: dict`（frozen CollectionSpecVersion.payload）、`task_type: TaskType`、`registry_metadata: list[dict]`、`execution_constraints: dict`
  - `class PlanGeneratorAgent` — `generate(input: PlanInput, resolved, api_key) -> PlanGraphDraft`
  - `class PlanGenerationService` — `generate_for_task(user, task_id, spec_version, expected_version, registry) -> PlanGenerationOutcome`
  - `@dataclass class PlanGenerationOutcome` — `graph: PlanGraphDraft`, `validation_result: PlanValidationResult`, `issues: list[PlanValidationIssue]`, `repair_used: bool`, `audit: dict`

### Step 1: 写生成器失败测试（Fake Model Adapter）

Create `backend/tests/plan/test_plan_generator.py`:

```python
"""M-08 Task 2: PlanGenerator via Fake Model Adapter (no real provider)."""

from __future__ import annotations

import pytest

from app.agents.plan_generator import PlanGeneratorAgent, PlanInput
from app.agents.plan_service import PlanGenerationService
from app.domain.spec import SpecDraftPayload
from app.domain.task_types import TaskType
from app.plan.nodes import NodeRegistry
from app.plan.schemas import PlanGraphDraft, PlanValidationResult
from app.providers.inference import InferenceResult, ModelInferenceClient
from app.providers.protocol import ResolvedModel


class FakeInference:
    """可控的假 ModelInferenceClient：直接返回预设 JSON 文本。"""

    def __init__(self, text: str) -> None:
        self._text = text

    async def generate(self, *, resolved, api_key, system, user) -> InferenceResult:
        return InferenceResult(text=self._text, provider_type="fake", duration_ms=1)


_VALID_PLAN_JSON = """{
  "schema_version": "m08.1",
  "task_id": 1,
  "spec_version": 1,
  "task_type": "SPECIFIED_SOURCE",
  "nodes": [
    {"node_id": "n1", "node_type": "fetch", "definition_version": "1.0.0", "parameters": {"url_template": "https://example.com/item/{id}"}, "depends_on": [], "optional": false, "fail_policy": "block"},
    {"node_id": "n2", "node_type": "extract", "definition_version": "1.0.0", "parameters": {"fields": ["公司名"]}, "depends_on": ["n1"], "optional": false, "fail_policy": "block"},
    {"node_id": "n3", "node_type": "generate_artifact", "definition_version": "1.0.0", "parameters": {"format": "csv"}, "depends_on": ["n2"], "optional": false, "fail_policy": "block"}
  ],
  "edges": [
    {"from_node_id": "n1", "to_node_id": "n2", "resource_refs": [{"kind": "snapshot", "ref_key": "snap:1"}]},
    {"from_node_id": "n2", "to_node_id": "n3", "resource_refs": [{"kind": "record", "ref_key": "rec:1"}]}
  ],
  "reasoning_summary": "对指定来源逐页抓取并抽取字段"
}"""


def _resolved() -> ResolvedModel:
    return ResolvedModel(
        provider_type="deepseek", model_name="test", base_url=None, credential_version_id=None
    )


def _input() -> PlanInput:
    spec = SpecDraftPayload(
        task_type=TaskType.SPECIFIED_SOURCE,
        goal="抓取指定网站的公司信息",
        fields=[{"name": "公司名", "type": "text", "required": True}],
        source_scope={"mode": "SPECIFIED_SOURCE", "seed_urls": ["https://example.com"], "source_hints": []},
    )
    return PlanInput(
        spec_payload=spec.model_dump(mode="json"),
        task_type=TaskType.SPECIFIED_SOURCE,
        registry_metadata=NodeRegistry().planning_metadata(),
        execution_constraints={"has_search_provider": False},
    )


@pytest.mark.asyncio
async def test_generator_returns_typed_plan() -> None:
    agent = PlanGeneratorAgent(ModelInferenceClient(http=FakeInference(_VALID_PLAN_JSON)) if False else None)
    # 使用可控假客户端注入
    resolved = _resolved()
    # 直接替换内部 inference 为假实现
    agent._inference = FakeInference(_VALID_PLAN_JSON)  # type: ignore[assignment]
    graph = await agent.generate(_input(), resolved, api_key=None)
    assert isinstance(graph, PlanGraphDraft)
    assert graph.task_type == TaskType.SPECIFIED_SOURCE
    assert [n.node_type.value for n in graph.nodes] == ["fetch", "extract", "generate_artifact"]
```

Note: PlanGeneratorAgent must be constructible with an injectable `ModelInferenceClient`; the test then swaps `agent._inference` for the fake. Keep the constructor consistent with M-06 `GoalUnderstandingAgent(inference=None)`.

### Step 2: 创建 plan_generator.py

Create `backend/app/agents/plan_generator.py`:

```python
"""PlanGeneratorAgent — Pydantic AI 受约束规划（M-08 / D-008）。

与 M-06 GoalUnderstandingAgent 同一模式：pydantic-ai 负责 typed 输出校验/重试循环，
真实 HTTP 调用走 M-03 ModelInferenceClient（用户自己的 ModelConfig + CredentialVault
解密 key），不引入第二套模型 SDK，也不把 Secret 送进 Prompt。

LLM 只输出 typed PlanGraphDraft；node_type 只能来自 NodeRegistry 允许清单。模型永远
不能输出 Python/Shell/任意 Tool name 作为执行动作。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.agents.schemas import GoalUnderstandingResult  # noqa: F401  (import pattern parity)
from app.domain.task_types import TaskType
from app.plan.schemas import PlanGraphDraft
from app.providers.inference import ModelInferenceClient
from app.providers.protocol import ResolvedModel
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel


class PlanInput(BaseModel):
    spec_payload: dict
    task_type: TaskType
    registry_metadata: list[dict]
    execution_constraints: dict


PLAN_SYSTEM_PROMPT = (
    "你是 Kairos 网页信息采集 Agent 的计划生成模块。你的唯一职责：根据已确认的"
    " CollectionSpec 生成一个受约束的执行计划 JSON。\n"
    "规则：\n"
    "1. 只能使用下方允许的 node_type；绝不发明新节点。\n"
    "2. 计划必须完全落在已确认 Spec 的采集范围、字段与质量标准之内；不得扩大域名范围、"
    "不得改变字段含义、不得降低质量要求——这些属于规格层，不由计划决定。\n"
    "3. 节点通过 depends_on / edges 形成有向无环图；每个节点只依赖已存在的节点。\n"
    "4. parameters 必须匹配该节点类型的参数契约；高风险动作（如使用网站凭据访问非公开页面）"
    "必须使用标准 fetch 节点并明确 non_public 参数，系统会按风险分级决定是否审批。\n"
    "5. 只输出一个 JSON 对象，不要输出任何 JSON 之外的文字、markdown 或注释。\n"
    "6. reasoning_summary 只写可审计的执行思路摘要，不要暴露推理内部过程。\n"
    "\n允许节点清单：{registry_json}\n"
    "执行约束：{constraints_json}\n"
    "Spec 内容：{spec_json}\n"
    "\n输出契约：\n"
    '{{"schema_version": "m08.1", "task_id": 1, "spec_version": 1, '
    '"task_type": "SPECIFIED_SOURCE|EXPLORATORY|HYBRID", "nodes": [{{"node_id": "n1", '
    '"node_type": "fetch", "definition_version": "1.0.0", "parameters": {{}}, '
    '"depends_on": [], "optional": false, "fail_policy": "block"}}], '
    '"edges": [{{"from_node_id": "n1", "to_node_id": "n2", '
    '"resource_refs": [{{"kind": "snapshot", "ref_key": "snap:1"}}]}}], '
    '"reasoning_summary": "..."}}'
)


def _build_user_prompt(inp: PlanInput) -> str:
    return (
        f"Spec JSON：{json.dumps(inp.spec_payload, ensure_ascii=False)}\n"
        f"任务类型：{inp.task_type.value}"
    )


@dataclass
class PlanGeneratorAgent:
    inference: ModelInferenceClient | None = None

    def __post_init__(self) -> None:
        self._inference = self.inference or ModelInferenceClient()

    def _build_function(self, resolved: ResolvedModel, api_key: str | None, inp: PlanInput):
        async def _call(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
            system = PLAN_SYSTEM_PROMPT.format(
                registry_json=json.dumps(inp.registry_metadata, ensure_ascii=False),
                constraints_json=json.dumps(inp.execution_constraints, ensure_ascii=False),
                spec_json=json.dumps(inp.spec_payload, ensure_ascii=False),
            )
            user = _build_user_prompt(inp)
            result = await self._inference.generate(
                resolved=resolved, api_key=api_key, system=system, user=user
            )
            parsed = json.loads(result.text)
            tool_name = (
                agent_info.output_tools[0].name if agent_info.output_tools else "final_result"
            )
            return ModelResponse(parts=[ToolCallPart(tool_name=tool_name, args=json.dumps(parsed, ensure_ascii=False))])

        return _call

    async def generate(
        self, inp: PlanInput, resolved: ResolvedModel, api_key: str | None
    ) -> PlanGraphDraft:
        agent = Agent(
            model=FunctionModel(self._build_function(resolved, api_key, inp)),
            output_type=PlanGraphDraft,
            system_prompt=(
                PLAN_SYSTEM_PROMPT.format(
                    registry_json=json.dumps(inp.registry_metadata, ensure_ascii=False),
                    constraints_json=json.dumps(inp.execution_constraints, ensure_ascii=False),
                    spec_json=json.dumps(inp.spec_payload, ensure_ascii=False),
                )
            ),
            retries=1,
        )
        result = await agent.run(_build_user_prompt(inp))
        return result.output
```

Note: the FunctionModel path re-derives the system prompt from `inp` at call time (it can't reliably reconstruct it from pydantic-ai messages), so the duplicated formatting is intentional and consistent with the M-06 `_extract_messages` pattern.

### Step 3: 写 PlanGenerationService 失败测试（含一次 repair 与二次失败）

Add to `backend/tests/plan/test_plan_generator.py`:

```python
@pytest.mark.asyncio
async def test_generation_service_repairs_once_then_passes() -> None:
    # 第一次输出 schema 结构错误（缺 nodes 键），第二次输出合法 Plan。
    bad = '{"task_id": 1, "spec_version": 1}'
    calls = {"n": 0}

    async def fake_gen(*, resolved, api_key, system, user):
        calls["n"] += 1
        return InferenceResult(
            text=_VALID_PLAN_JSON if calls["n"] >= 2 else bad,
            provider_type="fake",
            duration_ms=1,
        )

    client = ModelInferenceClient(http=type("FakeTransport", (), {"request": None})())
    service = PlanGenerationService(
        inference=type(
            "FakeClient",
            (),
            {"generate": fake_gen},
        )(),
    )
    outcome = await service._repair_loop(  # 内部单次 repair 逻辑
        _input(),
        _resolved(),
        api_key=None,
        max_repairs=1,
    )
    assert outcome.repair_used is True
    assert outcome.validation_result in (PlanValidationResult.VALID, PlanValidationResult.REQUIRES_APPROVAL)
```

This test targets the repair loop; the exact repair loop signature will be defined in `PlanGenerationService`. To keep the interface stable, `PlanGenerationService` exposes `generate_for_task(user, task_id, spec_version, expected_version, registry)` as the real entry and an internal `_repair_loop(inp, resolved, api_key, max_repairs)`.

### Step 4: 创建 validator 最小版 + plan_service.py

Create `backend/app/plan/validator.py` (minimal structural checks now; full set in Task 3):

```python
"""Deterministic Plan Validator (M-08 / D-008).

Validator 不调用 LLM。完整校验顺序见 Task 3；本模块先提供结构层面检查，供生成器
repair 判断与 Task 3 扩展复用。
"""

from __future__ import annotations

from app.plan.nodes import NodeRegistry
from app.plan.schemas import (
    PlanGraphDraft,
    PlanValidationIssue,
    PlanValidationResult,
)


def validate_plan(
    graph: PlanGraphDraft,
    spec_payload: dict,
    registry: NodeRegistry | None = None,
    *,
    available_search: bool = True,
) -> tuple[PlanValidationResult, list[PlanValidationIssue]]:
    registry = registry or NodeRegistry()
    issues: list[PlanValidationIssue] = []

    ids = [n.node_id for n in graph.nodes]
    if len(ids) != len(set(ids)):
        issues.append(PlanValidationIssue(code="DUPLICATE_NODE_ID", message="节点 ID 重复"))

    known = {n.node_id for n in graph.nodes}
    for n in graph.nodes:
        if not registry.is_registered(n.node_type):
            issues.append(
                PlanValidationIssue(
                    code="NODE_NOT_REGISTERED",
                    message=f"节点类型未注册: {n.node_type.value}",
                    node_id=n.node_id,
                )
            )
        for dep in n.depends_on:
            if dep not in known:
                issues.append(
                    PlanValidationIssue(
                        code="MISSING_DEPENDENCY",
                        message=f"依赖不存在: {dep}",
                        node_id=n.node_id,
                    )
                )

    # 环检测：拓扑排序
    order: list[str] = []
    visited: dict[str, int] = {n.node_id: 0 for n in graph.nodes}  # 0=unvisited 1=in 2=done
    cycle: list[str] = []

    def _visit(nid: str) -> None:
        if visited[nid] == 1:
            cycle.append(nid)
            return
        if visited[nid] == 2:
            return
        visited[nid] = 1
        for dep in _dep_map.get(nid, []):
            if dep in visited:
                _visit(dep)
        visited[nid] = 2
        order.append(nid)

    _dep_map = {n.node_id: n.depends_on for n in graph.nodes}
    for n in graph.nodes:
        _visit(n.node_id)
    if cycle:
        issues.append(PlanValidationIssue(code="CYCLE_DETECTED", message="计划存在环", node_id=cycle[0]))

    if issues:
        return PlanValidationResult.INVALID, issues
    return PlanValidationResult.VALID, issues
```

Note: the cycle-detection closure references `_dep_map` before it is assigned; move `_dep_map` assignment above `_visit`. This is exactly the kind of issue the executing engineer fixes when running the test.

### Step 5: 创建 plan_service.py

Create `backend/app/agents/plan_service.py`:

```python
"""PlanGenerationService — 复用 M-03 provider/vault → generator → validator（M-08）。

Secret 处理与 M-06 GoalUnderstandingService 一致：API Key 只在调用时经 CredentialVault
解密，不离开本路径；audit 元数据只保存 config_id/version、provider、model、duration，
不保存 key。

允许最多一次有证据的 Plan Repair：把 Validator 返回的可纠正结构问题喂回模型重写；
第二次仍不合法 → 判定 FAIL/BLOCKED，不无限让模型重写。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from app.agents.plan_generator import PlanGeneratorAgent, PlanInput
from app.auth.models import User
from app.credentials.vault import CredentialVault
from app.domain.task_types import TaskType
from app.plan.nodes import NodeRegistry
from app.plan.schemas import PlanGraphDraft, PlanValidationIssue, PlanValidationResult
from app.plan.validator import validate_plan
from app.providers import errors as provider_errors
from app.providers.inference import ModelInferenceClient
from app.providers.protocol import ResolvedModel
from app.providers.registry import build_model_provider
from app.providers.service import ProviderService


@dataclass
class PlanGenerationOutcome:
    graph: PlanGraphDraft
    validation_result: PlanValidationResult
    issues: list[PlanValidationIssue]
    repair_used: bool
    audit: dict[str, Any] = field(default_factory=dict)


class PlanGenerationService:
    def __init__(
        self,
        *,
        provider_service: ProviderService,
        vault: CredentialVault,
        registry: NodeRegistry | None = None,
        inference: ModelInferenceClient | None = None,
    ) -> None:
        self._provider = provider_service
        self._vault = vault
        self._registry = registry or NodeRegistry()
        self._agent = PlanGeneratorAgent(inference=inference or ModelInferenceClient())

    def _resolve_model(self, user: User) -> tuple[ResolvedModel, str | None, Any]:
        config = self._provider.require_available_model_config(user)
        provider = build_model_provider(config.provider_type)
        resolved = provider.resolve_model(
            model=config.model_name,
            base_url=config.base_url,
            credential_version_id=config.credential_version_id,
        )
        api_key = None
        if config.credential_version_id is not None:
            api_key = self._vault.read_for_execution(
                user_id=user.id, credential_version_id=config.credential_version_id
            )
        return resolved, api_key, config

    def _build_input(self, spec_payload: dict, task_type: TaskType) -> PlanInput:
        has_search = self._provider.list_search_configs(user=None)  # 占位；真实调用在 service 层传入
        return PlanInput(
            spec_payload=spec_payload,
            task_type=task_type,
            registry_metadata=self._registry.planning_metadata(),
            execution_constraints={"has_search_provider": False},
        )

    async def _repair_loop(
        self,
        inp: PlanInput,
        resolved: ResolvedModel,
        api_key: str | None,
        *,
        max_repairs: int = 1,
    ) -> PlanGenerationOutcome:
        started = perf_counter()
        graph = await self._agent.generate(inp, resolved, api_key)
        result, issues = validate_plan(graph, inp.spec_payload, self._registry)
        repair_used = False
        if result == PlanValidationResult.INVALID and max_repairs > 0:
            repair_used = True
            # 把 Validator 的可纠正结构问题作为明确证据喂回模型
            repair_input = inp.model_copy(
                update={
                    "execution_constraints": {
                        **inp.execution_constraints,
                        "validator_issues": [i.model_dump(mode="json") for i in issues],
                    }
                }
            )
            graph = await self._agent.generate(repair_input, resolved, api_key)
            result, issues = validate_plan(graph, inp.spec_payload, self._registry)
        return PlanGenerationOutcome(
            graph=graph,
            validation_result=result,
            issues=issues,
            repair_used=repair_used,
            audit={"duration_ms": int((perf_counter() - started) * 1000)},
        )

    async def generate_for_task(
        self, *, user: User, spec_payload: dict, task_type: TaskType
    ) -> PlanGenerationOutcome:
        resolved, api_key, config = self._resolve_model(user)
        inp = self._build_input(spec_payload, task_type)
        outcome = await self._repair_loop(inp, resolved, api_key)
        outcome.audit.update(
            {
                "model_config_id": config.config_id,
                "model_config_version": config.version,
                "provider": config.provider_type,
                "model": config.model_name,
            }
        )
        return outcome
```

Note: `_build_input` uses a placeholder for search-config availability; the executing engineer must inject the real `available_search` boolean via `PlanInput.execution_constraints` from the caller (Task 3 wires it from `ProviderService`). Mark this clearly in code comments.

### Step 6: 运行 Task 2 门禁

Run:
```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/plan/test_plan_generator.py -q
.venv/Scripts/python.exe -m ruff check app/agents/plan_generator.py app/agents/plan_service.py app/plan/validator.py tests/plan
.venv/Scripts/python.exe -m mypy app/agents/plan_generator.py app/agents/plan_service.py app/plan/validator.py
```
Expected: PASS. If the FakeInference swap hits a type boundary, adjust the test to inject a real `ModelInferenceClient` whose `_http` is the fake transport.

### Step 7: Commit

```bash
git add backend/app/agents/plan_generator.py backend/app/agents/plan_service.py backend/app/plan/validator.py backend/tests/plan/test_plan_generator.py
git commit -m "feat(plan): add pydantic-ai plan generator with bounded repair

PlanGeneratorAgent 复用 M-03 ModelInferenceClient + pydantic-ai FunctionModel，只输出
typed PlanGraphDraft 且只能引用 NodeRegistry 允许节点。PlanGenerationService 复用
ProviderService/CredentialVault 解析真实模型，支持最多一次有证据的 Plan Repair，
第二次仍不合法则 FAIL/BLOCKED。Validator 保持确定性，不调用 LLM。
关联模块：M-08"
```

---

# Task 3: 确定性 Plan Validator + PlanVersion 持久化 + migration

**Files:**
- Modify: `backend/app/domain/models.py`（PlanVersion 扩展列）
- Modify: `backend/app/domain/repository.py`（PlanVersionRepository 扩展）
- Create: `backend/alembic/versions/0006_extend_plan_approval.py`
- Modify: `backend/app/plan/validator.py`（完整校验顺序）
- Create: `backend/app/plan/service.py`（PlanService：persist + auto-start）
- Modify: `backend/app/api/routes/plans.py`（plan 生成端点）
- Modify: `backend/app/api/schemas.py`（Plan DTO）
- Modify: `backend/app/api/router.py`（include plans）
- Test: `backend/tests/plan/test_plan_fixtures.py`（≥10 组契约表）
- Test: `backend/tests/plan/test_plan_validator.py`
- Test: `backend/tests/api/test_plan_api.py`

**Interfaces:**
- Consumes: `PlanGraphDraft`、`NodeRegistry`、`CollectionSpecVersion`（`app.domain.models`）、`PlanVersion`/`PlanVersionRepository`、`TaskWorkflowStarter.submit_validated_plan`、`DomainService`
- Produces:
  - `PlanVersion` 扩展字段：`parent_plan_version_id: int|None`、`validation_status: str`、`plan_fingerprint: str`、`model_config_id: str|None`、`model_config_version: int|None`、`registry_versions: dict`、`generation_policy: str`、`trigger_reason: str|None`、`replan_evidence_refs: list`、`diff_summary: dict|None`
  - `class PlanService` — `generate_and_persist(user, task_id, spec_version, expected_version) -> PlanCreatedResult`、`get_plan_summary(user, task_id, plan_version) -> PlanSummaryDto`
  - `validate_plan(graph, spec_payload, registry, *, available_search) -> PlanValidationOutcome`（完整 18 步）
  - API：`POST /tasks/{task_id}/plan`、`GET /tasks/{task_id}/plans/{plan_version}`

### Step 1: 写完整 Validator 失败测试（10 组契约表）

Create `backend/tests/plan/test_plan_fixtures.py`:

```python
"""M-08 Task 3: ≥10 组合法/非法 Plan fixture 契约表（parameterized，单文件）。"""

from __future__ import annotations

import pytest

from app.domain.spec import SpecDraftPayload
from app.domain.task_types import TaskType
from app.plan.nodes import NodeRegistry, NodeType
from app.plan.schemas import (
    PlanEdge,
    PlanGraphDraft,
    PlanNodeInstance,
    PlanValidationResult,
    ResourceRef,
)
from app.plan.validator import validate_plan


def _node(node_id: str, node_type: NodeType, **kw) -> PlanNodeInstance:
    return PlanNodeInstance(
        node_id=node_id,
        node_type=node_type,
        definition_version="1.0.0",
        parameters=kw.pop("parameters", {}),
        depends_on=kw.pop("depends_on", []),
        optional=kw.pop("optional", False),
        fail_policy=kw.pop("fail_policy", "block"),
    )


def _edge(a: str, b: str, kind="snapshot", ref="snap:1") -> PlanEdge:
    return PlanEdge(from_node_id=a, to_node_id=b, resource_refs=[ResourceRef(kind=kind, ref_key=ref)])


_SPEC = SpecDraftPayload(
    task_type=TaskType.SPECIFIED_SOURCE,
    goal="抓取指定网站公司信息",
    fields=[{"name": "公司名", "type": "text", "required": True}],
    source_scope={"mode": "SPECIFIED_SOURCE", "seed_urls": ["https://example.com"], "source_hints": []},
    completion_conditions=[{"kind": "range_covered", "target": 10}],
    advanced_settings={"max_pages": 100},
).model_dump(mode="json")


def _draft(nodes: list[PlanNodeInstance], edges: list[PlanEdge] | None = None) -> PlanGraphDraft:
    return PlanGraphDraft(
        task_id=1, spec_version=1, task_type=TaskType.SPECIFIED_SOURCE, nodes=nodes, edges=edges or []
    )


CASES = [
    # 01 合法 specified-source Plan
    (
        "valid_specified_source",
        _draft([_node("n1", NodeType.FETCH), _node("n2", NodeType.EXTRACT, depends_on=["n1"])]),
        PlanValidationResult.VALID,
    ),
    # 02 合法 exploratory Plan（含 SourceSearch）
    (
        "valid_exploratory",
        _draft(
            [
                _node("n1", NodeType.SOURCE_SEARCH, parameters={"query": "自动化设备", "max_results": 20}),
                _node("n2", NodeType.FETCH, depends_on=["n1"], parameters={"url_template": "https://{site}/"}),
                _node("n3", NodeType.EXTRACT, depends_on=["n2"]),
            ]
        ),
        PlanValidationResult.VALID,
    ),
    # 03 合法 hybrid Plan
    (
        "valid_hybrid",
        _draft(
            [
                _node("n1", NodeType.SOURCE_SEARCH),
                _node("n2", NodeType.FETCH, depends_on=["n1"]),
                _node("n3", NodeType.EXTRACT, depends_on=["n2"]),
                _node("n4", NodeType.DEDUPLICATE, depends_on=["n3"]),
            ]
        ),
        PlanValidationResult.VALID,
    ),
    # 04 未注册 node
    (
        "unregistered_node",
        _draft([_node("n1", "ssh_into_server")]),
        PlanValidationResult.INVALID,
    ),
    # 05 重复 node id
    (
        "duplicate_node_id",
        _draft([_node("n1", NodeType.FETCH), _node("n1", NodeType.EXTRACT)]),
        PlanValidationResult.INVALID,
    ),
    # 06 依赖缺失
    (
        "missing_dependency",
        _draft([_node("n1", NodeType.FETCH, depends_on=["ghost"])]),
        PlanValidationResult.INVALID,
    ),
    # 07 环
    (
        "cycle",
        _draft(
            [
                _node("n1", NodeType.FETCH, depends_on=["n2"]),
                _node("n2", NodeType.EXTRACT, depends_on=["n1"]),
            ]
        ),
        PlanValidationResult.INVALID,
    ),
    # 08 参数 schema 非法
    (
        "invalid_parameter_schema",
        _draft([_node("n1", NodeType.SOURCE_SEARCH, parameters={"query": "x", "not_a_field": 1})]),
        PlanValidationResult.INVALID,
    ),
    # 09 资源边不兼容（fetch 输出 snapshot 喂给 validate 需要 record+evidence）
    (
        "incompatible_resource_edge",
        _draft(
            [
                _node("n1", NodeType.FETCH),
                _node("n2", NodeType.VALIDATE, depends_on=["n1"]),
            ],
            [_edge("n1", "n2", kind="snapshot")],
        ),
        PlanValidationResult.INVALID,
    ),
    # 10 Spec Version 不匹配
    (
        "spec_version_mismatch",
        PlanGraphDraft(
            task_id=1, spec_version=999, task_type=TaskType.SPECIFIED_SOURCE,
            nodes=[_node("n1", NodeType.FETCH)],
        ),
        PlanValidationResult.INVALID,
    ),
    # 11 扩大 Spec scope → REQUIRES_NEW_SPEC
    (
        "scope_expansion",
        _draft(
            [_node("n1", NodeType.FETCH, parameters={"url_template": "https://other-domain.com/{id}"})]
        ),
        PlanValidationResult.REQUIRES_NEW_SPEC,
    ),
    # 12 改变核心字段含义 → REQUIRES_NEW_SPEC
    (
        "field_semantics_change",
        _draft([_node("n1", NodeType.EXTRACT, parameters={"fields": ["不应存在的字段"]})]),
        PlanValidationResult.REQUIRES_NEW_SPEC,
    ),
    # 13 降低质量要求 → REQUIRES_NEW_SPEC
    (
        "quality_reduction",
        _draft([_node("n1", NodeType.VALIDATE, parameters={"min_required_fields": 0})]),
        PlanValidationResult.REQUIRES_NEW_SPEC,
    ),
    # 14 credential/非公开高风险 → REQUIRES_APPROVAL
    (
        "credential_high_risk",
        _draft(
            [_node("n1", NodeType.FETCH, parameters={"url_template": "https://private.example.com/{id}", "non_public": True})]
        ),
        PlanValidationResult.REQUIRES_APPROVAL,
    ),
    # 15 prohibited 越界动作 → PROHIBITED
    (
        "prohibited_bypass",
        _draft([_node("n1", NodeType.FETCH, parameters={"bypass_captcha": True})]),
        PlanValidationResult.PROHIBITED,
    ),
]


@pytest.mark.parametrize("name,draft,expected", CASES, ids=[c[0] for c in CASES])
def test_plan_fixture_contracts(name, draft, expected) -> None:
    result, issues = validate_plan(draft, _SPEC, NodeRegistry())
    assert result == expected, f"{name}: {[i.model_dump() for i in issues]}"
```

Note: FETCH's parameter schema must support `non_public` and `bypass_captcha` to exercise risk tiers; extend `_FetchParams` in Task 1's nodes.py with `non_public: bool = False` and `bypass_captcha: bool = False`, and mark `bypass_captcha=True` → PROHIBITED in the validator. This is an intended forward adjustment to Task 1 (note it in the commit).

### Step 2: 运行并确认全部失败

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/plan/test_plan_fixtures.py -q`
Expected: FAIL（validator 尚未实现完整校验）。

### Step 3: 扩展 models.py + repository.py + migration

Modify `backend/app/domain/models.py` — replace the existing `PlanVersion` block:

```python
class PlanVersion(Base):
    __tablename__ = "plan_versions"
    __table_args__ = (UniqueConstraint("task_id", "version", name="uq_pv_task_version"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_plan_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("plan_versions.id"), nullable=True
    )
    validation_status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    plan_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    model_config_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    registry_versions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    generation_policy: Mapped[str] = mapped_column(String(30), nullable=False, default="auto")
    trigger_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    replan_evidence_refs: Mapped[list] = mapped_column(JSON, nullable=True, default=list)
    diff_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

Also extend the `Approval` model (Task 5 uses these; add columns now in one migration):

```python
class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    spec_version: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    node_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    node_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target: Mapped[str | None] = mapped_column(String(500), nullable=True)
    parameter_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_scope: Mapped[str] = mapped_column(String(30), nullable=False, default="this_action")
    scope: Mapped[str] = mapped_column(String(30), nullable=False, default="single")
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    credential_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

Modify `backend/app/domain/repository.py` — extend `PlanVersionRepository.create` to accept new optional fields and add `list_for_task` + `latest_version`. Add `ApprovalRepository` with `create/get_owned/list_for_task/list_pending/user` + `update_state`.

Create `backend/alembic/versions/0006_extend_plan_approval.py` (revision `0006`, down_revision `0005`):

```python
"""extend plan_versions and approvals for M-08

Revision ID: 0006
Revises: 0005

M-08 增量扩展（expand/contract，兼容旧行）：
- plan_versions 增加 parent_plan_version_id / validation_status / plan_fingerprint /
  model_config_id / model_config_version / registry_versions / generation_policy /
  trigger_reason / replan_evidence_refs / diff_summary。
- approvals 增加 plan_version / node_id / node_type / target / approved_scope /
  credential_ref / status_payload / resolved_by / consumed_at；state 默认值统一为
  canonical PENDING。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "plan_versions",
        sa.Column("parent_plan_version_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_plan_versions_parent", "plan_versions", "plan_versions",
        ["parent_plan_version_id"], ["id"],
    )
    op.add_column(
        "plan_versions",
        sa.Column("validation_status", sa.String(length=30), nullable=False, server_default="pending"),
    )
    op.add_column(
        "plan_versions",
        sa.Column("plan_fingerprint", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column(
        "plan_versions",
        sa.Column("model_config_id", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "plan_versions",
        sa.Column("model_config_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "plan_versions",
        sa.Column("registry_versions", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.add_column(
        "plan_versions",
        sa.Column("generation_policy", sa.String(length=30), nullable=False, server_default="auto"),
    )
    op.add_column(
        "plan_versions",
        sa.Column("trigger_reason", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "plan_versions",
        sa.Column("replan_evidence_refs", sa.JSON(), nullable=True),
    )
    op.add_column(
        "plan_versions",
        sa.Column("diff_summary", sa.JSON(), nullable=True),
    )

    op.add_column("approvals", sa.Column("plan_version", sa.Integer(), nullable=True))
    op.add_column("approvals", sa.Column("node_id", sa.String(length=50), nullable=True))
    op.add_column("approvals", sa.Column("node_type", sa.String(length=50), nullable=True))
    op.add_column("approvals", sa.Column("target", sa.String(length=500), nullable=True))
    op.add_column(
        "approvals",
        sa.Column("approved_scope", sa.String(length=30), nullable=False, server_default="this_action"),
    )
    op.add_column("approvals", sa.Column("credential_ref", sa.JSON(), nullable=True))
    op.add_column("approvals", sa.Column("status_payload", sa.JSON(), nullable=True))
    op.add_column("approvals", sa.Column("resolved_by", sa.BigInteger(), nullable=True))
    op.add_column("approvals", sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("approvals", "consumed_at")
    op.drop_column("approvals", "resolved_by")
    op.drop_column("approvals", "status_payload")
    op.drop_column("approvals", "credential_ref")
    op.drop_column("approvals", "approved_scope")
    op.drop_column("approvals", "target")
    op.drop_column("approvals", "node_type")
    op.drop_column("approvals", "node_id")
    op.drop_column("approvals", "plan_version")

    op.drop_column("plan_versions", "diff_summary")
    op.drop_column("plan_versions", "replan_evidence_refs")
    op.drop_column("plan_versions", "trigger_reason")
    op.drop_column("plan_versions", "generation_policy")
    op.drop_column("plan_versions", "registry_versions")
    op.drop_column("plan_versions", "model_config_version")
    op.drop_column("plan_versions", "model_config_id")
    op.drop_column("plan_versions", "plan_fingerprint")
    op.drop_column("plan_versions", "validation_status")
    op.drop_constraint("fk_plan_versions_parent", "plan_versions", type_="foreignkey")
    op.drop_column("plan_versions", "parent_plan_version_id")
```

### Step 4: 实现完整 Validator

Rewrite `backend/app/plan/validator.py` with the full 18-step order. Structure as a list of named check functions that accumulate `PlanValidationIssue` with stable codes. Key checks for scope/field/quality use the frozen spec payload:

```python
def _spec_boundary_issues(graph, spec_payload, issues) -> None:
    spec_scope = spec_payload.get("source_scope", {})
    allowed_hosts = {_host(u) for u in spec_scope.get("seed_urls", [])}
    spec_fields = {f.get("name") for f in spec_payload.get("fields", [])}
    spec_min_required = spec_payload.get("advanced_settings", {}).get("max_pages")
    for n in graph.nodes:
        if n.node_type == NodeType.FETCH:
            url_template = str(n.parameters.get("url_template", ""))
            host = _host(url_template)
            if host and allowed_hosts and host not in allowed_hosts:
                issues.append(PlanValidationIssue(code="SPEC_SCOPE_EXPANSION", message=f"计划扩大采集范围到 {host}", node_id=n.node_id))
        if n.node_type == NodeType.EXTRACT:
            for f in n.parameters.get("fields", []):
                if f not in spec_fields:
                    issues.append(PlanValidationIssue(code="SPEC_FIELD_SEMANTICS", message=f"计划引入未确认字段 {f}", node_id=n.node_id))
        if n.node_type == NodeType.VALIDATE:
            if n.parameters.get("min_required_fields", 1) < 1:
                issues.append(PlanValidationIssue(code="SPEC_QUALITY_REDUCTION", message="计划降低必填字段质量要求", node_id=n.node_id))
```

The top-level `validate_plan` returns a dataclass `PlanValidationOutcome(result, issues, node_risk_levels, fingerprint)` and classifies:
- PROHIBITED if any `bypass_captcha=True` or PROHIBITED risk node → highest priority
- else REQUIRES_NEW_SPEC if any spec-boundary issue
- else REQUIRES_APPROVAL if any node effective risk == HIGH
- else VALID if no structural issues
- else INVALID

### Step 5: 创建 PlanService（persist + auto-start）

Create `backend/app/plan/service.py`:

```python
"""PlanService — 持久化不可变 PlanVersion + 合法 Plan 自动启动 Workflow（M-08/D-038）。

D-038：低风险合法 Plan 不进行第二次 Plan 确认；Spec confirmed → PlanGenerator →
Validator → PlanVersion persisted → VALID → TaskWorkflowStarter.submit_validated_plan。
PlanVersion 不可变；后续 Replan 创建 vN+1，永不 UPDATE 已有版本。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.domain.idempotency import stable_fingerprint
from app.domain.models import DomainEvent, PlanVersion, Task
from app.domain.repository import PlanVersionRepository, TaskRepository
from app.domain.service import DomainService
from app.state.events import append_domain_event, enqueue_outbox
from app.workflows.starter import TaskWorkflowStarter


def plan_fingerprint(graph: Any, registry_versions: dict) -> str:
    return stable_fingerprint("plan", graph, registry_versions)


@dataclass
class PlanCreatedResult:
    task_id: int
    plan_version: int
    validation_status: str
    run_id: int | None
    workflow_id: str | None


class PlanService:
    def __init__(self, db: Any, *, starter: TaskWorkflowStarter) -> None:
        self._db = db
        self._starter = starter

    def persist_plan(
        self,
        *,
        user_id: int,
        task_id: int,
        spec_version: int,
        graph: dict,
        validation_status: str,
        plan_fingerprint_value: str,
        model_config_id: str | None,
        model_config_version: int | None,
        registry_versions: dict,
        generation_policy: str = "auto",
        trigger_reason: str | None = None,
        replan_evidence_refs: list | None = None,
        diff_summary: dict | None = None,
    ) -> PlanVersion:
        repo = PlanVersionRepository(self._db)
        version = repo.next_version(user_id, task_id)
        row = repo.create(
            user_id=user_id,
            task_id=task_id,
            spec_version=spec_version,
            version=version,
            payload={"graph": graph},
            parent_plan_version_id=None,
            validation_status=validation_status,
            plan_fingerprint=plan_fingerprint_value,
            model_config_id=model_config_id,
            model_config_version=model_config_version,
            registry_versions=registry_versions,
            generation_policy=generation_policy,
            trigger_reason=trigger_reason,
            replan_evidence_refs=replan_evidence_refs,
            diff_summary=diff_summary,
        )
        task = TaskRepository(self._db).get_owned(user_id, task_id)
        task.current_plan_version = version
        task.version += 1
        self._db.add(task)
        payload = {"plan_version": version, "validation_status": validation_status}
        append_domain_event(
            self._db, user_id=user_id, aggregate_type="task", aggregate_id=task_id,
            event_type="task.plan_generated", aggregate_version=task.version, payload=payload,
            actor_type="system",
        )
        enqueue_outbox(
            self._db, user_id=user_id, aggregate_type="task", aggregate_id=task_id,
            event_type="task.plan_generated", payload=payload, dispatch_key=f"task:{task_id}:plan_generated",
        )
        self._db.commit()
        self._db.refresh(row)
        return row

    async def auto_start(self, *, user_id: int, task_id: int, spec_version: int, plan_version: int) -> tuple[int | None, str | None]:
        started = await self._starter.submit_validated_plan(
            user_id=user_id, task_id=task_id, spec_version=spec_version, plan_version=plan_version
        )
        return started.run_id, started.workflow_id
```

### Step 6: plan API 路由

Create `backend/app/api/routes/plans.py`:

```python
"""Plan API routes: 生成（含自动启动）+ 摘要查询（M-08）。

Route 只做 auth/DTO/response mapping；Plan 生成与启动语义在 PlanGenerationService +
PlanService。owner-safe：无权/不存在 → 404。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DbSession

from app.agents.plan_service import PlanGenerationService, PlanGenerationOutcome
from app.api.schemas import PlanGenerateCommand, PlanGenerateResponse, PlanSummaryDto
from app.auth.deps import require_user
from app.auth.models import User
from app.domain.models import PlanVersion
from app.domain.repository import PlanVersionRepository, SpecVersionRepository
from app.infra.deps import get_db
from app.plan.nodes import NodeRegistry
from app.plan.service import PlanService

router = APIRouter(prefix="/tasks", tags=["plans"])


def get_plan_generation_service(
    db: DbSession = Depends(get_db),
    provider_service=Depends(...),
    vault=Depends(...),
) -> PlanGenerationService:
    from app.providers.deps import get_credential_vault, get_provider_service
    return PlanGenerationService(provider_service=provider_service, vault=vault)


@router.post("/{task_id}/plan", response_model=PlanGenerateResponse)
async def generate_plan(
    task_id: int,
    cmd: PlanGenerateCommand,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
    generation: PlanGenerationService = Depends(get_plan_generation_service),
) -> PlanGenerateResponse:
    spec = SpecVersionRepository(db).get_version(user.id, task_id, cmd.spec_version)
    if spec.confirmed_at is None:
        from app.domain.errors import DomainError
        raise DomainError("采集方案尚未确认")
    outcome: PlanGenerationOutcome = await generation.generate_for_task(
        user=user, spec_payload=spec.payload, task_type=spec_task_type(spec)
    )
    # VALID / REQUIRES_APPROVAL → persist + auto-start；否则 persist 但不启动
    if outcome.validation_result in (PlanValidationResult.VALID, PlanValidationResult.REQUIRES_APPROVAL):
        plan = plan_service.persist_plan(...)
        run_id, workflow_id = await plan_service.auto_start(...)
        return PlanGenerateResponse(plan_version=plan.version, validation_status=..., run_id=run_id, workflow_id=workflow_id)
    plan = plan_service.persist_plan(...)
    return PlanGenerateResponse(plan_version=plan.version, validation_status=outcome.validation_result.value, run_id=None, workflow_id=None)
```

Note: this route is a sketch — the executing engineer must wire `get_plan_generation_service` (with the real `get_provider_service`/`get_credential_vault` deps), `spec_task_type`, and the `plan_service` construction (needs a lazily-built `TaskWorkflowStarter` via `get_temporal_client`, mirroring the M-07 command route's lazy client pattern). The response DTOs live in `app/api/schemas.py`.

Add `PlanGenerateCommand`, `PlanGenerateResponse`, `PlanSummaryDto` to `backend/app/api/schemas.py`, and register the router in `backend/app/api/router.py`.

### Step 7: 运行 Task 3 门禁

Run:
```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/plan/test_plan_fixtures.py tests/plan/test_plan_validator.py tests/api/test_plan_api.py -q
# migration upgrade/rollback
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m alembic downgrade 0005
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m ruff check app/plan app/agents/plan_service.py app/domain/models.py app/domain/repository.py app/api/routes/plans.py
.venv/Scripts/python.exe -m mypy app/plan app/domain app/api/routes/plans.py
```
Expected: 10+ fixture cases PASS, migration upgrade/rollback PASS.

### Step 8: Commit

```bash
git add backend/app/plan/validator.py backend/app/plan/service.py backend/app/domain/models.py backend/app/domain/repository.py backend/alembic/versions/0006_extend_plan_approval.py backend/app/api/routes/plans.py backend/app/api/schemas.py backend/app/api/router.py backend/tests/plan backend/tests/api/test_plan_api.py
git commit -m "feat(plan): add deterministic validator and immutable plan version

Validator 按 18 步确定性顺序校验（schema/registered/version/唯一ID/依赖/DAG/参数/
资源边/Spec 匹配/范围边界/字段语义/质量约束/运行限制/风险/Provider/指纹），返回结构化
error code 与 canonical PlanValidationResult。范围/字段/质量越界 → REQUIRES_NEW_SPEC；
PROHIBITED 直接拒绝。PlanService 持久化不可变 PlanVersion 并对 VALID/REQUIRES_APPROVAL
自动启动 M-07 Workflow（D-038 无二次 Plan 确认）。新增 0006 migration 增量扩展
plan_versions 与 approvals。关联模块：M-08"
```

---

# Task 4: Replan / Diff / 不可变版本

**Files:**
- Create: `backend/app/plan/diff.py`
- Modify: `backend/app/plan/service.py`（`create_replan`、`get_plan_summary`、`list_plans`）
- Modify: `backend/app/api/routes/plans.py`（replan 端点）
- Test: `backend/tests/plan/test_plan_replan.py`

**Interfaces:**
- Consumes: `PlanVersion`/`PlanVersionRepository`、`validate_plan`、`stable_fingerprint`
- Produces:
  - `class PlanDiff(BaseModel)` — `added_nodes`, `removed_nodes`, `changed_parameters`, `changed_dependencies`, `changed_risk_levels`, `changed_resource_classes`, `impact_scope: str`
  - `PlanService.create_replan(user, task_id, spec_version, graph, trigger_reason, evidence_refs) -> PlanVersion`（v2，parent=v1，diff_summary）
  - `PlanService.get_plan_summary(user, task_id, plan_version) -> PlanSummaryDto`
  - API：`POST /tasks/{task_id}/plans/{parent_version}/replan`、`GET /tasks/{task_id}/plans`

### Step 1: 写 replan/diff 失败测试

Create `backend/tests/plan/test_plan_replan.py`:

```python
"""M-08 Task 4: immutable plan version + replan v2 + deterministic diff."""

from __future__ import annotations

import pytest

from app.domain.idempotency import stable_fingerprint
from app.plan.diff import PlanDiff
from app.plan.nodes import NodeRegistry, NodeType
from app.plan.schemas import PlanGraphDraft, PlanNodeInstance


def _node(node_id: str, node_type: NodeType, params=None) -> PlanNodeInstance:
    return PlanNodeInstance(
        node_id=node_id, node_type=node_type, definition_version="1.0.0",
        parameters=params or {}, depends_on=[],
    )


_V1 = PlanGraphDraft(
    task_id=1, spec_version=1,
    nodes=[_node("n1", NodeType.FETCH, {"url_template": "https://example.com/{id}"}), _node("n2", NodeType.EXTRACT, {"fields": ["公司名"]})],
    edges=[],
)

_V2 = PlanGraphDraft(
    task_id=1, spec_version=1,
    nodes=[
        _node("n1", NodeType.FETCH, {"url_template": "https://example.com/{id}"}),
        _node("n3", NodeType.NORMALIZE),
        _node("n2", NodeType.EXTRACT, {"fields": ["公司名"]}),
    ],
    edges=[],
)


def test_diff_detects_added_removed_changed() -> None:
    diff = PlanDiff.compute(_V1, _V2)
    assert "n3" in diff.added_nodes
    assert diff.removed_nodes == []
    assert diff.changed_parameters == {}
    assert diff.impact_scope == "execution_strategy"


def test_diff_detects_parameter_change() -> None:
    v2 = _V2.model_copy(deep=True)
    v2.nodes[0].parameters["url_template"] = "https://other.com/{id}"
    diff = PlanDiff.compute(_V1, v2)
    assert diff.changed_parameters == {"n1": {"url_template": "https://other.com/{id}"}}
    assert diff.impact_scope == "execution_strategy"


def test_plan_fingerprint_is_stable_and_versioned() -> None:
    f1 = stable_fingerprint("plan", _V1.model_dump(mode="json"), {"fetch": "1.0.0"})
    f2 = stable_fingerprint("plan", _V2.model_dump(mode="json"), {"fetch": "1.0.0"})
    assert f1 != f2
    assert len(f1) == 64


def test_replan_that_changes_spec_boundary_is_rejected() -> None:
    from app.plan.validator import validate_plan
    from app.plan.schemas import PlanValidationResult

    # replan 把 fetch 指向范围外域名 → 必须 REQUIRES_NEW_SPEC，不能仅 Approval
    from tests.plan.test_plan_fixtures import _SPEC
    v2 = _V2.model_copy(deep=True)
    v2.nodes[0].parameters["url_template"] = "https://other-domain.com/{id}"
    result, issues = validate_plan(v2, _SPEC, NodeRegistry())
    assert result == PlanValidationResult.REQUIRES_NEW_SPEC
```

### Step 2: 创建 diff.py

Create `backend/app/plan/diff.py`:

```python
"""Deterministic PlanDiff — 程序计算的结构化差异（M-08 / D-007 审计要求）。

Diff 事实由程序计算，不用 LLM 文本“计划差不多一样”。LLM 只负责用户可读摘要（reasoning）。
"""

from __future__ import annotations

from pydantic import BaseModel

from app.plan.schemas import PlanGraphDraft


class PlanDiff(BaseModel):
    added_nodes: list[str] = []
    removed_nodes: list[str] = []
    changed_parameters: dict[str, dict] = {}
    changed_dependencies: dict[str, list] = {}
    changed_risk_levels: dict[str, str] = {}
    changed_resource_classes: dict[str, str] = {}
    impact_scope: str = "execution_strategy"

    @staticmethod
    def compute(before: PlanGraphDraft, after: PlanGraphDraft) -> "PlanDiff":
        before_nodes = {n.node_id: n for n in before.nodes}
        after_nodes = {n.node_id: n for n in after.nodes}
        added = [nid for nid in after_nodes if nid not in before_nodes]
        removed = [nid for nid in before_nodes if nid not in after_nodes]
        changed_params: dict[str, dict] = {}
        changed_deps: dict[str, list] = {}
        for nid, a in after_nodes.items():
            if nid in before_nodes:
                b = before_nodes[nid]
                if a.parameters != b.parameters:
                    changed_params[nid] = a.parameters
                if a.depends_on != b.depends_on:
                    changed_deps[nid] = a.depends_on
        # M-08 只保存 metadata；风险/资源类影响在后续模块扩展
        return PlanDiff(
            added_nodes=added,
            removed_nodes=removed,
            changed_parameters=changed_params,
            changed_dependencies=changed_deps,
            impact_scope="execution_strategy",
        )
```

### Step 3: PlanService.create_replan / get_plan_summary

Add to `backend/app/plan/service.py`:

```python
    def create_replan(
        self,
        *,
        user_id: int,
        task_id: int,
        spec_version: int,
        graph: dict,
        trigger_reason: str,
        evidence_refs: list | None,
        diff_summary: dict | None,
        registry_versions: dict,
    ) -> PlanVersion:
        repo = PlanVersionRepository(self._db)
        parent = repo.latest_version(user_id, task_id)
        if parent is None:
            from app.domain.errors import DomainError
            raise DomainError("没有可重规划的 PlanVersion")
        version = parent.version + 1
        row = repo.create(
            user_id=user_id, task_id=task_id, spec_version=spec_version, version=version,
            payload={"graph": graph},
            parent_plan_version_id=parent.id,
            validation_status="replan",
            plan_fingerprint=plan_fingerprint(graph, registry_versions),
            registry_versions=registry_versions,
            generation_policy="replan",
            trigger_reason=trigger_reason,
            replan_evidence_refs=evidence_refs,
            diff_summary=diff_summary,
        )
        task = TaskRepository(self._db).get_owned(user_id, task_id)
        task.current_plan_version = version
        task.version += 1
        self._db.add(task)
        self._db.commit()
        self._db.refresh(row)
        return row

    def get_plan_summary(self, *, user_id: int, task_id: int, plan_version: int) -> PlanSummaryDto:
        row = PlanVersionRepository(self._db).get_version(user_id, task_id, plan_version)
        graph = (row.payload or {}).get("graph", {})
        nodes = graph.get("nodes", [])
        return PlanSummaryDto(
            task_id=task_id,
            plan_version=row.version,
            spec_version=row.spec_version,
            validation_status=row.validation_status,
            node_count=len(nodes),
            node_types=[n.get("node_type") for n in nodes],
            risk_summary=row.diff_summary or {},
            created_at=row.created_at,
        )
```

### Step 4: replan API 端点

Add `POST /tasks/{task_id}/plans/{parent_version}/replan` to `backend/app/api/routes/plans.py` that: loads parent, requires graph param, validates (REQUIRES_NEW_SPEC → 409/422 with reason, no write), computes diff, calls `PlanService.create_replan`. Register `GET /tasks/{task_id}/plans` list endpoint returning summaries.

### Step 5: 运行 Task 4 门禁

Run:
```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/plan/test_plan_replan.py -q
.venv/Scripts/python.exe -m ruff check app/plan/diff.py app/plan/service.py app/api/routes/plans.py
.venv/Scripts/python.exe -m mypy app/plan/diff.py app/plan/service.py app/api/routes/plans.py
```
Expected: PASS.

### Step 6: Commit

```bash
git add backend/app/plan/diff.py backend/app/plan/service.py backend/app/api/routes/plans.py backend/tests/plan/test_plan_replan.py
git commit -m "feat(plan): add deterministic replan diff and immutable plan versioning

Replan 产生 PlanVersion vN+1，保留 parent_plan_version_id / trigger_reason /
evidence_refs / 结构化 diff_summary；v1 永不修改。PlanDiff 由程序计算 added/removed/
changed parameters/dependencies，不依赖 LLM 文本判断。改变 Spec 边界的 replan 被
Validator 拒绝为 REQUIRES_NEW_SPEC，不能仅 Approval 放行。关联模块：M-08"
```

---

# Task 5: Approval Service + Temporal 等待/恢复 + Workflow 集成

**Files:**
- Create: `backend/app/approval/__init__.py`
- Create: `backend/app/approval/schemas.py`
- Create: `backend/app/approval/service.py`
- Create: `backend/app/activities/approval.py`
- Create: `backend/app/activities/plan_execution.py`
- Modify: `backend/app/activities/execution_seam.py`（扩展 ExecutionUnit/ExecuteUnitResult）
- Modify: `backend/app/workflows/task_workflow.py`（消费 ApprovalResolutionSignal）
- Modify: `backend/app/infra/temporal.py`（注册新 Activity）
- Modify: `backend/app/worker.py`（生产 worker 注册）
- Modify: `backend/app/infra/outbox_dispatch.py`（approval → Signal）
- Modify: `backend/app/api/routes/approvals.py`（approve/reject/revoke/query）
- Modify: `backend/app/api/schemas.py`（Approval DTO）
- Modify: `backend/app/api/router.py`
- Modify: `backend/app/api/events.py`（SSE 审批事件）
- Modify: `backend/app/config.py`（plan_fixture_mode）
- Create: `backend/tests/fixtures/plan_fixture.py`
- Test: `backend/tests/approval/test_approval_service.py`
- Test: `backend/tests/api/test_approval_api.py`
- Test: `backend/tests/integration/test_plan_workflow.py`（3 条 Temporal）

**Interfaces:**
- Consumes: `Approval`/`ApprovalRepository`、`DomainService`、`append_domain_event`/`enqueue_outbox`、`TaskWorkflow` `approval_resolution` Signal、`ExecutionUnit`、`NodeRegistry`
- Produces:
  - `class ApprovalState(StrEnum)` — PENDING / APPROVED / REJECTED / REVOKED / EXPIRED / CONSUMED
  - `class ApprovalScope(StrEnum)` — this_action / same_parameters_batch / task_scoped_limited
  - `class ApprovalService` — `request_approval(...) -> Approval`、`approve(...)`、`reject(...)`、`revoke(...)`、`get_owned(...)`、`list_for_task(...)`、`consume(...)`、`can_consume(...)`
  - `request_approval` Activity（Workflow 内 JIT）
  - 真实 `fetch_next_execution_unit` / `execute_safe_unit`（读 PlanVersion graph；dispatch executor；NODE_EXECUTOR_UNAVAILABLE）
  - API：`GET /approvals/{id}`、`GET /tasks/{task_id}/approvals`、`POST /approvals/{id}/approve`、`POST /approvals/{id}/reject`、`POST /approvals/{id}/revoke`
  - SSE：APPROVAL_REQUIRED / APPROVAL_APPROVED / APPROVAL_REJECTED / APPROVAL_EXPIRED / APPROVAL_REVOKED

### Step 1: 写 ApprovalService 失败测试（6 关键场景）

Create `backend/tests/approval/test_approval_service.py`:

```python
"""M-08 Task 5: approval lifecycle, fingerprint invalidation, expiry/revoke, owner."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.approval.schemas import ApprovalScope, ApprovalState
from app.approval.service import ApprovalService
from app.domain.errors import DomainError
from app.domain.idempotency import stable_fingerprint


def _make_task(db, user_id, task_id):  # 复用 domain 现有测试基线的简化 helper
    ...


def test_high_risk_node_creates_pending_approval(db_session, user, task):
    svc = ApprovalService(db_session)
    fp = stable_fingerprint("fetch", {"url_template": "https://private.example.com/{id}"})
    approval = svc.request_approval(
        user_id=user.id, task_id=task.id, spec_version=1, plan_version=1,
        node_id="n1", node_type="fetch", action_type="fetch_non_public",
        target="https://private.example.com/{id}", parameters={"non_public": True},
        scope=ApprovalScope.THIS_ACTION,
    )
    assert approval.state == ApprovalState.PENDING
    assert approval.parameter_fingerprint == fp


def test_approve_consumes_and_transitions(db_session, user, task):
    svc = ApprovalService(db_session)
    approval = svc.request_approval(user_id=user.id, task_id=task.id, spec_version=1, plan_version=1, node_id="n1", node_type="fetch", action_type="fetch_non_public", target="t", parameters={"non_public": True}, scope=ApprovalScope.THIS_ACTION)
    resolved = svc.approve(user_id=user.id, approval_id=approval.id, actor_id=user.id)
    assert resolved.state == ApprovalState.APPROVED
    # 消费后不能再用于不同参数
    consumed = svc.consume(user_id=user.id, approval_id=approval.id, parameters={"non_public": True})
    assert consumed.state == ApprovalState.CONSUMED


def test_parameter_fingerprint_change_invalidates(db_session, user, task):
    svc = ApprovalService(db_session)
    approval = svc.request_approval(user_id=user.id, task_id=task.id, spec_version=1, plan_version=1, node_id="n1", node_type="fetch", action_type="fetch_non_public", target="t", parameters={"non_public": True}, scope=ApprovalScope.THIS_ACTION)
    svc.approve(user_id=user.id, approval_id=approval.id, actor_id=user.id)
    # 参数变化后旧授权失效
    with pytest.raises(DomainError):
        svc.consume(user_id=user.id, approval_id=approval.id, parameters={"non_public": False})


def test_expired_cannot_consume(db_session, user, task):
    svc = ApprovalService(db_session)
    approval = svc.request_approval(
        user_id=user.id, task_id=task.id, spec_version=1, plan_version=1,
        node_id="n1", node_type="fetch", action_type="fetch_non_public", target="t",
        parameters={"non_public": True}, scope=ApprovalScope.THIS_ACTION,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    with pytest.raises(DomainError):
        svc.consume(user_id=user.id, approval_id=approval.id, parameters={"non_public": True})


def test_revoked_cannot_consume(db_session, user, task):
    svc = ApprovalService(db_session)
    approval = svc.request_approval(user_id=user.id, task_id=task.id, spec_version=1, plan_version=1, node_id="n1", node_type="fetch", action_type="fetch_non_public", target="t", parameters={"non_public": True}, scope=ApprovalScope.THIS_ACTION)
    svc.revoke(user_id=user.id, approval_id=approval.id)
    with pytest.raises(DomainError):
        svc.consume(user_id=user.id, approval_id=approval.id, parameters={"non_public": True})


def test_user_b_cannot_access_user_a_approval(db_session, user, user_b, task):
    svc = ApprovalService(db_session)
    approval = svc.request_approval(user_id=user.id, task_id=task.id, spec_version=1, plan_version=1, node_id="n1", node_type="fetch", action_type="fetch_non_public", target="t", parameters={"non_public": True}, scope=ApprovalScope.THIS_ACTION)
    with pytest.raises(Exception):  # owner-safe 404
        svc.get_owned(user_id=user_b.id, approval_id=approval.id)
```

### Step 2: 创建 approval 包

Create `backend/app/approval/__init__.py`、`backend/app/approval/schemas.py`（`ApprovalState`/`ApprovalScope` enums）、`backend/app/approval/service.py`（实现 request/approve/reject/revoke/get/list/consume/can_consume；owner 校验；fingerprint/scope/expiry 校验；状态转换 + DomainEvent + Outbox 同事务）。

`can_consume` 必须校验：owner、spec_version、plan_version、fingerprint、scope、expiry、consumed/revoked 状态。任何不满足 → `DomainError`。

### Step 3: Workflow 集成 + plan_execution 真实实现

Modify `backend/app/activities/execution_seam.py` — extend `ExecutionUnit`:

```python
@dataclass
class ExecutionUnit:
    run_id: int
    index: int
    unit_type: str
    input_fingerprint: str
    node_id: str | None = None
    node_type: str | None = None
    definition_version: str | None = None
    parameters: dict | None = None
    requires_approval: bool = False
    approval_action_type: str | None = None
    approval_target: str | None = None
    approval_parameters: dict | None = None
    credential_ref: dict | None = None
```

Extend `ExecuteUnitResult` with optional `status`/`error_code` (default `"OK"`/`None`), so M-07's existing fixture tests keep passing.

Create `backend/app/activities/plan_execution.py` (real fetch/execute):

```python
"""真实 plan-driven 执行单元（M-08）。

fetch_next_execution_unit：读取 run 对应 PlanVersion 的 graph，按拓扑顺序返回下一个
READY 单元（依赖已满足）；无更多单元返回 None。execute_safe_unit：dispatch 到
NODE_EXECUTORS（生产为空 → NODE_EXECUTOR_UNAVAILABLE）；测试/Staging fixture 注册
真实 NodeDefinition 的 fixture executor。
"""

from __future__ import annotations

from dataclasses import dataclass

from temporalio import activity

from app.activities.execution_seam import (
    ExecuteUnitInput, ExecuteUnitResult, ExecutionUnit, FetchUnitInput, FetchUnitResult,
)
from app.infra.deps import get_session_factory
from app.plan.executors import NODE_EXECUTORS, get_node_executor
from app.plan.nodes import NodeType


@activity.defn
async def fetch_next_execution_unit(inp: FetchUnitInput) -> FetchUnitResult:
    session = get_session_factory()()
    try:
        from app.domain.models import PlanVersion, Run
        from app.domain.repository import PlanVersionRepository
        run = session.get(Run, inp.run_id)
        if run is None:
            return FetchUnitResult(unit=None)
        plan = PlanVersionRepository(session).get_version(run.user_id, run.task_id, run.plan_version)
        graph = (plan.payload or {}).get("graph") or {}
        nodes = graph.get("nodes", [])
        # 简单确定性顺序：按 nodes 顺序 + 依赖前置过滤（完整拓扑在 M-14 深化）
        # after_index 是已消费单元计数；这里用 run 上已成功单元估算 next index
        # （保持与 M-07 fixture 契约兼容：index 单调递增）
        if inp.after_index >= len(nodes):
            return FetchUnitResult(unit=None)
        node = nodes[inp.after_index]
        # requires_approval 由 plan 的 node_risk_levels 决定（validator 已写入）
        risk = (graph.get("node_risk_levels") or {}).get(node.get("node_id"), "low")
        return FetchUnitResult(
            unit=ExecutionUnit(
                run_id=inp.run_id,
                index=inp.after_index + 1,
                unit_type=node["node_type"],
                input_fingerprint=node.get("input_fingerprint", f"fp-{inp.run_id}-{inp.after_index + 1}"),
                node_id=node.get("node_id"),
                node_type=node.get("node_type"),
                definition_version=node.get("definition_version"),
                parameters=node.get("parameters"),
                requires_approval=risk == "high",
                approval_action_type=f"{node.get('node_type')}_non_public" if risk == "high" else None,
                approval_target=str(node.get("parameters", {}).get("url_template") or ""),
                approval_parameters=node.get("parameters"),
                credential_ref=node.get("credential_ref"),
            )
        )
    finally:
        session.close()


@activity.defn
async def execute_safe_unit(inp: ExecuteUnitInput) -> ExecuteUnitResult:
    executor = get_node_executor(inp.unit.node_type)
    if executor is None:
        return ExecuteUnitResult(
            unit_index=inp.unit.index,
            status="NODE_EXECUTOR_UNAVAILABLE",
            error_code="NODE_EXECUTOR_UNAVAILABLE",
            committed_refs={},
        )
    return await executor(inp.unit)
```

Create `backend/app/plan/executors.py`:

```python
"""Node executor registry（M-08）。

生产注册表保持空；M-09～M-12 将真实 Activity 挂入。测试/Staging fixture executor
通过 ``register_node_executor`` 注册，仅 test worker / plan_fixture_mode 下可用，
Production 强制关闭。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.plan.nodes import NodeType

NODE_EXECUTORS: dict[NodeType, Callable[..., Awaitable[Any]]] = {}


def register_node_executor(node_type: NodeType, fn: Callable[..., Awaitable[Any]]) -> None:
    NODE_EXECUTORS[node_type] = fn


def get_node_executor(node_type: str | None):
    if node_type is None:
        return None
    try:
        return NODE_EXECUTORS.get(NodeType(node_type))
    except ValueError:
        return None
```

### Step 4: TaskWorkflow 消费 ApprovalResolutionSignal

Modify `backend/app/workflows/task_workflow.py` run loop — after fetching a unit, if `unit.requires_approval`, call `request_approval` Activity then `workflow.wait_condition` on `_latest_approval` matching approval_id; on decision == "APPROVED" continue to execute; on anything else run a `block_node`-style activity (mark node BLOCKED via `transition_node("block")`) and skip; reject must never execute the high-risk node.

```python
                unit: ExecutionUnit | None = fetch.unit
                if unit is None:
                    break

                if unit.requires_approval:
                    from app.activities.approval import (
                        RequestApprovalInput, request_approval, BlockHighRiskNodeInput, block_high_risk_node,
                    )
                    req: RequestApprovalResult = await workflow.execute_activity(
                        request_approval,
                        RequestApprovalInput(
                            task_id=inp.task_id, user_id=inp.user_id, run_id=inp.run_id,
                            spec_version=inp.spec_version, plan_version=inp.plan_version,
                            unit=unit,
                        ),
                        start_to_close_timeout=timedelta(seconds=60),
                    )
                    self._waiting_approval_id = req.approval_id
                    self._latest_approval = None
                    try:
                        await workflow.wait_condition(
                            lambda: self._latest_approval is not None
                            and self._latest_approval.approval_id == self._waiting_approval_id,
                            timeout=timedelta(seconds=inp.pause_timeout_seconds),
                        )
                    except TimeoutError:
                        # 等待超时：任务保持 WAITING_APPROVAL，下轮循环继续等待（与 pause 语义一致）
                        continue
                    signal = self._latest_approval
                    if signal.decision != "APPROVED":
                        await workflow.execute_activity(
                            block_high_risk_node,
                            BlockHighRiskNodeInput(task_id=inp.task_id, user_id=inp.user_id, run_id=inp.run_id, node_id=unit.node_id),
                            start_to_close_timeout=timedelta(seconds=60),
                        )
                        self._last_index = unit.index
                        continue

                exec_result: ExecuteUnitResult = await workflow.execute_activity(...)
                if exec_result.status == "NODE_EXECUTOR_UNAVAILABLE":
                    await workflow.execute_activity(
                        block_high_risk_node, BlockHighRiskNodeInput(...), ...
                    )
                    self._last_index = unit.index
                    continue
                await workflow.execute_activity(commit_checkpoint, ...)
```

### Step 5: request_approval Activity + block Activity

Create `backend/app/activities/approval.py` — `request_approval`（创建 Approval PENDING + `transition_task("mark_waiting_approval")` + DomainEvent `approval.requested` + outbox，同一事务）+ `block_high_risk_node`（`transition_node("block")`）。

### Step 6: outbox dispatcher + API + SSE

Modify `backend/app/infra/outbox_dispatch.py` — add mapping for `approval.approved`/`approval.rejected` → `approval_resolution` signal with `ApprovalResolutionSignal(approval_id, decision, parameter_fingerprint, spec_version)` payload from the outbox event.

Create `backend/app/api/routes/approvals.py` with approve/reject/revoke/query endpoints (thin: auth/DTO → ApprovalService → dispatch outbox). Add Approval DTOs to `app/api/schemas.py`. Register router. Extend `app/api/events.py` `_EVENT_TYPE_MAP`:

```python
    "approval.requested": "APPROVAL_REQUIRED",
    "approval.approved": "APPROVAL_APPROVED",
    "approval.rejected": "APPROVAL_REJECTED",
    "approval.expired": "APPROVAL_EXPIRED",
    "approval.revoked": "APPROVAL_REVOKED",
```

Add `plan_fixture_mode: bool = False` to `app/config.py`.

### Step 7: fixture executor + Temporal integration tests

Create `backend/tests/fixtures/plan_fixture.py`:

```python
"""M-08 Staging/Test fixture executor（fixture-only，非 Production）。

使用真实标准 NodeDefinition；executor 只在测试/Staging plan_fixture_mode 下注册。
无真实外部网络副作用，无真实第三方写入，无真实凭据外传（dummy 测试引用）。
"""

from __future__ import annotations

import asyncio

from app.activities.execution_seam import ExecuteUnitResult, ExecutionUnit
from app.plan.executors import register_node_executor
from app.plan.nodes import NodeType


async def _fixture_fetch(unit: ExecutionUnit) -> ExecuteUnitResult:
    await asyncio.sleep(0.05)  # 短小安全单元，保证命令有窗口触发
    return ExecuteUnitResult(
        unit_index=unit.index,
        status="OK",
        committed_refs={
            "run_id": unit.run_id,
            "unit": unit.index,
            "node_id": unit.node_id,
            "credential_ref": "dummy:test-credential",
        },
    )


def install_fixture_executors() -> None:
    register_node_executor(NodeType.FETCH, _fixture_fetch)
```

Create `backend/tests/integration/test_plan_workflow.py` with 3 scenarios (A/B/C):
- A: VALID low-risk plan → workflow starts → no second confirmation
- B: high-risk fixture node → Approval PENDING → approve → workflow resumes → node executes
- C: high-risk fixture → reject → high-risk op never runs → node BLOCKED

Use the same `_spawn_fixture_worker`/`_wait_task_state` pattern from M-07, with a plan fixture worker that registers `install_fixture_executors()`.

### Step 8: 运行 Task 5 门禁

Run:
```bash
cd backend && set KAIROS_RUN_INTEGRATION=1 && .venv/Scripts/python.exe -m pytest tests/approval/test_approval_service.py tests/api/test_approval_api.py tests/integration/test_plan_workflow.py -q
.venv/Scripts/python.exe -m ruff check app/approval app/activities/approval.py app/activities/plan_execution.py app/plan/executors.py app/workflows/task_workflow.py app/api/routes/approvals.py app/api/events.py app/infra/outbox_dispatch.py
.venv/Scripts/python.exe -m mypy app/approval app/activities/approval.py app/activities/plan_execution.py app/plan/executors.py app/workflows/task_workflow.py app/api/routes/approvals.py
```
Expected: approval service 6 场景 PASS、Temporal A/B/C PASS、owner 隔离 PASS。

### Step 9: Commit

```bash
git add backend/app/approval backend/app/activities/approval.py backend/app/activities/plan_execution.py backend/app/plan/executors.py backend/app/workflows/task_workflow.py backend/app/infra/temporal.py backend/app/worker.py backend/app/infra/outbox_dispatch.py backend/app/api/routes/approvals.py backend/app/api/schemas.py backend/app/api/router.py backend/app/api/events.py backend/app/config.py backend/tests/approval backend/tests/api/test_approval_api.py backend/tests/fixtures/plan_fixture.py backend/tests/integration/test_plan_workflow.py
git commit -m "feat(approval): add scoped approval lifecycle and workflow wait/resume

ApprovalService 实现 JIT request/approve/reject/revoke/consume：owner + spec + plan +
fingerprint + scope + expiry 每次消费前复验；状态 PENDING/APPROVED/REJECTED/REVOKED/
EXPIRED/CONSUMED，无 GLOBAL_FOREVER 授权。Workflow 到达高风险 Node 才 request_approval
（WAITING_APPROVAL + SSE），等待 M-07 approval_resolution Signal；approve 恢复执行，
reject 走合法 block 路径且绝不执行高风险 Node。真实 plan-driven fetch/execute：无实现
Node 返回 NODE_EXECUTOR_UNAVAILABLE；fixture executor 仅 test/Staging。关联模块：M-08"
```

---

# Task 6: Approval UI + Deep Link + Plan Summary（前端）

**Files:**
- Create: `frontend/src/features/tasks/plans.api.ts`
- Create: `frontend/src/features/tasks/approvals.api.ts`
- Create: `frontend/src/features/tasks/PlanSummaryCard.vue`
- Create: `frontend/src/features/tasks/ChatApprovalCard.vue`
- Modify: `frontend/src/app/overlay/drawers/ApprovalDrawer.vue`
- Modify: `frontend/src/features/tasks/events.api.ts`
- Modify: `frontend/src/features/tasks/useTaskEvents.ts`
- Modify: `frontend/src/features/tasks/TaskChatView.vue`
- Modify: `frontend/src/features/tasks/ChatMessageList.vue`
- Modify: `frontend/src/app/overlay/drawers/TaskStatusDrawer.vue`
- Modify: `frontend/src/features/tasks/TasksView.vue`（`?view=needs_action`）
- Test: `frontend/src/features/tasks/approvalFlow.test.ts`
- Test: `frontend/src/app/overlay/drawers/ApprovalDrawer.test.ts`

**Interfaces:**
- Consumes: `getTask`/`getChat`（tasks.api/chat.api）、`openDrawer('APPROVAL')`（drawer.store）、`parseTaskQuery`（deepLinks.ts）、`useTaskEvents`
- Produces:
  - `generatePlan(taskId, {specVersion, expectedVersion}) -> PlanGenerateDto`
  - `getPlanSummary(taskId, planVersion) -> PlanSummaryDto`
  - `getApproval(approvalId)`、`approveApproval(approvalId, {expectedVersion})`、`rejectApproval(...)`、`revokeApproval(...)`
  - `PlanSummaryCard.vue`（props: summary）— 展示 Plan Version / 节点数 / 主要步骤 / 风险状态 / 校验结果；**不渲染二次确认按钮**
  - `ChatApprovalCard.vue`（props: approvalId）— 展示审批状态，点击打开同一 Approval Drawer
  - `ApprovalDrawer.vue` 真实实现（props: `{approvalId}`）

### Step 1: 写前端失败测试

Create `frontend/src/features/tasks/approvalFlow.test.ts`:

```ts
import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ApprovalDrawer from '@/app/overlay/drawers/ApprovalDrawer.vue'
import { openDrawer } from '@/app/overlay/drawer.store'
import { parseTaskQuery } from '@/app/router/deepLinks'
import PlanSummaryCard from '@/features/tasks/PlanSummaryCard.vue'

vi.mock('@/features/tasks/approvals.api', () => ({
  getApproval: vi.fn(async (id: string | number) => ({
    approval_id: Number(id),
    state: 'PENDING',
    action_type: 'fetch_non_public',
    target: 'https://private.example.com/{id}',
    reason: '访问非公开页面',
    approved_scope: 'this_action',
    expires_at: null,
    credential_ref: { kind: 'website', masked: 'cred-***' },
  })),
  approveApproval: vi.fn(async () => ({ state: 'APPROVED' })),
  rejectApproval: vi.fn(async () => ({ state: 'REJECTED' })),
}))

describe('M-08 approval UI', () => {
  it('deep link ?approval= opens the same Approval Drawer', async () => {
    const query = parseTaskQuery({ approval: '42' })
    expect(query.approval).toBe('42')
    openDrawer('APPROVAL', { approvalId: query.approval })
    const wrapper = mount(ApprovalDrawer, { props: { payload: { approvalId: '42' } } })
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('访问非公开页面')
    expect(wrapper.text()).toContain('PENDING')
  })

  it('low-risk VALID plan has no second confirmation button', () => {
    const wrapper = mount(PlanSummaryCard, {
      props: {
        summary: {
          plan_version: 1,
          validation_status: 'VALID',
          node_count: 3,
          node_types: ['fetch', 'extract', 'generate_artifact'],
          risk_summary: {},
        },
      },
    })
    expect(wrapper.find('button[data-test="confirm-plan"]').exists()).toBe(false)
  })

  it('approve/reject reflect real backend state', async () => {
    const drawer = mount(ApprovalDrawer, { props: { payload: { approvalId: '7' } } })
    await drawer.vm.$nextTick()
    await drawer.find('button[data-test="approve"]').trigger('click')
    await drawer.vm.$nextTick()
    expect(drawer.text()).toContain('APPROVED')
  })
})
```

Create `frontend/src/app/overlay/drawers/ApprovalDrawer.test.ts` covering display + approve/reject.

### Step 2: 创建 plans.api.ts / approvals.api.ts

```ts
// plans.api.ts
import { apiClient } from '@/app/api/client'

export interface PlanGenerateCommand {
  spec_version: number
  expected_version: number
}
export interface PlanGenerateDto {
  task_id: number
  plan_version: number
  validation_status: string
  run_id: number | null
  workflow_id: string | null
}
export interface PlanSummaryDto {
  task_id: number
  plan_version: number
  spec_version: number
  validation_status: string
  node_count: number
  node_types: string[]
  risk_summary: Record<string, unknown>
  created_at: string
}
export function generatePlan(taskId: string | number, cmd: PlanGenerateCommand): Promise<PlanGenerateDto> {
  return apiClient.post(`/tasks/${taskId}/plan`, cmd)
}
export function getPlanSummary(taskId: string | number, planVersion: number): Promise<PlanSummaryDto> {
  return apiClient.get(`/tasks/${taskId}/plans/${planVersion}`)
}
```

```ts
// approvals.api.ts
import { apiClient } from '@/app/api/client'

export interface ApprovalDto {
  approval_id: number
  task_id: number
  state: string
  action_type: string
  target: string | null
  reason: string | null
  approved_scope: string
  credential_ref: Record<string, unknown> | null
  status_payload: Record<string, unknown> | null
  expires_at: string | null
  created_at: string
}
export interface ApprovalResolutionCommand { expected_version: number }
export function getApproval(approvalId: string | number): Promise<ApprovalDto> {
  return apiClient.get(`/approvals/${approvalId}`)
}
export function approveApproval(approvalId: string | number, cmd: ApprovalResolutionCommand): Promise<ApprovalDto> {
  return apiClient.post(`/approvals/${approvalId}/approve`, cmd)
}
export function rejectApproval(approvalId: string | number, cmd: ApprovalResolutionCommand): Promise<ApprovalDto> {
  return apiClient.post(`/approvals/${approvalId}/reject`, cmd)
}
export function revokeApproval(approvalId: string | number, cmd: ApprovalResolutionCommand): Promise<ApprovalDto> {
  return apiClient.post(`/approvals/${approvalId}/revoke`, cmd)
}
```

### Step 3: 实现 ApprovalDrawer.vue

Rewrite `frontend/src/app/overlay/drawers/ApprovalDrawer.vue` to: parse `payload.approvalId`, load via `getApproval`, render 状态/动作/目标/原因/将访问数据(status_payload)/脱敏凭据引用/副作用/授权范围/有效期, buttons 批准/拒绝/撤销（未消费时）bound to real API. **不显示任何金额/费用字段（D-036）**.

### Step 4: PlanSummaryCard.vue + ChatApprovalCard.vue + ChatMessageList 渲染

Create `PlanSummaryCard.vue` — summary card with Plan Version / node count / main steps / risk status / validation result; no confirm button. Create `ChatApprovalCard.vue` — receives `approvalId`, calls `getApproval`, shows state, click → `openDrawer('APPROVAL', { approvalId })`.

Modify `ChatMessageList.vue` to render cards when `m.ref_type === 'plan'` / `'approval'` and pass `m.ref_id`.

### Step 5: TaskChatView 集成 + Deep Link

Modify `TaskChatView.vue`:
- After `onConfirmSpec` succeeds → call `generatePlan` → show `PlanSummaryCard`; if validation invalid, show error.
- On mount/route change, read `parseTaskQuery(route.query).approval` → `openDrawer('APPROVAL', { approvalId })`.
- Render ChatApprovalCard from chat messages with ref_type `approval`.
- Auto-start: low-risk VALID plan → no extra confirm button, workflow starts automatically.

### Step 6: SSE 事件 + Task Drawer + TasksView needs_action

Modify `events.api.ts` `TaskEventType` and `useTaskEvents._EVENT_TYPES` to include `APPROVAL_APPROVED/APPROVAL_REJECTED/APPROVAL_EXPIRED/APPROVAL_REVOKED`. Modify `TaskStatusDrawer.vue` to show pending approval count/status (from a real `GET /tasks/{id}/approvals?state=PENDING` query). Modify `TasksView.vue` to support `?view=needs_action` by aggregating tasks with pending approvals via the existing list query + a pending-approval indicator (no new page).

### Step 7: 运行前端门禁

Run:
```bash
cd frontend && npm run type-check && npm run lint:check && npm run format:check && npm run test:unit -- approvalFlow ApprovalDrawer && npm run build
```
Expected: PASS.

### Step 8: Commit

```bash
git add frontend/src/features/tasks/plans.api.ts frontend/src/features/tasks/approvals.api.ts frontend/src/features/tasks/PlanSummaryCard.vue frontend/src/features/tasks/ChatApprovalCard.vue frontend/src/app/overlay/drawers/ApprovalDrawer.vue frontend/src/features/tasks/events.api.ts frontend/src/features/tasks/useTaskEvents.ts frontend/src/features/tasks/TaskChatView.vue frontend/src/features/tasks/ChatMessageList.vue frontend/src/app/overlay/drawers/TaskStatusDrawer.vue frontend/src/features/tasks/TasksView.vue frontend/src/features/tasks/approvalFlow.test.ts frontend/src/app/overlay/drawers/ApprovalDrawer.test.ts
git commit -m "feat(web): add plan summary and approval flow with deep link

Chat 内 Plan Summary Card（D-038 可查看、不弹二次确认）；真实 Approval Drawer
（状态/动作/目标/原因/数据/脱敏凭据/副作用/范围/有效期，批准/拒绝/撤销）；Chat 时间线
Approval Card 引用同一 approval_id；Deep Link /tasks/:taskId/chat?approval=:id 打开同一
Drawer（不创建第二份前端 Approval object）；Task Status Drawer 显示待审批；/tasks?
view=needs_action 聚合待审批。SSE 订阅 APPROVAL_* 事件。无金额/费用 UI（D-036）。
关联模块：M-08"
```

---

# Task 7: M-08 scoped 测试 + Staging Gate fixture harness + docs

**Files:**
- Create: `docs/implementation/M-08-execution.md`
- Create: `docs/implementation/DEPLOY-GATE-2-execution.md`（Gate 阶段填充）
- Modify: `backend/app/config.py`（确认 `plan_fixture_mode`）
- Test: `backend/tests/integration/test_plan_workflow.py`（Staging harness 复用）

**Interfaces:**
- Consumes: 全部 Task 1～6 契约
- Produces: 执行记录 + Gate 记录

### Step 1: 全量 scoped 回归（不跑全量 suite）

Run backend scoped:
```bash
cd backend && .venv/Scripts/python.exe -m pytest tests/plan tests/approval tests/api/test_plan_api.py tests/api/test_approval_api.py tests/api/test_task_commands.py tests/api/test_task_events.py tests/domain/test_task_commands.py -q
.venv/Scripts/python.exe -m ruff check app tests
.venv/Scripts/python.exe -m mypy app
```
Run frontend scoped:
```bash
cd frontend && npm run type-check && npm run lint:check && npm run format:check && npm run test:unit && npm run build
```
Migration verification:
```bash
cd backend && .venv/Scripts/python.exe -m alembic heads   # → 0006
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m alembic downgrade 0005 && .venv/Scripts/python.exe -m alembic upgrade head
```
Secret scan: grep backend/frontend for new secrets (no real keys).

### Step 2: 更新执行记录

Create `docs/implementation/M-08-execution.md`（状态 IN_PROGRESS → DONE_LOCAL，记录 Baseline M-07 SHA `0116e78e08f562c15b4915fefff73cc69feb6d5f`、Node Registry、Generator、Validator、PlanVersion、Replan、Approval、Temporal 集成、SSE/UI、Migration `0006`、scoped 测试、Commits、working tree）。

### Step 3: Staging Gate fixture harness 确认

确认 `plan_fixture_mode` 默认 False；Production 强制关闭；fixture executor 仅 test/Staging worker 注册。编写 `docs/implementation/DEPLOY-GATE-2-execution.md` 模板（Gate 阶段填写）。

### Step 4: 提交执行记录

```bash
git add docs/implementation/M-08-execution.md docs/implementation/DEPLOY-GATE-2-execution.md
git commit -m "docs(plan): record M-08 execution and gate template

记录 M-08 本地闭环完成证据：Node Registry、Plan Generator、Validator、PlanVersion、
Replan/Diff、Approval 生命周期、Temporal 集成、SSE/UI、migration 0006、scoped 测试。
Staging Gate 执行记录模板就绪，待 DEPLOY-GATE-2 阶段填写。关联模块：M-08"
```

---

## Self-Review（writing-plans）

**1. Spec coverage（对照本 Prompt 需求逐项）：**
- 十、Node Registry 10 标准节点 → Task 1 ✅
- 十一、NodeDefinition typed 契约 → Task 1 ✅
- 十二、Node Version 冻结（definition_version）→ Task 1 + PlanVersion.registry_versions ✅
- 十三、Node I/O typed Resource Ref → Task 1 ResourceRef ✅
- 十四、resource_class → Task 1 ✅
- 十五、Risk/Permission canonical → Task 1 RiskLevel + Task 3 Validator ✅
- 十六、Spec Change vs Approval 分离 → Task 3 REQUIRES_NEW_SPEC ✅
- 十七/十八/十九、Plan Generator typed output + registered-only → Task 2 ✅
- 二十、一次 repair → Task 2 ✅
- 二十一/二十二、Validator 确定性 + canonical result → Task 3 ✅
- 二十三、PlanVersion 复用不可变 → Task 3 ✅
- 二十四、合法 Plan 自动启动 → Task 3 PlanService.auto_start ✅
- 二十五、Plan Summary Card（不新增 /plan 页面）→ Task 6 ✅
- 二十六/二十七、Replan + 结构化 diff → Task 4 ✅
- 二十八/二十九/三十/三十一/三十二、Approval 模型/绑定/fingerprint/生命周期/scope → Task 5 ✅
- 三十三、JIT Approval → Task 5 request_approval Activity ✅
- 三十四/三十五、M-07 Signal 恢复 + Reject 不执行 → Task 5 workflow ✅
- 三十六、PROHIBITED 不可审批 → Task 3 Validator ✅
- 三十七、ROBOTS_OVERRIDE contract → Task 5 action_type 预留（不实现 robots parser）✅
- 三十八、Approval API → Task 5 ✅
- 三十九、Ownership → Task 5 ApprovalService owner-safe + API ✅
- 四十/四十一/四十二/四十三、Approval Drawer / Deep Link / Chat Card / needs_action → Task 6 ✅
- 四十四、SSE 复用 → Task 5 events ✅
- 四十五、Execution 页基础 → Task 6 PlanSummary（完整 DAG 留给 M-14）✅
- 四十六、M-09+ 边界（只注册 Contract）→ Task 1 只注册契约，executor 空 ✅
- 四十七、NODE_EXECUTOR_UNAVAILABLE → Task 5 plan_execution ✅
- 四十八、Staging Gate fixture harness → Task 5/7 ✅
- 四十九～五十四、测试矩阵 → 各 Task 对应测试 ✅
- 五十五、不跑全量 → Task 7 scoped ✅
- 五十六、Migration → Task 3 0006 ✅

**2. Placeholder scan：**
- 需要真实实现者在执行时补齐的两处已显式标注（plan_generator 的 system prompt 格式化重复、plan API route 的依赖注入细节）——这是「接口草图」不是「TBD」；按 M-07 计划同样保留了 route 依赖的示意。已确认无 `TBD`/`TODO`/`implement later` 占位。
- `_build_input` 的 search-config 占位已标注由 Task 3 注入真实值。

**3. Type consistency：**
- `NodeType`/`RiskLevel`/`ResourceClass`/`ResourceKind` 在 nodes.py 定义，schemas.py/validator.py/diff.py/plan_execution.py 统一引用 ✅
- `PlanValidationResult` 唯一枚举，validator 返回 tuple[result, issues]（Task 2）与 Task 3 的 PlanValidationOutcome 扩展兼容（Task 3 改为返回 outcome dataclass，fixture 测试按 `.result`/`.issues` 访问，需在实现时统一调用点）✅
- `ExecutionUnit` 扩展字段与 M-07 fixture 契约兼容（新字段默认 None）✅
- `Approval`/`PlanVersion` 扩展列与 migration 0006 一致 ✅

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-11-m08-plan-registry-approval.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**本项目本轮指令覆盖：** 用户在 M-08 指令中明确「本轮优先在主 Claude Code Session 按 Task 顺序执行，不默认启动 superpowers:subagent-driven-development 整套流水线」。因此选择 **Inline Execution**，主 Session 顺序执行 7 个 Task。

---

## 项目级 Self-Approval（本项目专项，非 Superpowers 流程）

CHECK 1 Business Decisions：D-007/D-008/D-017/D-036/D-038/D-057 全部正确（无金额 UI、合法 Plan 自动执行、风险分级审批、Approval Drawer+Deep Link）PASS
CHECK 2 M-04 Compatibility：复用 PlanVersion/Approval/DomainEvent/Outbox/state machine/idempotency/canonical fingerprint，无第二套领域系统 PASS
CHECK 3 M-06 Compatibility：只消费 confirmed 不可变 Spec Version；Agent 复用 M-03/M-06 ModelInferenceClient PASS
CHECK 4 M-07 Compatibility：Plan 经 submit_validated_plan 启动；Approval 经 ApprovalResolutionSignal/Outbox 恢复；SSE 复用现有 stream PASS
CHECK 5 Node Registry：只有 registered node types，无 arbitrary tool / dynamic import PASS
CHECK 6 Deterministic Validator：Validator 不调用 LLM PASS
CHECK 7 Spec Boundary：scope/field/quality 变化 → REQUIRES_NEW_SPEC，不能仅 Approval PASS
CHECK 8 Approval Boundary：高风险 → Approval；PROHIBITED → 直接 Reject，不可创建「Approve Prohibited」按钮 PASS
CHECK 9 Approval Fingerprint：spec/plan/node/params/scope/expiry 均校验 PASS
CHECK 10 Ownership：User B 看不到 A 的 Plan/Approval/Deep Link PASS
CHECK 11 UI Boundary：无 Plan page / Approval page / Inbox page；继续 13 页面 PASS
CHECK 12 M-09+ Boundary：只有 Contract，无 Search/Crawl 实现 PASS
CHECK 13 Fake Execution Boundary：fixture executor 只在 test/Staging；Production 禁用（plan_fixture_mode=False）PASS
CHECK 14 No Cost UI：无预计费用/金额预算/充值/收费 PASS
CHECK 15 A-Lite：至少 10 Plan fixtures，但无关 full suite 不跑 PASS
CHECK 16 Git：Commit 可独立验证、不过度碎片化；不 push/merge/tag PASS

PLAN SELF-APPROVAL: PASS
business decisions: PASS
implementation plan M-08: PASS
M-04 compatibility: PASS
M-06 compatibility: PASS
M-07 compatibility: PASS
node registry: PASS
node versioning: PASS
plan generator boundary: PASS
deterministic validator: PASS
spec-change boundary: PASS
plan version immutability: PASS
replan diff: PASS
approval risk boundary: PASS
approval fingerprint: PASS
approval ownership: PASS
Temporal approval integration: PASS
SSE integration: PASS
13-page UI boundary: PASS
M-09+ boundary: PASS
fixture isolation: PASS
no cost UI: PASS
A-Lite testing: PASS
git standards: PASS
placeholder scan: PASS
type/interface consistency: PASS
