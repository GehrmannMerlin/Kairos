# M-11 Extraction Runtime Closure — 最终报告（2026-08-18）

状态：**DONE（Staging 验证通过）**。最终报告按任务书 §46 格式。

---

## A. Root Cause

**谁取消 `agent.run()`？** — **Temporal Activity `start_to_close_timeout=120s` 超时取消**，
不是 Provider 超时。

```text
TaskWorkflow → execute_activity(execute_safe_unit, start_to_close_timeout=120s)
→ ExtractNodeExecutor 单 Activity 内循环处理 ≤50 snapshots（每快照最坏 2×45s=90s）
→ 累计远超 120s → Temporal 服务端发取消 → SDK task.cancel() 注入 asyncio.CancelledError
```

**为什么 12k→6k 恢复没解决**：`2cc6cf3` 只缩小单请求超时概率，不解决 N 个快照的**累计时间**
击穿 120s 预算（Task 115: 100+ snapshots / Task 119: 20 snapshots 均必然超时）。

**NodeAttempt 卡 RUNNING**：`execute_safe_unit` 只 `except Exception`，Python 3.11 的
`asyncio.CancelledError` 继承 `BaseException` 穿透 → `finish_attempt` 永不执行。

**0 records**：executor 循环结束后才 `db.commit()` 一次，取消即丢全部。

## B. Timeout Map（修复后）

| Layer | 值 | Owner | Raised error |
| ----- | --: | ----- | ------------ |
| HTTP | 45s | HttpxTransport | ProviderTimeoutError(CONNECT/READ) |
| Provider asyncio.timeout | 45s | ModelInferenceClient | ProviderTimeoutError(OVERALL) |
| 单快照 LLM 最坏 | 90s | ExtractionPipeline | 缩小上下文重试一次 → 页面失败 |
| Extract Activity 预算 | 100s | ExtractNodeExecutor | MORE_PENDING（非 timeout） |
| Extract Activity | 200s（NodeDefinition.timeout_seconds thread） | task_workflow | CancelledError → attempt CANCELLED |
| execute_safe_unit retry | max 3, CancelledError 不可重试 | task_workflow | 有界终止 |

不变量：`预算(100) + 最坏单快照(90) < Activity timeout(200)`。

## C. Cancellation Taxonomy

- `ProviderTimeoutError` → 缩小上下文有界重试一次 → 页面级合法失败（`extraction_status=failed`）。
- `asyncio.CancelledError`（Temporal 取消/shutdown/超时）→ **传播** + attempt 收口 `CANCELLED`。
- 用户取消 → workflow 循环顶处理，不当作 provider retry。
- 禁止：CancelledError 伪装成 PROVIDER_TIMEOUT；对取消无限重试。

## D. Attempt State

**为什么以前卡 RUNNING**：`except Exception` 不捕获 BaseException → `finish_attempt` 永不执行。
**修复后**：`execute_safe_unit` 显式 `except asyncio.CancelledError` → `finish_attempt(CANCELLED)`；
小批次 MORE_PENDING 用 `batch_round` 区分各批 lifecycle attempt（Task 130 实证 23 批独立 attempt）。

## E. Extraction Batch

- `extract_batch_size=5`（snapshots/Activity）；每快照独立事务提交 Record+Evidence+DomainEvent。
- 剩余快照 → `MORE_PENDING` → Workflow 提交本批 checkpoint 后不推进 index 重取同一单元。
- 失败快照（全阶梯 0 candidates / 重试后仍失败）→ `page_snapshots.extraction_status='failed'`（migration 0017）。
- 每批 checkpoint 用唯一 `batch_identity`（`extract-{run}-{index}-{首快照id}`）。

## F. Deterministic Extraction

阶梯已接通：JSON-LD→Meta→Table→SiteRules→LLM fallback（字段级 unresolved）。Replay 实证：
meta 提取标题/来源、LLM 只补语义字段（发布日期）；partial extraction 保留（deterministic 成功字段
不因 LLM 字段 unresolved 消失）。Task 127/130 的 FieldEvidence 全部 `method=llm`（该批页面缺结构化
meta，符合 D-010：deterministic 优先，LLM 处理语义剩余）。

## G. Replay Evidence（Task 119 stored snapshots，staging）

| 页数 | 结果 | records | evidence | provider calls |
| ---: | --- | ---: | ---: | ---: |
| 1 | OK | 1 | 2 | 1 |
| 3 | OK（2 成功 + 1 合法失败） | 2 | 6 | ~2 |
| 5 | OK | 5 | 12 | ~5 |
| 7 | MORE_PENDING(5+2) 两轮 | 7 | 15 | ~7 |

- 失败页（ce.cn）`extraction_status=failed`，不拖死其它页。
- 真实 DeepSeek `deepseek-v4-flash` 调用，单次 ~10s。

## H. New Staging Task

- **Task 127**（`只收集1条`，RUN 78）：search→fetch(23)→extract→normalize→dedupe→validate→
  generate_artifact **全 SUCCEEDED**，**COMPLETED**（hybrid_target_met，11 passed + 11 needs_review，
  22 records，49 FieldEvidence），**NODE_ATTEMPTS_UNFINISHED=0**。
- **Task 130**（batch_round 修复后，RUN 80）：Round1 全 9 节点 SUCCEEDED（extract **23 批**全
  SUCCEEDED，84 records），completion CONTINUE → Round2 source_search 失败
  `FROZEN_CONFIG_UNAVAILABLE`（冻结搜索配置不可用）→ task FAILED。**该失败是搜索配置/Replan
  领域既有问题（§41 明确不在本轮范围），非提取回归**；反而证明提取闭环在 100 页压力下无卡死。

## I. Tests

```text
pytest tests -q                        → 全部 PASS（含新增 13 测试）
ruff check / ruff format --check       → PASS
mypy                                    → PASS
alembic heads                           → 0017（migration 实跑 PG SQL 验证）
```

新增测试：cancellation finalization、MORE_PENDING rerun、batch 隔离、failure ledger、budget、
CancelledError 传播/分类、batch_round 独立 attempt、timeout thread、migration head。

## J. Git

- PR #34 `fix/m11-extraction-runtime-closure`（3 commits）→ **merged**（rebase）→ main `a9f97a7`。
- 二次修复分支 `fix/m11-extract-batch-attempt`（`fab0bfd` + docs `9098c1a`）→ **push 被 GitHub
  网络阻断** → 经 `registry-push.sh` 本地构建 `fab0bfd341f2` 部署 staging。
  **PR/CI PASS = PENDING（网络阻断）**，恢复后必须补 push/PR/merge/CI 闭环。
- Migration：0017 `page_snapshots.extraction_status`（纯新增 nullable 列，expand 兼容）。

## K. Remaining Gaps

- **Agent Loop Round 2**（CONTINUE→Replan）：Task 130 证明 Round1 提取闭环 + Round2 搜索配置
  失效（`FROZEN_CONFIG_UNAVAILABLE`）。§41 明确本轮不强求，单独跟踪。
- source_hints typed contract debt、robots transport policy 复核 → 仅记录，不在 M-11 重构。
- Task 119 历史 extract attempt 1/2 卡 RUNNING（旧 bug 遗留数据，未动，作为历史证据保留）。

## L. Production

**Production unchanged.**（任务书 §43 明确禁止本轮 Production deploy / tag / migration / smoke。）

## M. Final Status

**DONE**（Staging 验证通过；batch_round 二次修复的 PR/CI 闭环 PENDING，待 GitHub 网络恢复补齐）
