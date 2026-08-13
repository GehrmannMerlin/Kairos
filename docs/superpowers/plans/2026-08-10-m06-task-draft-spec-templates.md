# M-06 Implementation Plan — Task Draft、Agent 对话、CollectionSpec 与模板闭环

- 日期：2026-08-10
- 分支：`feature/M-06-task-spec-templates`
- 基线：M-05 HEAD `2f7c0bc0477a423ff937591173f4d959074fa397`（working tree clean，未 push）
- 依赖模块：M-02（DONE）、M-03（DONE）、M-04（DEPLOYED）、M-05（DONE）
- 执行方式：superpowers writing-plans 等价流程（当前环境无 superpowers 技能，按用户授权以 inline 方式执行 subagent-driven-development）
- 目标：完成第一条核心产品链（NL → Task Draft → Chat 持久化 → Pydantic AI 目标理解 → CollectionSpec Draft → 澄清 → 摘要卡/Editor → 确认 → 不可变 Version → M-07/M-08 稳定交接契约）+ 模板闭环。

---

## 0. 真实仓库扫描结论（不是猜测）

- 后端：`backend/app` 分层为 `api/ auth/ credentials/ domain/ providers/ state/ storage/ infra/ agents/(无)/ workflows/ activities/`。
- `agents/` 目录**尚不存在**，M-06 创建 `backend/app/agents/goal_understanding.py`。
- `domain/models.py` 已有 `Task` / `CollectionSpecVersion` / `PlanVersion` / `Run` / `DomainEvent` / `OutboxEvent` / `IdempotencyKey` 等。`Task` 无 ChatMessage / SpecDraft / Template 关联。
- `domain/service.py`：`DomainService.transition_task(...)` 内部自行 `db.commit()`，可用于独立状态转换；M-06 确认 Spec 需要**单事务**组合（建版本 + 置 current + DRAFT→QUEUED + 事件 + outbox），因此新增 `confirm_spec(...)` 命令在领域服务内单次 commit。
- `state/states.py`：`TASK_COMMANDS` 中 `submit: DRAFT→QUEUED`、`delete: DRAFT→DELETED`、`restore` 已存在；M-06 无需新 Task 状态。
- `providers/protocol.py`：`ModelProvider` 目前只有 `test_connection` / `resolve_model`，**无推理能力**；`transport.py` 提供 `HttpClient` + `HttpxTransport`（可注入 fake transport 做单测）。`errors.py` 有 `ProviderError` 分类与 `ModelNotConfiguredError`（code=`MODEL_NOT_CONFIGURED`，409）。
- `credentials/vault.py`：`CredentialVault.read_for_execution(user_id, credential_version_id)` 是唯一解密路径；`ProviderService.require_available_model_config(user)` 校验默认可用 ModelConfig。
- Migration head = **0004**（`alembic heads` 确认）。M-06 创建 **0005**。
- pydantic-ai：本地 venv 未安装 → 已安装 **2.27.0**；pyproject 需追加依赖。已核对实际 API：
  - `Agent(model=..., output_type=..., system_prompt=..., retries=...)`。
  - `FunctionModel(function=...)` 可注入 callable `(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse`。
  - `AgentInfo.output_tools[0].name` 是结构化输出工具名；callable 合成 `ModelResponse(parts=[ToolCallPart(tool_name=..., args=<json字符串>)])` 即被 pydantic-ai 校验为 `result_type`。这是 M-06 复用 M-03 传输层的接入点，不调用 pydantic-ai 自带 provider SDK。
- 前端：`features/app/AppView.vue` 是工作台（真实路径，非 `features/workbench/`）；`features/tasks/TaskChatView.vue` 是空态；`overlay/modals/CollectionSpecEditorSheet.vue`、`TemplateVariablesSheet.vue`、`ModelRequiredModal.vue` 均为 M-06 占位；`overlay/modal.store.ts` 支持 `openModal(type, payload)`；`apiErrorMapper` 已映射 `MODEL_NOT_CONFIGURED`→`/models`。路由已注册 13 类，`/templates/new`、`/templates/:templateId/edit` 已存在。

---

## 1. 任务总览（8 个可独立验证任务）

| # | 任务 | 主要产出 | Commit |
|---|---|---|---|
| 1 | M-06 persistence + migration 0005 | ChatMessage / CollectionSpecDraft / CollectionTemplate + tasks 关联 + TaskType 枚举 + pydantic-ai 依赖 | `feat(db): add chat, spec draft and template persistence` |
| 2 | TaskDraftService + Task/chat API + idempotency + owner | create empty/draft+message、append、chat 查询、spec-draft 读写、understand 编排 | `feat(task): add task draft and chat persistence` |
| 3 | ModelInferenceClient + Pydantic AI GoalUnderstandingAgent | M-03 推理扩展 + typed GoalUnderstandingResult + 三类识别 + 澄清 | `feat(agent): add typed goal understanding` |
| 4 | CollectionSpec confirm / 不可变版本 / 事务 | confirm_spec 单事务命令、v1/v2 不可变、DRAFT→QUEUED | `feat(spec): add collection spec draft and confirmation` |
| 5 | Chat UI + Workbench 接入 + Model Required 恢复链 | 真实 Chat、工作台创建、ModelRequired returnTo、/models 返回 | `feat(web): connect workbench and task chat` |
| 6 | Spec Summary Card + Editor Sheet | 摘要卡、完整编辑器、确认按钮 | `feat(web): add spec summary card and editor sheet` |
| 7 | Templates 后端 + 前端 + 版本 + 变量 + 使用 + 从任务保存 | 模板 CRUD/版本/变量/use/create_from_task | `feat(template): add versioned collection templates` |
| 8 | M-06 fake smoke + 真实 Provider E2E + 文档 | fake 集成、单条真实 E2E、架构文档、执行记录 | `test(task)` + `docs(task)` |

---

## Task 1 — M-06 persistence + migration 0005

### Files
- 改 `backend/app/domain/models.py`：新增 `ChatMessage`、`CollectionSpecDraft`、`CollectionTemplate`；`Task` 增加 `template_id`、`template_version`（nullable）。
- 改 `backend/alembic/versions/0005_*.py`（新建）。
- 改 `backend/pyproject.toml`：dependencies 追加 `pydantic-ai>=2.27`。
- 新建 `backend/app/domain/task_types.py`（或并入 models）：`TaskType` 规范枚举。
- 改 `backend/app/domain/repository.py`：新增 `ChatMessageRepository`、`SpecDraftRepository`、`TemplateRepository`（放本任务，供 Task2/7 复用）。

### Interfaces
```python
class TaskType(StrEnum):
    EXPLORATORY = "EXPLORATORY"
    SPECIFIED_SOURCE = "SPECIFIED_SOURCE"
    HYBRID = "HYBRID"

class ChatMessage(Base):
    __tablename__ = "chat_messages"   # append-only
    id; user_id(FK); task_id(FK, index); role(str); content(Text)
    ref_type(str|None); ref_id(int|None)     # spec_draft / goal_result / clarification / model_required / error
    metadata(JSON, nullable); created_at
    # 无 updated_at：历史不可覆盖

class CollectionSpecDraft(Base):
    __tablename__ = "collection_spec_drafts"   # 每 Task 一个
    id; task_id(FK, unique); user_id(FK); payload(JSON); updated_at

class CollectionTemplate(Base):                 # 单表版本化（对齐 ModelConfig 模式）
    __tablename__ = "collection_templates"
    id; template_id(str(32), index); user_id(FK); version(int)
    name(str); task_type(str); goal_template(Text); variables(JSON)
    field_schema(JSON); completion_conditions(JSON); advanced_settings(JSON)
    field_expansion(JSON); default_model_config_ref(JSON|None)
    is_current(bool); is_favorite(bool); created_at
    UniqueConstraint("template_id", "version")
```

Migration 0005：
- `create_table chat_messages / collection_spec_drafts / collection_templates`（全表 `user_id NOT NULL` + FK）。
- `op.add_column tasks.template_id`（BigInteger nullable）、`tasks.template_version`（Integer nullable）。
- `op.alter_column tasks.task_type nullable=True`（兼容 expand；空 Draft 尚无类型）。
- 更新 `TaskRepository.create` 默认 `task_type=None`，并允许传入 `template_id/template_version`。

### Consumes
- M-04 `Base` / FK 约定；现有 `tasks` 表；`IdempotencyKey` 唯一约束模式。
- `CollectionSpecVersion`（M-04）作为不可变 Version 目标，不新增第二套 Spec 表。

### Produces
- 3 张新表 + tasks 两列 + TaskType 枚举；`ChatMessageRepository` / `SpecDraftRepository` / `TemplateRepository`（owner-safe，均以 `user_id` 校验）。

### 最小测试
- migration upgrade/rollback 在临时 SQLite DB 可执行（`alembic upgrade head` → 关键表存在 → `downgrade` 还原）。
- `test_models_roundtrip` 风格：ChatMessage / SpecDraft / Template 持久化与 owner 字段非空。

### Downstream 消费
- M-07：以 `CollectionSpecVersion` + `tasks.current_spec_version` 为 Workflow 输入；`chat_messages` 为 Chat 工作区事实。

---

## Task 2 — TaskDraftService + Task/chat API + 幂等 + owner

### Files
- 新建 `backend/app/domain/task_draft.py`（`TaskDraftService`）。
- 新建 `backend/app/api/routes/tasks.py` 追加（或新文件 `backend/app/api/routes/chat.py`，注册进 router）。
- 改 `backend/app/api/router.py`。
- 改 `backend/app/api/schemas.py`：新增 `CreateTaskDraftCommand`、`ChatMessageDto`、`ChatListResponse`、`SpecDraftDto`、`SpecDraftUpdateCommand`。

### Interfaces
```python
class TaskDraftService:
    def create_empty_draft(user_id, *, idempotency_key: str | None = None) -> Task
    def create_draft_with_message(user_id, *, content, seed_urls: list[str], idempotency_key) -> tuple[Task, ChatMessage]
    def append_user_message(user_id, *, task_id, content, idempotency_key) -> ChatMessage
    def list_messages(user_id, *, task_id) -> list[ChatMessage]     # 按 created_at asc，append-only
    def get_spec_draft(user_id, *, task_id) -> CollectionSpecDraft | None
    def update_spec_draft(user_id, *, task_id, payload: SpecDraftPayload) -> CollectionSpecDraft
    def add_seed_url(user_id, *, task_id, url) -> CollectionSpecDraft   # 基础 URL 格式验证，仅进 Draft Context
```
- 路由（均 `Depends(require_user)` + `get_db`；薄层）：
  - `POST /api/tasks`：body `{content?, seed_urls?, template_id?, template_version?, variables?, idempotency_key?}` → 创建空 Draft 或 Draft+首条消息；幂等；返回 `{task_id}`。
  - `GET /api/tasks/{task_id}/chat`：owner-safe 消息列表。
  - `POST /api/tasks/{task_id}/messages`：body `{content, idempotency_key}` → append；幂等。
  - `GET /api/tasks/{task_id}/spec-draft` / `PUT /api/tasks/{task_id}/spec-draft`：Draft 读写（保存 ≠ 确认）。
  - `POST /api/tasks/{task_id}/understand`：调用 Task3 Agent，保存 GoalUnderstandingResult + Spec Draft + agent 消息；失败时 Task/消息保留。

### Consumes
- M-04 `TaskRepository.create/get_owned/list_by_user`、`IdempotencyService.record`、`append_domain_event/enqueue_outbox`。
- M-02 `require_user` / `NotFoundError`（跨用户 404 不泄漏）。
- D-034：seed_urls 仅写入 Draft Context，不 Fetch。

### Produces
- 创建/追加/查询/编辑的 typed API；idempotency key 与消息去重；owner 边界。

### 最小测试（TEST A）
- create empty draft → 进入 chat 的 task 存在。
- create draft + first message → Task + 1 条 User ChatMessage 持久化。
- 相同 idempotency key 重复请求 → 仍 1 条消息。
- User B 读取 A 的 chat / 消息 / draft → 404，列表不含。

### Downstream 消费
- M-07 以 Task + Chat 工作区为入口；M-08 以 Spec 为约束。

---

## Task 3 — ModelInferenceClient + Pydantic AI GoalUnderstandingAgent

### Files
- 新建 `backend/app/providers/inference.py`：`ModelInferenceClient`（OpenAI-compatible 家族 + ollama + anthropic + gemini 的 wire 格式，统一走 `HttpClient`）。
- 改 `backend/app/providers/protocol.py`：`ModelProvider` 增加 `async def generate(...)` 默认实现（或 `ResolvedModel` 不变，由 `ModelInferenceClient` 按 `provider_type` 分派）——**决策**：为保持适配器职责单一，`ModelInferenceClient` 接收 `ResolvedModel` + 解密后的 `api_key`，按 `provider_type` 构造请求；不调用任何第三方 SDK。
- 新建 `backend/app/agents/__init__.py`、`backend/app/agents/goal_understanding.py`。
- 新建 `backend/app/agents/schemas.py`（或并入 goal_understanding.py）：`GoalUnderstandingResult` 等 typed schema。

### Interfaces
```python
@dataclass
class InferenceRequest:
    system: str
    user_messages: list[str]          # Draft Context + 必要 Chat Context
    response_format: str = "json_object"   # openai-compatible 家族；其他家族用 prompt 约束

class ModelInferenceClient:
    def __init__(self, http: HttpClient | None = None): ...
    async def generate(self, *, resolved: ResolvedModel, api_key: str | None,
                       request: InferenceRequest) -> InferenceResult
    # InferenceResult: text, provider_type, duration_ms；错误映射到 M-03 taxonomy

class GoalUnderstandingResult(BaseModel):
    task_type: TaskType
    goal: str
    fields: list[FieldSpec]           # name/type/required/description
    auto_expand_fields: bool
    source_scope: SourceScope         # exploratory | specified | hybrid + seed_urls/source_hints
    completion_conditions: list[CompletionCondition]   # D-006 多条件；只定义 Schema
    advanced_runtime_limits: RuntimeLimits | None      # 非金额；D-071 并发不进 Spec
    confidence: float
    ambiguities: list[str]
    clarification_required: bool
    clarification_question: str | None
    template_variables: list[TemplateVariableSuggestion] | None  # 深圳 → {city}（D-047）

class GoalUnderstandingAgent:
    def __init__(self, inference: ModelInferenceClient): ...
    async def understand(self, *, draft_context: DraftContext, chat_context: list[str]) -> GoalUnderstandingResult
```
- 内部实现：`FunctionModel(function=callable)`，callable 组装 prompt → `ModelInferenceClient.generate(...)` → 解析 JSON → 合成 `ModelResponse(parts=[ToolCallPart(tool_name=agent_info.output_tools[0].name, args=<json>)])`；`Agent(output_type=GoalUnderstandingResult, retries=1)` 做输出校验。
- 模型选择：`ProviderService.require_available_model_config(user)` → 无则抛 `ModelNotConfiguredError`（MODEL_NOT_CONFIGURED，409）；有则 `resolve_model(...)` + `vault.read_for_execution(...)`（执行时临时解密，不落日志/Prompt 记忆）。

### Consumes
- M-03 `ModelProvider` / `ModelConfig` / `CredentialVault` / `ProviderService` / `errors` taxonomy / `transport`。
- M-04 `ResolvedModel`、`ProviderTestStatus`。

### 最小测试（TEST B）
- fake `HttpClient`/`ModelInferenceClient`：EXPLORATORY / SPECIFIED_SOURCE / HYBRID 三条输入 → typed `GoalUnderstandingResult` 对应 task_type。
- ambiguous 输入（无 URL 无列表）→ `clarification_required=True` + 单一高杠杆问题。
- 无 ModelConfig → `ModelNotConfiguredError`（供 Task5 的 Modal 链）。

### Downstream 消费
- Task4 用 `GoalUnderstandingResult` 生成 Spec Draft；M-07/M-08 只接收确认后的 `CollectionSpecVersion`。

---

## Task 4 — CollectionSpec confirm / 不可变版本 / 事务

### Files
- 改 `backend/app/domain/service.py`：新增 `confirm_spec(...)` 命令（单事务）。
- 改 `backend/app/domain/repository.py`：`SpecVersionRepository` 增加 `next_version`、`latest_version`、`mark_confirmed`。
- 新建 `backend/app/api/routes/spec.py`（或并入 tasks）：`POST /api/tasks/{task_id}/spec-confirm`。

### Interfaces
```python
class DomainService:
    def confirm_spec(self, *, user_id: int, task_id: int, expected_version: int,
                     spec_payload: SpecDraftPayload) -> CollectionSpecVersion:
        # 同一事务：require_user → owner-safe Task（version 乐观锁）
        #   → 服务端 schema 校验 spec_payload
        #   → next_version = max(existing)+1
        #   → create CollectionSpecVersion(payload, schema_version="m06.1")
        #   → task.current_spec_version = next_version; task.task_type = payload.task_type
        #   → transition DRAFT→QUEUED（submit；不允许则 IllegalTransitionError）
        #   → append_domain_event("task.spec_confirmed", {spec_version, ...})
        #   → enqueue_outbox("task.spec_confirmed")
        #   → 单次 commit（原子）
```
- `POST /api/tasks/{task_id}/spec-confirm`：body = Draft payload（或 draft id）；前端不带 version 直接确认（后端以 Draft 当前 payload 为准 + 服务端校验）；乐观锁 `expected_version` 由前端传 Task.version。

### Consumes
- M-04 状态机（`submit: DRAFT→QUEUED`）、`append_domain_event` / `enqueue_outbox`、`IdempotencyKey`。
- D-004/D-005/D-035：确认即冻结；修改必须新版本。

### 最小测试
- TEST D：Draft 可编辑（保存不产生 Version）；confirm → 创建 v1 且 `confirmed_at` 非空、Task `current_spec_version=1`、state=QUEUED；再改 Draft 再 confirm → v2；**v1 payload 不变**。
- TEST F：confirm 事务故意中断（如注入失败）→ 无半确认状态（无 Version 或 state 不变，回滚一致）。

### Downstream 消费
- M-07：`confirm_spec` 产生的 `task.spec_confirmed` 事件与 `CollectionSpecVersion` 是 Workflow 启动事实；M-08 以此生成 Plan。

---

## Task 5 — Chat UI + Workbench + Model Required 恢复链

### Files
- 新建 `frontend/src/features/tasks/chat.api.ts`。
- 改 `frontend/src/features/tasks/TaskChatView.vue`（真实 Chat）。
- 新建 `frontend/src/features/tasks/ChatComposer.vue`、`ChatMessageList.vue`（M-06 用普通 HTTP，不 SSE/不 token 流）。
- 改 `frontend/src/features/app/AppView.vue`（工作台真实输入 + 添加网址 + 使用模板 + 快速开始；最近任务保持真实 API）。
- 改 `frontend/src/app/overlay/modals/ModelRequiredModal.vue`（接受 payload `{returnTo}`，`/models?return_to=...`）。
- 改 `frontend/src/features/providers/ModelsView.vue`（读取 `return_to` query，显示「返回刚才的任务」）。
- 改 `frontend/src/features/tasks/tasks.api.ts`（新增 createTask 等）。

### Interfaces（frontend）
```ts
// chat.api.ts
export interface ChatMessageDto { id: number; role: 'user'|'assistant'|'system'; content: string;
  ref_type: string|null; ref_id: number|null; metadata: Record<string,unknown>|null; created_at: string }
createTaskDraft(req: { content?: string; seedUrls?: string[]; templateId?: string; variables?: Record<string,string>; idempotencyKey?: string }): Promise<{ task_id: number }>
getChat(taskId): Promise<{ messages: ChatMessageDto[] }>
sendMessage(taskId, content, idempotencyKey): Promise<ChatMessageDto>
runUnderstanding(taskId): Promise<ChatMessageDto>   // 或返回 agent message
```
- 工作台：`createTaskDraft({content})` 成功 → `router.push('/tasks/:id/chat')` → Chat 自动/可点击触发 `runUnderstanding`；创建成功但 Agent 失败 → 输入不丢（Task/消息已持久化）。
- ModelRequired：`runUnderstanding` 捕获 `model_not_configured` → `openModal('MODEL_REQUIRED', { returnTo: '/tasks/'+taskId+'/chat' })`；Modal 去 `/models?return_to=...`；ModelsView 显示返回链接（不依赖 localStorage 作为唯一事实来源）。

### Consumes
- M-05 `useTaskShell` / `openModal` / `apiErrorMapper` / 路由；`apiClient`。
- D-045/D-066：无模型仍可浏览/填写；真正调用 Agent 时才拦截。

### 最小测试（frontend scoped）
- workbench input → createTaskDraft → route `/tasks/:id/chat`（mock API）。
- MODEL_NOT_CONFIGURED → Modal 打开 → `/models` → return_to 保留（route query 断言）。

### Downstream 消费
- Task6 复用 Chat 消息渲染 Spec Summary Card；Task7 复用 Workbench 模板入口。

---

## Task 6 — Spec Summary Card + Editor Sheet

### Files
- 新建 `frontend/src/features/tasks/SpecSummaryCard.vue`。
- 改 `frontend/src/app/overlay/modals/CollectionSpecEditorSheet.vue`（真实编辑器）。
- 改 `frontend/src/features/tasks/chat.api.ts`：`getSpecDraft` / `updateSpecDraft` / `confirmSpec`。
- 改 `frontend/src/features/tasks/TaskChatView.vue`：渲染摘要卡；「查看/修改采集方案」→ Editor Sheet；「确认并执行」→ confirm。

### Interfaces
- `SpecSummaryCard`：任务名称 / TaskType / Agent 理解目标 / 字段列表 / 范围 / 完成条件摘要 / Spec 版本（Draft 或 Version）。
- Editor Sheet：目标、字段（name/type/required、新增/删除）、自动扩展字段开关、采集范围（seed_urls/source_hints）、完成条件、高级运行设置（折叠，仅非金额项：max_pages/max_duration/retries）；保存 = 更新 Draft（≠确认）。
- 确认按钮按 D-035：校验 → 确认 → 冻结 Version；本轮**不伪造执行中状态**（"确认并执行"保留交互契约，M-07/M-08 才接真实执行）。

### Consumes
- M-05 SheetHost/ModalHost；D-035/D-037/D-048（不新增一级页面）。

### 最小测试（frontend scoped）
- agent result → Spec Summary Card 渲染真实字段。
- Editor 修改 Draft → 保存；confirm → 调用 confirmSpec。

### Downstream 消费
- Task7 `create_template_from_task` 消费已确认 Spec；M-07 消费 `task.spec_confirmed`。

---

## Task 7 — Templates 后端 + 前端 + 版本 + 变量 + 使用 + 从任务保存

### Files
- 改 `backend/app/domain/repository.py`：`TemplateRepository`（复用 Task1，本任务补全 CRUD/版本操作）。
- 新建 `backend/app/templates/service.py`（或并入 domain）：`TemplateService`。
- 新建 `backend/app/api/routes/templates.py` + 注册 router。
- 新建 `frontend/src/features/templates/templates.api.ts`。
- 改 `frontend/src/features/templates/TemplatesView.vue`（列表/新建/编辑/复制/重命名/设为常用/删除/使用）。
- 改 `frontend/src/features/templates/TemplateEditView.vue`（全宽编辑器）。
- 改 `frontend/src/app/overlay/modals/TemplateVariablesSheet.vue`（真实变量填写）。

### Interfaces（backend）
```python
class TemplateService:
    def list(user) / create(user, spec) -> Template
    def get_version(user, template_id, version)
    def update(user, template_id, spec) -> Template        # 新版本，旧版本保留
    def duplicate(user, template_id) -> Template
    def set_favorite(user, template_id, favorite: bool)
    def delete(user, template_id)                          # owner-safe
    def use(user, *, template_id, variables: dict[str,str]) -> Task   # resolve → create Task Draft(+首条消息) → 保存 template_id/version → /chat
    def create_from_task(user, *, task_id) -> Template      # 要求 owner-safe + 已确认 Spec；只复制 Spec 骨架（不复制 Run/Record/Evidence/Checkpoint）；按 D-047 变量化
```
- 路由：`GET/POST /api/templates`；`GET/PATCH/DELETE /api/templates/{template_id}`；`POST /api/templates/{template_id}/duplicate`；`POST /api/templates/{template_id}/use`（variables）；`POST /api/tasks/{task_id}/template`（from-task）。
- TemplateVariable：`{name, label, type, required, default?}`；使用模板时校验必填变量 → resolve → 创建 Task Draft 并保存 `template_id/template_version` → 生成 Spec Draft（goal 模板替换变量）→ `/tasks/:id/chat`。运行事实以生成的 CollectionSpec Version 为准。

### Consumes
- Task1 `CollectionTemplate` 表、Task4 `confirm_spec` 产物、Task2 `TaskDraftService`。
- D-047/D-054：不新增 `/templates/:id/history` 页面；版本历史放编辑页信息区/后端 metadata。

### 最小测试（TEST E）
- Template v1 → use({city:'深圳'}) 创建 Task → Task 引用 v1，Spec Draft goal 含「深圳」。
- 模板编辑 → v2；原 Task 仍引用 v1（列表/读取不变）。
- 变量 resolve 正确（缺必填拒绝）。
- 跨用户：User B 引用/读取/复制/删除 A 的 Template → 404。

### Downstream 消费
- M-07/M-08 忽略 Template 引用，运行只以 CollectionSpecVersion 为准。

---

## Task 8 — M-06 fake smoke + 真实 Provider E2E + 文档 + 执行记录

### Files
- 新建 `backend/tests/integration/test_m06_smoke.py`（fake ModelProvider 全链，无外部成本）。
- 新建 `backend/tests/agents/` 单测（或并入 Task3）。
- 新建 `docs/architecture/task-draft-spec.md`。
- 新建 `docs/implementation/M-06-execution.md`（状态 IN_PROGRESS → DONE / BLOCKED_EXTERNAL_PROVIDER）。

### Fake Smoke 链（TEST G）
User A → create Task Draft → append User Message → fake GoalUnderstandingAgent → EXPLORATORY Spec Draft → edit 1 字段 → confirm → CollectionSpecVersion v1 → 验证不可变 → create Template from Spec → use Template（{city}=深圳）→ 第二个 Task Draft → 保留 TemplateVersion ref → User B 访问 A Task/Template → 拒绝。不触发 Plan/Temporal/Search/Crawler。

### 真实 Provider E2E（唯一门禁）
- 检查本地是否存在「AVAILABLE 且属于测试用户」的 ModelConfig（不打印/回读明文 Key）。
- 存在：单条输入「帮我搜集深圳的工业自动化设备供应商，获取公司名、官网、主营产品和联系方式」→ Task 创建 → User Message 保存 → 真实 Provider 调用 → `GoalUnderstandingResult`（task_type=EXPLORATORY）→ Spec Draft → 确认 → Version 冻结。不 Search/Crawl/Plan/Workflow。
- 不存在：不伪造、不下载大模型、不用 Claude Code 自身凭据；M-06 = `BLOCKED_EXTERNAL_PROVIDER`，仅要求用户在本地 `/models` 配置一个真实 Provider，之后重跑这一条 E2E。

### 文档
- `task-draft-spec.md`：Task Draft 生命周期、ChatMessage 语义、Goal Understanding Agent、Spec Draft vs Version、confirm_spec 契约、Model Required 恢复、Template/TemplateVersion、M-07/M-08 交接、如何跑 M-06 scoped 测试。

---

## 2. M-06 边界（明确不做）

- M-07：TaskWorkflow / Temporal / SSE / pause-resume-cancel / Worker heartbeat / Run 启动。
- M-08：PlanGenerator / Node Registry / Plan Validator / Approval 后端。
- M-09+：Search Provider 执行 / robots / URL Frontier / Fetch / Scrapy / Playwright / Extractor / Record Quality / Evidence / CSV。
- 不新增一级页面；不引入 Redis/K8s/计费；不做并发自由配置（D-071 属部署配置）。
- 「确认并执行」只保留交互契约，不伪造执行中状态。
- FastAPI 路由仅承载单次有界 LLM 调用（等价 M-03 test_connection），长任务编排留待 M-07。

---

## 3. 验证策略（A-Lite）

- 后端 scoped：`pytest tests/domain tests/agents tests/api/test_task_shell.py tests/integration/test_m06_smoke.py`（TEST A/B/C/D/E/F/G 相关）；`ruff check/format`、`mypy app`（受影响模块）。
- 前端 scoped：`npm run lint:check`、`npm run format:check`、`npm run type-check`、`npm run test:unit`（M-06 相关 spec）。
- Migration：`alembic upgrade head` + 关键 schema 检查（临时 DB）。
- 真实 Provider E2E：单条（Task8）。
- 不跑 `pytest tests/` 全量、M-03/M-04 全量、DEPLOY-GATE-1、全 Browser E2E。

---

## 4. Git 策略

- 分支 `feature/M-06-task-spec-templates`（从 `2f7c0bc` 创建，不 merge/rebase/push）。
- 8 个 Commit（见任务表），每个可独立验证；Commit 前读 `agent-git-standards.md`。
- 最终 working tree clean；pushed NO。

---

## 5. 计划自查（Writing Plans Self Review）

### 5.1 Spec Coverage
- D-003 三类识别 → Task3；D-004 分级确认/版本冻结 → Task4；D-005 核心字段冻结/自动扩展 → Task4/6；D-006 多条件完成 → Task3 Schema + Task6 编辑器；D-033 Task=Chat → Task2/5；D-034 添加网址仅 Draft Context → Task2；D-035 摘要卡+编辑器 → Task6；D-036 无金额 UI → 全计划无费用字段；D-037 高级设置折叠 → Task6；D-038 确认后执行契约保留 → Task4/6；D-045 工作台 → Task5；D-047 模板 Spec 骨架+变量化 → Task7；D-054 模板编辑页 → Task7；D-066 无模型可浏览、调用时拦截 → Task5；D-071 并发不进 Spec → 全计划。

### 5.2 Placeholder Scan
- 无「待定/后续」占位接口；Task5/6/7 全部为真实 API 接线；M-05 占位组件在本轮全部替换为真实实现。

### 5.3 Type Consistency
- TaskType 单一规范枚举（EXPLORATORY/SPECIFIED_SOURCE/HYBRID），无 SEARCH/DISCOVERY 多套命名。
- `confirm_spec` 输出 = `CollectionSpecVersion`（M-04 既有类型），不新增第二套 Version。
- pydantic-ai 接口已对照 2.27.0 源码核实（FunctionModel/AgentInfo.output_tools/ToolCallPart/ModelResponse）。

---

## 6. 项目专项 Self Approval

### CHECK 1：Business Decisions — PASS
D-003/004/005/006/029/033/034/035/037/038/045/047/054/066/071 全部落地；无收费/金额/人民币字段；D-071 并发属部署配置不进 Spec。

### CHECK 2：M-02 Compatibility — PASS
所有新资源（ChatMessage/SpecDraft/Template/Task）带 `user_id`，查询走 `require_user` + owner-safe 404；无第二套 Auth。

### CHECK 3：M-03 Compatibility — PASS
Agent 只经 `ModelProvider`/`ModelConfig`/`CredentialVault`；Key 在 `read_for_execution` 临时解密，不落日志/Prompt/Temporal；无未经授权 fallback；不使用 Claude Code 自身凭据。

### CHECK 4：M-04 Compatibility — PASS
Task 复用 M-04；confirm_spec 经状态机（submit: DRAFT→QUEUED）+ 单事务（Version+状态+DomainEvent+Outbox）；幂等复用 `IdempotencyService`；不直接 UPDATE Task state。

### CHECK 5：M-05 Compatibility — PASS
复用 AppShell/Workbench/Chat 路由/Template 路由/Drawer·Modal 基础设施/Global Error Mapper/useTaskShell；无新增一级页面。

### CHECK 6：Task/Chat Boundary — PASS
一个 Task 一个持续 Chat 工作区；无 ChatTask/AgentTask/DraftTask 重复对象。

### CHECK 7：Spec Boundary — PASS
Draft 可编辑；Confirmed Version immutable；旧 Version 不覆盖（v1/v2 均保留）。

### CHECK 8：Template Boundary — PASS
TemplateVersion immutable；历史 Task 保持旧 Template Version ref；运行事实以 CollectionSpec Version 为准。

### CHECK 9：Model Required — PASS
Draft 服务器持久化；User Message 不丢；/models 返回同一 Task 恢复（returnTo 传递，非 localStorage 唯一来源）。

### CHECK 10：M-07 Boundary — PASS
无 TaskWorkflow/pause-resume-cancel/SSE runtime/Worker heartbeat。

### CHECK 11：M-08 Boundary — PASS
无 PlanGenerator/Node Registry/Plan Validator/Approval 后端。

### CHECK 12：M-09+ Boundary — PASS
无 Search/robots/URL Frontier/Fetch/Scrapy/Playwright/Extractor/Quality/CSV。

### CHECK 13：No Fake Business Data — PASS
生产 UI 业务数据全部来自真实 API；仅测试 fixture 有 fake ModelProvider。

### CHECK 14：A-Lite Testing — PASS
关键路径（owner/幂等/版本不可变/confirm 事务/Model Required/模板变量/三类识别/澄清）有测试；无全量套件/Provider live matrix/浏览器抓取测试。

### CHECK 15：Real Provider Gate — PASS
Plan 明确区分 fake 自动化测试与单条真实 Provider E2E；不用 fake 冒充完成门禁。

### CHECK 16：Git — PASS
Commit 可独立验证；不 Push/Merge/Tag/Deploy；分支从 M-05 HEAD 创建。

### CHECK 17：Secret Safety — PASS
API Key 不落日志/前端明文/Prompt 长期记忆；Agent 审计只留 model_config ref/provider/model/duration/error class。

### CHECK 18：Placeholder Scan — PASS
无未定占位；pydantic-ai API 已按 2.27.0 核实。

### CHECK 19：Type/Interface Consistency — PASS
TaskType 单一枚举；confirm_spec 返回既有 CollectionSpecVersion；推理层统一走 M-03 transport。

---

PLAN SELF-APPROVAL: PASS

business decisions: PASS
implementation plan M-06: PASS
M-02 compatibility: PASS
M-03 compatibility: PASS
M-04 compatibility: PASS
M-05 compatibility: PASS
task/chat boundary: PASS
spec version semantics: PASS
template version semantics: PASS
model-required persistence: PASS
M-07 boundary: PASS
M-08 boundary: PASS
M-09+ boundary: PASS
no fake business data: PASS
secret safety: PASS
A-Lite testing: PASS
real-provider gate: PASS
git standards: PASS
placeholder scan: PASS
type/interface consistency: PASS

> 自审批通过后自动进入执行（用户已预先授权 subagent-driven-development / inline 等价方式）。
