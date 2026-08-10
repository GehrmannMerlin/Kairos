# Git Repository Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已确认的唯一 GitHub 仓库、分支命名、Commit、Pull Request、合并和本地收尾规则写入 Kairos 的权威 Git 规范。

**Architecture:** 继续使用根目录 `agent-git-standards.md` 作为 Git 规则的单一事实来源，不创建重复的贡献指南。通过新增正式仓库章节并定点修订现有分支、Commit、PR、Merge 和 Agent 工作流章节，使规则形成从远程核验到本地清理的完整闭环。

**Tech Stack:** Markdown、Git、PowerShell 文本断言与差异检查

## Global Constraints

- 唯一正式仓库必须是 `https://github.com/GehrmannMerlin/Kairos.git`，唯一标准远程名必须是 `origin`。
- 分支名不带人员、工具或 Agent 身份前缀，只使用小写英文、数字、`-` 和 `/`；模块编号写作 `m-xx`。
- 每个 Commit 必须具有英文 Conventional Commits 标题和中文正文。
- PR 标题必须使用英文 Conventional Commits，PR 正文必须使用中文并包含规定的审查信息。
- 默认使用 Rebase and Merge；仅对大量临时修正 Commit 使用 Squash Merge。
- PR 合并后保留远程功能分支，删除已合并的本地功能分支，并使本地项目停留在已同步的 `main`。
- 本计划不配置远程，不执行 Push、PR、Merge、Tag 或 Release。

---

### Task 1: 修订权威 Git 规范

**Files:**
- Modify: `agent-git-standards.md`
- Reference: `docs/superpowers/specs/2026-08-10-git-repository-workflow-design.md`

**Interfaces:**
- Consumes: 已确认的 Git 仓库与协作规范设计，以及现有 `agent-git-standards.md` 的章节结构。
- Produces: 一份可供后续 Agent 和开发者直接执行的 `agent-git-standards.md` v1.1。

- [ ] **Step 1: 核对实施基线**

Run:

```powershell
git status --short --branch
git remote -v
```

Expected: 当前分支为 `docs/git-repository-workflow`；除本计划文件外无意外变更；远程列表为空。

- [ ] **Step 2: 更新文档版本和核心策略摘要**

修改 `agent-git-standards.md` 文件头：

```markdown
> 版本：v1.1
> 日期：2026-08-10
> 核心策略：**唯一仓库 `GehrmannMerlin/Kairos`；`main` + 短生命周期功能分支；以可独立验证的小功能为 Commit 单位；英文 Conventional Commits 标题 + 中文正文；main 永远保持可部署。**
```

- [ ] **Step 3: 新增正式仓库与远程章节**

在“目标”之后新增以下规则，并顺延后续一级章节编号：

```markdown
## 2. 正式仓库与远程

- Kairos 唯一正式代码仓库为 `https://github.com/GehrmannMerlin/Kairos.git`。
- 本地唯一标准远程名为 `origin`。
- Push、Pull、Fetch、PR、Tag 或 Release 前，必须运行 `git remote -v`，确认相关远程 URL 精确指向正式仓库。
- Push 前还必须检查当前分支及其上游，禁止依赖隐式目标猜测推送位置。
- 禁止将 Kairos 项目代码推送到其他远程仓库。
- 变更正式仓库地址必须取得用户明确授权，并先更新本规范。
```

补充首次接入说明：当前远程为空时，只能在用户明确授权后配置 `origin`、建立 `main` 基线并首次 Push；文档规则本身不构成外部写操作授权。

- [ ] **Step 4: 统一分支命名规则**

将所有分支示例和规则统一为：

```text
feature/m-05-task-spec-versioning
feature/m-09-source-discovery
fix/m-12-duplicate-record
refactor/provider-adapter-boundary
docs/deployment-runbook
ci/staging-deploy
```

明确不使用人员、工具或 Agent 身份前缀，并把“能关联模块时带 `M-xx`”修改为“能关联模块时带小写 `m-xx`”。

- [ ] **Step 5: 强制 Commit 标题和正文语言**

在 Conventional Commits 章节明确每个 Commit 都必须采用：

```text
<type>(<scope>): <英文 subject>

<中文正文，说明变更内容和原因>
```

加入合规示例：

```text
feat(task): add task specification versioning

实现任务规格版本冻结与历史版本查询，确保已有任务继续引用创建时的稳定规格。
关联模块：M-05
```

明确标题与正文之间必须空一行；正文不得省略；能关联模块时写 `关联模块：M-xx`，无法关联时无需添加虚假的模块编号。

- [ ] **Step 6: 明确 PR 语言和必填信息**

在 Pull Request 章节加入：

```text
PR 标题：英文 Conventional Commits
PR 正文：中文
```

中文正文必须说明变更目标、主要实现、测试证据、风险和回滚影响；数据库、Migration、API、Event、Workflow 和 Staging 影响按实际情况补充。明确 `main` 禁止直接 Commit 和直接 Push，所有变更必须通过 PR。

- [ ] **Step 7: 修订合并和分支保留策略**

将默认策略改为 Rebase and Merge，以保留有意义且可独立验证的 Commit，并保持 `main` 线性。仅当分支包含大量临时修正 Commit 时允许 Squash Merge，且最终合并 Commit 仍必须符合英文标题和中文正文规则。

明确 PR 合并后不得自动删除远程功能分支。

- [ ] **Step 8: 补全 Agent 本地收尾流程**

将 Agent 流程末尾改为：

```text
PR → CI → 合并 main → 本地切回 main → 同步 origin/main
→ 确认工作树正常 → 删除已合并的本地功能分支 → Staging
```

明确功能开发期间允许检出本地功能分支；工作完成后本地必须位于已合并、已同步的 `main`。删除本地分支前必须确认 PR 已合并、远程分支仍存在，且本地分支与对应远程分支 SHA 一致。优先使用 `git branch -d`；Rebase and Merge 导致祖先关系不同而拒绝删除时，只有上述检查全部通过才允许使用 `git branch -D` 删除本地副本。未合并、远程分支缺失或本地仍有独立提交时不得强制删除。禁止推送到错误远程、直接推送 `main` 或擅自删除远程功能分支。

- [ ] **Step 9: 运行文档规则断言**

Run:

```powershell
$ErrorActionPreference = 'Stop'
$path = 'agent-git-standards.md'
$content = Get-Content -Raw -Encoding UTF8 -LiteralPath $path
$required = @(
  'https://github.com/GehrmannMerlin/Kairos.git',
  'origin',
  'feature/m-05-task-spec-versioning',
  '每个 Commit 都必须包含标题和正文',
  'PR 正文必须使用中文',
  'Rebase and Merge',
  '删除已合并的本地功能分支'
)
foreach ($item in $required) {
  if (-not $content.Contains($item)) { throw "缺少规则：$item" }
}
if ($content -cmatch 'feature/M-|fix/M-') { throw '仍存在大写模块分支示例' }
if ($content -match '(?im)^\s*(TBD|TODO)\b') { throw '仍存在未完成占位符' }
git diff --check
```

Expected: PowerShell 和 `git diff --check` 均以退出码 0 完成且无错误输出。

- [ ] **Step 10: 审阅最终差异和范围**

Run:

```powershell
git diff -- agent-git-standards.md docs/superpowers/plans/2026-08-10-git-repository-workflow.md
git status --short
```

Expected: 只包含权威 Git 规范和本实施计划的预期文档变更；不包含 Git 远程配置、代码、Secret 或其他模块文件。

- [ ] **Step 11: 提交文档修订**

Run:

```powershell
git add -- agent-git-standards.md docs/superpowers/plans/2026-08-10-git-repository-workflow.md
git commit -m "docs(git): establish canonical repository workflow" -m "明确 Kairos 唯一远程仓库，并统一分支命名、提交正文、PR、合并以及本地分支清理规则，避免推送目标和本地收尾状态出现歧义。"
```

Expected: Commit 成功；提交标题为英文 Conventional Commits，正文为中文；`git status --short` 无输出。
