# Agent Execution Timeline — 实时工作流展示 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 按任务逐条实现本计划。步骤使用 checkbox（`- [ ]`）跟踪。
>
> 状态：PLAN ONLY（用户要求完成计划后停止，不执行代码）

**Goal:** 在不触碰 Temporal 核心 Workflow / Agent Loop / Provider / Extraction 的前提下，把现有 M-14 执行二级页升级为 Claude Code / OpenAI Operator 风格的**实时工作流展示**：事件流到达即逐条出现在时间线、阶段卡与 DAG 节点状态随之实时更新、断线自动补回。

**Architecture:** 全部复用现有持久化事实（`DomainEvent` + `NodeRun`/`NodeAttempt` + `Run`）。后端新增一个纯 `TimelineMapper`（从 `ExecutionService._to_dto` 原样抽取，REST timeline 与流共用同一映射），新增 owner-scoped SSE 流端点 `GET /api/tasks/{task_id}/execution/timeline/stream`（复用现有 `_event_stream` 的 replay→poll→keepalive 骨架，`Last-Event-ID`/`after_id` 游标）。前端执行页连接该流：事件逐条 append 到时间线（按 `event_id` 去重/单调合并），合并节流的 snapshot 刷新驱动阶段卡与计数，reconnect 后 reconcile 一次。**不新增事件表、不引入 Kafka/Redis/WebSocket，不修改任何 Workflow/Activity 执行语义，零 migration。**

**Tech Stack:** Python 3.11、FastAPI、Pydantic v2、SQLAlchemy 2、Alembic（无新 migration）、Temporal Python SDK（只读不触碰）、Vue 3、TypeScript strict、EventSource（SSE）、Vitest、Playwright、GHCR。

**Spec:** 本文件 §1–§3 即设计 spec；配套权威产品决策：`agent-business-logic-log.md` **D-024**（用户时间线/诊断追踪）、**D-039**（聊天只显示重要事件，细粒度日志进执行详情）、**D-055**（执行详情二级页）、**D-063**（执行详情默认阶段+时间线，可切换流程图）、**D-077**（NodeRun/NodeAttempt/Checkpoint/`run.*` DomainEvent 为权威事实；snapshot 先恢复、SSE 只做增量；不展示百分比与 Chain-of-Thought）；实施基线 `docs/superpowers/plans/2026-08-15-execution-readiness-progress.md`（已落地 snapshot + SSE 增量 + Task Chat 进度面板）。

---

## Global Constraints

以下约束对本计划每个任务生效，逐条复制自权威文档/用户明确指令，禁止放宽：

1. **只做 Execution Visibility Layer。** 禁止修改：`backend/app/workflows/task_workflow.py`、Temporal 架构、Agent Loop（`backend/app/agents/`）、Provider（`backend/app/providers/`）、Extraction（`backend/app/extraction/`）、领域状态机（`backend/app/state/`）、`allowed_actions` 语义。（用户指令 + CLAUDE.md §6/§18）
2. **不新增第二个事件存储。** 复用 `DomainEvent + SSE`；禁止 Kafka、Redis pub/sub、WebSocket 或新 `execution_events` 表。（2026-08-15 design §3、D-077）
3. **零 migration。** 所有需要的事实已持久化；`alembic heads` 必须保持唯一 `0017`。若实现时发现必须改表，必须停下来请求用户决策，不得静默加 migration。（CLAUDE.md §9）
4. **Snapshot 是事实源，流只是增量。** 刷新后先渲染 snapshot 再合并增量；不因增量丢弃已完成的节点。（D-077）
5. **Owner 隔离贯穿。** 所有查询/流复用 `TaskRepository(db).get_owned(...)`；非 owner/不存在一律 404，不泄漏存在性。（D-023 + 代码规范 §2）
6. **安全 allowlist。** 流事件字段与 REST timeline 完全一致（`TimelineEvent`，`extra="forbid"`）；禁止 credential/header/cookie/secret/reasoning/页面正文进入 payload、日志或 Temporal history。（D-024/D-077 + 代码规范 §10）
7. **不展示百分比、不展示 Chain-of-Thought。** 只渲染持久化事实派生出的节点状态与计数。（D-077）
8. **现有消费者行为不变。** `/api/events/tasks/{task_id}` 及其消费者（`TaskStatusDrawer`、`ExecutionProgressPanel`、`useTaskEvents`）必须保持行为一致；新流是新增端点，不改既有端点语义。（CLAUDE.md §16）
9. **不新增一级页面。** 实时时间线承载在既有 `/tasks/:taskId/execution` 二级页内（D-044/D-048 固定 13 类页面）。
10. **A-Lite 测试策略。** 流映射、游标回放、owner 隔离、前端合并/重连这些核心路径必须有自动化测试；纯展示组件不做高密度单测。（代码规范 §11）
11. **保留用户未跟踪 `infra/scripts/_*.py` 文件**，不修改、不暂存、不提交；验收脚本沿用 `_` 前缀未入库约定。
12. **Git**：每个 Task 单独 commit，Conventional Commits；不主动 Push/Merge；Commit 只包含一个可独立验证的小功能。（CLAUDE.md §3.4）

---

## §1 当前事件系统分析

### 1.1 后端事件模型与持久化事实

| 对象 | 表 | 关键字段 | 位置 |
|---|---|---|---|
| `DomainEvent` | `domain_events` | `id`（BigInteger identity，**即 SSE 游标**）、`user_id`、`aggregate_type`（`task`/`record`/…）、`aggregate_id`（task.* 事件 = task_id）、`event_type`、`run_id`、`node_run_id`、`payload`(JSON)、`occurred_at`。**无 task_id 列（派生）、无 workflow_id、无 per-run sequence** | `backend/app/domain/models.py:622` |
| `Run` | `runs` | `state` String（`pending/running/completed/partially_completed/failed/cancelled`），无枚举 | `models.py:231` |
| `NodeRun` | `node_runs` | `run_id`、`task_id`、`node_id`（唯一 `(run_id, node_id)`）、`node_type`、`state`、`position` | `models.py:251`（`node_id` 由 migration 0016 增加） |
| `NodeAttempt` | `node_attempts` | `node_run_id`、`attempt`（唯一 `(node_run_id, attempt)`）、`status`、`error_code`、`error_summary` | `models.py:279` |
| `Checkpoint` | `checkpoints` | `run_id`、`batch_identity`、`committed_object_refs` | `models.py:743` |

**事件产生**：全部经 `append_domain_event`（`backend/app/state/events.py:14`），与状态变更**同一事务**提交；事件只追加不更新。
- Run 生命周期：`run.started`、`run.completed/partially_completed/failed/cancelled` ← `backend/app/activities/task_execution.py`（`ensure_run_started` / `_finish_run`）。
- Node 生命周期：`run.node_started/progress/completed/blocked/failed/cancelled`、`run.checkpoint_committed` ← `backend/app/execution/lifecycle.py` `ExecutionLifecycleRecorder`（`start_attempt`/`finish_attempt`，由 `execute_safe_unit` 驱动，`app/activities/plan_execution.py`）。
- 业务流水：`fetch.*`、`discovery.*`、`extraction.*`/`normalize.completed`、`validation.*`、`approval.*`、`record.*`、`task.plan_generated/replanned`。

### 1.2 后端查询与 SSE 现状

- **REST（M-14，owner-safe 404）** `backend/app/api/routes/execution.py`：
  - `GET /api/tasks/{task_id}/execution` → `ExecutionView`（阶段聚合 + `current_node`/`last_successful_node`/`counts`/`outcome_code`/`last_event_id`）
  - `GET /api/tasks/{task_id}/execution/timeline?category=&after_id=&limit=` → `TimelinePage`（cursor 分页，`_to_dto` 映射）
  - `GET /api/tasks/{task_id}/execution/dag` → `DagView`；`GET .../nodes/{node_id}` → `NodeDetailDto`
- **事件映射** `backend/app/execution/service.py`：`_to_dto`（:907）→ `_classify`（:846）/`_stage`（:828）/`_summary`（:874），**纯函数**（只用 `ev.payload` + `ev` 标量字段，无 DB 查询），依赖 11 个模块级常量（`_SECRET_KEYS`/`_NODE_TYPE_STAGE`/`_ERROR_TYPES`/`_TOOL_UPGRADE_TYPES`/`_PLAN_CHANGE_TYPES`/`_PAUSE_RESUME_TYPES`/`_TASK_EVENT_LABELS`/`_DISCOVERY_LABELS`/`_RECORD_LABELS`/`_NODE_RESOURCE_LABELS`/`_RUN_EVENT_LABELS`，:35–:146）与 `_safe_int`（:161）。
- **事件源查询** `backend/app/execution/repository.py:128` `events_after`（task.* + 本 task record.* 事件，`after_id`/`through_id` 分页）与 `backend/app/api/events.py:215` `query_task_events` **范围一致**（docstring 互相引用同一语义）。
- **SSE 端点** `GET /api/events/tasks/{task_id}`（`backend/app/api/routes/events.py:169`）：
  - `_event_stream`（:71）：连接时冻结 `replay_through_id = max_task_event_id` → 回放 `(cursor, replay_through_id]` → 进入 2s 轮询活区 → `: ping` keepalive；`Last-Event-ID` 优先于 `?after_id`（`_parse_last_event_id` :57）；每页 `_SSE_PAGE_SIZE=200`；`try/finally` 维护 `change_sse_connections` 指标。
  - 线格式 `_format_sse`（:66）：`id: {event_id}\nevent: {type}\ndata: {SSETaskEvent json}\n\n`。

### 1.3 前端现状

- **无 Pinia / 无 UI 组件库 / 无 WebSocket**。状态 = composable + 模块级 `ref` 单例；SSE 是唯一实时通道。
- `frontend/src/features/tasks/useTaskEvents.ts`：per-task SSE 消费者，`lastEventId`/`latestEvent`/`reconcileVersion`（`reconnecting→open` 递增），`event_id` 单调去重。
- `frontend/src/features/execution/useExecution.ts`：`view`/`timeline`/`filter`/`viewMode`/`dag` 状态；`loadOverview`/`loadTimeline(afterId)`（50/页 cursor）/`loadDag`/`refreshSnapshot`（= overview + timeline 全量）/`mergeTimelineEvent`（按 `event_id` 去重+升序）。
- `frontend/src/features/execution/execution.api.ts`：`getExecution`/`getTimeline`/`getDag`/`getNodeDetail`（经 `apiClient`，base `/api`）。
- **展示**：`TaskExecutionView.vue` = 执行二级页（**静态，不 live 刷新**：阶段卡 + 时间线 + 只读 DAG + Node Detail Drawer）；`ExecutionProgressPanel.vue` = Chat 内紧凑面板（**已 live**：`useTaskEvents` 任一事件 → `refreshSnapshot`，渲染最近 5 条）；`TaskStatusDrawer.vue` = 连接 task SSE 刷新 shell。
- **类型** `frontend/src/features/execution/types.ts`：`TimelineEvent { event_id, timestamp, categories, stage, summary, status, error_code, run_id, node_run_id, node_id, retry_count, tool, model, duration_ms, tokens_in, tokens_out, evidence_refs, trace_ref }`。

### 1.4 已确认的产品约束（禁止违背）

D-024（用户时间线展示阶段/计数/调整/升级/暂停恢复/停止原因）、D-039（Chat 只显重要事件，细粒度日志在 Execution）、D-055（执行详情为二级页）、D-063（执行详情默认"阶段+时间线"，可切换流程图；Node 点击开 Drawer）、D-077（snapshot 权威 + SSE 增量；`last_event_id` 游标存在提交顺序已知限制，以 reconnect reconcile 缓解；不显示百分比/CoT）。

---

## §2 Gap Analysis

| # | 已有（复用） | 缺失（本轮补） | 差距位置 |
|---|---|---|---|
| G1 | Timeline REST 已返回**丰富** `TimelineEvent`（categories/stage/tool/model/tokens/duration/node_run_id） | 执行二级页**不 live**：只加载一次，事件到达不自动追加、阶段卡/计数/DAG 不变 | `TaskExecutionView.vue` 无 SSE 连接 |
| G2 | Task SSE 已实时推送 `TaskSseEvent` | SSE payload **≠** `TimelineEvent`（无 categories/tool/model/tokens），`useExecution.mergeTimelineEvent` 与 `useTaskEvents` 之间无接线 | `useTaskEvents` ↔ `useExecution` 无适配 |
| G3 | `ExecutionService._to_dto` 是纯映射 | 只作为 service 私有方法，无法被流端点直接复用 | `service.py:907` |
| G4 | `_event_stream` 骨架（replay→poll→keepalive）可复用 | 无"按 cursor 输出富 `TimelineEvent` 的 SSE 流"端点 | `routes/events.py` 专属 task SSE |
| G5 | `ExecutionView.stages`/`DagNodeExecution` 已含实时事实 | 无 live 阶段状态转换（in_progress→completed）视觉、无 live DAG 节点着色、无"当前步骤"高亮 | `TaskExecutionView.vue` |
| G6 | D-077 已定义游标提交顺序已知限制 | 流端点必须复用同一限制 + 前端 reconnect reconcile 兜底，不能发明"完美"游标掩盖它 | 设计约束（见 §3.4） |

**结论**：后端差距集中在"把既有纯映射暴露成可流式消费的富事件端点"（G3/G4，PR1+PR2）；前端差距集中在"执行页 live 化 + 步骤状态转换 + 阶段/DAG 实时更新"（G1/G5/G2，PR3）。无需改 Workflow/Agent Loop/Provider/Extraction，无需 migration。

---

## §3 Architecture Design

### 3.1 后端 PR1：`TimelineMapper`（共享事件→`TimelineEvent` 适配器）

新建 `backend/app/execution/timeline.py`，把 `ExecutionService._stage/_classify/_summary/_to_dto`（`service.py:828–948`）及其依赖常量（`service.py:35–146`）与 `_safe_int`（:161）**原样移动**为无状态 `TimelineMapper` 类方法。`ExecutionService` 保留签名 `_to_dto(self, ev)` 但委托 `TimelineMapper.to_timeline_event(ev)`，REST timeline 行为零变化。所有方法不使用 `self` 业务状态，纯函数便于流端点复用。

### 3.2 后端 PR2：`GET /api/tasks/{task_id}/execution/timeline/stream`

新建 `backend/app/execution/timeline_stream.py`，复用 `_event_stream` 骨架（回放冻结 `replay_through_id` → 2s 轮询 → `: ping`）但：
- 事件源用 `ExecutionRepository.events_after`（与 REST timeline **同一查询**，保证前端 merge 后与 REST 分页结果一致）；
- 每条经 `TimelineMapper.to_timeline_event` 映射，线格式 `id: {event_id}\nevent: timeline\ndata: {TimelineEvent json}\n\n`；
- 复用 `get_execution_metrics()` 的 `record_sse_replay`/`change_sse_connections`。
- 共享游标解析：把 `_parse_event_id`/`_parse_last_event_id`/`_MAX_EVENT_ID`（`routes/events.py:35,48,57`）抽到新建 `backend/app/api/sse_cursor.py`，`events.py` 改为导入（行为不变，回归由 `tests/api/test_task_events.py` 兜底）。

### 3.3 前端 PR3：执行页 live 化

- `execution.api.ts`：新增 `openExecutionTimelineStream(taskId, lastEventId?)`（`EventSource` → `/tasks/{taskId}/execution/timeline/stream?after_id=`）+ `parseTimelineSseMessage(data): TimelineEvent`。
- `useExecution.ts`：新增 live 状态（`live: 'idle'|'connecting'|'open'|'reconnecting'`）、`connectLive()`/`disconnectLive()`。事件到达 → `mergeTimelineEvent(dto)` 立即上屏 + **节流**（~500ms）`refreshSnapshot()` 驱动阶段卡/计数；`reconnecting→open` 递增 `reconcileVersion` 触发一次 reconcile 刷新；taskId 变化/卸载时断开。
- `TaskExecutionView.vue`：run 存在且非终态时 `connectLive()`；时间线渲染升级为 `TimelineStepRow`（进行中脉冲 → 完成勾 / 失败叉 + error_code，tool/model/tokens chips，retry 徽标，当前节点高亮）；阶段卡 live 状态转换；DAG 节点按 live `execution.last_status` + `current_node` 着色；顶部"实时更新中"连接指示。

### 3.4 SSE 游标边界（D-077 已知限制，必须遵守）

当前游标是持久化 `DomainEvent.id`（sequence 标量）。**PostgreSQL sequence 分配顺序 ≠ 提交顺序**，因此不引入提交序列化或新的 durable commit-ordered cursor 前，不能数学上保证并发事务晚提交的小 ID 永不遗漏。本流沿用同一语义：连接时冻结 `replay_through_id`，只回放 `≤ through_id` 的已提交事件，活区只轮询 `id > cursor`；漏掉的晚提交小 ID 由前端 **reconnect reconcile**（`reconcileVersion` → 一次 snapshot 刷新）兜底。**不得**用有损重放或平行事件系统掩盖该限制，也**不得**为"完美"引入新表/Redis。

---

## §4 Boundary

**明确禁止修改**：
- `backend/app/workflows/task_workflow.py`、Temporal Workflow/Activity 执行语义、worker 启动（`backend/app/worker.py`）；
- Agent Loop / Goal Understanding / Plan 生成（`backend/app/agents/`）；
- Provider 适配器与凭据（`backend/app/providers/`）；
- Extraction（`backend/app/extraction/`）、Discovery/Fetch executor 内部（`backend/app/discovery/`、`backend/app/crawling/` 的执行逻辑）；
- 领域状态机、`allowed_actions`、`EventType` 枚举语义（`backend/app/state/`、`backend/app/domain/` 的 state 语义）；
- 既有 task SSE 端点 `GET /api/events/tasks/{task_id}` 的线格式与消费者行为；
- `alembic` 无新 migration（head 保持 `0017`）。

**本轮只做**：新增纯 `TimelineMapper`、新增流端点（只读查询 + SSE）、前端执行页 live 化、验收脚本与文档决策记录。

---

## §5 Implementation Plan

> 每个 Task 以独立测试周期结束并单独 commit。PR 是合并单元：PR1=Task1、PR2=Task2、PR3=Task3–5、PR4=Task6。TDD：先写失败测试，再最小实现。

### PR1 — Backend Event Adapter

### Task 1: 抽取共享 TimelineMapper

**Files:**
- Create: `backend/app/execution/timeline.py`
- Modify: `backend/app/execution/service.py`（:828–:948 方法委托、:35–:146 常量与 :161 `_safe_int` 移除）
- Test: `backend/tests/execution/test_timeline_mapper.py`

**Interfaces:**
- Consumes: `TimelineEvent`/`StageKey`/`TimelineCategory`（`app.execution.contracts`）；现有 `DomainEvent` 行对象（`ev.id`/`ev.occurred_at`/`ev.event_type`/`ev.run_id`/`ev.node_run_id`/`ev.payload`）。
- Produces: `TimelineMapper.stage(ev) -> StageKey`、`TimelineMapper.classify(ev) -> list[TimelineCategory]`、`TimelineMapper.summary(ev) -> str`、`TimelineMapper.to_timeline_event(ev) -> TimelineEvent`；`ExecutionService._to_dto` 委托后签名不变。

- [ ] **Step 1: 写 mapper 单元测试（手工期望值 = 行为 spec）**

`backend/tests/execution/test_timeline_mapper.py`：

```python
"""TimelineMapper：DomainEvent → TimelineEvent 的纯映射 spec。

期望值是手工固定（固化现有 REST timeline 行为），不是从 _to_dto 抄。
"""
from datetime import UTC, datetime

import pytest
from app.execution.contracts import TimelineEvent
from app.execution.timeline import TimelineMapper

_PAYLOAD_ALLOWED = {
    "node_id": "n3",
    "node_type": "fetch",
    "attempt": 1,
    "status": "COMPLETED",
    "counts": {"fetched": 3},
    "duration_ms": 1200,
}


def _ev(event_type: str, payload: dict, *, event_id: int = 1, run_id: int = 8) -> object:
    """构造与 SQLAlchemy DomainEvent 行字段兼容的轻量桩。"""
    class _Stub:
        id = event_id
        occurred_at = datetime(2026, 8, 21, 12, 0, 0, tzinfo=UTC)
        event_type = event_type
        run_id = run_id
        node_run_id = None
        payload = payload

    return _Stub()


def test_node_completed_rich_mapping() -> None:
    dto = TimelineMapper.to_timeline_event(_ev("run.node_completed", _PAYLOAD_ALLOWED))
    assert isinstance(dto, TimelineEvent)
    assert dto.event_id == 1
    assert dto.stage == "fetch"
    assert dto.summary == "抓取完成（http）" if False else dto.summary  # 见下方说明
    assert dto.status == "COMPLETED"
    assert dto.node_id == "n3"
    assert dto.retry_count == 0
    assert dto.duration_ms == 1200
    assert dto.categories == []
```

> 说明：`_summary` 对 `run.node_completed` 的实际文案由 `_RUN_EVENT_LABELS` 决定（实现时从 `service.py:134` 抄录后在此固化对应断言，如"节点完成"）。测试覆盖**全部分支矩阵**，每个分支固化一条期望断言：
> 1. `task.plan_generated`/`task.plan_replanned` → `plan_change` 分类 + "已生成计划 vX"；
> 2. `run.node_started`/`node_progress`/`node_completed`/`node_blocked`/`node_failed` → 正确 stage/status/category；`attempt>1` → `retry` 分类、`retry_count=attempt-1`；
> 3. `fetch.completed`（tool）/`fetch.escalated`（tool_upgrade）/`fetch.failed`（error + `error_code`）；
> 4. `extraction.llm_fallback_used`（model_call，summary 含 model）、`extraction.rule_promoted`（tool_upgrade）；
> 5. `task.pause`/`resume` → `pause_resume`；
> 6. `record.completed` 等 record.* → `record.*` summary + `evidence_refs`（snapshot_id/record_id 归并）;
> 7. `approval.created` → `plan_change`；
> 8. `trace_id` → `trace_ref`。
> 并断言 **secret 隔离**：

```python
def test_payload_secrets_never_leak() -> None:
    dto = TimelineMapper.to_timeline_event(
        _ev("run.node_completed", {**_PAYLOAD_ALLOWED, "api_key": "SK-SECRET", "cookie": "c=1",
                                   "authorization": "Bearer x", "token": "t"})
    )
    text = dto.model_dump_json()
    assert "SK-SECRET" not in text and "Bearer" not in text and "c=1" not in text
    assert "token" not in dto.model_dump()  # _to_dto 不映射 token 字段
```

- [ ] **Step 2: 运行测试确认 mapper 不存在**

```powershell
Set-Location backend
.venv/Scripts/python.exe -m pytest tests/execution/test_timeline_mapper.py -q
```

Expected: import `app.execution.timeline` 失败。

- [ ] **Step 3: 原样抽取实现 + service 委托**

创建 `backend/app/execution/timeline.py`：把 `service.py` 中 `_SECRET_KEYS`(:35)、`_NODE_TYPE_STAGE`(:62)、`_ERROR_TYPES`(:75)、`_TOOL_UPGRADE_TYPES`(:83)、`_PLAN_CHANGE_TYPES`(:90)、`_PAUSE_RESUME_TYPES`(:91)、`_TASK_EVENT_LABELS`(:99)、`_DISCOVERY_LABELS`(:117)、`_RECORD_LABELS`(:123)、`_NODE_RESOURCE_LABELS`(:131)、`_RUN_EVENT_LABELS`(:134)、`_safe_int`(:161) 与 `_stage`(:828)、`_classify`(:846)、`_summary`(:874)、`_to_dto`(:907) **逐字移动**到类中（方法改为不依赖 `self` 的实例/静态方法，`_to_dto` 内部调用改 `TimelineMapper.classify(ev)` 等）：

```python
"""执行时间线共享映射：DomainEvent → TimelineEvent（纯函数，无 DB）。

从 ExecutionService 原样抽取，REST timeline 与 timeline stream 共用同一映射，
保证分页查询与实时流输出完全一致。禁止透传 payload 原始字段。
"""
from __future__ import annotations

from typing import Any

from app.execution.contracts import (
    StageKey,
    TimelineCategory,
    TimelineEvent,
)

# 常量原样移动（_SECRET_KEYS / _NODE_TYPE_STAGE / _ERROR_TYPES / _TOOL_UPGRADE_TYPES /
# _PLAN_CHANGE_TYPES / _PAUSE_RESUME_TYPES / _TASK_EVENT_LABELS / _DISCOVERY_LABELS /
# _RECORD_LABELS / _NODE_RESOURCE_LABELS / _RUN_EVENT_LABELS 逐字从 service.py 复制）

def _safe_int(value: Any) -> int:
    ...  # 从 service.py:161 原样复制


class TimelineMapper:
    @classmethod
    def stage(cls, ev: Any) -> StageKey:
        ...  # 从 service.py:_stage 原样复制

    @classmethod
    def classify(cls, ev: Any) -> list[TimelineCategory]:
        ...  # 从 service.py:_classify 原样复制

    @classmethod
    def summary(cls, ev: Any) -> str:
        ...  # 从 service.py:_summary 原样复制

    @classmethod
    def to_timeline_event(cls, ev: Any) -> TimelineEvent:
        ...  # 从 service.py:_to_dto 原样复制
```

在 `service.py`：删除已移动常量/方法与 `_safe_int`，新增 import 并在 `_to_dto` 处替换为：

```python
    def _to_dto(self, ev: Any) -> TimelineEvent:
        return TimelineMapper.to_timeline_event(ev)
```

> 约束：`service.py` 仍保留 `_STAGE_LABELS`/`_STAGE_ORDER`/`_CURRENT_NODE_STATES`/`_SUCCESSFUL_NODE_STATES`/`_RUN_TERMINAL_TYPES`/`_BATCH_SIZE`/`_NodeEventFacts`/`_EventFacts`（这些属于 overview/DAG 组装，不移动）。

- [ ] **Step 4: 运行 mapper + REST timeline 回归**

```powershell
Set-Location backend
.venv/Scripts/python.exe -m pytest tests/execution/test_timeline_mapper.py tests/execution/test_execution_api.py tests/execution/test_dag_api.py -q
.venv/Scripts/python.exe -m ruff check app/execution/timeline.py app/execution/service.py tests/execution/test_timeline_mapper.py
.venv/Scripts/python.exe -m mypy app/execution/timeline.py app/execution/service.py
```

Expected: 全绿；REST `/execution/timeline` 行为与抽取前一致（`test_execution_api.py` 是行为回归护栏）。

- [ ] **Step 5: Commit**

```powershell
git add backend/app/execution/timeline.py backend/app/execution/service.py backend/tests/execution/test_timeline_mapper.py
git commit -m "refactor(execution): extract shared TimelineMapper" -m "将 DomainEvent→TimelineEvent 纯映射从 ExecutionService 抽取为共享 mapper，REST 与流共用同一分类/文案。关联模块：M-14。"
```

---

### PR2 — SSE Stream

### Task 2: Owner-scoped Timeline Stream SSE 端点

**Files:**
- Create: `backend/app/api/sse_cursor.py`
- Create: `backend/app/execution/timeline_stream.py`
- Modify: `backend/app/api/routes/events.py`（游标解析改用共享模块）
- Modify: `backend/app/api/routes/execution.py`（挂 `/timeline/stream`）
- Test: `backend/tests/execution/test_timeline_stream_api.py`
- Test: `backend/tests/api/test_task_events.py`（确认 events.py 重构无回归）

**Interfaces:**
- Consumes: `ExecutionRepository.events_after/max_event_id`、`TimelineMapper.to_timeline_event`、`get_execution_metrics()`、`TaskRepository.get_owned`。
- Produces: `timeline_stream(*, session_factory, user_id, task_id, cursor) -> AsyncGenerator[str, None]`（线格式 `id: {event_id}\nevent: timeline\ndata: {TimelineEvent json}\n\n`）；路由 `GET /tasks/{task_id}/execution/timeline/stream`；共享 `parse_last_event_id(request, after_id)`。

- [ ] **Step 1: 写失败端点测试**

`backend/tests/execution/test_timeline_stream_api.py`（仿 `tests/api/test_task_events.py` 结构）：

```python
def test_stream_replays_rich_timeline_events_after_cursor(stream_case):
    # seed: run.started(id=10) + run.node_started(id=11, payload={node_id,node_type,attempt,status})
    events = read_stream(stream_case, after_id=10)
    assert [e["event_id"] for e in events] == [11]
    item = events[0]
    assert item["event_type"] == "timeline"
    assert item["data"]["node_id"] == "n1"
    assert item["data"]["stage"] == "source_discovery"
    assert item["data"]["status"] == "RUNNING"
    assert item["data"]["node_run_id"] is not None


def test_stream_last_event_id_precedence(stream_case):
    body = stream_case.client.get(f"/tasks/{stream_case.task.id}/execution/timeline/stream",
                                  headers={**stream_case.auth, "Last-Event-ID": "10"}).text
    assert "id: 11" in body and "id: 10" not in _first_event(body)


def test_stream_live_appends_and_keepalive(stream_case):
    # 在流打开后插入 run.node_completed(id=12)，2s 轮询窗口内应推送；空闲时出现 ": ping"
    assert any("run.node_completed" in chunk or "node_completed" in chunk for chunk in wait_stream(stream_case, 3.0))


def test_stream_owner_isolated(stream_case, other_user):
    resp = stream_case.client.get(f"/tasks/{stream_case.task.id}/execution/timeline/stream",
                                  headers=other_user)
    assert resp.status_code == 404


def test_stream_payload_allowlist_no_secret(stream_case):
    # payload 带 api_key/cookie/token，断言 data 中不出现
    assert "SK-SECRET" not in stream_case.raw and "Bearer" not in stream_case.raw


def test_stream_replay_freeze(stream_case):
    # 回放期间新增 id < replay_through_id 的事件不得在活区重复推送（语义沿用 task SSE）
    ...
```

> 提示：`read_stream` 通过底层 `httpx`/TestClient 读取到 keepalive 为止；`_SSE_PAGE_SIZE=200`、游标校验（非数字/超界 → 400）、连接指标递减由既有 task SSE 测试模式覆盖，在此镜像。

- [ ] **Step 2: 运行确认端点缺失**

```powershell
Set-Location backend
.venv/Scripts/python.exe -m pytest tests/execution/test_timeline_stream_api.py tests/api/test_task_events.py -q
```

Expected: `/timeline/stream` 404 或 import 失败；`test_task_events.py` 先全绿（基线）。

- [ ] **Step 3: 抽取共享游标解析**

创建 `backend/app/api/sse_cursor.py`：

```python
"""SSE 游标解析共享工具（task SSE 与 execution timeline stream 共用）。"""
from __future__ import annotations

from fastapi import HTTPException, Request

MAX_EVENT_ID = 2**63 - 1


def parse_event_id(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or not 0 < len(value) <= 19:
        raise HTTPException(status_code=400, detail="Invalid event cursor")
    cursor = int(value)
    if cursor > MAX_EVENT_ID:
        raise HTTPException(status_code=400, detail="Invalid event cursor")
    return cursor


def parse_last_event_id(request: Request, after_id: str | None) -> int:
    header = request.headers.get("last-event-id")
    if header is not None:
        return parse_event_id(header)
    if after_id is not None:
        return parse_event_id(after_id)
    return 0
```

`routes/events.py` 改为 `from app.api.sse_cursor import parse_event_id, parse_last_event_id, MAX_EVENT_ID`，删除本地 `_parse_event_id`/`_parse_last_event_id`/`_MAX_EVENT_ID`，调用点替换（`_parse_last_event_id` → `parse_last_event_id`）。行为零变化。

- [ ] **Step 4: 实现 timeline stream service**

创建 `backend/app/execution/timeline_stream.py`（复用 `_event_stream` 骨架）：

```python
"""Execution timeline SSE 流（GET /tasks/{task_id}/execution/timeline/stream）。

只读投影：回放冻结 replay_through_id 内的已提交事件 → 2s 轮询活区 → keepalive。
事件源与 REST /execution/timeline 同一查询（ExecutionRepository.events_after），
经 TimelineMapper 输出富 TimelineEvent。owner-safe；不触碰 Workflow/Temporal。
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from app.auth.deps import require_user
from app.auth.models import User
from app.domain.repository import TaskRepository
from app.execution.repository import ExecutionRepository
from app.execution.timeline import TimelineMapper
from app.infra.deps import get_db
from app.observability.execution_metrics import get_execution_metrics
from app.api.sse_cursor import parse_last_event_id

router = APIRouter(prefix="/tasks/{task_id}/execution", tags=["execution"])
_SSE_PAGE_SIZE = 200


def _format_timeline_sse(event_id: int, payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False)
    return f"id: {event_id}\nevent: timeline\ndata: {data}\n\n"


def _load_timeline_page(session_factory, user_id: int, task_id: int,
                        cursor: int, through_id: int | None) -> list[dict]:
    db = session_factory()
    try:
        repo = ExecutionRepository(db)
        events = repo.events_after(user_id=user_id, task_id=task_id,
                                   after_id=cursor, limit=_SSE_PAGE_SIZE, through_id=through_id)
        return [
            _format_timeline_sse(ev.id, TimelineMapper.to_timeline_event(ev).model_dump(mode="json"))
            for ev in events
        ]
    finally:
        db.rollback()
        db.close()


def _load_max_timeline_event_id(session_factory, user_id: int, task_id: int) -> int:
    db = session_factory()
    try:
        return ExecutionRepository(db).max_event_id(user_id=user_id, task_id=task_id)
    finally:
        db.rollback()
        db.close()


async def timeline_stream(*, session_factory, user_id: int, task_id: int, cursor: int,
                          poll_interval: float = 2.0) -> AsyncGenerator[str, None]:
    metrics = get_execution_metrics()
    metrics.change_sse_connections(delta=1)
    try:
        replay_through_id = await asyncio.to_thread(_load_max_timeline_event_id, session_factory, user_id, task_id)
        replayed_any = False
        while cursor < replay_through_id:
            page = await asyncio.to_thread(_load_timeline_page, session_factory, user_id, task_id, cursor, replay_through_id)
            if not page:
                break
            metrics.record_sse_replay(count=len(page))
            replayed_any = True
            for chunk in page:
                yield chunk
                cursor = int(chunk.split("\n", 1)[0].split(": ", 1)[1])
        if not replayed_any:
            metrics.record_sse_replay(count=0)
        while True:
            page = await asyncio.to_thread(_load_timeline_page, session_factory, user_id, task_id, cursor, None)
            if page:
                for chunk in page:
                    yield chunk
                    cursor = int(chunk.split("\n", 1)[0].split(": ", 1)[1])
                continue
            yield ": ping\n\n"
            await asyncio.sleep(poll_interval)
    finally:
        metrics.change_sse_connections(delta=-1)
```

> 说明：`_format_timeline_sse` 在 `_load_timeline_page` 内就完成格式化并返回块，因此游标从块的 `id:` 首行解析；这与 `events.py` 的"格式化后在循环内推进 cursor"等价。若实现时更偏好返回 `TimelineEvent` 对象再在生成器内格式化，可等效实现，只要游标推进逻辑一致即可。

在 `backend/app/api/routes/execution.py` 追加路由（复用现有 `router`）：

```python
@router.get("/timeline/stream")
def get_timeline_stream(
    task_id: int,
    request: Request,
    after_id: str | None = None,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
) -> StreamingResponse:
    cursor = parse_last_event_id(request, after_id)
    TaskRepository(db).get_owned(user.id, task_id)  # owner-safe 404
    stream_sessions = sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)
    db.rollback()
    stream = timeline_stream(
        session_factory=stream_sessions, user_id=user.id, task_id=task_id, cursor=cursor,
    )
    return StreamingResponse(stream, media_type="text/event-stream")
```

> 路由挂载注意：`timeline_stream.py` 也定义 `router`，但只需在 `routes/execution.py` 挂载即可；若沿现有模块边界把流 service 独立成文件，则 `timeline_stream.py` 不导出 router，路由仍放 `routes/execution.py`（本计划按此）。`app/api/router.py` 无需改动（execution router 已挂载）。

- [ ] **Step 5: 运行流 + task SSE 回归**

```powershell
Set-Location backend
.venv/Scripts/python.exe -m pytest tests/execution/test_timeline_stream_api.py tests/execution/test_execution_api.py tests/api/test_task_events.py -q
.venv/Scripts/python.exe -m ruff check app/api/sse_cursor.py app/execution/timeline_stream.py app/api/routes/events.py app/api/routes/execution.py
.venv/Scripts/python.exe -m mypy app/api/sse_cursor.py app/execution/timeline_stream.py app/api/routes/events.py app/api/routes/execution.py
```

Expected: 全绿；task SSE 行为不变（`test_task_events.py` 全过）。

- [ ] **Step 6: Commit**

```powershell
git add backend/app/api/sse_cursor.py backend/app/execution/timeline_stream.py backend/app/api/routes/events.py backend/app/api/routes/execution.py backend/tests/execution/test_timeline_stream_api.py
git commit -m "feat(execution): stream rich execution timeline over SSE" -m "新增 owner-scoped /execution/timeline/stream，复用 DomainEvent+SSE 与 TimelineMapper，实时推送富 TimelineEvent。关联模块：M-14。"
```

---

### PR3 — Frontend Timeline

### Task 3: Timeline stream 客户端 + useExecution live 模式

**Files:**
- Modify: `frontend/src/features/execution/execution.api.ts`（新增 stream opener + parser）
- Modify: `frontend/src/features/execution/useExecution.ts`（live 状态与接线）
- Test: `frontend/src/features/execution/useExecution.live.test.ts`

**Interfaces:**
- Consumes: `TimelineEvent`（`features/execution/types.ts`）；新端点 `GET /tasks/{id}/execution/timeline/stream`。
- Produces: `openExecutionTimelineStream(taskId, { lastEventId }) -> EventSource`、`parseTimelineSseMessage(data) -> TimelineEvent | null`、`useExecution.connectLive()/disconnectLive()`、`useExecution.live`、`useExecution.reconcileVersion`。

- [ ] **Step 1: 写失败测试（EventSource mock）**

`frontend/src/features/execution/useExecution.live.test.ts`：

```typescript
import { flushPromises } from '@vue/test-utils'
import { nextTick, ref } from 'vue'

import { useExecution } from './useExecution'

vi.mock('./execution.api', () => ({
  getExecution: vi.fn(() => Promise.resolve({ task_id: 25, last_event_id: 9 })),
  getTimeline: vi.fn(() => Promise.resolve({ task_id: 25, items: [], next_cursor: null, has_more: false })),
  getDag: vi.fn(() => Promise.resolve({ task_id: 25, plan_version: 1, spec_version: 1, validation_status: 'VALID', stage_status: {}, nodes: [], edges: [] })),
  openExecutionTimelineStream: vi.fn(() => fakeSource),
}))

const fakeSource = {
  close: vi.fn(),
  addEventListener: vi.fn((type: string, cb: (e: any) => void) => { listeners[type] = cb }),
  ...{},
}
const listeners: Record<string, (e: any) => void> = {}

function emitTimeline(eventId: number) {
  listeners.timeline?.({ data: JSON.stringify({ event_id: eventId, timestamp: '2026-08-21T12:00:00Z', categories: [], stage: 'fetch', summary: '抓取完成', status: 'COMPLETED', node_id: 'n3', node_type: 'fetch', run_id: 8, retry_count: 0 }) })
}

it('事件到达即追加并去重', async () => {
  const store = useExecution(ref(25))
  store.connectLive()
  await flushPromises()
  emitTimeline(10)
  emitTimeline(10)
  emitTimeline(11)
  await nextTick()
  expect(store.timeline.value.map((e) => e.event_id)).toEqual([10, 11])
})

it('burst 事件只触发一次节流 snapshot 刷新', async () => {
  const store = useExecution(ref(25))
  const refresh = vi.spyOn(store, 'refreshSnapshot')
  store.connectLive()
  await flushPromises()
  emitTimeline(10); emitTimeline(11); emitTimeline(12)
  await nextTick()
  vi.advanceTimersByTime(600)   // 节流窗口结束
  expect(refresh).toHaveBeenCalledTimes(1)
})

it('reconnect→open 触发一次 reconcile 刷新', async () => {
  const store = useExecution(ref(25))
  const refresh = vi.spyOn(store, 'refreshSnapshot')
  store.connectLive()
  await flushPromises()
  ;(fakeSource as any).onerror?.()
  ;(fakeSource as any).onopen?.()   // EventSource mock 触发 reconnecting→open
  await nextTick()
  expect(refresh).toHaveBeenCalledTimes(1)
})

it('taskId 变化断开旧流并重置', async () => {
  const taskId = ref(25)
  const store = useExecution(taskId)
  store.connectLive()
  taskId.value = 26
  await nextTick()
  expect(fakeSource.close).toHaveBeenCalled()
  expect(store.live.value).toBe('idle')
})
```

- [ ] **Step 2: 运行测试确认 live 能力缺失**

```powershell
Set-Location frontend
npx vitest run src/features/execution/useExecution.live.test.ts
```

Expected: `connectLive` 未定义 / `openExecutionTimelineStream` 未导出。

- [ ] **Step 3: 实现 stream opener + parser**

`frontend/src/features/execution/execution.api.ts` 追加：

```typescript
const SSE_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api'

export interface TimelineStreamOptions {
  lastEventId?: number
}

export function openExecutionTimelineStream(
  taskId: string | number,
  options: TimelineStreamOptions = {},
): EventSource {
  const url = new URL(`${SSE_BASE_URL}/tasks/${taskId}/execution/timeline/stream`, window.location.origin)
  if (options.lastEventId != null) url.searchParams.set('after_id', String(options.lastEventId))
  return new EventSource(url.toString())
}

export function parseTimelineSseMessage(data: string): TimelineEvent | null {
  try {
    return JSON.parse(data) as TimelineEvent
  } catch {
    return null
  }
}
```

- [ ] **Step 4: 实现 useExecution live 模式**

`frontend/src/features/execution/useExecution.ts` 追加（保持现有导出契约，新增字段）：

```typescript
export type LiveState = 'idle' | 'connecting' | 'open' | 'reconnecting'

// 状态新增：
//   live: Ref<LiveState>           → 'idle'
//   reconcileVersion: Ref<number>  → 0
// 内部：let streamSource: EventSource | null = null; let liveTimer: number | undefined
//       let lastStreamEventId = 0; let refreshToken = 0

function scheduleCoalescedRefresh(): void {
  clearTimeout(liveTimer)
  liveTimer = window.setTimeout(() => {
    const token = ++refreshToken
    void Promise.all([loadOverview(), loadDagIfNeeded()])
      .finally(() => { if (token !== refreshToken) return })
  }, 500)
}

function connectLive(): void {
  disconnectLive()
  if (view.value?.run == null) return            // 无 run 不建流
  live.value = 'connecting'
  streamSource = openExecutionTimelineStream(taskId.value, { lastEventId: lastStreamEventId || undefined })
  streamSource.addEventListener('timeline', (e: MessageEvent) => {
    const dto = parseTimelineSseMessage(String(e.data))
    if (!dto || dto.event_id <= lastStreamEventId) return
    lastStreamEventId = dto.event_id
    mergeTimelineEvent(dto)
    scheduleCoalescedRefresh()
  })
  streamSource.onopen = () => {
    if (live.value === 'reconnecting') reconcileVersion.value += 1
    live.value = 'open'
  }
  streamSource.onerror = () => { live.value = 'reconnecting' }
}

function disconnectLive(): void {
  if (streamSource) { streamSource.close(); streamSource = null }
  clearTimeout(liveTimer)
  live.value = 'idle'
}
```

> `refreshSnapshot()`（overview + timeline 全量）保留作为 reconcile/手动刷新路径；节流刷新只做 `loadOverview()` + 需要时 `loadDag()`（DAG 当前未自动刷新），不重置 timeline（增量以流为准）。在 `watch(taskId, ...)` 中先 `disconnectLive()` 再重置 `lastStreamEventId=0`；返回对象新增 `live`、`reconcileVersion`、`connectLive`、`disconnectLive`。

- [ ] **Step 5: 运行前端单元/类型/lint**

```powershell
Set-Location frontend
npx vitest run src/features/execution/useExecution.live.test.ts src/features/execution/execution.api.test.ts
npm run type-check
npm run lint:check
npm run format:check
```

Expected: 全绿。

- [ ] **Step 6: Commit**

```powershell
git add frontend/src/features/execution/execution.api.ts frontend/src/features/execution/useExecution.ts frontend/src/features/execution/useExecution.live.test.ts
git commit -m "feat(execution): live timeline stream client with coalesced refresh" -m "useExecution 接入 timeline SSE：事件单调去重、节流刷新 snapshot、reconnect reconcile。关联模块：M-14。"
```

---

### Task 4: 实时时间线 UI（步骤行 + 状态转换）

**Files:**
- Create: `frontend/src/features/execution/TimelineStepRow.vue`
- Modify: `frontend/src/features/tasks/TaskExecutionView.vue`（live 连接 + 步骤行渲染 + 当前节点高亮）
- Test: `frontend/src/features/tasks/TaskExecutionView.live.test.ts`

**Interfaces:**
- Consumes: `useExecution.live/connectLive/disconnectLive/reconcileVersion`、`TimelineEvent`、`view.current_node`。
- Produces: `TimelineStepRow.vue`（props `{ event: TimelineEvent; active?: boolean }`）、执行页 live 行为。

- [ ] **Step 1: 写失败组件测试**

`frontend/src/features/tasks/TaskExecutionView.live.test.ts`：

```typescript
it('run 激活时自动连接流并显示实时状态', async () => {
  const wrapper = mount(TaskExecutionView, { props: { taskId: '25' } })
  await flushPromises()
  expect(connectLiveSpy).toHaveBeenCalled()
  expect(wrapper.text()).toContain('实时')
})

it('新事件追加为步骤行且当前节点高亮', async () => {
  emitTimeline({ event_id: 12, summary: '抓取完成', status: 'COMPLETED', node_id: 'n3', stage: 'fetch', node_type: 'fetch' })
  await nextTick()
  expect(wrapper.text()).toContain('抓取完成')
  expect(wrapper.find('.timeline-step-row--active').exists()).toBe(true)
})

it('进行中状态显示脉冲指示，完成后显示成功态', async () => {
  emitTimeline({ event_id: 13, status: 'RUNNING', node_id: 'n4', summary: '提取字段', stage: 'extraction' })
  await nextTick()
  expect(wrapper.find('.step-status--running').exists()).toBe(true)
})
```

- [ ] **Step 2: 运行确认无实时组件/接线**

```powershell
Set-Location frontend
npx vitest run src/features/tasks/TaskExecutionView.live.test.ts
```

Expected: `TimelineStepRow` import 失败、执行页无 live 连接。

- [ ] **Step 3: 实现 TimelineStepRow**

`frontend/src/features/execution/TimelineStepRow.vue`（无 UI 库，scoped 样式 + 现有 design tokens）：props `{ event: TimelineEvent; active?: boolean }`；按 `event.status` 渲染状态指示（`RUNNING`/`WAITING_*` → 脉冲点；`SUCCEEDED`/`COMPLETED` → 成功勾；`FAILED`/`BLOCKED` → 叉 + `error_code`）；展示 `event.summary`、本地时间（`Intl.DateTimeFormat`）、`tool`/`model`/`tokens_in/out`/`duration_ms` chips、`retry_count > 0` 重试徽标、`node_id`；`active` 时加 `.timeline-step-row--active` 高亮。

```vue
<script setup lang="ts">
import { computed } from 'vue'
import type { TimelineEvent } from './types'

const props = defineProps<{ event: TimelineEvent; active?: boolean }>()
const time = computed(() =>
  new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(new Date(props.event.timestamp)),
)
const running = computed(() => ['RUNNING', 'WAITING_RESOURCE', 'WAITING_RETRY', 'PENDING'].includes(props.event.status ?? ''))
const failed = computed(() => ['FAILED', 'BLOCKED'].includes(props.event.status ?? ''))
</script>

<template>
  <li class="timeline-step-row" :class="{ 'timeline-step-row--active': active }">
    <span class="step-status" :class="running ? 'step-status--running' : failed ? 'step-status--failed' : 'step-status--done'">
      <span v-if="running" class="step-status__pulse" />
      <span v-else-if="failed" class="step-status__cross" />
      <span v-else class="step-status__check" />
    </span>
    <div class="step-body">
      <div class="step-line">
        <span class="step-summary">{{ event.summary }}</span>
        <span class="step-time">{{ time }}</span>
      </div>
      <div class="step-meta">
        <span v-if="event.node_id" class="step-chip">{{ event.node_id }}</span>
        <span v-if="event.retry_count > 0" class="step-chip">重试 {{ event.retry_count }}</span>
        <span v-if="event.error_code" class="step-chip step-chip--error">{{ event.error_code }}</span>
        <span v-if="event.tool" class="step-chip">{{ event.tool }}</span>
        <span v-if="event.model" class="step-chip">{{ event.model }}</span>
        <span v-if="event.duration_ms != null" class="step-chip">{{ event.duration_ms }}ms</span>
      </div>
    </div>
  </li>
</template>
```

（scoped styles 使用 `base.css` 的 `--color-success/--color-danger/--color-border/--color-text-secondary` 设计变量。）

- [ ] **Step 4: 执行页 live 接线**

`TaskExecutionView.vue`：`onMounted` 中当 `view.value?.run` 存在且状态非终态时调用 `useExecution.connectLive()`；`onBeforeUnmount` 调用 `disconnectLive()`；顶部连接指示（`live === 'open'` → "实时更新中"，`reconnecting` → "连接中断，正在恢复…"）；时间线列表改为遍历 `timeline` 渲染 `<TimelineStepRow :event="item" :active="item.node_id === view?.current_node?.node_id" />`；保留 category 过滤（过滤作用于渲染前）。reconcileVersion watch → `refreshSnapshot()`。

- [ ] **Step 5: 运行前端测试/类型/lint/build**

```powershell
Set-Location frontend
npx vitest run src/features/tasks/TaskExecutionView.live.test.ts src/features/tasks/TaskExecutionView.test.ts src/features/execution
npm run type-check
npm run lint:check
npm run format:check
npm run build
```

Expected: 全绿；build（vue-tsc + vite）通过。

- [ ] **Step 6: Commit**

```powershell
git add frontend/src/features/execution/TimelineStepRow.vue frontend/src/features/tasks/TaskExecutionView.vue frontend/src/features/tasks/TaskExecutionView.live.test.ts
git commit -m "feat(execution): render live timeline steps with status transitions" -m "执行页连接 timeline 流，步骤行实时追加并显示进行中/完成/失败状态，当前节点高亮。关联模块：M-14。"
```

---

### Task 5: 实时阶段卡与 DAG 节点着色

**Files:**
- Modify: `frontend/src/features/tasks/TaskExecutionView.vue`（阶段卡 live 状态、DAG 节点着色、当前节点高亮）
- Modify: `frontend/src/features/execution/useExecution.ts`（`loadDagIfNeeded` 并入节流刷新；DAG reconcile）
- Test: `frontend/src/features/tasks/TaskExecutionView.live.test.ts`（扩展）

**Interfaces:**
- Consumes: `view.stages`（`StageSummary.state`：`not_started/in_progress/completed/partial/failed`）、`dag.nodes[].execution.last_status`、`view.current_node`。
- Produces: 阶段卡在事件到达后 `in_progress → completed/failed` 视觉转换；DAG 节点按 live 状态着色。

- [ ] **Step 1: 写失败测试**

扩展 `TaskExecutionView.live.test.ts`：

```typescript
it('阶段卡随事件由 in_progress 转为 completed', async () => {
  // 初始 snapshot stages: [{key:'fetch', state:'in_progress'}]
  expect(wrapper.find('.stage-card--in_progress').exists()).toBe(true)
  emitTimeline({ event_id: 12, status: 'COMPLETED', node_id: 'n3', node_type: 'fetch', stage: 'fetch' })
  await nextTick(); await advanceTimers(600)   // 节流刷新后 snapshot 返回 completed
  expect(wrapper.find('.stage-card--completed').exists()).toBe(true)
})

it('DAG 节点按 live 状态着色并高亮当前节点', async () => {
  wrapper.find('.exec-toggle').trigger('click')   // 切到 DAG
  await flushPromises()
  expect(wrapper.find('.dag-node--succeeded').exists()).toBe(true)
  expect(wrapper.find('.dag-node--active').exists()).toBe(true)   // current_node 高亮
})
```

- [ ] **Step 2: 运行确认失败**

```powershell
Set-Location frontend
npx vitest run src/features/tasks/TaskExecutionView.live.test.ts
```

- [ ] **Step 3: 实现**

`useExecution.ts` 节流刷新中加入 `loadDagIfNeeded()`（DAG 已加载且 `viewMode==='dag'` 时刷新）；`TaskExecutionView.vue`：阶段卡 class 由 `stage.state` 驱动（`in_progress` 加脉冲动画）；DAG 节点 class 由 `node.execution.last_status`（`SUCCEEDED`/`FAILED`/`RUNNING`/`WAITING_*`）驱动，`node_id === view.current_node?.node_id` 时加 `.dag-node--active`。无金额/百分比/CoT。

- [ ] **Step 4: 运行前端检查**

```powershell
Set-Location frontend
npx vitest run src/features/tasks/TaskExecutionView.live.test.ts src/features/execution
npm run type-check && npm run lint:check && npm run format:check && npm run build
```

Expected: 全绿。

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/features/tasks/TaskExecutionView.vue frontend/src/features/execution/useExecution.ts frontend/src/features/tasks/TaskExecutionView.live.test.ts
git commit -m "feat(execution): live stage transitions and dag node coloring" -m "阶段卡随事件实时转换状态，DAG 节点按 live 事实着色并高亮当前节点。关联模块：M-14。"
```

---

### PR4 — Real Task Verification

### Task 6: 集成回归 + 真实任务验收 + 文档决策

**Files:**
- Modify: `backend/tests/execution/test_timeline_stream_api.py`（补齐边界：超界游标 400、大回放分页、连接指标递减）
- Modify: `agent-business-logic-log.md`（追加 **D-078**）
- Modify: `agent-project-implementation-plan.md`（M-14/相关验收行补实施证据）
- Create: `infra/scripts/_execution_timeline_staging_acceptance.py`（**未入库**，`_` 前缀约定）
- Create: `docs/audits/agent-execution-timeline-verification-2026-08-21.md`（验收证据）

**Interfaces:** 消费 Task 1–5 全部接口；产出真实任务验收证据（事件序列、无重复、无 secret、Temporal/DB/UI 一致）。

- [ ] **Step 1: 后端 scoped 回归 + 门禁**

```powershell
Set-Location backend
.venv/Scripts/python.exe -m pytest tests/execution tests/api/test_task_events.py tests/api/test_understand.py tests/api/test_plan_api.py -q
.venv/Scripts/python.exe -m ruff check app tests
.venv/Scripts/python.exe -m mypy app
.venv/Scripts/python.exe -m alembic heads   # 期望唯一 0017（head）
.venv/Scripts/python.exe -c "from app.main import create_app; create_app()"
```

Expected: 全绿；`alembic heads` 唯一 `0017`（零 migration）；无 EventType/状态机改动。

- [ ] **Step 2: 前端全量门禁**

```powershell
Set-Location frontend
npm run test:unit
npm run type-check
npm run lint:check
npm run format:check
npm run build
```

Expected: 全绿。

- [ ] **Step 3: 真实任务验收脚本（未入库）**

`infra/scripts/_execution_timeline_staging_acceptance.py`：CLI `--base-url --username-env --password-env --output`（沿用现有 `_m16/_m17` 验收脚本模式，**不 git add**）。登录后创建一个可运行任务（或复用受控测试任务），轮询 `/execution/timeline/stream` 与 `/execution`，断言：
1. 流按序推送 `RUN_STARTED → NODE_STARTED → NODE_COMPLETED → RUN_*`，`event_id` 严格递增；
2. 每条 `TimelineEvent` 含 `event_id/timestamp/stage/summary`，secret-scan 通过（无 api_key/cookie/authorization/token）；
3. `?after_id=最后游标` 重连后**不重复**事件；
4. UI 等价：`/execution` snapshot 的 `last_event_id` 与流最终游标一致；
5. 输出 secret-free JSON 报告到 `--output`，退出码非零表示失败。写 `docs/audits/agent-execution-timeline-verification-2026-08-21.md`。

浏览器 E2E（可选，`frontend/e2e/execution-live-timeline.spec.ts`）：打开 `/tasks/:id/execution`，断言运行中页面出现实时步骤行、刷新/断网重连后事件不重复、阶段卡/当前节点高亮可见；截图走既有 Playwright 输出配置。

- [ ] **Step 4: 写文档决策 D-078**

在 `agent-business-logic-log.md` 追加（状态【已确认】需用户确认前先标【待讨论】；实施前由用户确认）：

```text
## D-078：执行详情页提供实时执行时间线流（Execution Visibility Layer）
- 状态：待讨论
- 日期：2026-08-21
- 维度：12. 可观测性
- 背景：D-055/D-063 已确定执行详情二级页与"阶段+时间线"；D-077 已确定 snapshot 权威 + SSE 增量。
- 决定：在既有 /tasks/:taskId/execution 二级页提供实时工作流展示：
  - 新增 owner-scoped SSE 流 GET /tasks/{taskId}/execution/timeline/stream，输出与 REST timeline 完全一致的富 TimelineEvent（复用 TimelineMapper）。
  - 前端执行页连接该流：事件逐条实时追加、阶段卡与计数节流刷新、DAG 节点按 live 事实着色、reconnect 后 reconcile 一次。
  - 复用 D-077 的 DomainEvent.id 游标语义与已知提交顺序限制，由 reconnect reconcile 兜底；不新增事件表/消息中间件，不修改 Workflow/Temporal/Agent Loop/Provider/Extraction，零 migration。
- 影响：用户可实时看到 Agent 每一步执行；后端只读新增，不影响 DEPLOY_GATE-3 已验证据与 Production 既有行为。
```

- [ ] **Step 5: 提交代码与文档**

```powershell
git add backend/tests/execution/test_timeline_stream_api.py agent-business-logic-log.md agent-project-implementation-plan.md docs/audits/agent-execution-timeline-verification-2026-08-21.md
git commit -m "docs(execution): record real-time timeline acceptance and decision" -m "记录 timeline stream 真实任务验收证据与 D-078 决策；验收脚本保持未入库。关联模块：M-14、M-17。"
```

> 若实现后需要部署到 Staging/Production，必须按 CLAUDE.md §3.5 先完整重读 `agent-production-deployment-standards.md`，走 Git→CI→GHCR→Staging→Smoke→Production 受控链路；本计划默认交付到"本地全量门禁 + 真实任务验证"，不自动部署。

- [ ] **Step 6: Review 与 PR**

按 PR 提交后 invoke `superpowers:requesting-code-review`，修复每条验收建议（带测试）后合入 main。Record PR URL / merge SHA / CI 地址于验收 audit。**未经用户明确要求不 Push/Merge**。

---

## Spec Coverage Matrix

| 需求/差距 | 实现于 |
|---|---|
| G3 共享 TimelineMapper（REST 与流一致映射） | Task 1 |
| G4 Owner-scoped 富 TimelineEvent SSE 流（replay+poll+keepalive+Last-Event-ID） | Task 2 |
| G2 SSE 事件→`TimelineEvent` 客户端适配 + 单调去重 + 节流 snapshot + reconnect reconcile | Task 3 |
| G1 执行页 live 时间线（步骤行 + 进行中/完成/失败状态 + 当前节点高亮） | Task 4 |
| G5 阶段卡 live 转换 + DAG 节点 live 着色 | Task 5 |
| G6 游标提交顺序限制沿用 + reconcile 兜底 | Task 2/3（设计 §3.4） |
| 安全：owner 隔离 / allowlist / 无 secret / 无百分比 / 无 CoT | Task 1–4（Global Constraint 4–7） |
| 零 migration、不改 Workflow/Temporal/Agent Loop/Provider/Extraction、既有 task SSE 不变 | Task 1–5 设计约束 + Task 6 门禁 |

---

## Task Dependency Order

```text
Task 1 TimelineMapper 抽取
  → Task 2 Timeline Stream SSE 端点
    → Task 3 前端流客户端 + useExecution live
      → Task 4 实时步骤行 UI
        → Task 5 实时阶段/DAG
          → Task 6 集成回归 + 真实任务验收 + 决策
```

PR 边界：PR1=Task1、PR2=Task2、PR3=Task3–5、PR4=Task6。每 Task 独立测试周期与 commit；依赖顺序必须遵守。

---

## §6 自检（DEPLOY_GATE_3 / Production 安全确认）

| 检查项 | 结论 |
|---|---|
| 是否触碰 Temporal 核心 Workflow / Activity 执行语义？ | **否**。仅新增只读查询 + SSE 流；`task_workflow.py`、`plan_execution.py`、`execution_seam.py` 零改动。 |
| 是否触碰 Agent Loop / Goal Understanding / Plan 生成 / Provider / Extraction？ | **否**。`app/agents/`、`app/providers/`、`app/extraction/`、`app/discovery/` 执行逻辑零改动。 |
| 是否改事件 schema / 状态机 / `allowed_actions`？ | **否**。`DomainEvent` 语义、`event_type` 集合、Task/Node 状态机、`allowed_actions` 均不变；新增端点只读投影。 |
| 是否新增事件表 / 消息中间件 / WebSocket？ | **否**。复用 `DomainEvent + SSE`；无 Redis/Kafka/新表。 |
| 是否新增 migration？ | **否**。`alembic heads` 保持唯一 `0017`；Task 6 门禁强制验证。 |
| 既有 SSE `/api/events/tasks/{task_id}` 及其消费者行为？ | **不变**。游标解析抽取到共享模块但逻辑逐字一致，`test_task_events.py` 回归兜底；新流是纯新增端点。 |
| DEPLOY_GATE_3 已验证据影响？ | **无**。Gate-3 证据基于任务执行/事件/UI 事实链路，本计划只增加只读展示层；不重放、不覆盖任何事件。 |
| Production 风险面？ | 新增只读 SSE 端点 + 前端页面增强；并发连接走既有每进程 2s 轮询模式与连接指标；无写路径。 |
| 安全 | owner 404、payload allowlist、无 secret/CoT/百分比；验收脚本 secret-free。 |
