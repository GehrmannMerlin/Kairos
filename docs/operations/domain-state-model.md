# 领域状态模型与执行契约（M-04）

> 本文档描述 M-04 建立的核心领域状态机、事务性 Outbox、幂等与 Checkpoint 语义。安全边界以 `agent-business-logic-log.md` D-011/D-015/D-016/D-023 与 `agent-code-standards.md` 为准。

## 1. Canonical 状态词汇表

状态以枚举字符串（大写）存数据库，全项目唯一；禁止为同一含义引入多个名称。

**TaskState**：`DRAFT, QUEUED, RUNNING, PAUSING, PAUSED, WAITING_APPROVAL, WAITING_RESOURCE, CANCELLING, CANCELLED, COMPLETED, PARTIALLY_COMPLETED, FAILED, DELETED`

**NodeState**：`PENDING, READY, RUNNING, WAITING_RETRY, WAITING_RESOURCE, SUCCEEDED, SKIPPED, BLOCKED, FAILED, CANCELLED`

## 2. 转换矩阵与 allowed_actions

转换矩阵定义在 `app/state/states.py`（`TASK_COMMANDS` / `NODE_COMMANDS`），是唯一事实来源。`allowed_task_actions(state)` / `allowed_node_actions(state)` 由矩阵派生，返回当前状态合法的命令名（小写），例如：

```json
{ "state": "PAUSED", "allowed_actions": ["resume", "cancel", "delete"] }
```

关键约束：
- 运行中状态（`RUNNING` / `PAUSING` / `CANCELLING`）**不得** `delete`；必须先取消并等待停止。
- `DELETED` 只从非运行状态进入；`restore` 回到 `DRAFT`（永久级联清理留给 M-15）。
- `WAITING_RESOURCE` / `WAITING_APPROVAL` 是真实等待态，不误报为失败。

## 3. 状态转换事务原子性

一次状态变化必须经过 `DomainService.transition_task(...)` / `transition_node(...)`，在**同一 DB 事务**内完成：

```text
校验 owner → 读取 current state + optimistic version → 校验转换 → 更新 current state + version+1 → append DomainEvent → enqueue Outbox → commit
```

任一步失败则整体 rollback。禁止直接 `UPDATE task.state` 绕过。

- 乐观锁：`expected_version` 与当前 `version` 不符 → `STALE_VERSION`（409），不静默覆盖。
- `allowed_actions` 与转换矩阵一致，前端只消费它，不复制后端状态机。

## 4. DomainEvent 与 Outbox

- `domain_events`：append-only，禁止 UPDATE 历史事件。至少含 `aggregate_type/aggregate_id/event_type/aggregate_version/actor/payload`。payload 禁止存 API Key/Cookie/密码/Authorization。
- `outbox_events`：与业务写入同事务入队（`enqueue_outbox`）；`OutboxRepository.claim_pending` 领取、`mark_dispatched` / `mark_failed` 记账。真正对外 side effect 属后续模块，M-04 不发送外部请求。

## 5. 幂等键

- `stable_fingerprint(*parts)`：canonical JSON（`sort_keys` + 稳定分隔符）+ SHA-256。**禁止 random-only 幂等键。**
- `idempotency_key_for_node(task_id, spec_version, node_type, input_fingerprint)`：节点/批次派生键。
- `idempotency_key_for_artifact(dataset_version, export_type, filter_snapshot, content_hash)`：产物 identity。
- `IdempotencyService.record(...)`：相同 owner+operation+key+payload → 复用；同 key 不同 payload → `IDEMPOTENCY_CONFLICT`（409）；DB 唯一约束 `(user_id, operation, idempotency_key)` 作为最后兜底。

## 6. Checkpoint

- **只在批次业务事务 COMMIT 成功后**创建（`DomainService.commit_checkpoint`），代表「已提交业务进度」。
- 重放：同一 `(run_id, batch_identity)` 已存在 → 复用已提交结果，不重复业务写入；同 batch 不同 `input_fingerprint` → 冲突。
- **Temporal heartbeat 不是业务 Checkpoint**；M-04 不写入任何 heartbeat 冒充。
- 失败事务 → 无 checkpoint、无半写入。

## 7. Owner 隔离

所有业务表 `user_id NOT NULL`；Repository 一律 owner-scoped（`get_owned` / `list_by_user`），跨用户一律 404（复用 M-02 `assert_owned` / `NotFoundError`）。没有默认全表读取面。

## 8. M-04 scoped 测试命令

```bash
cd backend
.venv/Scripts/python -m pytest tests/domain/ -q
.venv/Scripts/python -m ruff check app tests && .venv/Scripts/python -m ruff format --check app tests
.venv/Scripts/python -m mypy app
# migration 可逆性（本地服务已起）
.venv/Scripts/python -m alembic upgrade head && .venv/Scripts/python -m alembic downgrade 0003 && .venv/Scripts/python -m alembic upgrade head
```
