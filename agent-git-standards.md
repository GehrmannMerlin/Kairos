# 网页信息采集 Agent：Git 提交与分支规范

> 版本：v1.1
> 日期：2026-08-10
> 核心策略：**唯一仓库 `GehrmannMerlin/Kairos`；`main` + 短生命周期功能分支；以可独立验证的小功能为 Commit 单位；英文 Conventional Commits 标题 + 中文正文；main 永远保持可部署。**

---

## 1. 目标

Git 历史必须同时满足：

1. Agent 可以按模块任务持续开发，不被繁重流程拖慢。
2. 每个 Commit 都能理解、审查、定位和必要时回退。
3. `main` 始终具备部署到 Staging 的条件。
4. Production 发布与具体 Commit、Migration、Docker Image、Release Tag 可追溯。
5. 禁止直接在服务器修改代码制造“Git 之外的真实版本”。

---

## 2. 正式仓库与远程

- Kairos 唯一正式代码仓库为 `https://github.com/GehrmannMerlin/Kairos.git`。
- 本地唯一标准远程名为 `origin`。
- 执行 Push、Pull、Fetch、创建 PR、Tag 或 Release 前，必须运行 `git remote -v`，确认相关远程 URL 精确指向上述正式仓库。
- Push 前还必须检查当前分支及其上游，禁止依赖隐式目标猜测推送位置。
- 禁止将 Kairos 项目代码推送到其他远程仓库。
- 变更正式仓库地址必须取得用户明确授权，并先更新本规范。

远程操作前的最小检查：

```text
git remote -v
git branch --show-current
git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}'
```

如果 `origin` 缺失、URL 不匹配或上游分支不符合预期，必须停止远程写操作并先修正配置。

### 2.1 初次接入

远程仓库为空，或本地尚未配置 `origin` / `main` 时，只有在用户明确授权后才允许：

```text
配置 origin
确认初始 main 基线
首次 Push
建立远程分支保护规则
```

本规范只定义目标状态，不构成执行上述外部写操作的授权。

---

## 3. 分支模型

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

### 3.1 `main`

`main` 必须：

- CI 通过。
- 可构建。
- 可部署 Staging。
- 不接受未经检查的实验代码。
- 不包含明文 Secrets。
- 不接受直接 Commit 或直接 Push；所有变更必须通过 PR。

### 3.2 短生命周期分支

一个分支聚焦一个模块内的一个明确工作目标。

推荐：

```text
feature/m-05-task-spec-versioning
feature/m-09-source-discovery
fix/m-12-duplicate-record
refactor/provider-adapter-boundary
docs/deployment-runbook
ci/staging-deploy
```

规则：

- 分支名称只用小写英文、数字、`-`、`/`。
- 不使用人员、工具或 Agent 身份前缀。
- 能关联模块时带小写 `m-xx`。
- 分支完成后合并并删除本地分支；远程分支按本规范的保留规则处理。
- 不维护长期个人开发分支。
- 分支偏离 `main` 时间过长时应及时同步，避免大规模冲突。

---

## 4. Commit 粒度

采用“可独立验证的小功能”为单位。

标题示例（实际提交仍必须包含中文正文）：

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

### 4.1 一个 Commit 应满足

- 只有一个主要意图。
- 测试和实现可以放在同一个 Commit，只要属于同一行为。
- 不混入无关格式化或大范围重构。
- 能通过 Commit Message 理解变更原因。
- 回退该 Commit 时影响范围可预测。
- 包含英文标题和中文正文，不能只有单行标题。

### 4.2 禁止巨大混合 Commit

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

## 5. Conventional Commits

格式：

```text
<type>(<scope>): <英文 subject>

<中文正文，说明变更内容和原因>
```

`scope` 可省略。

每个 Commit 都必须包含标题和正文，标题与正文之间必须保留一个空行。

合规示例：

```text
feat(task): add task specification versioning

实现任务规格版本冻结与历史版本查询，确保已有任务继续引用创建时的稳定规格。
关联模块：M-05
```

### 5.1 允许的 type

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

### 5.2 推荐 scope

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

### 5.3 subject

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

### 5.4 正文

- 必须使用中文。
- 必须说明变更内容和变更原因，不得省略。
- 能关联模块时必须增加 `关联模块：M-xx`。
- 无法关联模块时无需添加虚假的模块编号或“关联模块：无”等占位说明。
- 复杂变更可继续补充影响范围、验证证据或迁移注意事项。

---

## 6. Breaking Change

如果修改了已经被其他模块消费的公共契约，必须明确标识。

例如：

```text
feat(api)!: version task event payload

调整任务事件的版本契约，确保消费者能够明确识别不兼容的 payload。
BREAKING CHANGE: 任务 SSE payload 现在必须包含 event_id
```

但第一版开发期间优先使用兼容迁移，不应频繁制造 Breaking Change。

---

## 7. Commit 前快速门禁

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

## 8. Pull Request 规则

分支进入 `main` 必须通过 PR；`main` 禁止直接 Commit 和直接 Push。

- PR 标题必须使用英文，并符合 Conventional Commits。
- PR 正文必须使用中文。
- 合并 Commit 的标题和正文仍须遵守“英文标题 + 中文正文”规则。

PR 必须包含：

```text
变更目标
主要实现
测试证据
风险点
回滚影响
```

能关联模块时必须写明 `关联模块：M-xx`。数据库/Migration、API/Event/Workflow 契约及 Staging 验证影响按实际情况补充；无影响时可简要写明“无”，不得省略对审查有实际价值的信息。

### 8.1 PR 不追求形式主义

以下不强制：

- 长篇模板。
- 无意义截图。
- 每个小函数解释。
- 机械式 checklist 全复制。

只保留对审查和部署有实际价值的信息。

### 8.2 CI

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

## 9. Merge 策略

默认使用 **Rebase and Merge**，保留分支内有意义、可独立验证和回退的 Commit，同时保持 `main` 历史线性。

只有当分支包含大量 `fixup`、拼写修正或其他不值得独立保留的临时 Commit 时，才允许使用 **Squash Merge**。最终合并 Commit 仍必须包含英文 Conventional Commits 标题和中文正文。

PR 合并后必须保留远程功能分支，不得开启或执行自动删除远程已合并分支。

原则：

> `main` 历史应清晰，但不要求为了“漂亮历史”牺牲开发速度。

不得为了整理历史而对已经共享的 `main` 执行 Force Push 或重写历史。

---

## 10. Agent 工作规则

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
本地切回 main
↓
同步 origin/main
↓
确认工作树正常
↓
删除已合并的本地功能分支
↓
Staging
```

功能开发期间允许本地检出对应的短生命周期分支。PR 合并完成后，本地项目必须位于已合并、已同步的 `main`，已合并的本地功能分支必须删除，远程功能分支继续保留为开发过程记录。

删除本地分支前必须确认 PR 已合并、远程分支仍存在，且本地分支没有尚未推送到对应远程分支的独立提交。未合并、远程分支缺失或本地仍有独立提交时不得强制删除。

本地收尾命令示例：

```text
git fetch origin
git switch main
git pull --ff-only origin main
git status --short --branch
git branch -d feature/m-xx-description
```

Rebase and Merge 会重写 Commit 哈希，因此即使 PR 已合并，`git branch -d` 也可能因祖先关系不同而拒绝删除。此时不得立即强制删除；必须先在 GitHub 确认 PR 状态为已合并，再确认本地分支与保留的远程分支指向同一 Commit：

```text
git rev-parse feature/m-xx-description
git rev-parse origin/feature/m-xx-description
```

只有两个 SHA 完全一致时，才允许使用 `git branch -D feature/m-xx-description` 删除本地副本。不得执行 `git push origin --delete feature/m-xx-description`；远程功能分支由本规范要求保留。

Agent 禁止：

- 在一个分支偷偷实现多个未来模块。
- 测试失败仍然 Commit 并声称模块完成。
- 使用 `git push --force` 覆盖共享 `main`。
- 直接 Commit 或 Push 到 `main`。
- 向 `https://github.com/GehrmannMerlin/Kairos.git` 之外的远程推送 Kairos 项目代码。
- 擅自删除已合并的远程功能分支。
- 为通过 CI 删除测试。
- 关闭核心 lint/type rule 而不说明原因。
- 把 Secrets 加入 Git 后再“删掉文件”了事；一旦泄露必须轮换密钥。

---

## 11. Version Tag 与 Production Release

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

### 11.1 Patch

Bug 修复：

```text
v0.3.1
```

### 11.2 Minor

向后兼容的新功能：

```text
v0.4.0
```

### 11.3 Major

正式稳定后发生不兼容契约变更：

```text
v1.0.0 → v2.0.0
```

---

## 12. Release Tag 与镜像关系

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

## 13. Hotfix

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

## 14. Revert

优先使用 Git Revert 保留历史：

```text
revert: revert "feat(task): ..."

回退引发任务状态异常的功能提交，恢复上一版可验证行为。
```

不要为了隐藏错误重写已经共享的 `main` 历史。

---

## 15. Git 禁止提交内容

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

## 16. 模块完成与 Git 证据

一个模块 `DONE` 至少要能够关联：

- [ ] 分支/PR。
- [ ] 关键 Commit。
- [ ] CI 结果。
- [ ] Migration version（如有）。
- [ ] Staging 部署版本（达到 Gate 时）。
- [ ] Smoke Test 结果（达到 Gate 时）。

没有这些证据，不把“代码写完”视为模块闭环。
