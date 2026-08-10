# M-04 模块执行记录

状态：IN_PROGRESS → DONE（local）
负责人/Agent：Claude Code — 2026-08-10
基线 Commit：`602a5c30a8270de27206063ac2e1c2ea5efd7002`（M-03 HEAD，未合并入 main）
依赖模块：M-01（DONE）、M-02（DONE）、M-03（DONE）
目标环境：local

> 说明：M-04 基于尚未远程集成的 M-03 HEAD 开发。分支 `feature/M-04-domain-state-idempotency` 未 push/merge。DEPLOY-GATE-1 在本记录完成后进行 Preflight（见报告）。

## 1. 本模块目标

把 D-004～D-008、D-011、D-015、D-016、D-030 的核心业务事实固化为数据库和领域服务，为后续所有任务执行建立唯一可信状态模型：
- 建立核心表/模型：Task、CollectionSpecVersion、PlanVersion、Run、NodeRun、NodeAttempt、URLResource、PageSnapshot 索引、Record、FieldEvidence 索引、Approval、Artifact、DomainEvent、IdempotencyKey、Outbox、Checkpoint。
- 所有用户业务表 `user_id NOT NULL`。
- Task / Node 状态机、乐观锁 `version`、`allowed_actions` 计算。
- 状态变化、业务写入、DomainEvent、Outbox 同事务提交。
- API request idempotency、Node batch idempotency、Artifact identity 基础函数。
- Checkpoint 只在批次业务事务成功后生成；heartbeat 不冒充。
- 软删除基础（`DELETED` + `deleted_at`），永久删除留给 M-15。

## 2. 输入契约

- 上游数据模型：`users`、`sessions`（alembic 0002）。
- API/契约（复用）：`app.auth.errors.assert_owned` / `NotFoundError`、`app.infra.db.Base`、`app.infra.deps.get_db`。
- 使用页面/Drawer：无（M-04 为领域底座，无 UI 业务新增）。

## 3. 本模块实现清单

- [x] 数据模型/迁移：16 表（alembic 0004，可逆）
- [x] 领域服务：`DomainService`（transition_task / transition_node / commit_checkpoint）+ 全部 owner-scoped Repository
- [x] 状态机：`app/state/states.py`（TaskState/NodeState 枚举 + 显式转换矩阵 + allowed_actions）
- [x] 事件/Outbox：`app/state/events.py`（append_domain_event / enqueue_outbox）+ OutboxRepository（claim/mark）
- [x] 幂等：`app/domain/idempotency.py`（canonical JSON + SHA-256 fingerprint + 派生键 + IdempotencyService）
- [x] Checkpoint：`commit_checkpoint`（COMMIT 后创建 / replay 复用 / fingerprint 冲突）
- [x] 乐观锁：`version` + `expected_version` → `STALE_VERSION`（409）
- [x] 软删除：Task `DELETED` + `deleted_at`，运行中不可 delete（状态机约束）
- [x] 自动化测试：46 个 domain 单测 + M-04 Domain Smoke
- [x] 联动测试：M-04 Domain Smoke（见 §6）
- [x] 文档：domain-state-model.md、本记录、superpowers 计划文件

## 4. 明确不做

M-05+（App Shell/13 页面）、M-06 Agent/Spec Editor/Task Draft、M-07 Temporal Workflow/pause-resume-cancel 命令、M-08 Plan 生成/Node Registry/Approval 命令、M-09 SourceSearch/URL Frontier/robots、M-10 Fetch/Playwright、M-11 Extractor、M-12 Quality、M-15 CSV/永久级联清理、Credential Drawer、计费 UI、K8s/Redis、DEPLOY-GATE-1（另见 Gate Preflight）、远程 Git 集成。

## 5. 验收命令与证据

| 验收项 | 命令 | 预期 | 实际结果 |
|---|---|---|---|
| domain 单测 | `pytest tests/domain/` | 46 passed | PASS |
| 后端 ruff | `ruff check app tests && ruff format --check app tests` | PASS | PASS |
| 后端 mypy | `mypy app` | PASS | PASS（65 files） |
| migration | `alembic upgrade head` / `downgrade 0003` / `upgrade head` | head=0004，可逆 | PASS |
| Domain Smoke | `pytest tests/domain/test_domain_smoke.py -v` | 1 passed | PASS |
| secret scan | git grep 真实 Key 模式 | 无泄漏 | PASS |

## 6. 跨模块联动结果

- 上游兼容：PASS（未触碰 M-01 infra / M-02 auth / M-03 credential-provider）。
- 下游契约测试：PASS — M-04 Domain Smoke：创建 Task/Spec v1/Plan v1/Run/NodeRun → 合法转换（submit、ready、dispatch、succeed）→ 同事务写 state+event+outbox → 批次后写 Record+Checkpoint → 同 batch 重放复用无重复 → stale version CONFLICT → B 访问 A 的 Task 404 → 幂等复用/冲突 → 失败批次 rollback 无 checkpoint。

## 7. 部署结果

- 非 Deploy Gate；DEPLOY-GATE-1 进入 Preflight（见最终报告，预计 BLOCKED_EXTERNAL：无 staging host/SSH/registry/远程 Git/CI/域名/secret env）。

## 8. 完成结论

- 本地全部门禁 PASS。M-04 local = DONE。工作树干净，无 Secret 提交。
