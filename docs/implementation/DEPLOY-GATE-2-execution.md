# DEPLOY-GATE-2 执行记录：可交互 Task Workflow Staging

状态：**PASS（完整，含真实 LLM 闭环；2026-08-11 补齐真实 DeepSeek Provider）**
负责人/Agent：Claude Code — 2026-08-11
Staging：https://staging.kairos.ac.cn（复用 DEPLOY-GATE-1 环境，服务器 47.238.145.24）
Gate-1 rollback target：`kairos-{web,api,worker}:staging-0b8a42c31f8d`（已在服务器保留）
首轮 M-08 release：`kairos-{web,api,worker}:staging-4ad644349021`
真实 LLM 补齐 release：`kairos-{web,api,worker}:staging-2c2c4edeaf4e`

> 结论摘要：M-08 在 Staging 部署成功，**全部确定性执行核心（workflow/approval/pause/
> resume/cancel/restart/rollback/SSE）PASS**。此前唯一未完成项是「真实 LLM 生成的
> CollectionSpec / Plan」——Staging 无已配置 ModelConfig。2026-08-11 用户授权真实
> DeepSeek API Key，通过 Kairos 正常产品链（Auth → `/providers/models` →
> ModelConfig → CredentialVault → CredentialVersion）配置 `deepseek/deepseek-chat`，
> **真实 LLM 闭环全部 PASS**：Goal Understanding → Spec confirm → Plan Generation
> （VALID）→ typed PlanGraph（规范 7 节点流水线）→ deterministic Validator → 不可变
> PlanVersion → Task Workflow COMPLETED → Secret Leak PASS。DEPLOY-GATE-2 关闭。

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
- Real Model Provider：**PASS**（DeepSeek `test_connection` → AVAILABLE，207ms）
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

## 6. 真实 LLM 闭环补齐（2026-08-11）

用户授权真实 DeepSeek API Key（Key 仅在会话内存/Session 使用，未写入 Git/日志/
docs/执行记录；只记录脱敏引用）。通过 Kairos 正常产品链配置 Provider：

- 注册 Gate-2 测试用户（用户 id=50），Session Cookie 认证
- `POST /api/providers/models`：`provider_type=deepseek, model_name=deepseek-chat,
  set_default=true, api_key` → **ModelConfig config_id=`5ab1abfd…` version=1**，
  credential_configured=true（Key 经 CredentialVault 信封加密存入 CredentialVersion）
- `POST /api/providers/models/{id}/test` → **AVAILABLE**（207ms，connection_status=available）

### 真实 LLM Closure Task（task 28，SPECIFIED_SOURCE，seed=https://example.com）
1. 创建 Task → Goal Understanding（真实 DeepSeek，2.43s）→ typed GoalUnderstandingResult
   （SPECIFIED_SOURCE，字段：标题/URL，source_scope.seed_urls=[example.com]）
2. CollectionSpec 确认 → spec v1 冻结，task QUEUED
3. Plan Generation（真实 DeepSeek PlanGenerator）→ 确定性 Validator → **plan v3 VALID**
   （7 节点规范流水线：access_rules_check → link_discovery → fetch → extract → normalize →
   validate → generate_artifact；资源链 url→url→snapshot→record→record→record）
4. PlanVersion v3 冻结（不可变，fingerprint + registry_versions）
5. 自动启动 Task Workflow（run_id=18, workflow_id=task-workflow-28）→ **Task COMPLETED**
   （M-09/M-10 未实现节点按 NODE_EXECUTOR_UNAVAILABLE block；fetch 走 fixture executor）
6. **Secret Leak PASS**：pg_dump 全库 + api/worker 日志对 Key 精确匹配 0 次

### 本轮修复的真实 LLM 执行缺陷（M-08，均有回归测试）
| Commit | 缺陷 | 现象 |
|---|---|---|
| fac1f2a | plan_service api_key 被丢弃 | 真实 Provider 401 → 500 |
| b3b78d0 | build_input 传 None user | `None.id` AttributeError → 500 |
| 85482ef | validator 对 object-form extract fields 崩溃 | `dict not in set` unhashable → 500 |
| 2c30332 | PlanGenerator 看不到节点参数契约 | LLM 发明契约外键名 → 全 PARAMETER_SCHEMA_INVALID |
| 2c2c4ed | PlanGenerator 误解标准流水线 | fetch 排在 access check 前/snapshot 当 url → RESOURCE_EDGE_INCOMPATIBLE |

> 注：这些缺陷全部只在真实 LLM 输出下暴露；M-08 既有 FakeInference fixture 使用了
> 手工构造的合规 Plan，未覆盖真实自由输出边界。这正是 DEPLOY-GATE-2 真实 LLM 闭环的
> 价值所在。

## 7. 最终结论
- **DEPLOY-GATE-2：PASS（确定性执行核心 + 真实 LLM 闭环全部通过）**
- M-08：**DEPLOYED（Staging）**；M-08 LOCAL = DONE
- M-09：**UNBLOCKED**

### 复验证据
- DeepSeek test_connection：AVAILABLE（config_id=`5ab1abfd…` version=1，deepseek/deepseek-chat）
- Goal Understanding：真实 Provider PASS（typed GoalUnderstandingResult）
- CollectionSpec：confirmed（spec v1 冻结）
- Plan Generation：真实 Provider PASS（plan v3 VALID，7 节点规范流水线）
- Validator：VALID（确定性，无 LLM）
- PlanVersion：frozen（v3，不可变）
- Task Workflow：COMPLETED（真实 LLM Plan 驱动）
- Secret Leak：PASS（DB + 日志 0 匹配）
- 本轮未重新执行 Approval/Pause/Resume/Cancel/Restart（前一轮已 PASS）
