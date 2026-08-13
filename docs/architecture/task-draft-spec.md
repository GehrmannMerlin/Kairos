# M-06：Task Draft / Chat / Goal Understanding / CollectionSpec / Template

> 本文档记录 M-06 的核心契约，供 M-07/M-08 消费。

## 1. Task Draft 生命周期（D-033/D-034/D-045）

一个 Task 就是一个持续 Agent 对话工作区，不存在 ChatTask/AgentTask 等重复对象。

```text
+ 新任务        → POST /api/tasks {}                          → 空 Draft（task_type=NULL）
工作台直接输入  → POST /api/tasks {content, seed_urls?}       → Draft + 第一条 User ChatMessage
添加网址        → POST /api/tasks/:id/seed-urls {url}          → 只写入 Spec Draft Context，不 Fetch
```

- Task 复用 M-04 状态机（初始 `DRAFT`）；`allowed_actions` 由后端状态机计算。
- 创建与发送消息使用 M-04 `IdempotencyService`（客户端重试不会重复产生 User Message）。
- 创建成功但 Agent 调用失败时：Task 与 User Message 必须保留（分开的请求）。

## 2. ChatMessage 语义

- append-only：历史消息不 UPDATE。Agent 结构化结果通过 typed `ref_type/ref_id/meta` 关联
  （`goal_result` / `error` 等），不把业务事实只塞成 Markdown。
- owner 隔离：所有读写以 `user_id` 强制校验，跨用户 404 不泄漏存在性。

## 3. Goal Understanding Agent（D-003/D-066）

- `backend/app/agents/goal_understanding.py`：pydantic-ai `Agent` + `FunctionModel`。
  模型调用复用 M-03 `ModelInferenceClient`（同一 `HttpClient` 传输层，无第二套 SDK）。
- 只有在真正调用 Agent 时才要求可用 ModelConfig（`ProviderService.require_available_model_config`），
  无则返回稳定 `MODEL_NOT_CONFIGURED`（409）→ 前端 Model Required Modal。
- Key 仅经 `CredentialVault.read_for_execution` 执行时临时解密；审计只保留
  `model_config_id/version/provider/model/duration/error_class`，绝不记录 Key。
- typed 输出 `GoalUnderstandingResult`：EXPLORATORY / SPECIFIED_SOURCE / HYBRID、
  字段、范围、完成条件、高级限制、置信度、澄清、template_variables 建议。
- 低置信度返回 `clarification_required + clarification_question`（只问一个高杠杆问题）。

## 4. CollectionSpec：Draft vs Version（D-004/D-005/D-035）

- `collection_spec_drafts`：每 Task 一个可编辑 Draft；保存 ≠ 确认。
- `confirm_spec`（`POST /api/tasks/:id/spec-confirm`）单事务完成：
  服务端 typed 校验 → 乐观锁（`expected_version`）→ 创建不可变
  `CollectionSpecVersion`（`CollectionSpecVersion` 表，M-04）→ 置
  `task.current_spec_version` → `DRAFT→QUEUED`（submit；QUEUED 可再确认新版本）→
  追加 `task.spec_confirmed` DomainEvent → Outbox → commit。中途失败整体回滚。
- 修改已冻结 Spec 必须产生新版本（v1/v2 并存），绝不 UPDATE 旧版本。

## 5. Model Required 恢复链（D-066）

```text
用户已输入需求 → understand 返回 MODEL_NOT_CONFIGURED
→ Model Required Modal（payload.returnTo=/tasks/:id/chat）
→ /models?return_to=... → 配置模型 → 「返回刚才的任务」→ 回到同一 Task
→ 读取持久化 Draft → 可继续理解（不重复创建 Task / 不丢输入）
```

恢复事实由服务器持久化（Draft + ChatMessage），不依赖 localStorage 为唯一来源。

## 6. Template / TemplateVersion（D-047/D-054）

- `CollectionTemplate` 单表版本化：`template_id` 逻辑身份 + 每版本一行；编辑创建新版本，
  旧版本不可变。历史 Task 保持创建时引用的 `template_id/template_version`。
- 模板保存 Spec 骨架 + 变量（不含 Run/Record/Evidence/Checkpoint）。
- 使用模板：填写变量 → 校验必填 → resolve goal（`{city}` 替换）→ 创建 Task Draft 并
  保存 TemplateVersion ref → 生成 Spec Draft → `/tasks/:id/chat`。
- 从已确认任务保存模板：按 Goal Understanding 的 `template_variables` 建议把「深圳」
  类单次值变量化为 `{city}`。
- 跨用户读取/使用/编辑/复制模板均安全 404。

## 7. M-07/M-08 交接

M-06 提供稳定交接契约，不假装执行已开始：

- `task.spec_confirmed` DomainEvent + `CollectionSpecVersion`（含 `task_id/version/payload`）。
- `task.state = QUEUED`（等待 M-07 Workflow 启动）。
- M-07 只消费已冻结 Spec Version；M-08 以 Spec 为业务约束生成 Plan。

M-06 明确不做：PlanGenerator / TaskWorkflow / Temporal Run / SSE / Approval /
Search / robots / Fetch / Crawler / Extractor / CSV（后续模块）。

## 8. 如何运行 M-06 scoped 测试

```bash
# backend（SQLite，无外部依赖）
pytest tests/domain/test_spec_confirm.py tests/domain/test_templates.py \
       tests/agents/ tests/api/test_task_draft.py tests/api/test_understand.py \
       tests/api/test_templates_api.py tests/api/test_m06_smoke.py \
       tests/providers/test_inference.py
ruff check app tests && ruff format --check app tests
mypy app

# frontend
cd frontend
npm run type-check && npm run test:unit && npm run lint:check && npm run build

# migration（PostgreSQL 方言编译验证）
KAIROS_DATABASE_URL=... alembic upgrade head --sql   # 无需连接即可验证 DDL
```
