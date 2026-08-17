# M-12 HYBRID Completion / Dedupe Invariant / Continuation Closure

日期：2026-08-17
分支基线：`main` @ `f6a4819`（已同步 origin/main）
关联模块：M-08（Plan Validator）、M-12（Completion）
状态：IN_PROGRESS

---

## 1. 根因（systematic-debugging 已确认）

真实 Staging Task 104（HYBRID）：
- `4 fetched / 4 eligible / 3 records / 1 PASSED / 2 NEEDS_REVIEW / CSV ready / 全节点 SUCCEEDED`
- 终态 `FAILED`，reason `INCOMPLETE_WITHOUT_COMPLETED_WORK`

代码事实：

1. `CompletionDecisionService.decide()`（`backend/app/validation/completion.py`）只有
   `if task_type == "SPECIFIED_SOURCE"` 分支；**HYBRID 与 EXPLORATORY 一起落入第 150-170 行
   的探索饱和分支**。
2. `qualified_record_count` 来自 `resolve_completion` 的 `_count_partitions()["passed"]`
   （`activities/completion.py:93`）→ Task 104 为 `1`。
3. `min_qualified_records_for_saturation` 来自 `ValidationSettings`（`policies.py:23`，默认 1），
   与 Spec `completion_conditions` 中 `kind=="min_records"` 的 `target` 取 `max`。
4. 饱和历史 `batch_unique_counts` 在 Activity 中**硬编码为 `[]`**（`activities/completion.py:111`），
   `SaturationTracker.is_saturated([])` 恒为 `False` → 探索分支永不可能饱和。
5. `completed_work = fetched>0 or record>0` 在 Task 104 中为 `True`，因此第 98 行早退不触发；
   但探索分支第 170 行仍无条件 `raise CompletionIncompleteError()`（不检查 completed_work）。
6. `resolve_completion` 捕获 `CompletionIncompleteError` → `status="FAILED"`（`activities/completion.py:120-127`）。
7. Workflow `if completion.status == "FAILED": fail_run`（`task_workflow.py:465`）。
8. `CompletionDecisionView` 只能表达 `NORMAL_COMPLETED | PARTIALLY_COMPLETED`，**无 CONTINUE**。

结论：HYBRID 缺少独立 completion 语义 + 探索饱和历史从未真实喂入 + 无 CONTINUE 通道。

## 2. 修复设计

### 2.1 CompletionDecision：4 路 typed decision

在 `validation/completion.py` 新增 `CompletionOutcome(StrEnum)`：`COMPLETED | CONTINUE |
PARTIALLY_COMPLETED`。`FAILED` 仍用 `CompletionIncompleteError` 表达（Activity 已映射）。

`CompletionDecisionView` 新增：
- `outcome: CompletionOutcome`
- `continue_hints: dict = {}`（仅 CONTINUE 填充；机器可读）

保留 `status/is_partial/completion_type/qualified_record_count/...` 以兼容持久化与稳定消费者。
`status` 新增允许值 `CONTINUE`；`FAILED` 仍由异常路径产生，不进入 view。

持久化时 `model_dump(exclude={"outcome", "continue_hints"})`，避免 `create_completion(..., **decision)`
注入未知列（`validation/repository.py:198`）。**无需 migration**。

### 2.2 decide() 新输入与策略

`decide()` 新增参数：
- `search_round_count: int = 1`
- `max_search_rounds: int | None = None`（`None` = 无界）

`remaining_search_rounds = None if max_search_rounds is None else max(0, max_search_rounds - search_round_count)`
`has_remaining = remaining is None or remaining > 0`

判定优先级（确定性，无 LLM/网络）：

1. 空发现（eligible==0 且 fetched==0）→ COMPLETED(`NO_MATCHING_PAGES`)  [不变]
2. 无 completed work（fetched==0 且 record==0）→ `raise CompletionIncompleteError`（=FAILED）
3. scope done 且 fetched>0 且 record==0 → COMPLETED(`NO_MATCHING_RECORDS`)  [不变]
4. 硬停止（有 completed work）：
   - `runtime_limit_reason` → PARTIALLY_COMPLETED(`runtime_limit`)
   - `user_stopped` → PARTIALLY_COMPLETED(`user_stopped`)
5. 任务类型分支：
   - **SPECIFIED_SOURCE**（不变，无 CONTINUE）：
     `access_limited_reason` → PARTIALLY_COMPLETED(`access_limited`)
     `scope_done` → COMPLETED(`directional_scope_complete`)
     否则 `raise CompletionIncompleteError`
   - **EXPLORATORY**：
     `reached_min` 且 `saturated` → COMPLETED(`exploratory_saturation`)  [不变]
     `has_remaining` → CONTINUE(`search_more_required`)  **[新增]**
     有 completed work → PARTIALLY_COMPLETED(`resource_limit_reached_with_results`)  **[新增，替代无条件 raise]**
   - **HYBRID**（两阶段语义，D-003/D-077）：
     `reached_min` 且 `scope_done` → COMPLETED(`hybrid_target_met`)  **[新增]**
     `has_remaining` → CONTINUE(`search_more_required`)  **[新增]**
     有 completed work → PARTIALLY_COMPLETED(`resource_limit_reached_with_results`)  **[新增]**

`continue_hints` 内容（CONTINUE 时）：
```python
{"reason": "SEARCH_MORE_REQUIRED", "search_round_count": n, "max_search_rounds": m,
 "remaining_search_rounds": r, "qualified_record_count": q, "min_qualified_records": k,
 "scope_complete": bool}
```

### 2.3 resolve_completion Activity

- 输入新增 `search_round_count: int = 1`；`max_search_rounds` 从 `ValidationSettings()` 读取。
- 计算真实 `runtime_limit_reason`（Spec `advanced_settings.max_pages` vs `_count_fetched`，
  `max_duration_minutes` vs run 耗时——若可稳定取耗时；否则仅 max_pages）。
- 决策 `outcome==CONTINUE`：**不持久化 CompletionDecision**，直接返回
  `ResolveCompletionResult(partial=False, status="CONTINUE", outcome=..., continue_hints=...)`。
- `outcome==COMPLETED/PARTIALLY_COMPLETED`：持久化（exclude outcome/continue_hints）并返回。
- `CompletionIncompleteError` → FAILED（不变）。

### 2.4 Workflow 受控继续（continuation loop）

`TaskWorkflow.run`：
- 局部 `self._search_round_count = 1`、`self._current_plan_version = inp.plan_version`。
- 执行完当前 plan 的 units 后，用 `_current_plan_version` + `_search_round_count` 调 `resolve_completion`。
- `outcome == CONTINUE`：
  - 读 `continue_hints.remaining_search_rounds`；若 `None` 或 `>0` 且 `_search_round_count < 硬上限`：
    调新 Activity `replan_for_continuation`（LLM 副作用在 Activity）→ 得到新 plan_version。
    `_current_plan_version = new`；`self._last_index = 0`；`_search_round_count += 1`；`continue`。
  - 否则（无剩余）→ `mark_partial`（有 work）/ `fail_run`（无 work）防御分支。
- `outcome == COMPLETED` → `complete_run`；`PARTIALLY_COMPLETED` → `mark_partial`；`FAILED` → `fail_run`。

**无限循环防护**：`resolve_completion` 已把 `remaining_search_rounds<=0` 判为 PARTIAL/FAILED；
Workflow 再加硬上限（`self._max_search_rounds`，默认取 `continue_hints.max_search_rounds`，
缺失时 3）作为确定性 backstop。不引入 `Date.now()/random`。

### 2.5 新 Activity：replan_for_continuation

`backend/app/activities/replan.py`（新文件）：
- 输入：`task_id/user_id/run_id/spec_version/current_plan_version/search_round_count/continue_hints`。
- 解析模型：复用 `ExtractionModelResolver.resolve_for_run(run)` 模式（`extraction/model_resolver.py`）。
- 生成：`PlanGeneratorAgent.generate(PlanInput(..., repair_context=continuation_context))`，
  其中 `continuation_context` 含上一轮 continue_hints + 状态摘要（不扩展 Spec 范围）。
- 校验：`validate_plan(...)`；`INVALID/REQUIRES_NEW_SPEC/PROHIBITED` → 返回失败，Workflow 走 PARTIAL/FAIL。
- 持久化：`PlanVersionRepository.create(version=N+1, parent_plan_version_id=当前, trigger_reason=...,
  replan_evidence_refs=..., diff_summary=...)`；更新 `Run.plan_version = N+1`。
- 返回 `new_plan_version`。

约束（D-007/D-013）：replan 只改执行策略层（搜索词/来源顺序/参数），不改 Spec 边界；
同一种失败需有有效变化才允许（continuation 由不足结果触发，天然带新上下文）。

### 2.6 Dedupe 业务 invariant（M-08 Validator + Prompt）

- `plan_generator.py` 系统提示第 52-53 行：标准链加入 `deduplicate`：
  `... → extract → normalize → deduplicate → validate → generate_artifact`。
- `plan/validator.py` 新增确定性检查（在 structural 检查阶段，第 8 步资源边校验之后）：
  若 graph 含 `NodeType.VALIDATE`（即 record-producing / 进入正式验证）：
  - 必须含 `NodeType.NORMALIZE` 与 `NodeType.DEDUPLICATE` 节点；
  - 否则追加 `PlanValidationIssue(code="REQUIRED_CAPABILITY_MISSING", ...)` → `INVALID`。
  - 只判断合法性；不私自补节点（Repair/Planner 负责生成合法修复）。
- 不强制纯发现/访问检查/快照类 plan 含 Deduplicate（只有 `VALIDATE`/record-producing 才触发）。

## 3. 测试矩阵

### 3.1 Completion（`tests/validation/test_completion.py` 扩展 + 修正）
- SPECIFIED_SOURCE scope done → COMPLETED（不变，已有）。
- EXPLORATORY min+saturation → COMPLETED（不变，已有）。
- EXPLORATORY 未达标 + 有剩余轮 → CONTINUE（新增）。
- HYBRID 完成：source discovered + scope processed + min met → COMPLETED(`hybrid_target_met`)。
- HYBRID 继续：1 PASSED + min 未达 + 有剩余轮 → CONTINUE（不能 FAILED）。
- HYBRID 部分：有 PASSED + 无剩余轮 → PARTIALLY_COMPLETED。
- HYBRID 真失败：无 completed work → CompletionIncompleteError。
- Task 104 fixture：HYBRID 4 fetched/3 records/1 PASSED/2 NEEDS_REVIEW/CSV/节点成功 + 有剩余轮 → CONTINUE（明确，不模糊）。
- `test_exploratory_not_saturated_is_partial` 语义修正（未饱和+有剩余→CONTINUE）。

### 3.2 Plan completeness（`tests/plan/` 新增）
- HYBRID record-producing plan 缺 Deduplicate → INVALID(`REQUIRED_CAPABILITY_MISSING`)。
- SPECIFIED_SOURCE record-producing plan 缺 Deduplicate → INVALID。
- 纯 discovery plan（无 VALIDATE）→ 不强制 Deduplicate（VALID）。
- Normalize→Deduplicate 资源边 → VALID；Deduplicate→Validate 资源边 → VALID。

### 3.3 Workflow continuation（`tests/integration/test_task_workflow.py` 扩展）
- 首轮 CONTINUE → replan activity 被调 → 第二轮 units 执行 → 终态 COMPLETED/PARTIAL。
- 硬上限触发 PARTIAL（不无限循环）。

## 4. 文件变更清单

| 文件 | 变更 |
|---|---|
| `backend/app/validation/completion.py` | `CompletionOutcome` + view 字段 + `decide()` 重写策略 |
| `backend/app/validation/policies.py` | 新增 `max_search_rounds` |
| `backend/app/activities/completion.py` | 计算 runtime_limit/remaining；CONTINUE 不持久化；返回 outcome/hints |
| `backend/app/workflows/task_workflow.py` | continuation loop + replan 编排 |
| `backend/app/activities/replan.py` | 新 Activity（模型解析 + 生成 + 校验 + 持久化 vN+1） |
| `backend/app/plan/validator.py` | record-producing Dedupe invariant |
| `backend/app/agents/plan_generator.py` | 标准链补 `deduplicate` |
| `backend/tests/...` | 上述矩阵 |

无 migration。无前端/Live-Activity 重构（复用既有事件链）。

## 5. Staging 验证（第 21-22 节）

- 部署 Staging 后新建等价真实 HYBRID Task（公开政府网页），记录 Task/Run/Workflow/Spec/Plan
  及每轮 search_round/query/candidates/eligible/fetched/new unique PASSED/NEEDS_REVIEW/REJECTED/
  dedupe count/remaining/decision。
- 至少一条任务第一轮不足 → 真实触发一次 CONTINUE/Replan → 第二轮执行。
- 确认新 Plan 真实含 `deduplicate` Node + NodeRun SUCCEEDED + dedupe count。
- 保留 Task 104 作为 Incident evidence，不篡改。

## 6. Git 边界

- `fix(quality): evaluate hybrid completion semantics`（Completion + continuation + policies + 测试）
- `fix(plan): require deduplication before validation`（validator + prompt + 测试）
- （如 continuation Workflow 独立，可单独 commit `fix(workflow): controlled replan continuation`）

## 7. 自查（Section 18 九问）

1. 只是 FAILED→PARTIAL？**否**：实现真正 CONTINUE→replan→第二轮。
2. 真正实现 CONTINUE？**是**（replan_for_continuation + workflow loop）。
3. 无限循环风险？**有防护**：remaining_search_rounds + 硬上限，无 Date.now/random。
4. 改变 SPECIFIED_SOURCE？**否**（分支不变，无 CONTINUE）。
5. 改变 EXPLORATORY 正常语义？**否**（min+saturation→COMPLETED 保留）。
6. 强制所有无关 Plan Deduplicate？**否**（仅 VALIDATE/record-producing 触发）。
7. 绕过 Plan Validator？**否**（replan 仍经 validate_plan）。
8. CompletionEvaluator 调网络？**否**（decide 纯函数；replan 在 Activity）。
9. 新增第二套状态机？**否**（复用 Run/Task 状态机 + PlanVersion 不可变链）。
