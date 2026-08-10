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

| provider_type | protocol_family | 需要 Key | 需要 model | 需要 base_url |
|---|---|---|---|---|
| `openai` | openai_compatible | ✅ | ✅ | — |
| `deepseek` | openai_compatible | ✅ | ✅ | — |
| `openrouter` | openai_compatible | ✅ | ✅ | — |
| `custom_openai_compatible` | openai_compatible | ✅ | ✅ | ✅ |
| `anthropic` | anthropic | ✅ | ✅ | — |
| `gemini` | gemini | ✅ | ✅ | — |
| `ollama` | ollama | ❌ | ✅ | ✅ |

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

- `GET /definitions`：Model/Search Provider registry metadata。
- `GET/POST /models`、`PATCH /models/{id}`、`POST /models/{id}/key`、`POST /models/{id}/test`、`POST /models/{id}/default`、`DELETE /models/{id}`。
- Search 同族：`/searches` 下 GET/POST/PATCH/key/test/delete。

API Key 仅写入/更换（`SecretStr` 请求体）；响应只含 `credential_configured` 与安全 metadata，永不回显明文。
