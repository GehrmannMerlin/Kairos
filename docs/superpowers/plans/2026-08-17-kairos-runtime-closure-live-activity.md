# Kairos Agent Runtime Closure + Live Activity — 第二阶段闭环实施计划

> 日期：2026-08-17
> 入口：用户提示词「Agent Runtime Closure + Live Activity Streaming 修复」
> 结论类型：先实证当前仓库真实状态，再只修复**仍然真实存在**的缺口。

## 0. 已读取权威文档

- `CLAUDE.md`
- `agent-code-standards.md`（全文）
- `docs/implementation/agent-runtime-audit-2026-08-16.md`（上一轮审计报告）
- `docs/audits/task-25-execution-incident.md`
- `docs/audits/execution-readiness-progress-verification-2026-08-15.md`
- 业务/实施计划相关章节（D-006/D-008/D-010/D-013/D-014/D-016/D-025/D-029/D-039/D-055/D-063/D-068/D-071；M-07/M-08/M-11/M-12/M-14/M-16）

## 1. 实证结论：提示词假设状态 vs 真实仓库状态

基线：`git status` 干净（仅未跟踪的 `_` 前缀临时脚本），HEAD=`cfc169a`，分支 `main`，
与 `origin/main` 一致。本地 scoped 测试 `369 passed, 2 skipped`。

| 提示词假设 | 真实状态 | 处置 |
|---|---|---|
| P0-1 Plan/Node/Executor 契约漂移（`RESOURCE_EDGE_INCOMPATIBLE`） | **已修复并合并**：`366557c`/`0da0ce2`/`b07b900` 恢复被 `6299223` 误回退的 Golden B 修复；`tests/plan/test_plan_fixtures.py`、`tests/discovery/test_frontier.py` 已有回归 | 不重做；仅确认绿 |
| P0-2 Extract 超时/批次 | **部分开放**：提取已按 snapshot 逐个执行（非大 batch 塞单次调用）；单 snapshot 失败已隔离；超时层级已正确（`provider_inference_timeout=45 < plan_lifecycle=105 < 反代=120`，有 `test_timeout_defaults_are_strictly_ordered` 锁住）。**真实缺口**：LLM fallback 单次上下文 `max_context_chars=30_000` 过大，45s 内易超时 → `PROVIDER_TIMEOUT` → snapshot `extraction.failed` → records=0；且超时后无「缩小上下文」的有界重试（违反 D-013 精神） | **修复** |
| P0-3 Dedupe `business_key VARCHAR(500)` | **开放**：`business_key_fingerprint`（SHA-256, `String(64)` + 唯一约束）已是稳定 identity；但 `DedupeCluster.business_key`/`Record.business_key`（`String(500)`）仍写入未截断的完整拼接键，长键仍会 `StringDataRightTruncation` | **修复** |
| P0-4 Zombie RUNNING | **开放（窄）**：Workflow 内所有终态路径 + broad `except→fail_run` + CAS 终态认领已存在；但 Workflow 在 Temporal 层被 terminate/丢失且未执行终态 Activity 时，Run 永久 `running`，无 out-of-band 对账 | **最小修复** |
| P1 Live Activity | **大部分已落地**：`ExecutionProgressPanel.vue` + `useTaskEvents`(SSE 重连/reconcile) + `useExecution` + `TimelineEvent`（脱敏 allowlist）+ `ExecutionLifecycleRecorder`（node lifecycle facts）+ `api/events.py`（SSE 映射/回放）。工具事件（discovery/fetch/extraction/validation）已流入 SSE。**真实缺口**：模型调用（DeepSeek inference）只走 `inference_telemetry` 结构化日志，未持久化为 DomainEvent → 用户看不到「正在调用 DeepSeek」 | **最小补齐** |

## 2. 本轮实际修复范围（4 项，全部无 Migration）

### Fix A — Dedupe 业务键有界化（P0-3）
- `app/validation/dedupe.py`：新增 `bounded_business_key(text, limit=500)`（稳定截断，identity 仍由 `business_key_fingerprint` 承担）。
- `app/validation/executor.py` `DeduplicateNodeExecutor.execute`：`create_group(...business_key=bounded_business_key(g["business_key"]))` 与 `rec.business_key = bounded_business_key(g["business_key"])`。
- 测试：长键(>500)、中文/unicode、字段顺序稳定、同语义 identity 稳定、不同内容不误判。

### Fix B — Extract 上下文收敛 + 超时有界重试（P0-2）
- `app/extraction/contracts.py` `ExtractionSettings` 增：`llm_max_context_chars: int = 12_000`、`llm_retry_reduced_context_chars: int = 6_000`。
- `app/extraction/pipeline.py`：LLM fallback 前把 `readable_text` 截断到 `llm_max_context_chars`；`extract` 抛 `ProviderTimeoutError`（含其 `__cause__` 链上的超时）时，用 `llm_retry_reduced_context_chars` 缩小上下文重试**一次**，再失败即抛（有界）。
- 测试：结构化页面确定性提取不触发 LLM；超时→缩小重试→成功；重试仍超时→失败局部化（单页失败不丢其它页）；重试改变了输入（D-013）。

### Fix C — Zombie RUNNING 最小对账（P0-4）
- 新增 `app/reconciliation/service.py`：
  - 纯函数 `resolve_terminal_command(*, temporal_status, has_completion_decision, qualified_records, fetched_pages)` → `"complete" | "mark_partial" | "fail" | "mark_cancelled" | None`。
  - `reconcile_stale_runs(db, temporal_client, *, stale_after_seconds, dry_run=True)`：查询 `state='running'` 且 `started_at` 超窗的 Run，查 Temporal workflow（`task-workflow-{task_id}`）状态，terminal/not-found 时经既有 `fail_run`/`mark_partial`/`complete_run`/`mark_cancelled` 收口（不直接 SQL UPDATE，绕过 CAS 的 `_finish_run` 会抛 `RunTerminalError`）。
  - 复用 `_finish_run` 的 CAS 语义保证幂等、owner-safe、并发安全。
- 新增 `scripts/reconcile_runs.py`：dry-run 默认，`--apply` 才写。
- 测试：纯决策函数全分支；stale 查询（注入 fake temporal client）；dry-run 不写；非 stale 不动。

### Fix D — 模型调用事件入 SSE（P1 缺口）
- `app/extraction/executor.py` `ExtractNodeExecutor`：`_build_pipeline` 解析模型后记录 `provider/model`；批次内若任一 snapshot `technical_metadata["llm_invocations"] > 0`，在提交前 emit 单条 `extraction.llm_fallback_used`（payload 含 `model`/`provider`/`llm_invocations`/`duration_ms`），复用现有 SSE 映射 `LLM_FALLBACK_USED` 与 timeline `model_call` 分类。
- 测试：使用 LLM 时 emit 含安全 model/provider 的 event；确定性提取时**不** emit；无 secret 字段。

## 3. 不改动（明确排除）

- 不新增 Migration、不改状态机、不改认证/隔离、不引入新依赖、不新增第 14 个一级页面。
- 不回退/不恢复 Golden C 动态网页实验。
- 不部署 Production、不创建 Release Tag、不 Force Push。
- 不把 `inference_telemetry` 的日志路径改成 DB 事实来源；模型调用仍走 DomainEvent（业务事实），日志保留原 allowlist 边界。

## 4. 测试策略（A-Lite）

1. 新增/修改对应单测（Fix A/B/C/D）。
2. 跑 scoped：`tests/validation tests/extraction tests/reconciliation tests/execution tests/plan tests/discovery tests/crawling`。
3. 跑 `ruff` + `mypy` 改动文件。
4. 不跑 Playwright E2E / 全量重型集成（与本轮无交集）。

## 5. Git 边界（独立可回退）

单一功能分支，逻辑上 4 个独立 commit：
1. `fix(validation): bound dedupe business key preview`
2. `fix(extract): bound semantic extraction context with timeout retry`
3. `fix(workflow): reconcile terminal task state for lost workflows`
4. `feat(execution): expose model calls in live activity events`

## 6. Staging 验证（后续，需读部署规范）

- 授权：短周期分支 → commit → push → PR → CI → merge main → Staging pull → Smoke/Vertical Slice。
- 至少一条 HYBRID 任务 `records > 0`、`Task` 合法终态、前端实时可见 Fetch/Extract/Model 进度。
- 未授权：Production 部署/Release Tag/数据变更。
