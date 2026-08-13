# Provider 与凭据管理（M-03）

> 本文档覆盖 Model/Search Provider 注册、API Key 信封加密、Master Key 配置、连接测试与版本轮换行为。安全边界以 `agent-business-logic-log.md` D-029/D-059/D-069 与 `agent-code-standards.md` 为准。

## 1. 信封加密结构

CredentialVault 使用 AES-256-GCM 信封加密：

```text
Master KEK（32 字节，来自环境 KAIROS_CREDENTIAL_MASTER_KEY，与数据库分离）
  └─ 包装（wrap）随机 DEK：wrapped_dek = AES-GCM(KEK, wrapped_dek_nonce, DEK, aad)
       └─ 加密 Secret：secret_ciphertext = AES-GCM(DEK, nonce, secret, aad)
```

数据库 `credential_versions` 只保存：`secret_ciphertext`、`wrapped_dek`、`nonce`、`wrapped_dek_nonce`、`algorithm`、`key_version` 与凭据元数据。**数据库绝不保存** Master KEK、plaintext DEK、plaintext API Key。

AAD 绑定 `owner_id:credential_id:version`，防止密文被跨对象替换。

## 2. Master Key 配置

- 环境变量：`KAIROS_CREDENTIAL_MASTER_KEY`（32 字节 hex，64 字符）。
- 本地生成：

```bash
cd backend && .venv/Scripts/python scripts/generate_master_key.py
```

- 将输出复制到 `.env`（`.env` 已 gitignore，禁止提交）。
- staging/production 通过 Secret 注入，禁止写入 compose 或仓库。
- 若未配置，Vault 构造时抛 `CREDENTIAL_CONFIGURATION_ERROR`，明确提示生成方式。

## 3. 存储模型与版本语义

- `credentials` = 逻辑凭据身份（owner、kind、name、status）。`credential_versions` = 每个已加密 Secret 的版本（active / retired）。
- `rotate`：Credential 逻辑 ID 不变，创建新 version，旧 version 标记 retired（明文 identity 保留）。
- `revoke`（删除配置时触发）：active version 标记 retired 且 ciphertext 物理清零，credential 标记 disabled；历史 metadata 保留供审计，Secret 不可再解密。
- `model_configs` / `search_configs`：单表 + `config_id`（逻辑 ID）+ `version` + `is_current` 历史版本模式。编辑 = 追加新版本；M-06 冻结 `(config_id, version)`。默认标记只影响未来任务。

## 4. Model Provider 注册

Registry 为代码注册、typed，禁止 DB 存 class path 动态 import。

| provider_type | protocol_family | 需要 Key | 需要 model | 需要 base_url | base_url 模式 |
|---|---|---|---|---|---|
| `openai` | openai_compatible | ✅ | ✅ | — | managed |
| `deepseek` | openai_compatible | ✅ | ✅ | — | managed |
| `openrouter` | openai_compatible | ✅ | ✅ | — | managed |
| `custom_openai_compatible` | openai_compatible | ✅ | ✅ | ✅ | required |
| `anthropic` | anthropic | ✅ | ✅ | — | managed |
| `gemini` | gemini | ✅ | ✅ | — | managed |
| `ollama` | ollama | ❌ | ✅ | ✅ | local_required |

Base URL 三种模式（Registry 为事实来源，前端不硬编码 URL）：

- `managed`：内置 Provider 的官方/default endpoint 由 Registry 管理，普通用户无需输入 Base URL，前端在高级设置只读展示解析后的地址。
- `required`：`custom_openai_compatible` 需要用户输入 Base URL（合法 http/https URL 校验）。
- `local_required`：Ollama 无需 API Key，但需要用户输入 Base URL，仍可测试连接并返回耗时。

OpenAI-compatible 族复用共享核心 Adapter，通过 `ProviderDefinition` 区分，不复制业务逻辑。

新增 Provider：

1. 在 `app/providers/adapters/` 新增 Adapter（或复用 `OpenAICompatibleModelProvider`）。
2. 在 `app/providers/registry.py` 注册 definition + builder。
3. 追加 `app/providers/errors.py` 不涉及；前端 `definitions` 端点自动返回新 metadata。

## 5. Search Provider 注册

`search_protocol.py` 独立于 ModelProvider。当前注册：

| provider_type | 说明 |
|---|---|
| `custom_compatible_search` | 兼容 HTTP 契约：`GET {base_url}/search?q=&limit=` + `Authorization: Bearer`，返回 `{"results": [{url,title,snippet}]}` |

新增商业 Provider（如 Tavily/Brave）在 M-09 前再按注册表接入，不改变 Agent 主流程。

## 6. Provider 连接测试结果

统一 `ProviderTestResult` 稳定状态：

| status | 含义 |
|---|---|
| `AVAILABLE` | 真实最小请求成功 |
| `AUTH_FAILED` | 401/403（Gemini 400 亦视为认证失败） |
| `MODEL_NOT_FOUND` | 404（模型侧） |
| `RATE_LIMITED` | 429 |
| `NETWORK_ERROR` | 连接/超时失败 |
| `FAILED` | 其他 |

错误正文不直接回传前端，只返回安全 `error_code` 与简短脱敏 message。

## 6.1 Model Probe（未保存配置也能调用的检测连接）

`POST /api/providers/models/probe`（D-073）：

- 请求体 `ModelProbeCommand`：`api_key`（`SecretStr`，可选）、`provider_type`（可选，用户手工选择）、`base_url`（可选）、`model_name`（可选）。
- 响应体 `ModelProbeResultDto`：`status`（未发起请求时为 `null`）、`detection_confidence`（`HIGH`/`AMBIGUOUS`/`NONE`）、`detected_provider`、`candidates`、`resolved_base_url`、`latency_ms`、`error_code`、`message`、`probe_method`（`fingerprint`/`manual`）。
- 安全策略（一次 Probe 最多把 Key 发送给一个外部 Provider）：
  1. 阶段 1 本地 fingerprint（确定性 Key 前缀 matcher，纯字符串匹配，不发网络请求）：高置信度如 `sk-ant-` → Anthropic、`sk-or-` → OpenRouter、`sk-proj-`/`sk-svcacct-` → OpenAI、`AIza` → Gemini；通用 `sk-`（OpenAI/DeepSeek 共用）→ `AMBIGUOUS`；其余 → `NONE`。
  2. 阶段 2 single-provider probe：仅当 fingerprint 高置信度唯一命中，或用户明确选择 `provider_type`，才对该单个 Provider 发起一次真实最小请求（`GET {base_url}/models` 等）。
  3. `AMBIGUOUS`/`NONE` 或缺少必需 Base URL/API Key 时**不发任何请求**，直接返回安全文案要求用户选择 Provider。
- 不创建 ModelConfig / Credential，不持久化 API Key；Key 不落库、不写日志/Temporal History/错误 message/响应。
- `latency_ms` 为发起真实 probe 到收到可判断结果之间的耗时（monotonic timer），非精确网络 ping，超时有界。
- 原有 `POST /api/providers/models/{id}/test`（已保存配置测试）保持不变并继续兼容。

## 7. Key 轮换与撤销行为

- 更换 Key（`POST /api/providers/models/{id}/key`）：Credential 新 version + ModelConfig 新 version（引用新 credential_version）。
- 删除配置（`DELETE .../models/{id}`）：凭据 revoke（物理清零）+ 配置软禁用，历史版本保留。
- 跨用户：所有 Provider/Credential 访问经 `require_user` + `assert_owned`，一律 404，不泄露存在性。

## 8. 运行 M-03 scoped 测试

```bash
cd backend
# 单测（无需本地服务）
.venv/Scripts/python -m pytest tests/credentials/ tests/providers/ -q
# lint / 类型
.venv/Scripts/python -m ruff check app tests && .venv/Scripts/python -m ruff format --check app tests
.venv/Scripts/python -m mypy app
# 集成 Smoke（需要本地 PostgreSQL 运行 + .env 配置 master key）
KAIROS_RUN_INTEGRATION=1 .venv/Scripts/python -m pytest tests/integration/test_provider_smoke.py -v
```

前端：

```bash
cd frontend
npm run lint:check && npm run format:check && npm run type-check && npm run build
npx vitest run src/features/providers/providers.test.ts
```

## 9. API 一览（/api/providers）

- `GET /definitions`：Model/Search Provider registry metadata（含 `base_url_mode`）。
- `GET/POST /models`、`PATCH /models/{id}`、`POST /models/{id}/key`、`POST /models/{id}/test`、`POST /models/{id}/default`、`DELETE /models/{id}`。
- `POST /models/probe`：未保存配置也能调用的检测连接（见 6.1），不创建配置/凭据。
- Search 同族：`/searches` 下 GET/POST/PATCH/key/test/delete。

API Key 仅写入/更换（`SecretStr` 请求体）；响应只含 `credential_configured` 与安全 metadata，永不回显明文。
