# DEPLOY-GATE-3 执行记录：真实网页采集 E2E Staging

状态：**BLOCKED**（2026-08-11）
负责人/Agent：Claude Code
Staging：https://staging.kairos.ac.cn（服务器 47.238.145.24）
部署 release（当前运行镜像）：`kairos-{web,api,worker}:staging-f0edd653fca5`
Git HEAD（收口时）：`e68b6be`（诊断已 revert；含确认修复）
Migration：`0010`（已应用，5 张新表 + records review 字段）
部署前备份：`/srv/kairos/backups/staging/kairos-staging-pre-gate3-20260811-211056.dump`
回滚基线：`kairos-{web,api,worker}:staging-f25a5378113a`（Gate-3 前稳定 release）

## 1. 已经 PASS 的项目（真实结果）
- M-12 本地实现 + scoped tests（46 passed）+ migration 0010 upgrade/downgrade SQL 验证
- Staging 部署：镜像构建/传输/compose up 成功，API healthy，worker 加载全部 executor
- HTTPS / /api/health/live / /api/health/ready：全部 PASS（postgresql/temporal/object_storage ok）
- Migration 0010：5 张新表 + records.review_type/review_reason/validated_at 已应用
- Provider 配置（正常产品链 API → Service → CredentialVault）：
  - gate3.a@kairos.test（user 53）：DeepSeek `deepseek-chat` **AVAILABLE**、Tavily Search `tavily` **AVAILABLE**（POST https://api.tavily.com/search，1650ms）
  - gate3.b@kairos.test（user 54）：DeepSeek `deepseek-chat` **AVAILABLE**、Search **NONE**（预期）
- Secret 检查：**SECRET_LEAK=false**（凭据全部信封加密密文；api/worker 日志 0 匹配）
- 本轮确认修复（均带针对性验证）：
  - `feat(provider)`：新增 `tavily` Search Provider adapter（POST /search + content→snippet 映射），契约测试 PASS
  - `fix(worker)`：注册 `resolve_completion`/`mark_partial`/`resolve_robots_override` 到 worker activity 列表（原 workflow 调用但未注册导致卡 RUNNING）
  - `fix(workflow)`：`ensure_run_started` 摄入 Spec seed_urls 到 URL Frontier（原产品链缺 seed 摄入），回归测试 PASS

## 2. 未完成 / NOT_COMPLETED
- Golden Path A（指定来源静态）：**NOT_COMPLETED**（fetch/extract/validate 主链未产出记录）
- Golden Path B（探索式 Tavily）：**NOT_COMPLETED**（未运行——Golden A 主链未通）
- Golden Path C（动态 Playwright）：**NOT_COMPLETED**（未运行）
- pause/resume、worker restart、两用户隔离：**NOT_COMPLETED**

## 3. 核心 Blocker
**URLResource state transition persistence inconsistency**

Observed behavior（Task 34，Gate-3 重复同 seed 任务暴露）：
- `ensure_run_started` 摄入 seed → URLResource status=DISCOVERED ✓
- `access_rules` 对 URL 决策 ALLOW，调用 `frontier.mark_state(ACCESS_ALLOWED)` 返回成功（无异常），且 access_checked 事件落库 ✓
- 但 commit 后重读（同一 session reread）URLResource.status **仍为 DISCOVERED**
- 后续 `link_discovery` 查 ACCESS_ALLOWED → seeds=0 → 无 fetch → extract/validate 0 记录 → 任务 PARTIALLY_COMPLETED（无记录产出）

Suspected boundary（未证实，供下一轮系统调试）：
- `UrlFrontierRepository._owned(user_id, url_hash)` **只按 user_id + url_hash 过滤、缺少 task_id**；url_hash 是 URL 的 canonical 哈希，跨任务相同，`.first()` 可能返回最早任务的行 → mark_state 修改了错误任务的 URL 行。
- 观察佐证：task 30（首个同 seed 任务）URL=ACCESS_ALLOWED，task 31-34 的同 seed URL 全为 DISCOVERED；与“修改了最早任务行”假设一致。
- 需用 superpowers:systematic-debugging 建立最小复现证实后再修；本轮不提交该未验证修复。
- 备选关注：SQLAlchemy session / transaction ownership / repository persistence / stale ORM state。

## 4. 测试工件（保留供调试）
| Task | Run | Workflow | URL 状态 | 事件序列 |
|---|---|---|---|---|
| 29 | 19 | task-workflow-29 | (seed 摄入前运行，无 URL) | start→expanded(0)→validation(0)→blocked→partial |
| 30 | 20 | task-workflow-30 | ACCESS_ALLOWED | access→expanded(0)→fetch→extraction.failed(placeholder)→validation(0)→partial |
| 31 | 21 | task-workflow-31 | DISCOVERED | access(1)→expanded(0)→validation(0)→partial |
| 32 | 22 | task-workflow-32 | DISCOVERED | access(1)→validation(0)→blocked→partial（plan 无 link_discovery） |
| 33 | 23 | task-workflow-33 | DISCOVERED | access(1)→expanded(0)→validation(0)→partial |
| 34 | 24 | task-workflow-34 | DISCOVERED | access(1)→expanded(0)→validation(0)→partial |

- 所有测试任务均 PARTIALLY_COMPLETED（终态），无 RUNNING/PAUSED/WAITING 残留 workflow。
- Task 30 另暴露 extraction LLM fallback `不支持的推理 Provider: placeholder`（模型解析回退），本轮未深入。

## 5. 代码变更分类（收口时）
- **KEEP（确认修复）**：`9a77402`（tavily adapter）、`37aec80`（activity 注册）、`e83a652`（seed 摄入）
- **DIAGNOSTIC ONLY（已 revert）**：`f177cc5`、`d243312`、`f0edd65`、`4099b26`（临时 logging/instrumentation），由 `e68b6be` revert 撤销
- **未提交实验（已 restore）**：frontier/access_rules/link_discovery 的 task_id 过滤实验（不完整，未验证，恢复到最后已知正确状态）
- 无 push / merge / tag。

## 6. 结论
**DEPLOY-GATE-3 = BLOCKED**。M-12 保持 **DONE_LOCAL**（不因 Gate 失败降级）。M-13 **NOT_STARTED**。
下一轮：对 **URLResource state transition persistence** 使用 superpowers:systematic-debugging，先建立最小复现 → 找 root cause → 一个最小修复 → 一个针对性测试 → 只重跑受影响的 Gate-3 Golden Path。
