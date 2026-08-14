# CLAUDE.md — 网页信息采集 Agent 项目全局开发约束

> 本文件是 Claude Code 在本仓库中的**最高层工程执行约束与文档路由器**。  
> 它不重复五份详细文档的全部内容，而是规定：**什么时候必须读取哪份文档、文档优先级、允许做什么、什么时候必须停止、什么条件下才能宣称完成。**
>
> Claude Code 进入本项目后必须遵守本文件。不得以“我已经知道规范”“这个修改很小”“为了赶进度”为理由跳过要求的文档读取和门禁。

---

## 0. 项目权威文档

仓库根目录必须存在以下五份权威文档：

| 文档 | 职责 |
|---|---|
| `agent-business-logic-log.md` | 产品需求、业务规则、架构边界、UI/UX、数据/安全/Agent 执行规则的**唯一产品事实来源** |
| `agent-project-implementation-plan.md` | M-01～M-18 模块顺序、依赖、完成门禁、DEPLOY-GATE-1～5、Agent 模块执行方式 |
| `agent-code-standards.md` | Vue 3、FastAPI、Temporal、Worker、数据库、Provider、测试、日志、代码组织与质量门禁 |
| `agent-git-standards.md` | 分支、Commit、PR、CI、Tag、Release、Revert、Hotfix、禁止提交内容 |
| `agent-production-deployment-standards.md` | 香港云服务器、域名、DNS、HTTPS、SSH、Docker Compose、Staging/Production、备份、发布、Smoke Test、回滚 |

**禁止删除、随意改名或绕过这些文档。**

如果实际仓库将它们移动到 `docs/` 等目录，必须同步更新本 `CLAUDE.md` 中的路径。

---

# 1. 规则优先级

发生冲突时按以下优先级处理：

1. **当前用户明确指令**
2. `agent-business-logic-log.md`
3. `agent-project-implementation-plan.md`
4. 对应专项规范：
   - 代码工作 → `agent-code-standards.md`
   - Git 工作 → `agent-git-standards.md`
   - 部署工作 → `agent-production-deployment-standards.md`
5. 已有代码与历史实现

已有代码如果与更高优先级文档冲突，**不能因为“代码已经这样写了”就继续沿用错误实现**。

如果两个高优先级文档无法确定性协调：

- 立即停止冲突部分；
- 明确指出冲突位置；
- 给出最小影响分析；
- 请求用户决策；
- 不静默创造新产品规则。

---

# 2. 核心原则：先判断任务类型，再读取文档

Claude Code 在执行任何实质动作前，先把当前工作归入以下一种或多种类型：

```text
A. 需求 / 产品 / UX / 业务规则
B. 模块开发 / 模块验收
C. 写代码 / 改代码 / 重构 / 修 Bug / Migration
D. Git Commit / Push / PR / Merge / Rebase / Tag / Release
E. 本地基础设施 / Docker / CI
F. Staging / Production / 云服务器 / 域名 / DNS / HTTPS / SSH / 备份 / 回滚
G. 调试 / Incident / 线上故障
```

**一个任务可能命中多个类型。命中几个，就执行几个对应的读取规则。**

不得先改代码、先 Commit、先登录服务器、先执行部署，再回头补读规范。

---

# 3. 强制文档读取路由

## 3.1 任何产品行为、业务逻辑、页面交互变更

在以下行为之前：

- 新增/修改产品功能；
- 改状态机；
- 改 Task/Spec/Plan/Run/Record/Evidence/Approval/Artifact 语义；
- 改页面、Drawer、Modal、路由或 Deep Link；
- 改模型/Search Provider 行为；
- 改抓取/验证/审核/导出规则；
- 改认证、用户隔离、安全边界；
- 判断需求“应该怎么做”。

**必须先读取：**

```text
agent-business-logic-log.md
```

读取策略：

1. 先定位与当前任务相关的 D-xxx 决策。
2. 同时检查该决策之后是否有“替代关系”。
3. 后编号明确替代旧决定时，以新决定为准。
4. 不允许只读旧决定就开始实现。

特别注意：当前文档存在显式替代关系，不能把被替代内容重新实现回来。

---

## 3.2 开始一个 M-XX 模块或判断“下一步做什么”

**必须先读取：**

```text
agent-project-implementation-plan.md
```

至少读取：

- I-001～I-005 全局实施规则；
- 当前 M-XX 完整章节；
- 当前模块前置依赖；
- 当前模块完成门禁；
- 与后续模块联动；
- 当前模块之后紧邻的 DEPLOY-GATE（如存在）；
- 模块依赖矩阵；
- “Agent 实施优先级与停止规则”。

然后确认：

```text
当前模块：
依赖模块：
依赖状态：
本模块明确不做：
是否命中部署 Gate：
```

**不得跳模块。**

默认顺序：

```text
M-01 → M-02 → ... → M-18
```

只有实施计划明确允许的模块内部小任务可以并行。

---

## 3.3 第一次写/改代码之前

每个 Claude Code 会话中，**第一次发生代码编辑前**必须完整读取：

```text
agent-code-standards.md
```

代码包括但不限于：

- Vue / TypeScript；
- FastAPI / Python；
- Pydantic AI；
- Temporal Workflow / Activity；
- Scrapy / HTTP / Playwright；
- Provider Adapter；
- SQL / Repository；
- Migration；
- 测试；
- Shell 脚本；
- CI 配置；
- Docker/Compose 配置中的应用工程逻辑。

后续同一会话继续开发时，不必每次重新全文读取，但以下情况必须重新打开相关章节：

- 切换到新的技术边界；
- 进入 Temporal/状态机/幂等/用户隔离等高风险区域；
- 代码规范文件刚被修改；
- 距离上次读取已发生大量上下文切换；
- 对规则记忆不确定。

### 写代码前还必须读取

如果代码属于某个 M-XX 模块，同时读取：

```text
agent-project-implementation-plan.md 中当前 M-XX
agent-business-logic-log.md 中与该功能相关的 D-xxx
```

因此正常模块开发的最小上下文是：

```text
当前需求决策
+ 当前模块计划
+ 代码规范
```

---

## 3.4 Git 变更操作之前

以下操作之前必须先完整读取：

```text
agent-git-standards.md
```

触发操作包括：

```text
git add
git commit
git push
git merge
git rebase
git revert
创建/删除分支
创建 PR
创建 Tag
Production Release Tag
Hotfix
```

纯只读命令如：

```text
git status
git diff
git log
git show
```

可以用于了解状态，不要求每次先全文读取 Git 规范。

### Git 强制约束

执行写操作前必须确认：

```text
当前分支是否正确？
是否关联 M-XX？
Commit 是否只包含一个可独立验证的小功能？
相关快速门禁是否通过？
是否存在不应提交的 Secret/数据文件？
Commit Message 是否符合 Conventional Commits？
```

**未经用户明确要求，不主动 Push、Merge、创建 Tag 或发布 Production。**

即使用户说“提交一下”，也只执行其实际授权范围内的 Git 动作，不自动扩大为 Merge/Tag/Deploy。

---

## 3.5 CI、镜像、服务器和部署动作之前

以下任一动作发生前，必须完整读取：

```text
agent-production-deployment-standards.md
```

包括：

- 修改 Production/Staging Compose；
- 云服务器初始化；
- SSH 登录服务器执行变更；
- 配置安全组、防火墙；
- 域名/DNS；
- Caddy/Nginx；
- HTTPS/TLS；
- Staging 部署；
- Production 部署；
- Docker Registry / 镜像发布；
- Migration 发布；
- 备份/Restore；
- Smoke Test；
- 回滚；
- 迁移云服务器；
- 修改 `/srv/kairos`；
- 处理线上 Incident。

如果操作同时涉及 Git Release/Tag，还必须同时读取：

```text
agent-git-standards.md
```

如果命中 DEPLOY-GATE，还必须读取：

```text
agent-project-implementation-plan.md 中对应 DEPLOY-GATE 完整章节
```

因此 Production Release 最小强制上下文为：

```text
实施计划对应 Gate
+ Git 规范
+ 部署规范
+ 与 Release 相关的代码/业务规范
```

### 3.5.1 部署前必须重新读取部署规范（不可模糊解释）

**任何一次 Staging 或 Production Deployment，即使在同一 Claude Code 会话中此前已经读取过，在真正执行服务器/镜像写操作之前，必须重新读取当前最新版本的 `agent-production-deployment-standards.md`。**

触发动作包括但不限于：

```text
docker build for release
registry login / push / pull
服务器 compose update
Migration
Staging deploy
Production deploy
release rollback
```

执行部署写操作前，Claude Code 必须明确输出：

```text
Deployment Standard reread: PASS
Target environment:
Release identity:
Image digests:
Rollback target:
```

未重新读取 → **禁止执行部署写操作**。

### 3.5.2 默认部署路线

Kairos 默认部署路线（Registry 增量镜像交付，见部署规范 4A 章）：

```text
Git
→ GitHub Actions CI
→ OCI Registry（GHCR）
→ Staging docker pull
→ Smoke
→ Production docker pull
```

禁止 Claude Code 为了“方便”自行退回：

```text
docker save → SSH → docker load
```

除非：Registry 确认不可用，**并且**用户明确批准 Break-glass deployment（EMERGENCY ONLY 脚本：`deploy-*-breakglass.sh`）。

---

# 4. 每个模块的标准工作流程

开始 M-XX 后严格按以下顺序：

```text
1. 读取当前 M-XX
2. 读取相关 D-xxx
3. 读取代码规范
4. 检查 Git 状态和基线 Commit
5. 创建/确认正确短生命周期分支
6. 只实现当前模块范围
7. 写/更新必要测试
8. 运行快速质量门禁
9. 运行当前模块自动化/联动验收
10. 更新必要文档
11. 读取 Git 规范
12. 形成可独立验证 Commit / PR
13. 如果命中 DEPLOY-GATE：
      读取部署规范
      部署 Staging/Production
      Migration
      Health/Readiness
      Smoke Test
      回滚检查
14. 所有门禁 PASS 后才标记 DONE / DEPLOYED
15. 再进入下一模块
```

不得把：

```text
“代码已经写完”
```

等同于：

```text
“模块已经完成”
```

---

# 5. 代码开发硬约束

详细规则以 `agent-code-standards.md` 为准，以下是不可绕过的全局底线：

- 前端：Vue 3 + TypeScript strict。
- 后端：FastAPI + Python 类型标注。
- Agent：Pydantic AI。
- 长任务事实：Temporal。
- 业务事实：PostgreSQL。
- 原始网页/截图/证据/CSV：S3-compatible Storage / MinIO。
- 浏览器只访问 API/SSE，不直接访问 PostgreSQL、Temporal、MinIO 私有端口。
- FastAPI Route 不承载长任务。
- Workflow 保持可重放确定性；网络/LLM/Browser/文件副作用放 Activity。
- LLM 不直接写数据库业务状态。
- 状态变化必须经过状态机/领域命令。
- `allowed_actions` 由后端事实驱动。
- 用户数据必须强制 owner 隔离。
- API Key/Cookie/密码不得进入普通日志、前端明文、Prompt 长期记忆或 Temporal History。
- 所有 Schema 变化必须通过 Migration。
- 幂等、Checkpoint、状态机、认证隔离等核心链路必须测试。
- 不以追求高覆盖率拖慢普通 UI/CRUD 开发；执行 A-Lite 质量策略。
- 当前版本不建设收费、余额、套餐、支付或金额预算 UI。

---

# 6. 范围控制

Claude Code 必须对“当前模块明确不做”保持敏感。

禁止：

- 顺手实现未来模块；
- 顺手新增未确认页面；
- 顺手引入 Kubernetes；
- 顺手拆大量微服务；
- 顺手增加 Redis/消息队列/新数据库而没有明确需求；
- 顺手改变认证模型；
- 顺手增加计费系统；
- 因“行业最佳实践”覆盖已经确认的产品决定。

本项目第一版使用：

```text
Monorepo
+ 模块化单体
+ 可独立部署 Worker
+ Docker Compose
```

“有微服务思想”不等于“每个领域都拆成独立服务”。

---

# 7. 修改需求与文档的规则

`agent-business-logic-log.md` 是产品决策日志。

如果用户明确改变已经确认的需求：

- 不静默删除旧决定；
- 使用新的稳定编号；
- 写明“替代关系”；
- 标明日期和状态；
- 实施计划/规范受到影响时同步修订。

如果只是代码实现细节，不要把它伪装成新的产品 D-xxx 决策。

实施计划是执行方案，不得反向创造新的产品需求。

---

# 8. Debug / Bug Fix 规则

修 Bug 前：

1. 判断 Bug 属于哪个模块/领域。
2. 读取相关 D-xxx。
3. 读取代码规范相关章节。
4. 查看现有代码和测试。
5. 尽量先建立可复现失败测试。
6. 修复最小根因。
7. 运行相关回归测试。
8. 检查是否影响跨模块契约。
9. 提交前读取 Git 规范。

不得：

- 仅隐藏错误提示；
- 删除失败测试；
- 放宽用户隔离；
- 关闭幂等；
- 绕开状态机；
- 增加无限重试；
- 为临时修复把 Secret 写入日志。

线上 Bug 还必须读取部署规范，并通过 Git → CI → 新镜像 → Staging → Production 的受控路径处理。

---

# 9. 数据库 / Migration 特别规则

涉及数据库结构时，必须：

```text
读取代码规范中的数据库与 Migration 章节
+ 当前模块计划
+ 相关业务 D-xxx
```

并做到：

- Migration 进入 Git。
- 禁止生产服务器手工改表作为正式方案。
- 优先兼容式 expand/contract。
- 关键表保持 owner 边界。
- 唯一/幂等约束有数据库兜底。
- Migration 与运行镜像版本可追溯。
- 到部署阶段按部署规范执行备份、Migration、Readiness、Smoke Test。

---

# 10. Temporal / 状态机 / 幂等特别规则

以下区域属于高风险核心代码：

```text
Task state machine
Node state machine
Temporal Workflow
Activity retry
Checkpoint
Outbox
Idempotency
Pause / Resume / Cancel
Approval
User ownership
Credential access
```

进入这些区域时，即使本会话已经读过代码规范，也必须重新打开对应相关章节确认约束。

这些修改必须有自动化测试。

不得为了开发速度降低这些区域的门禁。

---

# 11. 前端/UI 特别规则

涉及页面/Drawer/Modal/Deep Link 时：

1. 先读取业务日志对应 D-031～D-067 及相关后续决定。
2. 确认是否已有页面可以承载。
3. 第一版原则上不新增一级页面。
4. Drawer 为 Overlay，不挤压底层布局。
5. Task 一级工作区固定为：
   - 对话
   - 数据
   - 质量
6. 执行详情和 Evidence 是二级页面。
7. 业务按钮必须调用真实后端命令。
8. 状态敏感动作优先由 `allowed_actions` 驱动。
9. 不用静态假数据冒充功能已完成。

---

# 12. Git 与发布边界

默认禁止未经授权的高影响动作：

```text
push --force
重写共享 main 历史
直接 Merge main
创建 Release Tag
部署 Production
删除 Production 数据
删除远程分支中的他人工作
```

需要执行这些动作时：

- 先读取对应规范；
- 明确当前用户要求确实包含该动作；
- 先检查工作树和测试状态；
- 再执行最小必要操作。

---

# 13. 云服务器与 Production 全局底线

详细规则以 `agent-production-deployment-standards.md` 为准。

当前第一版基线：

```text
中国香港云服务器
↓
app.example.com
↓
DNS
↓
HTTPS Reverse Proxy
↓
Vue 3 / FastAPI
↓
Docker 私有网络
↓
Temporal / Worker / PostgreSQL / MinIO
```

必须保持：

- Production 单域名同源。
- SSH 22 可公网开放，但只能密钥认证。
- 禁止 root 日常登录。
- 禁止 SSH 密码登录。
- PostgreSQL/Temporal/MinIO/Worker 不直接暴露公网。
- Production 不现场构建应用源码。
- Production 只运行可追溯不可变镜像。
- Staging / Production 数据和 Secrets 隔离。
- 发布前备份。
- Restore Drill。
- Health/Readiness。
- Smoke Test。
- 可验证回滚。
- 禁止在线上容器直接热改源码。

---

# 14. 完成声明规则

Claude Code 在回复“完成”“DONE”“已修复”“已部署”前必须有证据。

## 代码任务至少说明

```text
修改了什么
运行了哪些测试/检查
结果是什么
是否还有未完成项
```

## 模块任务至少说明

```text
M-XX
依赖状态
自动化测试
联动测试
安全检查
Git/PR 证据
是否命中 Deploy Gate
最终状态：DONE / DEPLOYED / BLOCKED
```

## 部署任务至少说明

```text
环境：staging / production
release/tag/image
migration
health/readiness
smoke test
backup
rollback readiness
最终结果
```

没有真实执行/验证过，就使用：

```text
“已实现，但尚未验证”
“已通过本地测试，尚未部署”
“Staging 通过，Production 尚未发布”
```

不得把推测写成完成事实。

---

# 15. 文档读取证明

为了避免“声称读了但实际没按规范做”，每次进入重要工作阶段时，在内部工作计划或用户可见简短状态中明确列出本次已读取的权威文档。

推荐格式：

```text
本次任务：M-09 来源发现
已读取：
- agent-business-logic-log.md：D-068～D-070
- agent-project-implementation-plan.md：M-09 + DEPLOY-GATE-3
- agent-code-standards.md
本阶段不涉及 Git/部署，因此暂不读取对应规范。
```

准备 Commit 时补充：

```text
- agent-git-standards.md
```

准备部署时补充：

```text
- agent-production-deployment-standards.md
```

目的是按需加载，不是每个小动作重复输出长报告。

---

# 16. 上下文效率规则

为了保持开发速度：

- 不要求每个请求都全文读取五份文档。
- **只在对应触发点读取对应文档。**
- 每个会话第一次写代码时完整读代码规范。
- 每个会话第一次 Git 写操作时完整读 Git 规范。
- 每个会话第一次部署/服务器写操作时完整读部署规范。
- 产品需求日志优先读取相关 D-xxx + 后续替代决定。
- 实施计划优先读取当前 M-XX + 对应 Gate + 全局 I-xxx。
- 如果权威文档发生修改，后续相关动作前必须重新读取新版本。

这条效率规则不能用于绕过高风险核心区域的重复确认。

---

# 17. 新会话启动协议

每次从项目根目录启动新的 Claude Code 会话后：

1. 自动遵守本 `CLAUDE.md`。
2. 运行/查看 `git status`，了解当前分支和工作树，不修改。
3. 不假设上个会话做到哪里。
4. 如果用户要求“继续开发”：
   - 读取 `agent-project-implementation-plan.md`；
   - 检查已有模块执行证据/Git 历史；
   - 确认当前 M-XX；
   - 再按本文件的路由读取对应文档。
5. 如果用户指定具体功能：
   - 先定位相关 D-xxx 和 M-XX；
   - 再行动。
6. 如果发现未提交变更：
   - 先理解其来源；
   - 不覆盖、丢弃或重置不属于当前任务的已有工作。

---

# 18. 禁止自作主张的事项

除非用户明确决定或权威文档已经定义，Claude Code 不得自行：

- 改产品范围；
- 新增收费系统；
- 新增角色/RBAC；
- 新增独立后台管理系统；
- 改多用户完全隔离原则；
- 允许绕验证码/鉴权；
- 增加未注册 Agent 可执行动作；
- 让 LLM 成为数据库状态事实来源；
- 将 LangGraph 引入并与 Temporal 形成双流程事实来源；
- 引入 Kubernetes；
- 把 Search Provider 与 Model Provider 混成同一配置类型；
- 绕过 robots/Approval 规则；
- 把 Staging 数据当 Production 数据；
- 将 Secret 提交 Git；
- 在 Production 手工热改代码。

---

# 19. 最终执行口诀

任何工作都按下面判断：

```text
先问：我正在做什么类型的任务？
        ↓
按任务类型读取权威文档
        ↓
确认当前 D-xxx / M-XX / Gate
        ↓
只做当前范围
        ↓
按代码规范实现和测试
        ↓
需要 Git → 先读 Git 规范
        ↓
需要部署 → 先读部署规范
        ↓
真实验证
        ↓
有证据才能宣布完成
```

**如果不确定该读哪份文档，宁可先读取，不要猜。**


---

# 20. Production Bugfix Default Closure（已上线功能 Bug 修复默认闭环）

> 用户已确认的长期规则：**Kairos 已上线功能的 Bug 修复，默认必须部署到 Production。**
> 适用于用户报告 `app.kairos.ac.cn` 或 Production 已上线功能存在 Bug 并要求“修复”时。

## 20.1 默认任务范围

除非用户明确说“只修代码，不部署”，否则修复已上线功能的 Bug 时，任务范围**自动包含**：

```text
Diagnosis
→ Fix
→ Scoped Tests
→ Git
→ PR / CI
→ Staging
→ Staging Smoke
→ Production
→ Production Smoke
```

不得在代码测试完成后停下来问“是否要部署？”——用户已给出长期默认授权。

## 20.2 完成措辞

禁止“修复完成”当任务实际只是 local code fixed。

必须区分并使用真实状态：

```text
IMPLEMENTED
VERIFIED
STAGING_DEPLOYED
PRODUCTION_DEPLOYED
```

只有 Production Smoke PASS 才能：

```text
DONE / DEPLOYED
```

## 20.3 必须 BLOCK 的情况

“默认部署”不能绕过安全 Gate。以下任一情况必须 BLOCK 并请求用户决策：

- Production health 不通过；
- Staging smoke 不通过；
- Secret 缺失；
- Migration 风险不明确；
- 需要新的付费基础设施；
- 需要产品决策；
- 部署可能造成不可逆数据损坏。

## 20.4 部署身份

默认目标服务器：`47.238.145.24`；用户验收入口：`https://app.kairos.ac.cn/`。

修复后的最终网页必须来自最新修复 Release：

```text
fix branch
→ scoped tests
→ PR / CI
→ main
→ immutable GHCR images
→ Staging pull
→ Staging smoke
→ patch release
→ Production pull
→ migration (如有)
→ compose
→ health / readiness
→ user-facing smoke
```

禁止出现：

```text
Local fixed
但
app.kairos.ac.cn 仍运行旧版本
```

然后宣布完成。

## 20.5 GitHub 网络阻断时的降级

若 `github.com` 网络不可达导致无法 Push / 开 PR / 触发 CI：

- 以本地完整等价门禁（全量测试 / ruff / mypy / vue-tsc / lint / build / secret scan）代替 CI；
- 使用 `infra/scripts/registry-push.sh` 本地构建不可变镜像并推 GHCR；
- 仍必须完成 Staging → Staging Smoke → Production → Production Smoke 全链路；
- 在最终报告中明确把 `PR / CI PASS` 标记为 **PENDING（网络阻断）**，不得伪称已通过；
- 网络恢复后必须补 Push / PR 闭环，并在下个会话记录。
