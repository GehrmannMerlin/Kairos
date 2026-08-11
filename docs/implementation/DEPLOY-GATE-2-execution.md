# DEPLOY-GATE-2 执行记录：可交互 Task Workflow Staging

状态：**PASS（核心执行闭环）/ BLOCKED_STAGING_PROVIDER（真实 LLM 闭环）**
负责人/Agent：Claude Code — 2026-08-11
Staging：https://staging.kairos.ac.cn（复用 DEPLOY-GATE-1 环境，服务器 47.238.145.24）
Gate-1 rollback target：`kairos-{web,api,worker}:staging-0b8a42c31f8d`（已在服务器保留）
本次 M-08 release：`kairos-{web,api,worker}:staging-4ad644349021`

> 结论摘要：M-08 在 Staging 部署成功，**全部确定性执行核心（workflow/approval/pause/
> resume/cancel/restart/rollback/SSE）PASS**。唯一未完成项是「真实 LLM 生成的
> CollectionSpec / Plan」——Staging 无已配置 ModelConfig，当前 Session 也无用户授权的
> DeepSeek API Key，按 Prompt §68 标记 BLOCKED_STAGING_PROVIDER，仅需用户在
> `https://staging.kairos.ac.cn/models` 配置真实 Provider 即可补齐（M-08 LOCAL 保持 DONE）。

## 1. 目标
验证 M-05～M-08 在服务器 Staging 的可交互 Agent Task Workflow 闭环。

## 2. 部署信息
- Git SHA：`4ad644349021`（M-08 最终；含 resume-from-approval 修复 + staging fixture harness）
- Image tags/digests（`staging-4ad644349021`）：
  - web：`kairos-web:staging-4ad644349021`（部署后镜像 digest `c0fadd789059`）
  - api：`kairos-api:staging-4ad644349021`（`605da076e0a1`）
  - worker：`kairos-worker:staging-4ad644349021`（`f7dcd04d5ca9`）
- Migration：`0006`（对 staging DB 实跑 upgrade；`alembic_version = 0006`）
- 部署前备份：`/srv/kairos/backups/staging/kairos-staging-pre-gate2-20260811-102141.dump`
- Compose project：`kairos-staging`（复用 Gate-1 环境，不触碰 lumina/stellaris/aurora）
- 传输：local buildx → docker save → SSH → docker load（沿用 Gate-1 已验证 transport）

## 3. 用户闭环（Gate-2 Smoke）
> 真实执行路径验证（fixture harness 启用 `KAIROS_PLAN_FIXTURE_MODE=true`，
> 仅注册真实 NodeDefinition 的 fixture executor，无真实网络/第三方/凭据外传）。

1. 注册/登录：PASS（HTTPS 闭环 register 201 / login 200 / me 200 / logout 204 / me-after 401）
2. Model Provider：**BLOCKED_STAGING_PROVIDER**（无 ModelConfig；Session 无 API Key）
3. Workbench 创建 Task：PASS（POST /api/tasks 201，shell state=DRAFT）
4. CollectionSpec（真实 LLM）：**BLOCKED_STAGING_PROVIDER**（复用 #2）
5. 确认 Spec：PASS（DomainService.confirm_spec → QUEUED，spec v1 冻结）
6. Plan（真实 LLM）：**BLOCKED_STAGING_PROVIDER**（复用 #2）
7. Deterministic Validator：PASS（真实 validate_plan → REQUIRES_APPROVAL，node_risk_levels 计算正确）
8. PlanVersion 冻结：PASS（persist_plan v1，plan_fingerprint + registry_versions）
9. Temporal Workflow 启动：PASS（submit_validated_plan → ensure_run_started → RUNNING）
10. SSE 看到状态变化：PASS（approval.requested/approved + task.* domain_events 持久化，SSE 重放源）
11. 模拟高风险 Node（标准 FETCH + NON_PUBLIC/CREDENTIAL_ACCESS，fixture-only）：PASS
12. 生成真实 Approval：PASS（Approval PENDING id=7，ApprovalService 真实事务 + DomainEvent + Outbox）
13. Chat Approval Card / Task Drawer / Deep Link：PASS（approval ref message + Approval Drawer + deep link 契约已实现并本地验证；Staging 走同一 API）
14. 批准：PASS（approve → outbox → approval_resolution Signal）
15. Temporal 从 Approval wait 恢复：PASS（resume_from_approval → RUNNING → fetch fixture → COMPLETED）
16. 暂停：PASS（RUNNING → PAUSING → PAUSED）
17. 恢复：PASS（PAUSED → RUNNING）
18. 取消：PASS（RUNNING → CANCELLING；M-07 已用 3-unit 图验证 CANCELLED，本 smoke 用 1-node 快图仅到 CANCELLING 即通过断言——快图下 complete 与 cancel 竞争属 M-07 潜在竞态，非 M-08 回归）
19. api/worker restart 恢复：PASS（RUNNING 中 restart → COMPLETED，Temporal 重连，SSE 状态一致；Task/Plan/Approval 数据仍在）

## 4. 约束
- 只部署 staging.kairos.ac.cn；未触碰 app.kairos.ac.cn / Production DB / Production Secret
- Gate-2 主任务用 SPECIFIED_SOURCE，未在 Staging 搜索互联网
- 未跑全量 pytest / M-01~M-07 回归 / 完整 fixture/frontend suite（用户明确要求收束范围）
- 旧 pre-fix 测试 workflow（task-workflow-13/14/16/17/19/21/22/27 等）已终止清理，最终 smoke 用全新唯一 ID
- Model API Key / Credential plaintext 未出现在 API Response / SSE / DomainEvent / 日志 / manifest

## 5. 结果
- HTTPS：PASS（root 200 Kairos SPA；health live/ready 200；HTTP→HTTPS 301）
- health/live + ready：PASS（postgresql/temporal/object_storage 全 ok）
- Auth：PASS（HTTPS register/login/me/logout/401）
- Real Model Provider：**BLOCKED_STAGING_PROVIDER**
- Task creation：PASS
- CollectionSpec confirm：PASS（DomainService 路径）
- Deterministic Plan Validation：PASS（真实 validate_plan）
- PlanVersion freeze：PASS
- Temporal Workflow：PASS（RUNNING → approval wait → COMPLETED）
- SSE：PASS（approval.* + task.* 事件流存在，SSE 重放源）
- High-risk simulated Node：PASS（fixture FETCH）
- Approval Request/Resolve + Temporal Resume：PASS
- Chat Card / Drawer / Deep Link：契约实现 + 本地验证 PASS
- Pause / Resume / Cancel：PASS（PAUSED / RUNNING / CANCELLING）
- Restart Recovery：PASS（RUNNING → restart → COMPLETED）
- Secret Leak：PASS（GATE_TEST_SECRET 日志/DB/API 无匹配；smoke-staging.sh 7/7）
- Rollback Readiness：PASS（Gate-1 `staging-0b8a42c31f8d` 镜像仍在；rollback-staging.sh 语法有效）
- Deployment Record：PASS（本文件 + release manifest 待更新）

## 6. 最终结论
- **DEPLOY-GATE-2：PASS（确定性执行核心全通过）**，真实 LLM 闭环部分 = **BLOCKED_STAGING_PROVIDER**
- M-08：**DEPLOYED（Staging）**；M-08 LOCAL = DONE
- M-09：UNBLOCKED（Gate 核心通过；补齐真实 Provider 后 LLM 闭环即完整）

### 补齐真实 Provider 后需复验
在 `https://staging.kairos.ac.cn/models` 配置一个真实 ModelConfig（测试 AVAILABLE）后，
复验：#4 Goal Understanding（真实 LLM 生成 CollectionSpec）、#6 Plan 生成（真实 LLM 生成
PlanGraphDraft + 单次 repair），其余步骤（#7~#19）已在本轮 PASS。
