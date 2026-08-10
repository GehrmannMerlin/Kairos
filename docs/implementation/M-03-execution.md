# M-03 模块执行记录

状态：DONE
负责人/Agent：Claude Code — 2026-08-10
基线 Commit：`e7dda2c1e2928689a1214715830e099cc98fe956`（M-02 HEAD，未合并入 main）
依赖模块：M-01（DONE）、M-02（DONE）
目标环境：local

> 说明：M-03 基于尚未远程集成的 M-02 HEAD 开发。分支 `feature/M-03-provider-credentials` 未 push/merge；不执行 DEPLOY-GATE-1。

## 1. 本模块目标

完成 D-029、D-049、D-066、D-069 与凭据安全要求，建立统一 Provider Registry 和加密凭据能力：
- 信封加密：主密钥与数据库分离；数据库只保存密文、key reference、必要元数据。
- `ModelProvider` 统一接口与首批 Provider 适配器注册能力。
- `SearchProvider` 独立接口，不与 Model Provider 混成同一个 DTO。
- `/models` 页面支持“AI 模型 / 搜索服务”两个配置区；新增/编辑使用 Drawer。
- Model/Search 配置可测试连接，返回稳定错误分类。
- API Key 不得被读取回前端明文；UI 只能看到“已配置”和脱敏元数据。
- 默认 ModelConfig 只影响未来任务。
- 无 ModelConfig 触发 `MODEL_NOT_CONFIGURED`；探索式/混合式任务无 Search Provider 触发 `SEARCH_PROVIDER_NOT_CONFIGURED`。
- 建立通用 `Credential` 对象，为后续网站 Cookie/用户名密码复用。

## 2. 输入契约

- 上游数据模型：`users`、`sessions`（alembic 0002）。
- API/契约（本模块复用）：`require_user`、`require_session`、`errors.assert_owned`、`app.infra.db.Base`、`app.config.Settings`（含 `credential_master_key`）、`app.infra.deps.get_db`。
- 使用的已有页面：`/app` 受保护占位与 route guard（M-02）；新增 `/models`（D-048/D-051 已确认页面）。

## 3. 本模块实现清单

- [x] 数据模型/迁移：`credentials`、`credential_versions`、`model_configs`、`search_configs`（alembic 0003，可逆）
- [x] 领域服务：`CredentialVault`（store_secret/read_for_execution/rotate/rotate_for_config/revoke/revoke_by_version）+ `CredentialRepository`
- [x] Provider 协议：`ModelProvider` / `SearchProvider` / `ProviderDefinition` / `ProviderTestResult` / `ResolvedModel` / `SearchResult`
- [x] Provider Registry（代码注册 typed）：7 个 Model Provider + `custom_compatible_search`
- [x] API/Workflow/Activity：`/api/providers/*` 薄层路由 + `ProviderError` handler
- [x] 前端交互：`/models` 页面（AI 模型/搜索服务 Tab + Overlay Drawer 新增/编辑/更换 Key/测试连接/设为默认/删除）
- [x] 安全/用户隔离：全部 owner-scoped，跨用户 404；SecretStr 仅写入；前端绝不回读 Key
- [x] 版本语义：ModelConfig/SearchConfig `(config_id, version)` 冻结；rotate 新版本+旧版本 retired；revoke 物理清零
- [x] 幂等/错误路径：`MODEL_NOT_CONFIGURED` / `SEARCH_PROVIDER_NOT_CONFIGURED` 稳定错误码（409）
- [x] 自动化测试：credentials 9 单测 + providers 47 单测 + 1 API stub 连接测试；前端 2 单测
- [x] 联动测试：M-03 Provider/Credential Smoke（见 §6）
- [x] 文档：provider-credentials.md、本记录、superpowers 计划文件

## 4. 明确不做

M-04+（Task/CollectionSpec/Plan/Run/状态机/Outbox/Checkpoint/域事件）、DEPLOY-GATE-1、M-05 完整 App Shell/13 页面、M-06 Task Draft/Agent 对话、M-09 SourceSearch/URL Frontier/robots、Credential Drawer/网站登录/Approval、Scrapy/Playwright、计费 UI、K8s/Redis、远程 Git 集成（push/PR/merge/tag/deploy）。

## 5. 验收命令与证据

| 验收项 | 命令 | 预期 | 实际结果 |
|---|---|---|---|
| credential 单测 | `pytest tests/credentials/` | 9 passed | PASS |
| provider 单测 | `pytest tests/providers/` | 47 passed | PASS |
| 后端 ruff | `ruff check app tests && ruff format --check app tests` | PASS | PASS |
| 后端 mypy | `mypy app` | PASS | PASS（56 files） |
| migration | `alembic upgrade head` / `downgrade 0002` / `upgrade head` | head=0003，可逆 | PASS |
| 集成 smoke | `KAIROS_RUN_INTEGRATION=1 pytest tests/integration/test_provider_smoke.py -v` | 1 passed | PASS |
| M-02 回归 | `KAIROS_RUN_INTEGRATION=1 pytest tests/integration/` | 4 passed（含 auth smoke） | PASS |
| 前端单测 | `npm run test:unit` | 10 passed（含 providers 2） | PASS |
| 前端 lint/format | `npm run lint:check && format:check` | PASS | PASS |
| 前端 type-check/build | `npm run type-check && build` | PASS | PASS |
| secret leak | git grep 真实 Key 模式 + 捕获 smoke 输出 | 0 处明文 | PASS |

## 6. 跨模块联动结果

- 上游兼容：PASS（M-02 Auth Smoke 回归无破坏；M-01 smoke 未受影响）。
- 下游契约测试：PASS — M-03 Provider/Credential Smoke：A 注册→创建 ModelConfig→写入 Key→响应无明文→DB 无明文→test 连接 AVAILABLE→编辑 version+1→更换 Key（Credential+Config version+1）→B 访问 404→创建 SearchConfig→test AVAILABLE→删除后不再可用。

## 7. 部署结果

- 非 Deploy Gate；DEPLOY-GATE-1 待 M-01～M-04 后执行，本轮不执行。

## 8. 完成结论

- 全部门禁 PASS。M-03 = DONE。工作树最终干净，无 Secret 提交（本地 `.env` 的 master key 已 gitignore）。
