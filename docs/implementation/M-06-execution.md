# M-06 模块执行记录

状态：IN_PROGRESS → **BLOCKED_EXTERNAL_PROVIDER**（全部代码/scoped 测试通过，唯一缺真实 Provider E2E）
负责人/Agent：Claude Code — 2026-08-10
Baseline（M-05 DONE）SHA：`2f7c0bc0477a423ff937591173f4d959074fa397`
依赖模块：M-02（DONE）、M-03（DONE）、M-04（DEPLOYED）、M-05（DONE）
目标环境：local（M-06 不属于 Deploy Gate；DEPLOY-GATE-2 必须等 M-05～M-08）

## 1. 本模块目标

完成第一条核心产品链：NL → Task Draft → Chat 持久化 → Pydantic AI 目标理解 →
CollectionSpec Draft → 澄清 → 摘要卡/Editor → 确认 → 不可变 CollectionSpecVersion →
M-07/M-08 稳定交接契约；同时完成模板 CRUD/Version/变量/使用/从任务保存。

## 2. 输入契约

- 上游复用：M-02 `require_user`/owner-safe 404；M-03 `ModelProvider/ModelConfig/CredentialVault/transport/errors`；
  M-04 `Task/CollectionSpecVersion/DomainEvent/Outbox/IdempotencyService/状态机`；M-05 AppShell/Drawer/Modal/Error Mapper/路由。
- 新增依赖：`pydantic-ai>=2.27`（仅用 Agent/FunctionModel 编排，模型调用仍走 M-03 传输层）。
- Migration：head 0004 → 新增 **0005**（chat_messages、collection_spec_drafts、collection_templates、tasks 模板关联、task_type 可空）。

## 3. 本模块实现清单

- [x] 迁移 0005 + TaskType 规范枚举 + typed Spec Draft schema
- [x] TaskDraftService + /api/tasks Draft/Chat/Spec Draft/seed-urls 命令 + idempotency + owner
- [x] ModelInferenceClient（openai-compatible 家族/anthropic/gemini/ollama，统一 M-03 transport；修复 transport 缺失请求体缺陷）
- [x] pydantic-ai GoalUnderstandingAgent + typed GoalUnderstandingResult（三类识别 + 澄清 + 变量建议）
- [x] GoalUnderstandingService 编排（MODEL_NOT_CONFIGURED 门禁、Provider 失败持久化可恢复错误消息、输入不丢）
- [x] confirm_spec 单事务（不可变 Version + DRAFT→QUEUED + DomainEvent + Outbox）
- [x] 前端：工作台真实创建 / Chat 工作区 / Model Required 返回链（return_to）/ Spec Summary Card / Editor Sheet / 模板列表·编辑·变量 Sheet
- [x] M-06 fake smoke（TEST G）全链通过
- [x] docs：`docs/architecture/task-draft-spec.md`、本执行记录
- [ ] 真实 Provider E2E（被外部 Provider / 本地环境阻塞，见第 7 节）

## 4. 明确不做

M-07（TaskWorkflow/Temporal/SSE/pause-resume-cancel）、M-08（Plan/Node/Approval 后端）、
M-09+（Search/robots/Fetch/Crawler/Extractor/Quality/CSV）、新增一级页面、计费/金额 UI、
并发自由配置（D-071 属部署配置）、DEPLOY-GATE-2、服务器变更、远程 Git 集成。

## 5. 验收命令与证据

| 验收项 | 命令 | 预期 | 实际 |
|---|---|---|---|
| 后端 scoped 测试 | `pytest tests/domain/ tests/agents/ tests/api/ tests/providers/test_inference.py` | PASS | **97 passed** |
| 后端 lint/format | `ruff check app tests && ruff format --check app tests` | PASS | PASS |
| 后端 mypy | `mypy app` | PASS | PASS（78 files） |
| 前端 type-check | `npm run type-check` | PASS | PASS |
| 前端 scoped Vitest | `npm run test:unit` | PASS | **15 files / 66 tests PASS** |
| 前端 lint/format | `npm run lint:check && npm run format:check` | PASS | PASS |
| 前端 build | `npm run build` | PASS | PASS |
| Migration | `alembic upgrade head --sql`（PG 方言编译）| PASS | PASS（0005 全套 DDL 编译通过）|
| 真实 Provider E2E | 自然语言→Spec→确认 | 单条真实链 | **BLOCKED**（见第 7 节）|

## 6. 跨模块联动结果

- 上游兼容：PASS — M-02 认证/隔离、M-03 Provider/Credential、M-04 状态机/幂等/事件/Outbox、M-05 Shell/Modal/路由全部复用。
- 下游契约：PASS — `task.spec_confirmed` 事件 + `CollectionSpecVersion` + `task.state=QUEUED` 为 M-07/M-08 提供启动事实；`template_id/template_version` 进入 TaskShellDto。

## 7. 真实 Provider E2E（唯一未完成门禁）

### 7.1 本地栈修复（2026-08-10 复查）

- 复查时发现本地 compose 从未把根目录 `.env` 的 `KAIROS_CREDENTIAL_MASTER_KEY` 注入容器，
  导致 `api` 容器在访问任何 Provider 端点时 500（`CredentialConfigurationError`）。
- 修复：`infra/compose/compose.yaml` 的 `x-backend-env` 增加 `KAIROS_CREDENTIAL_MASTER_KEY: ${KAIROS_CREDENTIAL_MASTER_KEY}`。
- 同时确认 compose v5 需要显式 `--env-file .env`（项目目录 ≠ 仓库根目录）才能读到根目录 `.env`。
- 修复后本地栈验证：`/api/health/live`、`/api/health/ready` 均 PASS；
  `/api/providers/models`、`/api/providers/definitions` 正常返回。API/Worker/Migrate 均以新镜像运行。

### 7.2 Provider 可用性检查（2026-08-10）

- `GET /api/providers/models` → `configs: []`：本地开发用户**无任何 ModelConfig**。
- `ollama` 命令不存在、安装目录不存在：**无现成 Ollama/本地模型**。
- 根目录 `.env` **不含任何 Provider API Key**（OpenAI/DeepSeek/Anthropic/Gemini/OpenRouter）。
- 本地无任何 LLM 服务端口（11434/1234/8080/3000/5000 等）在监听。
- 按预设策略：不能使用 Claude Code 自身凭据、不能用 Mock 冒充、不能自动下载大模型。
- 因此最终状态仍 = `BLOCKED_EXTERNAL_PROVIDER`。

**REQUIRED USER ACTION**：本地开发栈现在已可正常运行。请在 Kairos `/models`
配置任意一个真实可用 Model Provider（OpenAI/DeepSeek/OpenRouter/Anthropic/Gemini/Ollama 均可），
例如创建后调用 `POST /api/providers/models/{config_id}/test` 确认 `connection_status=AVAILABLE`。
配置完成后，仅重跑这一条 Real Provider E2E（自然语言 → GoalUnderstanding → Spec → 确认冻结），
不需要重跑其他测试。请勿把 API Key 粘贴到本会话 Prompt 中。

## 8. Git 证据

- 分支：`feature/M-06-task-spec-templates`（从 M-05 HEAD 创建，未 push）
- Commits：
  - `feat(db): add chat, spec draft and template persistence`
  - `feat(task): add task draft and chat persistence`
  - `feat(agent): add typed goal understanding`
  - `feat(spec): add collection spec draft and confirmation`
  - `feat(web): connect workbench and task chat`
  - `feat(web): add spec summary card and editor sheet`
  - `feat(template): add versioned collection templates`
  - `test(task): cover M-06 core contracts`（含 fake smoke）
  - `docs(task): record M-06 implementation`
  - `fix(infra): forward credential master key in local compose`（本轮复查补录）
  - `docs(task): update M-06 blocker with verified local stack`（本轮复查补录）
- working tree：clean；pushed：NO

## 9. 完成结论

- M-06 全部代码 + scoped 门禁 PASS（后端 97、前端 66、lint/format/mypy/build、migration DDL 编译、fake smoke）。
- 唯一未通过：真实 Provider E2E（环境无可用真实 ModelConfig + 本地服务未运行）。
- 最终状态：**BLOCKED_EXTERNAL_PROVIDER**（IMPLEMENTATION = COMPLETE）。
- 不进入 M-07；待用户配置真实 Provider 后仅重跑 Real Provider E2E。
