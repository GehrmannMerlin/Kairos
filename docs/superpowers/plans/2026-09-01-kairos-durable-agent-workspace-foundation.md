# Kairos Durable Agent + Workspace Foundation

日期：2026-09-01  
范围：Phase 0（源码研究与本地运行环境）、Phase 1（Temporal durable agent loop）、Phase 2（Workspace/FileSystem/Shell 动态绑定）

本计划以当前用户确认的架构为约束，不重新评估 Temporal、Vue 或 Pydantic AI 的选型。当前 `D:\Develop\kairos` 不是 Kairos 应用仓库，扫描结果是三个本地 upstream checkout 加历史文档，因此按项目规则建立唯一的新应用目录 `kairos-app/`，并在其中初始化 Git 仓库。三个 upstream 仓库只读，不写入 Kairos commit。

## 1. Current State Inventory

### 1.1 工作区实际内容

当前根目录包含：

- `pydantic-ai/`：Pydantic AI Core 源码，branch `main`。
- `pydantic-ai-harness/`：Pydantic AI Harness 源码，branch `main`。
- `Temporal/`：Temporal Python SDK 源码，branch `main`。
- 项目规范和历史设计文档：`CLAUDE.md`、`README.md`、`agent-code-standards.md`、`agent-git-standards.md`、`agent-business-logic-log.md`、`agent-project-implementation-plan.md`、`domain-state-model.md`、`frontend-shell.md`、`local-dev.md`、`local-run.md`、`provider-credentials.md`、`security-baseline.md`。

没有发现现成的 `frontend/`、`backend/`、FastAPI、Vue、Alembic、Docker Compose 或 Kairos Git 仓库源码。历史文档描述了目标产品，但不能冒充现有实现；本轮因此只创建一套 `kairos-app`，不复制第二套旧 App。

### 1.2 可复用内容与业务边界

当前没有可直接 import 的 Kairos 业务模块。历史决策仍作为边界复用：

- Owner isolation、Task、Workspace、Provider/Credential 的 ID 化边界。
- PostgreSQL 作为业务事实和 Event metadata 存储；Temporal History 只承载执行事实。
- Temporal Workflow 只做确定性编排；HTTP、模型、文件、Shell 和事件持久化都在 Activity 侧。
- Vue 3 + FastAPI + SSE，不使用 Pydantic AI `to_web` UI。
- Secrets 不进入 Workflow input、`AgentDeps`、tool args、Temporal history、SSE 或日志。
- 旧静态 Plan/DAG 只能作为历史业务规划概念，不能成为新的 Agent 执行核心。

### 1.3 本轮明确不实现

不实现 SearchProvider、SourceDiscovery、URL Frontier、Scrapy、完整 Evidence/Record pipeline、Validation、Deduplicate、Conflict Resolver、Completion Gate、复杂 Approval、Playwright/Browser Agent、SubAgent workflow、完整 Memory/Skills、Excel/Word/CSV/JSON Artifact、生产部署。

旧文档中 M-01 至 M-18 的完整产品路线不作为本轮执行顺序；本用户确认的 Phase 0–2 优先级覆盖历史实现计划。

## 2. Upstream Source Findings

### 2.1 版本锁定结果

| Upstream | 实际本地路径 | branch | commit SHA | Kairos 采用方式 |
|---|---|---|---|---|
| Pydantic AI Core | `D:\Develop\kairos\pydantic-ai` | `main` | `90de62ab5594912fc0e73a51b7adb2d4c0ee7554` | path/editable dependency，直接使用 public Agent、Toolset、Capability、Temporal integration |
| Pydantic AI Harness | `D:\Develop\kairos\pydantic-ai-harness` | `main` | `9e1c16b52025ad42749dae1a1d901dffdf55432e` | path/editable dependency，使用 FileSystem、Shell、FilteredToolset |
| Temporal Python SDK | `D:\Develop\kairos\Temporal` | `main` | `b6ee53cf748cfc98d9e32eee2d136ca8abb9a6ad` | path/editable dependency，使用 Client、Worker、Workflow、Activity、RetryPolicy |

三处 checkout 均为 clean，且没有在 upstream 仓库创建分支、commit 或修改文件。Python upstream requirement 均为 `>=3.10`；Kairos 本地开发固定使用当前可用的 Python 3.11.8，避免把 Python 3.14 的 Temporal sandbox 已知限制带入本轮。

### 2.2 Pydantic AI Durable/Temporal API 事实

已核对以下源码路径：

- `pydantic-ai/pydantic_ai_slim/pydantic_ai/durable_exec/temporal/_durability.py`
- `pydantic-ai/pydantic_ai_slim/pydantic_ai/durable_exec/temporal/__init__.py`
- `pydantic-ai/pydantic_ai_slim/pydantic_ai/durable_exec/temporal/_dynamic_toolset.py`
- `pydantic-ai/pydantic_ai_slim/pydantic_ai/durable_exec/_base.py`
- `pydantic-ai/pydantic_ai_slim/pydantic_ai/durable_exec/_runtime_toolsets.py`
- `pydantic-ai/pydantic_ai_slim/pydantic_ai/capabilities/abstract.py`
- `pydantic-ai/pydantic_ai_slim/pydantic_ai/capabilities/process_event_stream.py`
- `pydantic-ai/pydantic_ai_slim/pydantic_ai/capabilities/resolve_model_id.py`
- `pydantic-ai/pydantic_ai_slim/pydantic_ai/toolsets/_dynamic.py`
- `pydantic-ai/pydantic_ai_slim/pydantic_ai/toolsets/filtered.py`
- `pydantic-ai/pydantic_ai_slim/pydantic_ai/toolsets/combined.py`

关键事实：

1. 当前主路径是普通 `Agent` 加 `capabilities=[TemporalDurability(...)]`，不是旧的 `TemporalAgent` wrapper。`TemporalDurability` 会在 `for_agent()` 时发现 Agent 的 model、name、deps type 和构造期 toolsets。
2. Agent 必须在 Workflow 外、通常在 module/worker setup 阶段构造，且需要稳定且唯一的 Agent name。Kairos 固定为 `kairos-agent-v1`。
3. `TemporalDurability` 当前构造参数包含 `models`、`event_stream_handler`、`name`、`deps_type`、`activity_config`、`model_activity_config`、`event_stream_handler_activity_config`、`toolset_activity_config`、`run_context_type`。Agent 名称和工具集由绑定阶段发现。
4. `PydanticAIWorkflow` 只是提供 `__pydantic_ai_agents__` 的 typing base；Workflow 会声明 `__pydantic_ai_agents__ = [kairos_agent]`。Worker 通过当前 `PydanticAIPlugin()` 自动发现并注册 agent Activities。没有手写一套 model/tool durable wrapper。
5. Temporal engine 的 `unsupported_runtime_toolset_kinds` 包含 `function`、`mcp`、`dynamic`。因此 `agent.run(toolsets=[...])` 在 durable workflow 中不能加入执行型 runtime toolset；构造期注册的 toolset 才能被 Temporal wrapper 使用。
6. `DynamicToolset` 的实际签名是 `DynamicToolset(toolset_func, *, per_run_step=True, id=None)`。Temporal 的 `temporalize_dynamic_toolset(...)` 会注册稳定命名的 `get_tools`、`validate_args`、`call_tool` Activities；真实 resolver、toolset 创建、校验和调用都在这些 Activity 里进行。Kairos workspace dynamic toolset 固定 ID 为 `kairos-workspace-v1`，并且不能将其 Temporal config 设为 `False`。
7. `FunctionToolset` 支持稳定 `id` 和单工具 metadata。Kairos 的 `fetch_url` 使用构造期 `FunctionToolset(id='kairos-web-v1')`；TemporalDurability 自动把真实 HTTP tool call 路由到 Activity。
8. `ResolveModelId` 解析器收到 `ModelResolutionContext(agent, deps)`，适合把 ID 化 model config 映射到 worker 侧模型实例。解析器必须对 `(model_id, deps)` 确定，不能在 Workflow 内做外部 I/O。API key 只由 worker 进程从 CredentialVault/运行时配置取得。
9. `ProcessEventStream` 在 durable context 中的 handler 是 Workflow/flow 侧确定性代码；不适合 DB、HTTP、SSE 等副作用。`TemporalDurability(event_stream_handler=...)` 则为事件处理建立 Activity。Kairos 仅使用后者，并在 handler Activity 中写入 bounded Event metadata。

### 2.3 Harness API 事实

已核对：

- `pydantic-ai-harness/pydantic_ai_harness/filesystem/_capability.py`
- `pydantic-ai-harness/pydantic_ai_harness/filesystem/_toolset.py`
- `pydantic-ai-harness/pydantic_ai_harness/shell/_capability.py`
- `pydantic-ai-harness/pydantic_ai_harness/shell/_toolset.py`
- planning、code_mode、experimental/acp、tool_output_limits 目录及相关 README/docs。

`FileSystem` 当前使用 `root_dir`、`allowed_patterns`、`denied_patterns`、`protected_patterns`、大小上限参数和 `read_only`；`read_only=True` 通过官方 `FilteredToolset` 暴露 `read_file`、`list_directory`、`search_files`、`find_files`、`file_info`。内部先做 `Path.resolve()` / `os.path.realpath()` containment 检查，拒绝 traversal 和 symlink escape。Kairos 不复制 FileSystem 源码。

`Shell` 当前使用 `cwd`、`allowed_commands`、`denied_commands`、`denied_operators`、`env`、`denied_env_patterns`、`persist_cwd`、timeout 和 output limit。Harness 的 command check 是 best-effort safety boundary，不被当成完整 OS sandbox；Kairos 在其上加入 OS-aware allowlist、destructive denylist、`LLM_API_KEY_ENV_PATTERNS` 和 Kairos secret patterns。Windows 不假定 `ls`、`grep`、`cat` 存在，按当前机器可执行文件选择命令。

Harness Playwright docs 明确拒绝把 live Chromium page 与 `TemporalDurability` 直接挂在同一 durable main Agent 上。Kairos 本轮不注册 Playwright；未来应使用 Durable main Agent → Browser Tool Activity → non-durable Browser Agent → PlaywrightBrowser。

### 2.4 Temporal Python SDK 事实

已核对：

- `Temporal/temporalio/client/`
- `Temporal/temporalio/workflow/`
- `Temporal/temporalio/activity.py`
- `Temporal/temporalio/worker/`
- `Temporal/temporalio/worker/workflow_sandbox/`
- `Temporal/temporalio/converter/`
- `Temporal/temporalio/plugin.py`
- `Temporal/temporalio/testing/`

当前 `Client.connect('localhost:7233', ...)` 连接开发 Temporal；`Worker(client, task_queue=..., workflows=..., activities=..., plugins=...)` 注册工作流和 Activities。PydanticAIPlugin 负责 Pydantic payload converter、sandbox passthrough 和 agent Activities。Workflow 中只使用 `workflow.execute_activity`/upstream 的 durable operation；网络、数据库、文件和 shell 不出现在 Workflow 函数体。

HTTP tool 使用带 `maximum_attempts` 的 `RetryPolicy`，只把 transient transport/status failure 归入 retry；4xx 通过非 retryable 的业务错误返回 Agent，不配置无限重试。Tool result 和 event payload 都限制大小，避免把大 HTML 或 workspace 文件内容进入 Temporal History。

### 2.5 兼容性限制与采用结论

- 本轮不能把 Harness Playwright 与 durable Agent 直接组合。
- 本轮不能在 `agent.run()` 里 runtime 注入 `FunctionToolset`、`DynamicToolset` 或 MCP 执行型 toolset。
- Dynamic workspace resolver 的 DB lookup 和 FileSystem/Shell I/O 只能由 upstream 生成的 Activities 承载。
- Event handler 的 DB/SSE publish 不直接运行在 Workflow deterministic code；API 只读取 PostgreSQL 中已经 committed 的 event。
- `TemporalDurability` 的 `models` 只接受预注册 model instance；按 workspace/user 解析的模型使用 model-name string + `ResolveModelId`，worker 侧基于 ID 解析 secret。
- 当前根目录没有已有 CredentialVault/ModelProvider 实现可复用；Kairos 只实现最小 `ProviderResolver` port 和环境/本地 vault adapter，不建立第二套 Agent/provider framework。后续接入已有业务 CredentialVault 时替换 adapter，不修改 Agent loop。

没有发现需要 patch upstream 的问题；本轮不修改 upstream。

## 3. Existing Code Reuse Map

| 现有内容 | 结论 | 落地方式 |
|---|---|---|
| 历史 Auth/Owner isolation 决策 | 复用业务约束 | API 使用 owner-scoped service；本地 slice 用显式 user ID header，生产 auth 保留为后续接入点，不绕过 owner check |
| Provider/Credential 约束 | 复用安全边界 | `model_config_id`/`credential_id` 只进入 worker resolver；Secret 不进入 deps/history/log/SSE |
| Task、状态机、Event metadata 决策 | 复用 | 建立最小 SQLAlchemy/Alembic domain：Task、TaskRun、Workspace、AgentEvent，状态变更集中在 service |
| MinIO 约束 | 保留接口 | Compose 提供 MinIO；本轮不把 bounded tool result 放入 MinIO，也不实现 Artifact renderer |
| 历史静态 Plan DAG | 不作为执行核心 | Agent 通过 Pydantic AI loop 自主决定 tool call；后续 planning 只能作为 capability，不另建 executor |
| 三个 upstream 源码 | 只读复用 | path dependency + public API；不向 upstream 提交 Kairos 代码 |

## 4. Architecture Changes

### 4.1 唯一执行路径

```text
Vue 3 Task Chat
  → FastAPI owner-scoped API
  → Temporal Client.execute_workflow
  → KairosAgentWorkflow (deterministic orchestration)
  → kairos-agent-v1 (Pydantic AI Agent)
  → TemporalDurability
  → Pydantic AI generated model/tool/dynamic-toolset Activities
  → real httpx / Harness FileSystem / Harness Shell
  → typed bounded tool result
  → Pydantic AI observe and continue loop
  → final result
  → event handler Activity writes Kairos EventEnvelope to PostgreSQL
  → FastAPI SSE reads committed events
  → Vue timeline
```

只有一套 Agent loop、tool dispatcher 和 durable bridge：由 Pydantic AI、Harness、TemporalDurability 提供。Kairos 只提供 domain deps、resolver、业务 tool、workspace service、event envelope、API/UI。

### 4.2 Agent 与 deps

唯一主 Agent 的稳定身份是 `kairos-agent-v1`。`KairosAgentDeps` 是 Pydantic model，只包含：

`user_id`、`task_id`、`task_run_id`、`spec_version_id | None`、`workspace_id | None`、`workspace_permission`、`model_config_id`、`search_provider_config_id | None`。

它不包含 `AsyncSession`、HTTP client、Playwright、Redis/S3 client、API key、credential plaintext、file/process handle。Workflow input 也只传这些 ID、enum、primitive 和 prompt。

### 4.3 Model/provider

Agent default model 是可配置的 model-name string。`ResolveModelId` 在 worker 活动侧以 `model_config_id` 为 lookup key 调用 `ModelProviderResolver`；resolver 只读取 worker-local credential source，并构造本轮真实 provider model。没有有效 credential 时，real-model acceptance 明确为 blocked，不使用 TestModel 冒充 Gate B/D/E；TestModel 只用于局部 deterministic integration test。

### 4.4 Workspace

Task 只绑定 `workspace_id`，不把 root path 直接复制进每个 tool arg。动态 toolset 的 factory 从 serializable deps 取得 workspace ID，由 Temporal 动态 toolset Activity 读取 owner-scoped workspace metadata，调用统一 `WorkspacePathResolver`，再创建：

- `FileSystem(root_dir=canonical_root, read_only=...)`；
- READ_WRITE/LOCAL_FULL_ACCESS 时创建 `Shell(cwd=canonical_root, ...)`；
- READ_ONLY 使用 Harness FileSystem 官方 read-only filter；NONE 返回 `None`。

resolver 只在 worker Activity 中执行；FileSystem 本身负责最终 containment。Workspace A/B 测试使用两个实际临时目录，并验证 `../`、absolute path、symlink/junction escape 都被拒绝。

### 4.5 Event/SSE

Kairos 定义独立 `EventEnvelope`，当前至少有 `run.started`、`tool.started`、`tool.completed`、`tool.failed`、`run.completed`、`run.failed`。Pydantic AI FunctionToolCall/Result event 只在 Activity handler 中映射为 envelope；Vue 不依赖 Pydantic AI 内部 class。SSE 只读取 committed PostgreSQL events，不能从 Workflow 直接 publish。

## 5. Exact File Map

### 5.1 Application/backend

- `kairos-app/backend/pyproject.toml`：Python 依赖、`pydantic-ai-slim`/Harness/Temporal 本地 path source、lint/test 命令。
- `kairos-app/backend/app/config.py`：local-only 配置和 secret 名称，不记录 secret value。
- `kairos-app/backend/app/db.py`：SQLAlchemy async engine/session，PostgreSQL 连接。
- `kairos-app/backend/app/models.py`：Owner-scoped Task、TaskRun、Workspace、AgentEvent ORM。
- `kairos-app/backend/app/domain.py`：permission/status enum、transition 校验、bounded envelope。
- `kairos-app/backend/app/repositories.py`：owner-scoped数据库访问。
- `kairos-app/backend/app/workspace.py`：WorkspacePathResolver、WorkspaceService、OS-aware shell config、LocalFolderPicker abstraction。
- `kairos-app/backend/app/provider.py`：ModelProviderResolver port 和 worker-local env/vault adapter；不携带明文 secret 到 deps。
- `kairos-app/backend/app/agent/deps.py`：serializable `KairosAgentDeps`。
- `kairos-app/backend/app/agent/tools.py`：typed bounded `fetch_url` 与 `FetchUrlResult`，HTTP I/O 在 tool Activity。
- `kairos-app/backend/app/agent/events.py`：EventEnvelope、Pydantic AI event mapper、Temporal event handler Activity。
- `kairos-app/backend/app/agent/runtime.py`：module-level 唯一 Agent、stable toolsets、TemporalDurability、ResolveModelId。
- `kairos-app/backend/app/workflows.py`：`KairosAgentWorkflow`、workflow input/result、run event Activity 调度；不执行 I/O。
- `kairos-app/backend/app/worker.py`：Temporal Client/Worker、`PydanticAIPlugin()`、普通 domain Activities。
- `kairos-app/backend/app/api.py`：FastAPI routes、Temporal workflow start/query、SSE read path。
- `kairos-app/backend/alembic.ini`、`alembic/env.py`、`alembic/versions/0001_phase02_foundation.py`：migration。

### 5.2 Frontend

- `kairos-app/frontend/package.json`、`tsconfig*.json`、`vite.config.ts`：Vue 3 TypeScript strict build。
- `kairos-app/frontend/src/api.ts`：typed API/SSE client。
- `kairos-app/frontend/src/App.vue`：现有 Task Chat 位置（当前无旧页面，故为唯一最小页面），workspace selector、permission、prompt、timeline、answer。
- `kairos-app/frontend/src/main.ts`、`src/style.css`：启动与最小可读布局。

### 5.3 Infra/docs/tests

- `kairos-app/infra/compose/compose.yaml`：PostgreSQL、Temporal auto-setup server、Temporal UI、MinIO，仅本地开发。
- `kairos-app/infra/scripts/up.ps1`、`down.ps1`、`health.ps1`、`smoke.ps1`：清晰的本地命令和 health check。
- `kairos-app/.env.example`、`.gitignore`：不提交 secret。
- `kairos-app/docs/upstream-lock.md`：三处 repo/branch/SHA/Python lock。
- `kairos-app/docs/architecture/agent-runtime.md`：Agent/Temporal/event 架构与旧 DAG 冲突说明。
- `kairos-app/docs/architecture/workspace.md`：Workspace path/permission/DynamicToolset/secret 边界。
- `kairos-app/docs/operations/local-dev.md`、`local-run.md`、`README.md`：Windows 本地运行、health、worker/API/Vue 启动。
- `kairos-app/backend/tests/`：deps、permissions、DynamicToolset、containment、Shell、Temporal test server、HTTP。
- `kairos-app/tests/smoke/`：真实 Temporal/PostgreSQL/MinIO/HTTP/real-model/vertical-slice 脚本，缺 credential 或 Docker 时只报告 BLOCKED。

## 6. Phase 0 tasks

1. 初始化 `kairos-app` Git repo，创建 feature branch；写入 `.gitignore`，确保不追踪 `.env`、credential、workspace fixture、build output。
2. 写 backend path dependencies，确认 `import pydantic_ai`、`import pydantic_ai_harness`、`import temporalio` 的版本和来源；Pydantic AI/Harness 使用本地 editable source。Temporal SDK 当前本地 checkout 的 editable 构建依赖 Rust/maturin bridge，而开发机没有 Rust 且 rustup 下载被网络 TLS 阻断，因此运行依赖使用与本地 checkout 版本完全一致的 `temporalio==1.32.0` wheel，并在 upstream lock 中同时记录 source SHA 和 wheel 版本；不为 editable 依赖强行安装工具链，不改 upstream lock。
3. 写 Alembic foundation migration，包含 owner-scoped Task/TaskRun/Workspace/AgentEvent 所需字段和索引。
4. 写 Docker Compose，固定正式 Temporal Server/UI、PostgreSQL、MinIO 镜像版本；为 PostgreSQL/Temporal/UI/MinIO 提供 health checks；不引入 Kubernetes、TLS、反向代理或额外服务。
5. 写 local-dev/local-run/README 和 PowerShell helper。API、worker、frontend 默认本机运行，基础设施由 `docker compose -f infra/compose/compose.yaml up -d` 启动。
6. 运行 Phase 0 gate：`docker compose ps`、PostgreSQL connection、Temporal Client connection、Temporal UI HTTP、MinIO health、Python imports、三 upstream `git status --short`。Docker daemon 不可用时记录 BLOCKED，不伪造 PASS。

## 7. Phase 1 tasks

1. 实现 `KairosAgentDeps`、EventEnvelope、TaskRun input/result，做 Pydantic round-trip 和 secret field negative test。
2. 实现 `FetchUrlResult`，对 URL scheme/redirect/response bytes/text preview/content type/title 做明确校验和上限；httpx 只在 FunctionToolset 的 Activity 内执行；4xx non-retryable，合理连接/5xx transient 使用有限 RetryPolicy。
3. 构造唯一 `kairos_agent`：default model string、`FunctionToolset(id='kairos-web-v1')`、`TemporalDurability`、event handler capability；不加第二 Agent、不加静态 planner executor。
4. 定义 `KairosAgentWorkflow(PydanticAIWorkflow)`，声明 `__pydantic_ai_agents__ = [kairos_agent]`，Workflow 内只调用 `agent.run()` 和记录 event Activities；使用 `PydanticAIPlugin()` 注册 upstream activities。
5. 用 `ResolveModelId` 连接 provider adapter；model config ID 和 credential ID 只做 Activity-side lookup，secret 不进入 Temporal payload。缺 credential 时让真实 acceptance 明确失败/blocked。
6. 在 event handler Activity 将 FunctionToolCall/Result 映射成 `tool.started/completed/failed`，将 run start/end 由 Workflow 调度 event Activity 写入 PostgreSQL。bounded payload 只保存工具名、状态、摘要、长度和错误类别，不保存 key 或大 HTML。
7. 实现 FastAPI 创建 Task/启动 Workflow/读取 Task 状态/SSE endpoints；SSE 轮询 committed events，并以 owner/task/run cursor 过滤。
8. 运行 TestModel + Temporal test environment 的 loop test，证明 model→tool→observe→model；该测试只能作为自动化测试，不能作为真实模型 Gate。
9. 在 Docker/有效 credential 存在时运行真实 `https://example.com` workflow，采集 Workflow ID、Temporal Activity 名、fetch result、最终回答；否则记录 `real-model acceptance blocked by credential`。

## 8. Phase 2 tasks

1. 实现 Workspace model/service，字段至少为 workspace ID、owner ID、display name、root path、permission mode、enabled、created/updated timestamps；所有读取和绑定 owner-scoped。
2. 实现 Windows-aware `WorkspacePathResolver`：normalize/resolve/realpath，确认存在且为 directory，处理 drive/case/junction/symlink；API、Agent、Tool 不各自拼 root path。
3. 实现 `DynamicToolset(resolve_workspace_toolset, per_run_step=False, id='kairos-workspace-v1')`（实际参数名按当前源码为 `toolset_func`）；resolver 在 Temporal generated `get_tools/call_tool/validate_args` Activities 中加载 metadata 并构造 Harness FileSystem/Shell。
4. 实现 permission policy：NONE 返回 None；READ_ONLY 使用 FileSystem read-only filtered toolset 且不暴露 Shell；READ_WRITE 暴露 FileSystem+Shell；LOCAL_FULL_ACCESS 只在 `KAIROS_LOCAL_MODE=true` 且用户显式选定时允许，仍保留 protected patterns 和 secret env scrub。
5. 按 Windows/POSIX 检查实际 executable 生成 shell allowlist；保留 Harness destructive denylist，加入 provider/db/session/credential secret patterns；设置 `cwd=root`、`persist_cwd=False`，不关闭 Harness checks。
6. 把短 workspace context（available、display name、permission）作为 system/instruction context 传给 Agent，不把目录树或文件内容预加载到 prompt。
7. 在当前唯一 Vue Task Chat 中增加 Workspace name/path、permission、Select/Change/Disconnect；实现 `LocalFolderPicker` abstraction，native picker 仅 local-development，unsupported 时 manual absolute path，不使用 `webkitdirectory`。
8. 运行 A/B 隔离测试：真实两个临时目录、两个 owner/task/run，分别执行 list/read；A 对 B 的 relative/absolute/symlink/junction 路径均失败。再通过 Agent tool test 由模型/Tool 创建并重新读取 `kairos-agent-test.md`；Route/test code 不直接伪造文件成功。
9. 当 Docker、real model、真实 worker 可用时运行 Vue→FastAPI→Temporal→Agent→Workspace→SSE vertical smoke；不满足时按 Gate 证据标记 BLOCKED。

## 9. Tests

只跑受影响模块和真实 smoke，不运行无意义的整仓长测试。计划中的实际命令如下，最终报告只列实际执行过的命令：

### Unit/targeted

- `uv run --project backend pytest backend/tests/test_deps.py -q`
- `uv run --project backend pytest backend/tests/test_workspace.py -q`
- `uv run --project backend pytest backend/tests/test_dynamic_toolset.py -q`
- `uv run --project backend pytest backend/tests/test_temporal_agent.py -q`
- `uv run --project backend pytest backend/tests/test_http_tool.py -q`
- `uv run --project backend ruff check backend/app backend/tests`
- `npm --prefix frontend run typecheck`
- `npm --prefix frontend run build`

### Real infrastructure/smoke

- `docker compose -f infra/compose/compose.yaml up -d`
- `pwsh -File infra/scripts/health.ps1`
- `pwsh -File infra/scripts/smoke.ps1`
- `uv run --project backend python -m app.worker`
- `uv run --project backend python -m app.api`
- `npm --prefix frontend run dev`
- `uv run --project backend python tests/smoke/real_agent_smoke.py`
- `uv run --project backend python tests/smoke/vertical_slice_smoke.py`

真实 smoke 的成功条件必须能看到 Temporal 中至少一个 model request、真实 fetch/workspace tool activity、返回 tool result、第二次 model request 和 final result。没有真实 provider credential 时禁止替换为 hardcoded/TestModel 作为 Gate 证据。

## 10. Acceptance Gates

### GATE A — Source Foundation

PASS：lock 中三个 SHA 固定、upstream clean、path imports 正确、PostgreSQL/Temporal/UI/MinIO health 全部通过。  
BLOCKED：Docker daemon 或服务不可用；说明具体服务和诊断。  
FAIL：lock/import/path 或 upstream clean 任一不满足。

### GATE B — Real Agent

PASS：真实模型、真实 Pydantic AI loop、真实 Temporal model/tool Activities、真实 tool result、继续 model request、最终回答都有证据。  
BLOCKED：没有有效 real-model credential，报告原文 `real-model acceptance blocked by credential`；TestModel 只计 unit test。  
FAIL：有 credential 但真实链路失败，附 Workflow/Activity 错误。

### GATE C — Workspace

PASS：Task A/B 并行绑定 A/B，A/B 只能读取自身，traversal/absolute/symlink/junction 都被拒绝。  
BLOCKED：真实 worker/Temporal 或当前 OS 能力不可运行。  
FAIL：任何跨目录读取成功。

### GATE D — File Modification

PASS：Agent tool 自己通过 durable Activity 创建、重新读取并确认 `kairos-agent-test.md`；文件真实存在于绑定 root。  
BLOCKED：real model 或 durable worker 不可用。  
FAIL：Route、测试代码或静态脚本写文件冒充 Agent。

### GATE E — Web Vertical Slice

PASS：Vue 选择 workspace/permission、发 prompt、看到 event/tool 状态、得到 final answer、文件真实存在。  
BLOCKED：基础设施、worker、real credential 或浏览器运行环境缺失。  
FAIL：前端只显示静态/硬编码 timeline 或 API 走非 Temporal fallback。

### GATE F — No Duplicate Architecture

PASS：只有 Pydantic AI loop、Harness tools、TemporalDurability bridge；没有第二 Agent loop/dispatcher/DAG executor/to_web UI/fake path；Playwright 未直接挂 durable Agent。  
FAIL：代码审计发现重复执行路径或伪造成功路径。  
本 gate 不因环境缺失而 BLOCKED；它可以通过静态源码审计独立判定。

## 11. Rollback / failure handling

- 每个阶段以小范围 commit 提交在 `kairos-app` feature branch；不 push、不 merge、不修改 upstream。
- Phase 0 若 dependency resolver 或 Compose 失败，保留计划/lock/docs，撤销未完成 app commit，不改 upstream lock；记录 blocker。
- Phase 1 若模型/Activity serialization 失败，先保持 Agent/Workflow 单一路径，定位 payload/schema/registration；禁止添加旁路 HTTP tool executor 或 fallback fake。
- Phase 1 若 provider credential 缺失，保留 TestModel unit coverage，但 Gate B/D/E 标 BLOCKED，不修改代码伪造 acceptance。
- Phase 2 若 workspace containment 失败，禁止继续开放写权限；先修复统一 resolver 或停在 READ_ONLY/NONE，不能在 Route 中加入临时路径拼接。
- 若确实发现 upstream public API 缺陷，先提供最小复现测试，再提交单独 adapter/issue note；只有 adapter 无法解决且测试证明必须 patch 时才建立单独 patch 文件。本轮目前没有这样的缺陷。
- 不删除历史 Auth/Provider/Task/Evidence/MinIO 业务意图；由于当前没有这些代码，只通过边界和 extension point 预留后续接入，不提前实现后续产品模块。

## Self-review before implementation

- 与业务日志冲突：无；Owner、状态、DB/Temporal truth、SSE committed event 边界保持。
- 是否重复 Pydantic AI Agent loop：否；调用 `agent.run()`。
- 是否重复 TemporalDurability：否；由 `TemporalDurability`/`PydanticAIPlugin` 注册 Activities。
- 是否出现两个 Workflow truth source：否；唯一 `KairosAgentWorkflow`，静态 plan 不执行。
- 是否出现 mock/fake：计划只允许局部 TestModel unit；real gates 不接受 mock、hardcoded、setTimeout 或 Route 写文件。
- Tool I/O 是否进入 Activity：是；fetch、workspace resolve、FileSystem、Shell、event persistence 均由 upstream/普通 Activity 承载。
- AgentDeps 是否可序列化：是；只含 IDs、enum、primitive、Pydantic serializable values。
- Toolset ID 是否稳定：是；`kairos-web-v1`、`kairos-workspace-v1`，Agent `kairos-agent-v1`。
- 是否错误地运行时注入执行 Toolset：否；web 和 workspace 都是构造期注册；workspace 依赖 upstream DynamicToolset wrapper。
- Workspace 是否真正隔离：是；owner-scoped metadata + canonical resolver + Harness containment + A/B traversal/symlink tests。

## Execution decision

自审没有发现需要用户重新选择架构的缺口；直接进入执行阶段。任何真实环境缺失只影响对应 Gate 状态，不通过伪造数据掩盖。
