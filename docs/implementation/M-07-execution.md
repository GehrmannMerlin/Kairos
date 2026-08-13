# M-07 模块执行记录

状态：IN_PROGRESS → **DONE**（控制器全部门禁验证通过）
负责人/Agent：Claude Code — 2026-08-10 / 2026-08-11
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
  - `7c67e4d` docs(workflow): record M-07 execution
  - `db558a7` fix(workflow): keep paused tasks stable and retry outbox signals（整支评审 4 Important + 2 Minor 修复）
- working tree：clean；pushed：NO

## 8. 完成结论

**M-07 DONE 门禁验证（控制器最终复核，2026-08-11）：**

- M-06 = DONE（前置满足）✅
- TaskWorkflow / Run 启动 / typed IDs-only input / Temporal History 无 Secret ✅
- confirmed Spec 冻结校验（`RunSpecNotFrozenError` non-retryable）✅
- pause / PAUSING / PAUSED（协作式停止，超时保持 PAUSED 不腐化）✅
- resume / checkpoint resume（无重复）✅
- cancel / CANCELLING / CANCELLED / cancelled run 不可 resume ✅
- 重复命令幂等（同 key 一次转换 + 一次 outbox + 一次 Signal）✅
- Activity heartbeat（≠ checkpoint）；heartbeat_progress ✅
- Worker crash/restart（真实子进程 kill，batch1 不重复）✅
- SSETaskEvent schema / Last-Event-ID replay / 跨用户隔离 / Task Query fallback ✅
- Task Status Drawer 真实 RUNNING/PAUSING/PAUSED/CANCELLING/CANCELLED 过渡 ✅
- 后端 scoped tests：`tests/state/ domain/test_task_commands.py domain/test_checkpoint.py api/test_task_commands.py api/test_task_events.py` → 25 passed ✅
- Temporal 集成：`test_task_workflow.py + test_worker_crash_restart.py` → 6 passed（start / pause-resume / cancel / duplicate / crash-restart / pause-timeout）✅
- 前端：`type-check` PASS、`lint:check` PASS、`format:check` PASS、`test:unit -- taskEvents TaskStatusDrawer` 6/6 PASS、`build` PASS ✅
- ruff check/format PASS、mypy app PASS（87 files）✅
- secret scan：无新增 Secret（M-06 真实 Key 不在仓库）✅
- docs：本执行记录 DONE ✅
- working tree：clean；pushed：NO ✅

**最终状态：DONE**。下一模块：M-08（Plan 生成、节点注册表、确定性校验与人工审批），暂不开始。
