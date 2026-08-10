# 网页信息采集 Agent：Git 提交与分支规范

> 版本：v1.0  
> 日期：2026-08-10  
> 核心策略：**`main` + 短生命周期功能分支；以可独立验证的小功能为 Commit 单位；Conventional Commits；main 永远保持可部署。**

---

## 1. 目标

Git 历史必须同时满足：

1. Agent 可以按模块任务持续开发，不被繁重流程拖慢。
2. 每个 Commit 都能理解、审查、定位和必要时回退。
3. `main` 始终具备部署到 Staging 的条件。
4. Production 发布与具体 Commit、Migration、Docker Image、Release Tag 可追溯。
5. 禁止直接在服务器修改代码制造“Git 之外的真实版本”。

---

## 2. 分支模型

采用：

```text
main
 ↑
feature/*
fix/*
refactor/*
docs/*
ci/*
```

不设长期 `develop` 分支，不使用完整 GitFlow。

### 2.1 `main`

`main` 必须：

- CI 通过。
- 可构建。
- 可部署 Staging。
- 不接受未经检查的实验代码。
- 不包含明文 Secrets。

### 2.2 短生命周期分支

一个分支聚焦一个模块内的一个明确工作目标。

推荐：

```text
feature/M-05-task-spec-versioning
feature/M-09-source-discovery
fix/M-12-duplicate-record
refactor/provider-adapter-boundary
docs/deployment-runbook
ci/staging-deploy
```

规则：

- 分支名称只用小写英文、数字、`-`、`/`。
- 能关联模块时带 `M-xx`。
- 分支完成后合并并删除。
- 不维护长期个人开发分支。
- 分支偏离 `main` 时间过长时应及时同步，避免大规模冲突。

---

## 3. Commit 粒度

采用“可独立验证的小功能”为单位。

正确示例：

```text
feat(task): add CollectionSpec versioning
feat(state): add task pause transitions
fix(worker): prevent duplicate activity commit
test(auth): cover cross-user task access
refactor(provider): extract model adapter protocol
```

不要求：

- 每改一个文件就提交。
- 每写一个测试就单独提交。
- 每个模块只允许一个 Commit。

### 3.1 一个 Commit 应满足

- 只有一个主要意图。
- 测试和实现可以放在同一个 Commit，只要属于同一行为。
- 不混入无关格式化或大范围重构。
- 能通过 Commit Message 理解变更原因。
- 回退该 Commit 时影响范围可预测。

### 3.2 禁止巨大混合 Commit

禁止：

```text
feat: complete M-09
```

但其中同时包含：

- 搜索 Provider。
- robots。
- Playwright。
- 数据表修改。
- UI 重构。
- unrelated formatting。

这类必须拆分。

---

## 4. Conventional Commits

格式：

```text
<type>(<scope>): <subject>
```

`scope` 可省略。

### 4.1 允许的 type

```text
feat      新功能
fix       Bug 修复
refactor  不改变外部行为的重构
test      测试
docs      文档
chore     工程维护
ci        CI/CD
perf      性能优化
build     构建/依赖
revert    回退
```

### 4.2 推荐 scope

第一版固定优先使用：

```text
web
auth
task
spec
plan
state
agent
provider
search
crawl
browser
worker
workflow
record
quality
evidence
artifact
storage
db
api
infra
deploy
ci
```

无需为了“scope 完整”创建过多 scope。

### 4.3 subject

- 使用英文。
- 小写开头。
- 使用祈使/动作语义。
- 不以句号结尾。
- 建议不超过 72 个字符。
- 描述“做了什么”，不要写“update”“changes”。

正确：

```text
fix(worker): prevent duplicate checkpoint commit
```

错误：

```text
update
修改代码
fix bug
final
123
```

---

## 5. Breaking Change

如果修改了已经被其他模块消费的公共契约，必须明确标识。

例如：

```text
feat(api)!: version task event payload
```

或 Commit Footer：

```text
BREAKING CHANGE: task SSE payload now requires event_id
```

但第一版开发期间优先使用兼容迁移，不应频繁制造 Breaking Change。

---

## 6. Commit 前快速门禁

为了保证开发速度，本地 Commit 不要求跑所有真实 Browser E2E。

至少运行：

### 前端变更

```text
format/lint
type-check
相关测试
```

### 后端/Worker 变更

```text
ruff format/lint
类型检查
相关 pytest
```

### Migration 变更

额外验证：

```text
migration upgrade
关键 schema 检查
```

如果变更涉及状态机、幂等、认证/所有权、Workflow，必须运行对应核心测试。

---

## 7. Pull Request 规则

分支进入 `main` 必须通过 PR。

PR 必须包含：

```text
变更目标
关联模块：M-xx
主要实现
数据库/Migration 影响
API/Event/Workflow 契约影响
测试证据
风险点
Staging 验证要求
回滚影响
```

### 7.1 PR 不追求形式主义

以下不强制：

- 长篇模板。
- 无意义截图。
- 每个小函数解释。
- 机械式 checklist 全复制。

只保留对审查和部署有实际价值的信息。

### 7.2 CI

PR 至少必须通过：

```text
lint
format-check
type-check
unit/integration tests
build
migration validation（如适用）
secret scan
```

重型 E2E 按模块 Gate/部署 Gate 执行。

CI 失败不得合并 `main`。

---

## 8. Merge 策略

推荐默认使用 **Squash Merge** 处理大量微小修正型 Commit；如果分支内部已经由多个有意义、可独立回退的 Commit 组成，可保留 Merge/Rebase 后的 Commit。

原则：

> `main` 历史应清晰，但不要求为了“漂亮历史”牺牲开发速度。

合并后的 Commit Message 仍必须符合 Conventional Commits。

---

## 9. Agent 工作规则

Agent 开始一个模块任务时：

```text
main
↓
拉取最新
↓
创建短生命周期分支
↓
按可验证功能逐步 Commit
↓
本地快速门禁
↓
PR
↓
CI
↓
合并 main
↓
Staging
```

Agent 禁止：

- 在一个分支偷偷实现多个未来模块。
- 测试失败仍然 Commit 并声称模块完成。
- 使用 `git push --force` 覆盖共享 `main`。
- 为通过 CI 删除测试。
- 关闭核心 lint/type rule 而不说明原因。
- 把 Secrets 加入 Git 后再“删掉文件”了事；一旦泄露必须轮换密钥。

---

## 10. Version Tag 与 Production Release

Production 不因每次 `main` 更新自动发布。

流程：

```text
main
↓
Release Candidate 在 Staging 通过
↓
创建 Version Tag
↓
CI 构建不可变镜像
↓
人工发布门禁
↓
Production
↓
Smoke Test
```

推荐使用 Semantic Versioning：

```text
v0.1.0
v0.2.0
v0.2.1
v1.0.0
```

第一版未正式稳定前使用 `0.x.y`。

### 10.1 Patch

Bug 修复：

```text
v0.3.1
```

### 10.2 Minor

向后兼容的新功能：

```text
v0.4.0
```

### 10.3 Major

正式稳定后发生不兼容契约变更：

```text
v1.0.0 → v2.0.0
```

---

## 11. Release Tag 与镜像关系

同一次发布必须能对应：

```text
Git tag
Git commit SHA
web image tag/digest
api image tag/digest
worker image tag/digest
migration version
deploy time
```

禁止 Production 使用：

```text
latest
main
dev
test
```

作为唯一可追溯镜像标识。

可以额外推 `latest`，但部署记录必须使用不可变版本 tag/digest。

---

## 12. Hotfix

线上紧急 Bug：

```text
main
↓
fix/hotfix-description
↓
最小修复
↓
核心回归测试
↓
PR/CI
↓
main
↓
新 patch tag
↓
Staging 快速验证
↓
Production
```

即使紧急，也禁止 SSH 进入容器直接修改 Python/Vue 源码。

---

## 13. Revert

优先使用 Git Revert 保留历史：

```text
revert: revert "feat(task): ..."
```

不要为了隐藏错误重写已经共享的 `main` 历史。

---

## 14. Git 禁止提交内容

必须 `.gitignore` / Secret Scan 阻止：

```text
.env
.env.production
private keys
API keys
Cookie exports
database dumps
MinIO data
Playwright user data dir
local screenshots containing secrets
temporary evidence
```

示例环境变量文件只允许：

```text
.env.example
```

且只能包含变量名和安全示例。

---

## 15. 模块完成与 Git 证据

一个模块 `DONE` 至少要能够关联：

- [ ] 分支/PR。
- [ ] 关键 Commit。
- [ ] CI 结果。
- [ ] Migration version（如有）。
- [ ] Staging 部署版本（达到 Gate 时）。
- [ ] Smoke Test 结果（达到 Gate 时）。

没有这些证据，不把“代码写完”视为模块闭环。
