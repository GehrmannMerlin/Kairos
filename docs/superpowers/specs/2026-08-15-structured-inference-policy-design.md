# Structured Inference Policy Design

> 日期：2026-08-15
>
> 状态：已批准
>
> 关联模块：M-03 / M-07 / M-08 / M-16 / M-17 / M-18
>
> 目标发布：`v0.1.6`（以合并后的实际 patch tag 为准）

## 1. 背景与已确认根因

Production 当前 release identity：

- release tag：`v0.1.5`
- deployed Git SHA：`a3df2245c8a09e539b0b4294742543abd6cd7a0c`
- public URL：`https://app.kairos.ac.cn/`
- server：`47.238.145.24`

事故链已经通过 Production 日志、Task 数据与受控对照实验确认：

1. Goal Understanding 使用 DeepSeek，约 23,645 ms，成功。
2. Spec Confirm 成功。
3. `POST /tasks/:id/plan` 约 45.54 秒失败。
4. PlanVersion 未生成，Validator、Temporal、Worker、Crawler 和 Search Provider 均未到达。
5. 底层异常为 `httpx.ReadTimeout`，被错误包装成 `ProviderNetworkError("推理请求失败")`。

Plan prompt 大小约为 system 6,377 chars、user 1,160 chars、request body 10,280 bytes，显著大于 Goal Understanding。保持 Provider、model、credential、base URL、prompt 与 45 秒 timeout 不变，只切换 DeepSeek thinking：

| 实验 | model call #1 | repair | total | 结果 |
|---|---:|---:|---:|---|
| thinking 默认开启 | 约 135s+ | 约 135s+ | 约 272s | 不满足同步生命周期 |
| `thinking.type=disabled` | 约 5,836ms | 约 9,961ms | 约 16,364ms | HTTP 200 |

因此根因是 DeepSeek 默认 Thinking 与 Kairos 的受约束结构化 Plan JSON 生成不匹配，并在 45 秒 read timeout 处暴露。修复不能靠盲目放大 timeout，也不能在 `PlanGeneratorAgent` 中 hardcode Provider 私有参数。

## 2. 设计目标

本轮必须完成：

- capability-driven structured inference policy；
- DeepSeek `PLAN_STRUCTURED` 关闭 thinking，其他 intent 保持现状；
- 共享 inference client factory 与统一 settings source；
- timeout、network、HTTP、inference 与 internal error 的稳定分类；
- connect/read phase-aware retry；
- 带可执行 Validator context 的单次 Plan repair；
- 数学一致的 Plan lifecycle timeout budget；
- PlanVersion 单次持久化、幂等 Workflow start recovery 与前端 server-state reconcile；
- 安全 structured logging；
- TDD、真实 Staging DeepSeek 三轮、GHCR Production 发布与真实浏览器验证。

## 3. 非目标

本轮明确不做：

- 不把 Plan Generation 改成 Temporal async + SSE；
- 不实现 LLM Intent → Deterministic Plan Compiler；
- 不重写整个 Provider subsystem；
- 不放宽 Node schema、resource compatibility、DAG、Spec boundary 或 risk policy；
- 不改变 Goal Understanding 已验证的 thinking 行为；
- 不把 DeepSeek、`thinking=false` 或具体 timeout 数值写成产品业务决定；
- 不向 custom OpenAI-compatible Provider 猜测性发送 DeepSeek 私有扩展；
- 不引入无限 repair、无限 retry 或自动重复计费。

## 4. 当前实现风险

当前代码存在以下直接风险：

- `ModelInferenceClient.generate()` 以 generic `except Exception` 把意外异常包装成 `NETWORK_ERROR`。
- `ProviderNetworkError` 同时代表 connect、read timeout、connect failure 与多类 HTTP failure，retry 无法按阶段决策。
- `PlanGenerationService` 通过 `ModelInferenceClient()` 提前覆盖 `PlanGeneratorAgent` 的 settings-aware 默认构造。
- 所有 OpenAI-compatible Provider 共用 request body，没有 capability boundary。
- Plan repair 只把通用 Validator issue 放进 constraints，没有原始 graph、edge contract 或参数契约。
- Pydantic AI `retries=1` 可能在显式 repair 之外产生隐藏的第三次模型调用。
- Plan route 的宽泛 `except Exception` 同时包住首次持久化和 Temporal start；首次 persist 后 start 失败时 fallback 可能再次 persist。
- 前端 Plan request 仍使用 60 秒 timeout，而一次 generate + repair 的后端生命周期可能超过它。
- Nginx `/api/` 90 秒上限小于两次 45 秒 inference 加持久化/启动开销。

## 5. 架构与职责

固定调用链：

```text
Agent / Use Case
  -> InferenceIntent
  -> resolve_inference_policy(provider, model, intent)
  -> ProviderRequestPolicy
  -> ModelInferenceClient
  -> HttpClient transport
```

职责边界：

- Agent 说明推理用途，只传 intent 和安全调用上下文。
- Provider Registry 声明 Provider 明确支持的 capability。
- Policy resolver 根据 provider、model、intent 返回最小 request policy。
- ModelInferenceClient 把 policy 翻译成各协议 wire body，并负责 typed error translation、retry 与 inference telemetry。
- Transport 只处理 method、URL、headers、params、JSON body、timeout 和 HTTP response，不认识 intent、DeepSeek 或 Plan。
- Deterministic Plan Validator 继续是 Plan 合法性的唯一权威。

## 6. InferenceIntent

定义稳定 enum：

```text
GOAL_UNDERSTANDING
PLAN_STRUCTURED
STRUCTURED_EXTRACTION
FREEFORM_REASONING
AGENT_REASONING
```

本轮行为：

- `PlanGeneratorAgent` 传 `PLAN_STRUCTURED`。
- `GoalUnderstandingAgent` 传 `GOAL_UNDERSTANDING`，wire behavior 不变。
- `SemanticExtractionAgent` 传 `STRUCTURED_EXTRACTION`，wire behavior 不变。
- `FREEFORM_REASONING` 与 `AGENT_REASONING` 只冻结 vocabulary，不新增调用路径。

Intent 名称不得包含 DeepSeek、OpenAI、Gemini、Claude 等 Provider 名称。

## 7. Provider Capability 与 Request Policy

`ProviderDefinition` 增加最小类型化 inference capability，当前只需要表达 Provider 是否明确支持 thinking control。只有内置 DeepSeek 声明支持；协议相同不等于 capability 相同。

`ProviderRequestPolicy` 只包含当前真实需要的字段：

```text
thinking_mode: disabled | null
response_format: json_object | null
max_output_tokens: int | null
```

不得把几十个潜在 Provider 参数加入通用 DTO。未来只有出现已验证需求时才扩展字段。

Policy matrix：

| Provider / Intent | thinking | response format | max output tokens |
|---|---|---|---:|
| DeepSeek + `PLAN_STRUCTURED` | disabled | json object | 4096 |
| DeepSeek + `GOAL_UNDERSTANDING` | 不发送 | 保持当前 | 保持当前 |
| DeepSeek + `STRUCTURED_EXTRACTION` | 不发送 | 保持当前 | 保持当前 |
| 不支持 thinking control 的 Provider | 不发送 | 保持当前 | 保持当前 |
| custom OpenAI-compatible | 不发送 | 保持当前 JSON 行为 | 保持当前 |

Agent 不得直接传 `thinking=False`。DeepSeek 私有字段只能由 capability + policy resolver 产生。

## 8. `max_tokens=4096` 的验证依据

4096 是 Plan structured output 上限，不是 prompt token 上限。选值基于当前 `PlanGraphDraft`、10 个注册 NodeType、参数契约、资源边与 reasoning summary 的最大代表性输出。

实施时新增完整 10-node representative fixture：

- 每个当前注册 node type 至少出现一次；
- 使用完整合法参数；
- 包含完整 depends_on 与 resource_refs；
- 包含中文字段与合理 reasoning summary；
- 序列化为与 wire output 相同的紧凑 JSON。

验收要求：fixture 的保守 token estimate 不超过 2048，4096 提供至少 2 倍余量；完整 JSON 可解析并通过 Pydantic schema，不出现截断。如果测量证明 4096 不满足该要求，合并前必须根据测量结果调整 policy 上限并更新本 spec/测试，不能依赖 repair 掩盖截断。

## 9. Shared ModelInferenceClient Factory

新增唯一 factory，签名固定为：

```text
create_model_inference_client(settings, http=None)
```

Factory 统一注入：

- `provider_inference_timeout_seconds`；
- transport；
- retry policy；
- capability resolver；
- structured logging policy。

Goal、Plan、Semantic Extraction 的默认构造全部调用 factory。测试仍可直接注入 fake inference。`PlanGenerationService` 必须把 `inference` 原样交给 `PlanGeneratorAgent`；未注入时由 Agent/factory 创建，不得再次写 `ModelInferenceClient()` 默认构造。

`ModelInferenceClient.generate()` 接受 `intent`，并可接受只含 `config_id`、request/trace correlation 和 repair flag 的安全 metadata。metadata 不得包含 credential 或 prompt。

## 10. Error Taxonomy

复用 `backend/app/providers/errors.py`，新增最小 `ProviderTimeoutError`：

- stable code：`PROVIDER_TIMEOUT`
- HTTP status：504
- user message：`模型服务响应超时，请稍后重试。`
- phase：`connect`、`read` 或 logical call deadline 的 `overall`

固定映射：

| 原始事实 | Provider typed error | API code | retry view |
|---|---|---|---|
| `httpx.ConnectTimeout` | `ProviderTimeoutError(phase=connect)` | `PROVIDER_TIMEOUT` | connect timeout |
| `httpx.ReadTimeout` | `ProviderTimeoutError(phase=read)` | `PROVIDER_TIMEOUT` | read timeout |
| logical call 45s deadline | `ProviderTimeoutError(phase=overall)` | `PROVIDER_TIMEOUT` | no blind retry |
| `httpx.ConnectError` / DNS / TCP / TLS connect failure | `ProviderNetworkError` | `NETWORK_ERROR` | transient connect |
| HTTP 401/403 | `ProviderAuthFailedError` | `AUTH_FAILED` | no retry |
| HTTP 404 | `ProviderModelNotFoundError` | `MODEL_NOT_FOUND` | no retry |
| HTTP 429 | `ProviderRateLimitedError` | `RATE_LIMITED` | Retry-After + bounded jitter |
| HTTP 400 | `ProviderInferenceError` | `PROVIDER_INFERENCE_ERROR` | no transport retry |
| explicit Provider 5xx | `ProviderNetworkError("HTTP_STATUS")`，message 同时带实际状态码 | `NETWORK_ERROR` | bounded transient retry |
| HTTP success but missing/invalid required structure | `ProviderInferenceError` | `PROVIDER_INFERENCE_ERROR` | Plan semantic path only |
| unexpected `RuntimeError` / `KeyError` / `TypeError` | 不转换为 ProviderError | Internal 500 | no retry |

Transport 继续抛出原始 `httpx` exception；ModelInferenceClient 在进入 reliability classifier 前转换成 Provider typed exception。移除把所有意外异常包装为 `ProviderNetworkError` 的 generic catch。

`HttpResponse` 增加 response headers，以便 client 安全读取 `Retry-After`。Transport 不解释该 header。

## 11. Retry Semantics

Reliability view 增加可区分 connect/read timeout 的最小类别。固定策略：

- ConnectTimeout：最多 2 次总 attempt，即首次失败后最多自动重试 1 次。
- ConnectError：最多 2 次总 attempt。
- ReadTimeout：不自动 retry，model call count 必须为 1。
- logical call overall timeout：不自动 retry。
- 429：遵循 `Retry-After`；无该 header 时用 bounded exponential backoff + jitter；总 attempt 沿用现有有界 Provider retry policy。
- 401/403、404、400、typed structure failure：不做 transport retry。
- Plan Validator semantic failure：进入显式 Plan repair，而不是 transport retry。

单次逻辑模型调用的 45 秒 deadline 覆盖 limiter wait、Retry-After wait 和所有 transport attempt。Retry 决策不能让每个 attempt 重新获得完整 45 秒；剩余 deadline 不足以安全等待或发起请求时立即停止。

## 12. Plan Generation 与 Repair Contract

每个 Plan lifecycle 最多两次模型调用：

1. initial `PLAN_STRUCTURED` call；
2. 仅在第一次 graph 可解析但 Validator 返回 `INVALID` 时的一次 repair call。

Pydantic AI 隐式 output retry 设为 0，避免初次调用、隐式 retry、显式 repair 合计三次或更多。JSON/Pydantic 无法形成 `PlanGraphDraft` 时转换为 `PROVIDER_INFERENCE_ERROR`；本轮不为不可解析 raw output 新建第二套 repair 机制。

新增 `build_plan_repair_context(graph, issues, registry)`。它重新读取原始 graph 与 NodeRegistry，为每个可修复 issue 生成可执行 context。

`RESOURCE_EDGE_INCOMPATIBLE` 至少包含：

```json
{
  "issue_code": "RESOURCE_EDGE_INCOMPATIBLE",
  "edge": {"from_node_id": "node_3", "to_node_id": "node_4"},
  "current_resource_kind": "record",
  "source_allowed_outputs": ["snapshot"],
  "destination_allowed_inputs": ["snapshot", "spec"],
  "valid_intersection": ["snapshot"]
}
```

`PARAMETER_SCHEMA_INVALID` 至少包含：

- node id/type；
- current parameters；
- allowed parameter keys；
- 每个 key 的 type 与 required；
- Validator issue message/path。

Repair prompt 同时包含 original graph、structured issues 和以下约束：

- preserve valid nodes；
- preserve valid parameters；
- only repair listed violations；
- do not expand Spec；
- do not introduce unregistered nodes；
- do not change valid edges；
- output one complete `PlanGraphDraft` JSON object。

Repair 后只再运行一次 Validator。第二次仍 `INVALID` 时返回真实 issue，不再调用模型，不启动 Workflow。Validator 永远不能被模型声明覆盖。

## 13. Plan Persistence 与 Workflow Start Recovery

Plan route 必须把持久化和 Workflow start 分成两个阶段：

1. 根据 Validator outcome 计算 fingerprint；
2. PlanVersion 只持久化一次；
3. 只有 `VALID` / `REQUIRES_APPROVAL` 进入 start；
4. Temporal 获取或 start 失败时保留已持久化 PlanVersion，但不得 fallback persist；
5. 返回 domain/API stable code `PLAN_START_FAILED`（HTTP 503），携带 plan version 和可恢复语义；前端显示“执行计划已生成，但工作流启动失败，可重试启动”，不得显示推理失败。

新增 owner-safe start recovery command：

```text
POST /tasks/{task_id}/plans/{plan_version}/start
```

该 command：

- 不调用模型；
- 只接受当前 task 的合法 PlanVersion；
- 对 task row 做数据库锁定，查询该 plan 的 active/pending Run；
- active/pending Run 已存在时复用其 run id；
- 不存在时创建一个 pending Run；
- 使用稳定 workflow id 启动；
- `WorkflowAlreadyStarted` 转换为读取/返回现有服务器事实；
- 对 cancelled/terminal run 的显式新运行语义保持现有产品规则，不用全局 `(task_id, plan_version)` unique constraint阻止未来合法 rerun。

这一恢复路径解决 start 失败，不重新生成 Plan，不产生模型费用，也不创建第二个 PlanVersion。

Invalid PlanVersion 可以作为审计事实持久化一次，但绝不更新为 valid、绝不启动。Plan summary 返回 validation issues，前端不能把“PlanVersion 存在”单独视为成功。

## 14. Frontend Reconcile

Plan API 使用：

```text
timeoutMs: null
```

浏览器不设置比 Backend overall lifecycle 更短的业务 hard timeout，仍保留外部 AbortSignal 供导航/用户取消。

网络断开、Nginx 504 或响应丢失时，前端不得自动再次调用 Plan Generation。它只轮询 Task/Plan/Run 服务器事实：

- 新 PlanVersion + legal validation + Run/Workflow → Plan success；
- 新 PlanVersion + `INVALID` → 展示 issue codes/messages；
- legal PlanVersion + `PLAN_START_FAILED` / 无 active Run → 展示可恢复 start action；
- 无新 PlanVersion且服务器无成功事实 → 展示真实 Provider/API 错误；
- reload 后按 current PlanVersion、validation status 与 Run state 恢复页面。

Plan reconcile 使用独立 helper，不复用只观察 Chat `goal_result/error` 的 Goal Understanding reconcile 条件。任何 reconcile 都不得触发新模型 attempt。

## 15. Timeout Budget

固定层级：

| 边界 | 正常目标 | hard bound |
|---|---:|---:|
| 单次 logical provider call（含 retry/wait） | 5–20s | 45s |
| initial + Validator | 5–20s | 约 50s |
| initial + repair + Validator | 通常 <30s | 约 95s |
| Backend 完整 Plan lifecycle：inference、validation、repair、persistence、start | — | 105s |
| Reverse proxy `/api/` read/send | — | 120s |
| Frontend Plan request | — | null；由 server bounds 决定 |

105 秒按 `45 + 45 + 15` 计算；额外 15 秒覆盖 validation、repair context、DB persist 与 Temporal start。它是 safety ceiling，不是性能目标。

Backend overall deadline 超出时返回 domain/API stable code `PLAN_GENERATION_TIMEOUT`（HTTP 504），用户文案为“执行计划生成超时，请稍后查看任务状态。”如果 deadline 在 PlanVersion 已持久化后发生，reconcile 仍以 PlanVersion/Run 为事实；不得再次 persist 或重新生成。

Staging 与 Production outer Nginx 同步设置：

```text
proxy_read_timeout 120s
proxy_send_timeout 120s
```

## 16. Observability

复用现有 `app.observability.context`、structured logging 与 OTel trace correlation。

Inference events：

- `model_inference_started`
- `model_inference_completed`
- `model_inference_failed`

Plan events：

- `plan_generation_started`
- `plan_model_call_completed`
- `plan_validation_completed`
- `plan_repair_started`
- `plan_repair_completed`
- `plan_generation_completed`

安全 metadata：

- intent；
- provider family；
- model；
- config_id；
- phase；
- exception_type；
- duration_ms；
- attempt；
- thinking_mode；
- request_body_bytes；
- repair_used；
- model_call_1_ms / model_call_2_ms / total_ms；
- validator_result / issue_codes；
- request_id / trace_id；
- plan_version / run_id / workflow_id。

禁止记录：

- API Key、Authorization、credential plaintext；
- 完整 system/user prompt；
- 完整用户输入；
- 完整 Provider response；
- 可还原 Secret 的 request body。

`request_body_bytes` 只记录序列化长度。Staging/Production smoke 窗口必须执行 secret log scan。

## 17. TDD 与自动化测试

所有实现先写失败测试，再写最小代码。

### 17.1 Provider Policy

- DeepSeek + `PLAN_STRUCTURED` 发送 `thinking.type=disabled`。
- DeepSeek + `GOAL_UNDERSTANDING` 不发送 thinking。
- 不支持 thinking capability 的 Provider 不发送该字段。
- custom OpenAI-compatible 不发送 DeepSeek private extension。
- JSON response format 保留。
- 4096 max tokens 进入 request。
- full 10-node fixture 完整输出、不截断、可 parse。

### 17.2 Factory

- Goal、Plan、Extraction 使用同一 factory/settings source。
- 修改 `provider_inference_timeout_seconds` 后三者均生效。
- PlanGenerationService 不再实例化 class default client。

### 17.3 Error Taxonomy

- ConnectTimeout → `PROVIDER_TIMEOUT`, phase connect。
- ReadTimeout → `PROVIDER_TIMEOUT`, phase read。
- ConnectError → `NETWORK_ERROR`。
- 401/403 → `AUTH_FAILED`。
- 404 → `MODEL_NOT_FOUND`。
- 429 → `RATE_LIMITED` 并保留 Retry-After。
- invalid structure → `PROVIDER_INFERENCE_ERROR`。
- RuntimeError → Internal 500，不是 NETWORK_ERROR。

### 17.4 Retry

- ConnectTimeout / ConnectError bounded call count。
- ReadTimeout model call count = 1。
- 429 按 Retry-After + jitter。
- Auth / model-not-found call count = 1。
- logical call deadline 不因 retry 重置。

### 17.5 Plan Repair

- incompatible edge context 含 source output、destination input、intersection。
- parameter issue context 含 allowed keys/types/required。
- repair prompt 含 original graph 与 preserve-only constraints。
- initial invalid edge → repair valid → Validator VALID。
- second invalid → stop；总 model call count = 2。

### 17.6 Plan API / Lifecycle

- controlled fake：call #1 invalid edge、call #2 corrected graph、persist once、start once。
- Temporal start failure：PlanVersion count = 1，不进入 fallback persist。
- start recovery：复用 PlanVersion 与 pending/active Run，无模型调用。
- WorkflowAlreadyStarted：返回现有事实。
- invalid Plan：issues 可见、无 Workflow。
- overall timeout 前后持久化边界均不重复 PlanVersion。

### 17.7 Frontend

- Plan request 使用 `timeoutMs:null`。
- external AbortSignal 仍有效。
- disconnect 后 reconcile legal success。
- reconcile invalid Plan issues。
- reconcile start failure 与 recovery action。
- 无服务器事实时展示真实错误。
- reload 显示 persisted Plan success，不显示陈旧“推理请求失败”。

### 17.8 Logging / Config

- 所需 event 与 metadata 存在。
- prompt、response、API Key、Authorization 不存在。
- timeout hierarchy 为 45/105/120/null。

## 18. Staging Gate

部署 Staging 前重新完整读取 `agent-production-deployment-standards.md` 并输出：

必须明确输出 `Deployment Standard reread: PASS`，并在同一检查中列出：目标环境 `staging`、由合并 commit/tag 解析出的候选 release identity、CI 产出的 web/api/worker immutable digests，以及部署前实际运行的 Staging release 作为 rollback target。任何字段无法解析都阻止部署写操作。

必须使用真实 DeepSeek credential 与真实 product API，完成三轮正常 Plan structured inference：

1. 山东：`采集山东省人民政府官网发布的最近一个月的干部任前公示信息`。
2. 上海：同语义上海回归任务。
3. 第三轮真实正常生成，用于补足三次 latency sample。

每轮记录：

- goal_ms；
- plan_model_1_ms；
- repair_used；
- plan_model_2_ms；
- plan_total_ms；
- validation_result；
- plan_version；
- run_id；
- workflow_id；
- model/provider。

Repair Test C：优先使用真实自然触发的 `RESOURCE_EDGE_INCOMPATIBLE`。如果三轮均第一次 VALID，使用 Staging harness 注入受控 invalid edge，再通过真实 repair path 验证 INVALID → repair → VALID；正常 Plan model call 仍必须使用真实 DeepSeek。

Staging PASS 条件：

- Goal、Spec Confirm、Plan call #1、Validator、必要 repair、PlanVersion、Workflow auto-start、Run 与页面 Plan Summary 全部成功；
- 无 45 秒 ReadTimeout；
- 无“推理请求失败”；
- 无重复 PlanVersion；
- 无 Secret log leak；
- 正常延迟回到可接受范围，不能用 hard ceiling 解释长尾。

任一 Gate FAIL 即 BLOCKED，不继续 Production。

## 19. Git、CI、GHCR 与 Production

Git 流程：

```text
fix/structured-plan-inference
  -> scoped commits
  -> PR
  -> CI PASS
  -> rebase merge main
  -> Staging
  -> patch tag（预计 v0.1.6）
  -> GHCR immutable images
  -> Production
```

禁止 direct push main、force push、server source build、`docker save/load` 常规发布、git pull source、pip/npm build、scp/rsync source 或容器 hot patch。

Production 部署前再次重新完整读取 deployment standard，并记录：

- previous release；
- new release tag；
- Git SHA；
- web/api/worker digest；
- migration head；
- deploy timestamp；
- rollback target。

Production health：Web、API live/ready、Worker、PostgreSQL、Temporal 及所需 Redis/MinIO 全部 PASS。

Production browser smoke 必须真实访问 `https://app.kairos.ac.cn/`：登录、新建山东任务、Goal、Spec Confirm、Plan、Validator、Plan Summary、Workflow start。必须确认页面不显示“推理请求失败”。

同时核对 web/API container digest、reverse proxy upstream、index.html cache、asset hash、browser stale cache、service worker 与 CDN（如存在），证明 public URL 加载最新 release。

## 20. Release Traceability 与文档

OCI `org.opencontainers.image.revision` / `version` 缺失记录为 P2。如果补 labels 的改动小、测试明确且不扩大 incident 风险，可随当前 release 修复；否则建立后续 task，不阻塞本轮 P0/P1。

现有 D-008、D-013、D-029、D-038、D-076 已覆盖本轮产品原则，因此不新增 D-xxx。若实施发现必须记录稳定原则，只允许表达为：

> 结构化生成使用与任务意图匹配的 Provider capability policy，模型合法性仍由 deterministic validator 判断。

DeepSeek、thinking flag 和具体 timeout 数值只属于本技术设计，不进入 business decision。

## 21. Rollback

Rollback 使用上一 Production immutable release `v0.1.5` 的 web/api/worker digests 与兼容 migration head。发布前必须确认：

- previous compose manifest 可读取；
- previous digests 可 pull；
- 本轮 migration 若存在则具备兼容 rollback 决策；
- rollback health/readiness 与 browser smoke 命令可执行。

不得用线上源码热改替代 rollback。

## 22. 完成判定

只有以下全部真实 PASS 才能报告 `DEPLOYED`：

- Design approved；
- implementation plan executed；
- scoped/full tests PASS；
- PR/CI/merge PASS；
- GHCR immutable images PASS；
- Staging DeepSeek 山东、上海、第三轮 PASS；
- Repair Test PASS；
- Production deployed；
- Production health PASS；
- Production browser smoke PASS；
- `app.kairos.ac.cn` latest release visible；
- 没有未声明 P0/P1 blocker。

最终完成前使用 `superpowers:verification-before-completion` 逐项核验。代码完成但 Production 未运行时，最终状态只能是 `BLOCKED`，不能写 DONE。
