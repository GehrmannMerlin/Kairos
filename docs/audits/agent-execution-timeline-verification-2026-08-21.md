# Agent Execution Timeline — 集成回归 + 真实任务验收 + 决策（2026-08-21）

状态：**DOCS_ONLY / 本地门禁全绿 / 真实任务验收 PENDING**。

本文件记录分支 `feature/m-14-execution-timeline`（Task 6 收尾）的本地门禁证据、
分支提交清单与真实任务验收的诚实状态。**不包含任何 Staging / Production 部署证据。**

---

## A. 范围与本提交内容

Task 6 为“集成回归 + 真实任务验收 + 决策”。依据 controller preflight rulings：

1. 真实任务验收**当前不可行**——本地 API 未运行（curl localhost:8000 → 000）；
   staging 前端可访问但 staging 后端**未部署** `/execution/timeline/stream` 端点。
   因此真实任务验收标记为 **PENDING（需部署运行新端点的栈）**，**不伪造**。
2. 验收脚本 `infra/scripts/_execution_timeline_staging_acceptance.py` 已创建但**未入库**
   （`_` 前缀约定，与既有 `_m16/_m17` 等用户脚本一致）。
3. Task 6 不新增测试文件（全部边界测试已在 Task 2 落入 `test_timeline_stream_api.py`）。
4. D-078 按决策日志规则以 **待讨论** 状态写入，**未标记已确认**。
5. 本地 commit 仅限文档（`agent-business-logic-log.md`、`agent-project-implementation-plan.md`、本 audit）。
   无 push / 无 merge / 无 deploy。

---

## B. 本地门禁证据（2026-08-21）

### B.1 后端 scoped 回归

命令（工作目录 `backend/`，venv `.venv/Scripts/python.exe`）：

| 命令 | 结果 |
|---|---|
| `python -m pytest tests/execution tests/api/test_task_events.py tests/api/test_understand.py tests/api/test_plan_api.py -q` | **143 passed in 48.22s** |
| `python -m ruff check app tests` | **Feature 文件 All checks passed**；`tests/ops/test_release_contract.py` 6 条（4×E501 + 2×F401，2026-08-21 复跑确认）为 **2026-08-12 `e357bec` 既有基线**，本分支零改动（`git diff main...HEAD` 为空） |
| `python -m mypy app` | **Success: no issues found in 237 source files** |
| `python -m alembic heads` | **`0017 (head)`（唯一 head，零 migration）** |
| `python -c "from app.main import create_app; create_app()"` | **成功**（仅有 TracerProvider override 提示，非错误） |

补充：`ruff check` 仅对本次涉及文件（`app/execution`、`app/api/routes/events.py|execution.py`、
`app/api/sse_cursor.py`、`tests/execution`、`tests/api/test_task_events.py|test_understand.py|test_plan_api.py`）
**All checks passed**。`alembic heads` 证明本次功能零 migration（无 EventType / 状态机改动）。

### B.2 前端全量门禁

命令（工作目录 `frontend/`）：

| 命令 | 结果 |
|---|---|
| `npm run test:unit` | **41 test files / 201 tests passed** |
| `npm run type-check`（vue-tsc --noEmit） | **PASS** |
| `npm run lint:check` | **PASS（0 errors / 4 warnings**，warnings 为 `useExecution.live.test.ts` 测试桩 `no-explicit-any`） |
| `npm run format:check` | **FAIL（仓库级既有 CRLF 基线，119 个未触碰文件）**——见 B.3 |
| `npm run build` | **PASS**（vite build，195 modules，dist 产物正常） |

### B.3 format:check 说明（既有环境问题，非回归）

`npm run format:check` 在 Windows 上仓库级失败（119 个文件 code style issues），这些文件均为
**本分支未触碰的历史 CRLF 文件**。对本分支变更的 7 个前端文件单独执行
`npx prettier --check` → **All matched files use Prettier code style!**。
即：本次变更自身是 Prettier 干净的；仓库级 format 失败为既有 CRLF 基线，Task 4–5 已确认同一结论。

### B.4 变更文件清单（本分支 vs main）

```text
backend/app/api/routes/events.py
backend/app/api/routes/execution.py
backend/app/api/sse_cursor.py
backend/app/execution/service.py
backend/app/execution/timeline.py
backend/app/execution/timeline_stream.py
backend/tests/execution/test_timeline_mapper.py
backend/tests/execution/test_timeline_stream_api.py
docs/plans/2026-08-21-agent-execution-timeline.md
frontend/src/features/execution/TimelineStepRow.vue
frontend/src/features/execution/execution.api.ts
frontend/src/features/execution/useExecution.live.test.ts
frontend/src/features/execution/useExecution.ts
frontend/src/features/tasks/TaskExecutionView.live.test.ts
frontend/src/features/tasks/TaskExecutionView.test.ts
frontend/src/features/tasks/TaskExecutionView.vue
```

本 Task 6 提交新增：`agent-business-logic-log.md`、`agent-project-implementation-plan.md`、
`docs/audits/agent-execution-timeline-verification-2026-08-21.md`。
`infra/scripts/_execution_timeline_staging_acceptance.py` **保持未入库**。

---

## C. 分支提交清单（Task 1–5 + Task 6）

```text
304bdac docs(execution): plan real-time execution timeline system
2f4cecf refactor(execution): extract shared TimelineMapper
e062f71 feat(execution): stream rich execution timeline over SSE
8dc6140 feat(execution): live timeline stream client with coalesced refresh
b052df4 fix(execution): coalesced refresh keeps timeline incremental
7d0a203 feat(execution): render live timeline steps with status transitions
dc76628 feat(execution): live stage transitions and dag node coloring
f878c00 docs(execution): record real-time timeline acceptance and decision
```

---

## D. 真实任务验收状态：**PENDING**

状态：**PENDING（需部署运行新端点的栈；本地栈未运行，staging 尚未部署该端点）**

原因（controller 探测事实）：

- 本地 API 未运行：`curl http://localhost:8000/api` → 000（无栈在监听）。
- staging 前端可访问，但 staging 后端**未部署** `/tasks/{task_id}/execution/timeline/stream`
  端点，因此无法在 staging 上做真实任务验收。
- 真正的真实任务验收需要一个运行新端点的完整栈（部署动作默认不自动执行），
  超出 Task 6 范围（默认交付到“本地全量门禁 + 真实任务验证”）。

已创建但**未运行/未入库**的验收载体：

- `infra/scripts/_execution_timeline_staging_acceptance.py`（CLI
  `--base-url --username-env --password-env --output [--task-id]`，沿用 `_m16/_m17` 模式）。
  登录后复用受控任务或创建并启动真实任务，读取 timeline stream 断言：
  1. 流按序推送 `run.started → run.node_started → run.node_completed → run.*`，`event_id` 严格递增；
  2. 每条 `TimelineEvent` 含 `event_id/timestamp/stage/summary`，secret-scan 通过
     （无 api_key/cookie/authorization/token/password/secret）；
  3. `?after_id=<最后游标>` 重连后不重复事件；
  4. `/execution` snapshot 的 `last_event_id` 与流最终游标一致；
  5. 输出 secret-free JSON 报告到 `--output`，非零退出码表示失败。
- 浏览器 E2E（可选）：`frontend/e2e/execution-live-timeline.spec.ts` 未创建（Task 6 不做新代码）。

**严禁将 PENDING 表述为已验证。** 部署后执行该脚本并通过全部断言，才能将状态改为
`VERIFIED`（或 `STAGING_VERIFIED`）。

---

## E. 决策文档（D-078）

`agent-business-logic-log.md` 追加 D-078（状态 **待讨论**，维度 12. 可观测性）：
执行详情页提供实时执行时间线流（Execution Visibility Layer）——owner-scoped SSE 流 +
前端实时追加/阶段/DAG live/reconnect reconcile；复用 D-077 游标语义，零 migration。
按决策日志规则，用户确认前状态保持 **待讨论**，不标记已确认。

---

## F. 结论

- 后端 scoped 回归：**143 passed**；ruff（feature 文件）clean；mypy clean；`alembic heads` 唯一 `0017`；`create_app()` OK。
- 前端全量门禁：**201 unit tests + type-check + lint + build 全 PASS**；format:check 为既有 CRLF 基线失败（本次变更文件 Prettier-clean）。
- 真实任务验收：**PENDING**（需部署运行新端点的栈）。
- 本提交为 **DOCS ONLY**；验收脚本未入库；无 push / merge / deploy。
