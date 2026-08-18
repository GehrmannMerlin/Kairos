# M-11 Extraction Runtime Closure — 根因与实施计划

> 日期：2026-08-18
> 分支：`fix/m11-extraction-runtime-closure`（基线 `1ac3b78` = origin/main）
> 目标：修复 PageSnapshot → Extract → Evidence → Normalize → Deduplicate → Validate 真实 Runtime 闭环，
> 消除 `CancelledError` 后 NodeAttempt 残留 RUNNING 与 20 页全灭 0 records。
> 状态：**DONE（Staging 验证通过）** — 最终报告见 `docs/audits/` 或会话记录；batch_round 二次修复
> 走 §25.4 registry-push 降级部署，PR #35 push 被 GitHub 网络阻断 → **PENDING（网络恢复后补闭环）**。

---

## 1. 根因结论（systematic-debugging 输出）

### 1.1 谁取消了 `pydantic-ai agent.run()`？

**是 Temporal Activity 超时取消，不是 Provider 超时。**

完整调用链与证据：

```text
TaskWorkflow.run (task_workflow.py:364)
└─ workflow.execute_activity(execute_safe_unit, ..., start_to_close_timeout=120s)
   └─ execute_safe_unit (plan_execution.py)
      └─ await executor(unit)                                  ← CancelledError 落在这一层
         └─ ExtractNodeExecutor.execute (executor.py)
            └─ for snapshot in pending (≤50 snapshots, 单 Activity 全量处理)
               └─ pipeline.run() → agent.run()                 ← 此处观察到的 CancelledError
```

证据链（Temporal Python SDK `_activity.py`）：

- worker `_RunningActivity.cancel()`（`_activity.py:712-731`）在服务端发来取消时调用 `self.task.cancel()`，
  向正在执行的 Activity coroutine 注入 **`asyncio.CancelledError`**。
- 服务端在 `start_to_close_timeout` 到期时向 worker 发送该取消（`_activity.py:202-219` `_handle_cancel_activity_task`）。
- Activity 超时值：`task_workflow.py:364` 固定 `start_to_close_timeout = timedelta(seconds=120)`。

### 1.2 为什么 120s 会被击穿

`ExtractNodeExecutor.execute()`（`executor.py:60-82`）在**一个 Activity 调用内**按序处理
`pending_snapshots(limit=max_batch=50)`，每个快照最坏消耗：

```text
provider_inference_timeout_seconds = 45s（首次调用）
+ 45s（缩小上下文重试一次, pipeline.py:111-125）
= 90s / 快照
```

因此：

- Task 119：20 snapshots，最坏 20×90 = 1800s ≫ 120s。
- Task 115：100+ snapshots，同样必然击穿。
- 即便每个快照平均 10s，20×10 = 200s > 120s 仍会击穿。

**此前 12k→6k 上下文重试（commit 2cc6cf3）只缩小单请求超时概率，不解决 N 个快照的累计时间**
（120s 预算按整个 Activity 计算），因此没有消除 CancelledError。

### 1.3 为什么 NodeAttempt 残留 RUNNING（P0）

`execute_safe_unit`（`plan_execution.py:171-217`）只捕获 `except Exception`：

- Python ≥3.11 中 `asyncio.CancelledError` 继承 `BaseException`，**不被 `except Exception` 捕获**。
- 因此 CancelledError 直接穿透，`lifecycle.finish_attempt(...)` 从不执行。
- SDK 将 Activity 按 canceled/failed 收尾（`_activity.py:420-435`），worker 不再有机会补写 attempt。
- 结果：`node_attempts.status = RUNNING` 且 `finished_at IS NULL`，永久卡死。

### 1.4 为什么 0 records

`ExtractNodeExecutor.execute()`（`executor.py:114`）在**整个循环结束后才 `self._db.commit()` 一次**：

- Activity 中途被取消 → 已产生的 Record/FieldEvidence 全部未提交 → 全部丢失 → 0 records。
- 违反 D-015（小批次事务 + Checkpoint）、D-013（单页失败局部化）。

### 1.5 次要发现（顺带修正，不改语义）

- `BROWSER_RENDER` NodeDefinition `timeout_seconds=180`，但 Workflow 对所有单元固定 120s →
  Playwright 渲染 >120s 也会被同样取消。属同一条 Timeout Hierarchy 缺陷。
- `execute_safe_unit` 无 `retry_policy` → Temporal 默认无限重试 FAILED Activity（与内层 provider
  有界重试形成 retry multiplication 风险，§29）。需有界化并排除 CancelledError。

---

## 2. Timeout Ownership Map（修复后目标态）

原则：**内部超时 < 外部执行边界超时**，任何一层都不会先于内层 recovery 杀掉 coroutine。

| Layer | 当前值 | 修复后 | Owner | Raised error |
| ----- | -----: | -----: | ----- | ------------ |
| HTTP (connect/read/write) | 45s | 45s（不变） | HttpxTransport（transport.py:42-69） | `ProviderTimeoutError(CONNECT/READ)` |
| Provider 单请求 asyncio.timeout | 45s | 45s（不变） | ModelInferenceClient（inference.py:129） | `ProviderTimeoutError(OVERALL)` |
| 单快照 LLM 最坏（首次+缩小重试） | 90s | 90s（不变） | ExtractionPipeline（pipeline.py:111-125） | 超时→缩小重试→快照级失败 |
| Extract 单 Activity 内执行预算 | 无 | `extract_activity_budget_seconds=100` | ExtractNodeExecutor | 返回 `MORE_PENDING`（非 timeout） |
| Temporal Extract Activity | 120s | `EXTRACT NodeDefinition.timeout_seconds=200`（thread 到 Workflow） | NodeRegistry + task_workflow | 仍超时→CancelledError→**attempt 收口 CANCELLED** |
| Temporal BROWSER_RENDER Activity | 120s | 180s（NodeDefinition thread） | NodeRegistry + task_workflow | CancelledError→attempt 收口 |

关键不变量：

- 预算检查在**每个快照开始前**执行：`elapsed + extract_snapshot_worst_case(90s) > budget(100s)` 时停止，
  保证 Activity 墙钟时间 ≤ ~190s < 200s timeout，正常操作永不触发 Temporal 取消。
- `budget(100s) < activity_timeout(200s)`。
- 任何残余 CancelledError 都走 attempt CANCELLED 收口，绝不残留 RUNNING。

---

## 3. 异常分类（Cancellation Taxonomy）

| 类别 | 来源 | 处理 |
| ---- | ---- | ---- |
| `ProviderTimeoutError` | HTTP/`asyncio.timeout` 到期 | pipeline 缩小上下文有界重试一次 → 再失败 = 快照级合法失败（`extraction_status=failed`） |
| `asyncio.CancelledError`（真正取消） | Temporal 取消 / worker shutdown / start_to_close 超时 | **向上传播**，attempt → `CANCELLED`，lease/心跳释放 |
| 用户取消 | workflow `cancel` signal | 循环顶处理，不当作 provider retry |
| 其它 `Exception` | 业务错误 | attempt → `FAILED`，Temporal 有界重试 |

禁止：把 CancelledError catch 后伪装成 `PROVIDER_TIMEOUT`；对 CancelledError 无限重试。

---

## 4. 具体修改

### 4.1 Attempt 收口（P0）— `plan_execution.py` + `execution/lifecycle.py`

**`backend/app/execution/lifecycle.py`**

- `_LIFECYCLE_STATUS` 增加：
  - `"CANCELLED": ("run.node_cancelled", "CANCELLED", "CANCELLED", True)`
  - `"MORE_PENDING": ("run.node_completed", "SUCCEEDED", "SUCCEEDED", True)`
- `_SAFE_REASON_CODES` 增加 `"CANCELLED"`。

**`backend/app/activities/plan_execution.py`** — `execute_safe_unit` 在现有 `except Exception` 之前增加：

```python
except asyncio.CancelledError:
    # Temporal 取消（start_to_close 超时 / workflow cancel / worker shutdown）。
    # 必须先把 attempt 收口为 CANCELLED，绝不残留 RUNNING，再向上传播取消。
    try:
        lifecycle.finish_attempt(
            run_id=inp.run_id, unit=inp.unit, attempt=attempt,
            status="CANCELLED", committed_refs={},
            error_code="CANCELLED",
        )
    except Exception:
        lifecycle_session.rollback()
        logger.warning("lifecycle_cancel_finish_failed run_id=%s node_id=%s", ...)
    raise
```

说明：CancelledError 只落在 `await` 点，`start_attempt` 是同步的，故进入本 handler 时 attempt 必为
RUNNING；`finish_attempt` 全同步（SQLAlchemy 同步提交），取消后仍可完成一次 DB 写。最后 `raise`
恢复传播，`finally` 释放 lease + 停止心跳。

### 4.2 小批次 + 每快照提交 + 失败账本 — `extraction/executor.py` + `repository.py` + `contracts.py`

**`backend/app/extraction/contracts.py`** — `ExtractionSettings` 增加：

```python
extract_batch_size: int = 5            # 单次 Activity 最多处理的快照数
extract_activity_budget_seconds: int = 100  # 单次 Activity 墙钟预算（< Activity timeout）
```

**`backend/app/extraction/repository.py`** — `pending_snapshots` 排除已失败快照：

```python
rows = list(self._db.scalars(
    select(PS).where(PS.user_id == user_id, PS.task_id == task_id)
        .where(PS.extraction_status.is_(None))
        .order_by(PS.id)
))
```

**`backend/app/extraction/executor.py`** — `ExtractNodeExecutor.execute()` 重写循环：

1. `limit = min(self._max_batch, self._settings.extract_batch_size)`。
2. `started = perf_counter()`；**每个快照开始前**：
   `if perf_counter() - started > self._settings.extract_activity_budget_seconds: break`。
3. 每个快照：
   - `pipeline.run(...)` 成功且有 candidates → `_persist` 记录 + evidence → `emit_event(completed)`
     → `self._db.commit()`（**每快照独立事务提交**）。
   - `pipeline.run` 抛 ProviderTimeout（缩小重试后仍失败）或产生 0 candidates（`result.candidates` 为空）
     → `emit_event(extraction.failed)` → **`snapshot.extraction_status = "failed"`** → `self._db.commit()`。
   - 其它 `Exception` → `emit_event(extraction.failed)` → `self._db.commit()`，继续下一快照。
   - **CancelledError 不捕获**（BaseException），自然向上传播；此前快照已各自提交，不丢。
4. 结束后：
   - `remaining = 剩余未处理（含提取中失败标记前的 pending 数）`；用 `repo.pending_snapshots` 重新计数。
   - `batch_identity = f"extract-{run.id}-{unit.index}-{首快照id or 0}"`。
   - `remaining > 0` → `status="MORE_PENDING"`；否则 `status="OK"`。
   - committed_refs 含 `extracted / failed / remaining / batch_identity / run_id / node_id / node_type / snapshot_ids`。

**`backend/app/extraction/executors.py`** — `_extract` 构造时无需改（settings 从 executor 内部读取）。

### 4.3 失败账本 schema — `alembic/versions/0017_*.py`

`page_snapshots` 增加 nullable 列：

```python
op.add_column("page_snapshots", sa.Column("extraction_status", sa.String(30), nullable=True))
```

兼容 expand（纯新增，新旧代码共存）。`extraction_status` 取值：`None`（待提取）/ `"failed"`（已失败）。

### 4.4 Workflow MORE_PENDING + timeout thread — `task_workflow.py` + `execution_seam.py` + `plan_execution.py`

**`backend/app/activities/execution_seam.py`** — `ExecutionUnit` 增加 `timeout_seconds: int | None = None`。

**`backend/app/activities/plan_execution.py`** — `fetch_next_execution_unit` 填充
`timeout_seconds=definition.timeout_seconds if definition else None`。

**`backend/app/workflows/task_workflow.py`**：

- `exec_kwargs` 改为按 `unit.timeout_seconds` 取值，缺省 120s：
  ```python
  timeout = timedelta(seconds=unit.timeout_seconds or 120)
  exec_kwargs = {"start_to_close_timeout": timeout}
  ```
- 给 `execute_safe_unit` 增加有界 retry policy（防 retry multiplication）：
  ```python
  exec_kwargs["retry_policy"] = RetryPolicy(
      maximum_attempts=3, initial_interval=timedelta(seconds=2),
      non_retryable_error_types=["CancelledError", "asyncio.CancelledError"],
  )
  ```
- 增加 `MORE_PENDING` 分支（放在 `RESOURCE_WAITING` 之前）：
  ```python
  if exec_result.status == "MORE_PENDING":
      refs = exec_result.committed_refs or {}
      await workflow.execute_activity(
          commit_checkpoint, CommitCheckpointInput(..., batch_identity=str(
              refs.get("batch_identity") or f"unit-{unit.index}"), ...),
          start_to_close_timeout=timedelta(seconds=60))
      continue  # 不推进 index，重取同一 EXTRACT 单元处理下一小批
  ```
- 通用 OK 分支的 `batch_identity` 同样优先取 `committed_refs.batch_identity`。

**`backend/app/plan/nodes.py`** — `EXTRACT NodeDefinition.timeout_seconds: 120 → 200`
（保持 NodeDefinition 为单一时源）。

### 4.5 确定性提取阶梯

审计结论：`ExtractionPipeline.run` 已按 D-010 接好 JSON-LD → Meta → Table → SiteRules → LLM fallback
（`pipeline.py:77-90`），字段级 unresolved fallback（`pipeline.py:89-186`）。**Runtime 已接通，无结构性 Bug。**
本轮只补一条回归断言（结构化页面 `llm_invocations == 0`），并记录真实页面调用比例。

### 4.6 LLM Fallback

- 保持 12k → 6k 缩小上下文重试一次（D-013 有界）。
- 每快照只发 unresolved 字段 + 有界上下文（现状不变）。
- `llm_invocations/llm_retries` 继续通过 `technical_metadata` 上抛（现状不变）。

---

## 5. 测试计划（`backend/tests/`）

### `tests/activities/test_plan_execution.py` 增加

1. `test_execute_safe_unit_finalizes_attempt_on_cancellation`：executor 抛 `asyncio.CancelledError` →
   `pytest.raises(asyncio.CancelledError)`，断言 NodeAttempt 终态为 `CANCELLED` 且 `finished_at` 非空，
   事件序列含 `run.node_cancelled`，**无残留 RUNNING**。
2. `test_execute_safe_unit_more_pending_is_succeeded`：executor 返回 `status="MORE_PENDING"` →
   事件 `run.node_completed`，NodeAttempt SUCCEEDED。

### `tests/execution/test_lifecycle_recorder.py` 增加

3. `test_finish_attempt_cancelled_terminal`：`finish_attempt(status="CANCELLED")` → attempt/NodeRun 终态正确。

### `tests/extraction/test_executor.py`（新文件）增加

4. `test_executor_processes_bounded_batch_and_returns_more_pending`：seed 7 snapshots，`extract_batch_size=5` →
   首次调用处理 ≤5 且**每快照已提交**，返回 `MORE_PENDING` + `remaining>0`；二次调用处理剩余 → `OK`。
5. `test_executor_commits_snapshot_independently`：snapshot2 抛普通异常 → snapshot1 的 Record/Evidence
   已持久化，snapshot3 继续处理 → 不 0 records。
6. `test_executor_marks_terminal_failure_snapshot`：0 candidates → `extraction_status="failed"`，
   后续 `pending_snapshots` 不再返回它。
7. `test_executor_activity_budget_returns_more_pending`：预算检查（monotonic 打桩）→ 提前 MORE_PENDING。
8. `test_cancelled_error_not_caught_as_provider_timeout`：`_is_provider_timeout(asyncio.CancelledError())`
   == False；CancelledError 从 executor 正常传播（无 except Exception 吞掉）。

### `tests/extraction/test_pipeline.py` 增加

9. `test_structured_page_uses_no_llm`（回归）：JSON-LD/Meta 完备页 → `llm_invocations == 0`。

### 现有测试回归

- `tests/extraction/`、`tests/activities/test_plan_execution.py`、`tests/execution/` 全量通过。
- 移除对旧「单次 commit 全量」行为的隐式依赖（若有）。

---

## 6. Staging Replay 计划（代码合并 + 部署后）

### 第一阶段：Extraction-only Replay（复用 Task 119 已存 PageSnapshots）

工具：`infra/scripts/_m11_extract_replay.py`（开发/运维 scoped，owner-safe、默认 dry-run、
复用真实 `ExtractNodeExecutor` + frozen spec，不直接写绕过状态机的数据，不成为 Production 后门）。

矩阵：

| 页面数 | 预期 |
| -----: | ---- |
| 1 | Record/合法失败；Attempt 不残留 RUNNING |
| 3 | ≥1 成功批次提交；失败页不拖死其它 |
| 5 | 同上，`records > 0`、`FieldEvidence > 0` |

记录：input pages / cleaned chars / prompt chars / LLM duration / outcome / records / evidence / provider calls。

真实 DeepSeek 调用预算 ≤ 5 次（调试阶段）。

### 第二阶段：新小型真实采集 Task

`Search → Fetch → Extract → Evidence → Normalize → Deduplicate → Validate` 完整跑通；
Task ID / Run ID / URLs / Snapshots / Records / Evidence / 分区计数 / 终态。本轮不强求 Round 2（§41）。

---

## 7. Git 计划

分支：`fix/m11-extraction-runtime-closure`，从 `main`（`1ac3b78`）切出。

建议 commit 边界：

1. `fix(worker): finalize extraction attempts on cancellation`
   （plan_execution + lifecycle + MORE_PENDING status；CancelledError 收口 CANCELLED，绝不残留 RUNNING）
2. `fix(extract): process bounded batch with per-snapshot commit`
   （executor + repository + contracts + migration 0017 + MORE_PENDING 分支 + timeout thread + NodeDefinition）
3. `test(extract): cover cancellation/batch/failure taxonomy`
   （§5 全部测试）
4. `docs(plan): record m11 extraction runtime closure plan`

Commit message 遵循 Conventional Commits；正文附模块关联与中文说明（agent-git-standards.md）。

---

## 8. 自检（对照任务书 §33）

- [x] 1. 找到 CancelledError 真正来源：Temporal Activity start_to_close=120s 取消（SDK `_activity.py` 证据）。
- [x] 2. 不只加 timeout：结构化为小批次 + 预算 + 每快照提交 + Attempt 收口。
- [x] 3. 修 Attempt stuck RUNNING：`except asyncio.CancelledError` → finish_attempt(CANCELLED)。
- [x] 4. ProviderTimeout vs Cancellation 明确：taxonomy §3。
- [x] 5. Extract batch size：`extract_batch_size=5`（staging replay 观测再调）。
- [x] 6. 成功批次立即提交：每快照独立 commit。
- [x] 7. 业务 Checkpoint：MORE_PENDING/OK 后 Workflow `commit_checkpoint`（batch_identity 唯一）。
- [x] 8. 一页失败不拖死全部：每快照提交 + `extraction_status=failed` + 失败快照跳过。
- [x] 9. Deterministic extractor 真在 Runtime：pipeline 阶梯已接通，补回归断言。
- [x] 10. 不全部默认走 LLM：结构化页不触发 LLM（测试 + replay 记录比例）。
- [x] 11. 防 retry multiplication：execute_safe_unit 加 RetryPolicy(max 3, 排除 CancelledError)。
- [x] 12. 有界 Provider 调用：每快照 ≤2 次，预算 + batch 双重有界。
- [x] 13. 未改 Search / M-12 / M-16：只动 plan_execution/lifecycle/executor/workflow 边界。

---

## 9. DONE Gate（任务书 §44）

全部满足才可 DONE：

- 根因与 timeout hierarchy 明确（本计划 §1-§2）。
- 任何退出路径不残留 RUNNING（Attempt 收口测试）。
- 真实 stored snapshots → `records > 0`。
- `FieldEvidence > 0`。
- 一页失败 ≠ 全部丢失（batch 测试 + replay）。
- 成功批次存在 checkpoint。
- Deterministic ladder：至少一条真实页或 fixture 证明不调用 LLM。
- LLM 是 fallback 不是无条件默认路径。
- 新小型真实 Staging Task 跑通 Fetch→Extract→Evidence→Validate。
