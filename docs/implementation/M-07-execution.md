# M-07 模块执行记录

状态：IN_PROGRESS（最终 DONE 由控制器在全部门禁验证后更新）
负责人/Agent：Claude Code — 2026-08-10
Baseline（M-06 DONE）SHA：`408a9335174d0423b0f084fa0c38c2f8da8cf2bf`
依赖模块：M-04（DEPLOYED）、M-06（DONE）
目标环境：local（M-07 不属于 Deploy Gate；DEPLOY-GATE-2 必须等 M-05～M-08）

## 1. 模块目标
建立真实可靠的长期任务执行底座：TaskWorkflow（Run 启动、pause/resume/cancel、
heartbeat/checkpoint 复用、worker 崩溃恢复）与可重连的 SSE 事件流（Last-Event-ID
重放、跨用户隔离、前端 Task Status Drawer 真实状态过渡），并为 M-08 Plan 提供稳定
执行 seam（TaskWorkflowStarter.submit_validated_plan）。

## 2. 契约
- TaskWorkflowInput / Result / Signals（pause/resume/cancel/approval_resolution/safe_pause）
- TaskCommandService pause/resume/cancel（幂等 + 状态机事务 + outbox）
- OutboxTemporalDispatcher（outbox -> Temporal Signal，有界重试）
- SSETaskEvent schema + /api/events/tasks/{id} replay（cursor = domain_events.id）
- TaskWorkflowStarter（M-08 seam：submit_validated_plan）

## 3. 行为
- 协作式暂停/取消（PAUSING/CANCELLING 真实中间态由命令层写入，PAUSED/CANCELLED 由 Workflow 安全停止后写入）
- heartbeat 不生成 Checkpoint（heartbeat_progress 只发进度）
- checkpoint 复用（同 batch_identity + input_fingerprint 幂等，reused=True）
- worker crash/restart：真实子进程 kill，batch1 不重复、batch2 完成、最终结果一次
- SSE Last-Event-ID / ?after_id 重放；keepalive 是注释行非业务事件；SSE 不是事实源

## 4. Temporal 集成命令
cd backend && set KAIROS_RUN_INTEGRATION=1 && .venv/Scripts/python.exe -m pytest tests/integration/test_task_workflow.py tests/integration/test_worker_crash_restart.py -q

## 5. 前端验证
cd frontend && npm run type-check && npm run lint:check && npm run format:check && npm run test:unit -- taskEvents TaskStatusDrawer

## 6. Migration
NO MIGRATION（复用 M-04 runs/checkpoints/domain_events/outbox_events/idempotency_keys；SSE cursor = domain_events.id）

## 7. Git 证据
- 分支：feature/M-07-temporal-workflow-sse（从 M-06 HEAD 408a933 创建，未 push）
- Commits：
  - `191f3c8` docs(workflow): add M-07 temporal workflow and SSE implementation plan
  - `0867588` docs(workflow): fix M-07 plan start-contract test and checkpoint reuse
  - `ca91553` feat(workflow): add task workflow typed contract and run startup
  - `753a399` fix(workflow): add fail path and non-retryable spec gate
  - `a092cd2` docs(workflow): wire outbox dispatcher into task command route
  - `09b42e0` feat(task): add pause resume and cancel commands
  - `95d96fd` docs(workflow): make task command dispatch lazily connect to temporal
  - `5312ee7` fix(api): lazily connect to temporal in task command route
  - `5896e8e` feat(workflow): add heartbeat and checkpoint recovery
  - `83ef046` test(workflow): cover activity checkpoint reuse
  - `7449b74` docs(workflow): start crash-test workflow on fixture task queue
  - `2fae4fb` test(workflow): cover worker crash restart recovery
  - `261fbcc` docs(workflow): add tests/api conftest for sse event tests
  - `fa1b2ce` feat(api): add replayable task event stream
  - `59a46d2` docs(web): pass real expected_version in task command api
  - `98284ee` feat(web): connect task SSE and status drawer
  - `039184e` docs(web): consume named sse events in useTaskEvents
  - `30329e8` fix(web): consume named sse events in task event store
  - `b9a7111` docs(workflow): use real command path in m07 temporal integration tests
  - `167cd1a` test(workflow): cover pause resume cancel and command idempotency
  - `feb65ec` docs(workflow): fill m07 execution record template values
- working tree：clean；pushed：NO

## 8. 完成结论
- M-07 DONE 门禁全部满足后由控制器填写 DONE。
