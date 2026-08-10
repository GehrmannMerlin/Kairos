# Kairos Git 仓库与协作规范设计

## 目标

在现有 `agent-git-standards.md` 中建立唯一、明确且可执行的 Git 仓库与协作规则，防止代码推送到错误仓库，并统一分支、Commit、Pull Request、合并及本地收尾行为。

## 单一事实来源

- Git 规则继续只由仓库根目录的 `agent-git-standards.md` 维护。
- 不新增内容重复的 `CONTRIBUTING.md`、Git 指南或第二套分支规范。
- 本次只修改文档规则；除承载文档变更所需的本地分支和 Commit 外，不配置 Git 远程，不执行 Push、PR、Merge、Tag 或 Release。

## 已确认规则

### 正式仓库与远程

- Kairos 唯一正式代码仓库为 `https://github.com/GehrmannMerlin/Kairos.git`。
- 本地唯一标准远程名为 `origin`。
- 执行 Push、Pull、Fetch、创建 PR、Tag 或 Release 前，必须核验目标属于该仓库。
- 禁止将 Kairos 项目代码推送到其他远程仓库；变更正式仓库必须由用户明确授权，并先更新规范。

### 分支命名

- 分支名不使用人员、工具或 Agent 身份前缀。
- 分支名只使用小写英文、数字、`-` 和 `/`。
- 可关联模块时使用小写模块编号 `m-xx`。
- 标准示例为 `feature/m-05-task-spec-versioning`、`fix/m-12-duplicate-record`、`docs/git-standards`。

### Commit 说明

- 每个 Commit 必须包含标题和正文，不允许只有单行标题。
- 标题必须使用英文并符合 Conventional Commits：`<type>(<scope>): <subject>`。
- 正文必须使用中文，至少说明变更内容及原因；能关联模块时写明 `关联模块：M-xx`，不能关联时不强制写无意义的模块占位。
- 标题与正文之间保留一个空行。

### Pull Request

- PR 标题使用英文，并符合 Conventional Commits。
- PR 正文使用中文，必须说明变更目标、主要实现、测试证据、风险和回滚影响；数据库、Migration、API、Event、Workflow 或 Staging 影响按实际情况补充。
- `main` 不接受直接 Commit 或直接 Push，所有变更必须通过 PR。

### 合并与分支保留

- 默认使用 Rebase and Merge，保留有意义、可独立验证的 Commit，同时保持 `main` 历史线性。
- 分支包含大量临时修正 Commit 时允许使用 Squash Merge；最终合并 Commit 仍须满足英文标题和中文正文规则。
- PR 合并后保留远程功能分支，不自动删除。

### 本地收尾状态

- 功能开发期间允许本地检出对应的短生命周期分支。
- PR 合并完成后，本地必须切回 `main`，同步 `origin/main`，确认工作树状态正常，然后删除已合并的本地功能分支。
- 工作完成后的本地项目必须位于已合并、已同步的 `main`，远程功能分支作为开发记录继续保留。
- 未合并或仍有独立提交的本地分支不得强制删除。

## 初次接入边界

当前 GitHub 仓库为空，本地尚未配置 `origin` 且尚无 `main`。后续首次接入必须作为独立、经用户授权的 Git 操作处理，包括确认初始 `main` 基线、配置 `origin`、推送分支及建立远程保护规则。本次文档修订不隐含这些外部写操作的授权。

## 文档修改范围

实施阶段只修改 `agent-git-standards.md`：

1. 升级版本号并新增“正式仓库与远程”章节。
2. 修正现有分支示例中的大写 `M-xx`，统一为小写 `m-xx`。
3. 将 Commit 正文从可选说明改为强制中文正文。
4. 明确 PR 标题和正文语言及必填内容。
5. 将默认合并策略调整为 Rebase and Merge，并保留有限的 Squash Merge 例外。
6. 补充远程分支保留、本地切回 `main`、同步及删除本地功能分支的收尾规则。
7. 在 Agent 禁止事项中加入错误远程、直接推送 `main` 和擅自删除远程功能分支。

## 验收标准

- 文档中不存在与“分支名全小写”冲突的大写模块分支示例。
- 唯一仓库 URL、标准远程名和推送前核验规则明确且无歧义。
- Commit、PR、合并及本地收尾规则均包含可直接照做的示例或流程。
- 文档不授权或声称已经完成远程配置、Push、PR、Merge 或分支删除。
