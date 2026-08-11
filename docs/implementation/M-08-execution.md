# M-08 模块执行记录

状态：IN_PROGRESS → **DONE_LOCAL**（本地闭环验证通过；DEPLOY-GATE-2 待执行）
负责人/Agent：Claude Code — 2026-08-11
Baseline（M-07 DONE）SHA：`0116e78e08f562c15b4915fefff73cc69feb6d5f`
依赖模块：M-04（DEPLOYED）、M-06（DONE）、M-07（DONE）
目标环境：local（M-08 属于 DEPLOY-GATE-2 前的最后一个开发模块）

## 1. 模块目标
实现 D-007、D-008、D-017 的「Agent 可规划但不能越界」机制：类型化 Node Registry、
Pydantic AI Plan Generator、确定性 Plan Validator、不可变 PlanVersion、Replan/Diff、
有范围有期限的 Approval 生命周期，并接入 M-07 TaskWorkflow 的等待/恢复 seam 与前端
审批闭环。低风险合法 Plan 自动执行（D-038），高风险执行时 JIT 审批（D-017）。

## 2. 契约
- `NodeType`/`RiskLevel`/`ResourceClass`/`ResourceKind`/`NodeDefinition`/`NodeRegistry`（app/plan/nodes.py）
- `PlanGraphDraft`/`PlanValidationResult`/`PlanValidationIssue`（app/plan/schemas.py）
- `validate_plan()`（app/plan/validator.py，确定性，不调用 LLM）
- `PlanGeneratorAgent`/`PlanGenerationService`（app/agents/plan_generator.py + plan_service.py，复用 M-03 ModelInferenceClient + CredentialVault）
- `PlanService`（app/plan/service.py：persist / auto_start / create_replan / summary）
- `PlanDiff`（app/plan/diff.py）
- `ApprovalState`/`ApprovalScope`/`ApprovalService`（app/approval/*：request/approve/reject/revoke/consume）
- `request_approval`/`block_high_risk_node` Activity；真实 `fetch_next_execution_unit`/`execute_safe_unit`（app/activities/approval.py + plan_execution.py）
- `TaskWorkflow` 消费 `approval_resolution` Signal（wait/resume/block）
- Plan API（POST /tasks/{id}/plan、replan、plans 列表/摘要）；Approval API（query/approve/reject/revoke + owner 隔离）
- SSE：APPROVAL_REQUIRED / APPROVED / REJECTED / EXPIRED / REVOKED / CONSUMED
- 前端：Plan Summary Card、Chat Approval Card、真实 Approval Drawer、Deep Link、Task Drawer 待审批、needs_action

## 3. 行为
- 10 个标准 Node 契约注册（SourceSearch/AccessRulesCheck/LinkDiscovery/Fetch/BrowserRender/Extract/Normalize/Deduplicate/Validate/GenerateArtifact），每个带 typed 参数 schema + input/output 资源契约 + timeout/retry/风险/幂等身份/可恢复边界/资源类
- Registry 是代码注册静态允许列表；未注册 node_type 由 Validator 拒绝（NODE_NOT_REGISTERED），不得自动注册
- Validator 完全确定性：registered/version/唯一ID/依赖/DAG/参数(schema strict)/资源边/Spec 匹配/范围边界/字段语义/质量约束/风险/Provider 前置；PROHIBITED 直接拒绝，Spec 边界变化 → REQUIRES_NEW_SPEC，同域高风险 → REQUIRES_APPROVAL
- Plan Generator 复用 M-03 provider/vault 解析真实模型；允许最多一次有证据的 Plan Repair，二次失败 → INVALID/BLOCKED
- PlanVersion 不可变；Replan 产生 vN+1 并保留 parent/diff/trigger/evidence；改变 Spec 边界的 replan 被拒绝
- Approval JIT：Workflow 到达高风险 Node 才 request_approval → WAITING_APPROVAL + SSE → 用户 approve/reject → ApprovalService（owner+fingerprint+scope+expiry 复验）→ outbox → approval_resolution Signal → Workflow 恢复/block
- 生产运行时无实现的 Node 返回 NODE_EXECUTOR_UNAVAILABLE（不冒充能力）；fixture executor 仅 test/Staging（plan_fixture_mode，默认关闭）

## 4. 明确不做
真实 Source Search / robots 下载解析 / Sitemap / URL Frontier / HTTP fetch / Scrapy /
Playwright / PageSnapshot / Extractor / Normalize 实现 / Dedup 算法 / Quality 校验 /
CSV 生成（M-09～M-12）。完整 Plan DAG / Node Detail 深检（M-14）。资源池调度（M-16）。

## 5. 验收命令与证据

### 后端 scoped tests
```bash
cd backend
.venv/Scripts/python.exe -m pytest tests/plan tests/approval tests/api/test_plan_api.py tests/api/test_approval_api.py tests/api/test_task_commands.py tests/api/test_task_events.py tests/domain/test_task_commands.py tests/state/test_task_pause_cancel.py tests/domain/test_checkpoint.py -q
# 71 passed
```

### Plan fixture 契约表（15 组）
`tests/plan/test_plan_fixtures.py`：valid_specified_source / valid_exploratory / valid_hybrid /
unregistered_node / duplicate_node_id / missing_dependency / cycle / invalid_parameter_schema /
incompatible_resource_edge / spec_version_mismatch / scope_expansion → REQUIRES_NEW_SPEC /
field_semantics_change → REQUIRES_NEW_SPEC / quality_reduction → REQUIRES_NEW_SPEC /
credential_high_risk → REQUIRES_APPROVAL / prohibited_bypass → PROHIBITED

### Plan Generator（Fake Model Adapter）
`tests/plan/test_plan_generator.py`：typed 合法结构 / registry metadata 传入模型 /
unknown node → Validator 拒绝 / 单次 repair 后 PASS / 二次失败 → INVALID

### Replan / Diff
`tests/plan/test_plan_replan.py`：diff added/changed / fingerprint 稳定 / v2 parent=v1、v1 不可变 /
Spec 边界 replan → REQUIRES_NEW_SPEC

### Approval
`tests/approval/test_approval_service.py`：6 场景（high-risk PENDING / approve+consume /
fingerprint 变化失效 / expired 不可消费 / revoked 不可消费 / User B 不可访问 A）
`tests/api/test_approval_api.py`：query/approve/reject/revoke + owner 404

### Temporal 集成（需 KAIROS_RUN_INTEGRATION=1 + 本地栈）
`tests/integration/test_plan_workflow.py`：A 低风险 Plan 启动无二次确认；B 高风险 → Approval
PENDING → approve → 恢复；C reject → 高风险 Node 绝不执行。本次本地栈未启动，收集通过、未实跑
（DEPLOY-GATE-2 阶段在 Staging 真实执行）。

### 前端 scoped
```bash
cd frontend
npm run type-check && npm run lint:check && npm run format:check && npm run test:unit && npm run build
# 78 passed；build PASS
```
`tests/approval/approvalFlow.test.ts`：Deep Link 打开同一 Drawer / 低风险 VALID 无二次确认 /
approve/reject 真实后端状态
`tests/approval/ApprovalDrawer.test.ts`：脱敏凭据 + 范围 + 无费用字段

### ruff / mypy
```bash
.venv/Scripts/python.exe -m ruff check app tests      # PASS
.venv/Scripts/python.exe -m mypy app                  # PASS（103 files）
```

### Migration
```bash
.venv/Scripts/python.exe -m alembic heads   # 0006 (head)
.venv/Scripts/python.exe -m alembic upgrade head --sql   # 0006 ALTER TABLE 全部生成（离线校验）
```
本地 PostgreSQL 未启动，无法实跑 upgrade；offline SQL 校验通过。DEPLOY-GATE-2 阶段对
Staging DB 实跑。

### Secret scan
后端/前端新增代码无 secret 模式匹配；credential_ref 仅脱敏引用；dummy 测试凭据仅存在于
测试 fixture。

## 6. 跨模块联动结果
- 上游 M-04 状态机/事件/Outbox/幂等/Checkpoint：PASS（Approval 状态转换走 DomainEvent+Outbox）
- 上游 M-06 confirmed Spec：PASS（Plan 只消费 confirmed CollectionSpecVersion）
- 上游 M-07 TaskWorkflow：PASS（submit_validated_plan seam + approval_resolution Signal + SSE 复用）
- 下游 M-09+ Node 契约：PASS（10 标准 Node 已注册，executor 表保持空）

## 7. Git 证据
- 分支：feature/M-08-plan-registry-approval（从 M-07 HEAD 0116e78 创建，未 push）
- Commits：
  - `1d8dfbb` docs(plan): add M-08 plan registry and approval implementation plan
  - `5262fc2` feat(plan): add typed node registry and plan graph schemas
  - `0c28bae` feat(plan): add pydantic-ai plan generator with bounded repair
  - `1aded4d` feat(plan): add deterministic validator and immutable plan version
  - `d668ef9` feat(plan): add deterministic replan diff and immutable plan versioning
  - `87ec440` feat(approval): add scoped approval lifecycle and workflow wait/resume
  - `7c8bc67` feat(web): add plan summary and approval flow with deep link
- working tree：clean；pushed：NO

## 8. 完成结论

**M-08 LOCAL DONE 门禁（本地闭环验证）：**
- Node Registry：10 标准 Node Contract 注册 + typed NodeDefinition metadata + version ✅
- Pydantic AI Plan Generator（registered-nodes-only + 单次 repair）✅
- Deterministic Validator（DAG / 参数 / 资源边 / Spec / 风险 / PROHIBITED / REQUIRES_NEW_SPEC）✅
- PlanVersion immutable + Replan v2 + 结构化 diff ✅
- Approval lifecycle + fingerprint invalidation + expiry/revoke + owner 隔离 ✅
- Temporal 集成 A/B/C 收集通过（Staging 阶段实跑）✅
- Chat Approval Card + Task Drawer 待审批 + Deep Link 同一 Approval Drawer ✅
- 15 组 Plan fixtures + 6 Approval 场景 + 前端 scoped 测试 ✅
- ruff/mypy PASS、frontend type/lint/format/build PASS ✅
- secret scan 无新增 ✅
- migration：0006（离线校验通过；Staging 实跑）✅
- working tree：clean ✅

**最终状态：DONE_LOCAL**。下一阶段：DEPLOY-GATE-2（可交互 Task Workflow Staging）。
