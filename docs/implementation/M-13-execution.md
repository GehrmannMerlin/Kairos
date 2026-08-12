# M-13 模块执行记录

状态：**DONE**（2026-08-12）— 本地 scoped 验证 + Staging 轻量 Data/Review Acceptance 均通过
负责人/Agent：Claude Code
Baseline SHA：`31fac50`（docs(deploy): defer dynamic Gate-3 path for fast development，DEPLOY-GATE-3=PASS_FAST_DEV）
分支：`feature/M-12-validation-quality`（pushed：NO）
Staging 部署：`kairos-{web,api,worker}:staging-6d0472d3157d`（migration `0011` 已应用）
依赖模块：M-05（前端壳）、M-07（SSE/TaskWorkflow）、M-12（三分区 + ValidationResult + allowed_actions 契约 + QualitySnapshot）

## 1. 模块目标
完成 `/tasks/:id/data` 真实业务闭环：三分区 Tabs + 实时计数、后端 Records Query（分页/搜索/AND 筛选/排序/列设置）、Record Detail Drawer、单条审核（人工修正/通过/拒绝/Agent 重新处理）、批量审核（语义兼容 + 审计）、Data query 参数可被质量页 Deep Link 复用（D-040/041/042/044/060/061/062）。

## 2. 输入契约（只消费既有事实）
- M-12 `ValidationPartition` / `ReviewReason` / `AllowedReviewAction` / `ValidationResult.allowed_actions` / `ValidationRepository.latest_snapshot`（dataset_version）。
- M-04 `Record`（partition/review_type/review_reason/payload）+ `FieldEvidence`（证据元数据）。
- M-07 SSE `/api/events/tasks/{id}` 通道 + `map_domain_event_to_sse` / `query_task_events`。

## 3. 本模块实现清单
- [x] migration `0011`：records.data_version + record_field_overrides（人工修正保留 original/final/value_source/modified_by/modified_at）+ record_review_actions（单条/批量审计，append-only）
- [x] `app/review/` 领域包：contracts（RecordView/RecordDetailView/RecordListParams/ReviewAction/Batch*）/ repository（owner-safe 查询/覆写/审计）/ policy（allowed_actions 派生 + 批量语义兼容）/ views（payload 叠加覆写）/ service（approve/reject/edit/agent_reevaluate/batch）/ reevaluate（新事件+outbox+recompute 标记，保留历史）
- [x] Records Query API：GET /tasks/{id}/records（分页/搜索/AND 筛选/排序/partition 计数/dataset_version）+ GET /{record_id}（Drawer 详情）+ POST /{record_id}/review + POST /batch-review，全部 owner-safe 404
- [x] SSE `record.*` 事件映射（RECORD_APPROVED/REJECTED/EDITED/REEVALUATE_REQUESTED/APPROVED_BATCH/REJECTED_BATCH）；query_task_events 经 records 表关联并入 record 事件
- [x] 前端 `features/data/`：types / data.api / useRecords（含过期响应丢弃）/ useRecordEvents / useRecordDetail / useBatchReview
- [x] `TaskDataView.vue`：三分区 Tabs + 实时计数、搜索/筛选/排序/列设置（本地 UI）、分页、行多选批量工具条、Deep Link query 回读（status→Tab、q→搜索、review_type/source_type/extract_method→筛选）
- [x] `RecordDrawer.vue`：字段值 + 证据元数据 + 人工修正（USER_OVERRIDE 保留原值）+ 通过/拒绝/Agent 重新处理（allowed_actions 门控）
- [x] `.gitignore`：运行时 `data/` 规则改为根级 `/data/`，避免误吞 frontend/src/features/data

## 4. 明确不做（M-14+ / 延期）
M-14 Quality 完整页面、Evidence Viewer、Execution/DAG 完整 UI；M-15 CSV Artifact；M-16 Resource Scheduler；Golden C Dynamic Plan 修复（DEFERRED-DYNAMIC-E2E-01，留待 DEPLOY-GATE-4）；冲突裁决/合并重复只保留后端 allowed_actions 与 disabled UI 占位（不在 M-13 落地）。`agent_reevaluate` 只产生事件+outbox+recompute 标记，真实重抓/重提取不在本模块。

## 5. 验收证据
### M-13 scoped tests（未重跑历史模块全量回归 / Golden A/B/C）
```bash
.venv/Scripts/python.exe -m pytest tests/review -q        # 31 passed
#   persistence 6（migration 0011 表 + overrides + audit + AND 筛选 + q 搜索 + owner 隔离）/
#   policy 8（allowed_actions 派生 + 批量语义兼容）/
#   service 5（approve/reject/edit + 覆写保留 + 乐观锁 + 越权动作拒绝）/
#   batch 3（同原因通过 + 审计 batch_operation_id；不同原因整批拒绝；reject 不限原因）/
#   records_api 5（Query 计数/分区/Deep Link/越权 404/Drawer 证据/review 全链）/
#   reevaluate 1（事件+outbox+保留历史+recompute 标记）/
#   sse 3（record.*→SSE 映射 + 任务流重放含 record 事件）
.venv/Scripts/python.exe -m ruff check app/review app/api/routes/records.py app/api/events.py  # PASS
.venv/Scripts/python.exe -m mypy app/review app/api/routes/records.py app/api/events.py        # PASS（9 files）
.venv/Scripts/python.exe -m alembic heads                    # 0011 (head)；upgrade --sql 含新表
.venv/Scripts/python.exe -c "from app.main import create_app; create_app()"                    # PASS
cd frontend && npm run test:unit                               # 91 passed（24 files，含 data.api/useBatchReview/TaskDataView/RecordDrawer）
cd frontend && npm run build                                   # vue-tsc + vite build PASS
```
### Staging 轻量 Data/Review Acceptance（真实 task 44 数据，非全量 Deploy Gate）
- 部署：`kairos-{web,api,worker}:staging-6d0472d3157d`，migration `0011` 已应用（data_version + 两新表就位），/api/health/ready 200。
- `GET /api/tasks/44/records`：partition_counts `{needs_review:20, passed:18}`，total 38（与 Golden B 真实记录一致）。
- `GET ?partition=needs_review`：20 条，allowed_actions `[approve, edit, reject, agent_reevaluate]`。
- `GET /tasks/44/records/{id}`：7 字段 + 证据元数据。
- `POST review edit`：字段 USER_OVERRIDE 人工修正，partition 保持 needs_review。
- `POST review approve`（record 135）：→ passed。
- DB 确认 `record.approved` / `record.edited` domain event 落库且 payload.task_id=44。
- 说明：acceptance 对 task 44 真实修改 1 条（approve）+ 1 条（人工修正），属验收预期，record/evidence 历史保留。

## 6. Git 证据（feature/M-12-validation-quality，基线 31fac50，pushed NO，11 commits + 1 docs commit）
| Commit | 内容 |
|---|---|
| 42d9c25 | feat(review): add review persistence contracts and repository（migration 0011 + contracts/repository） |
| 8ad74ce | feat(review): derive record allowed_actions from partition policy |
| 77bc24a | feat(review): add single record review commands（approve/reject/edit + 覆写保留） |
| b2fc746 | feat(api): add records query and review endpoints |
| c0ae26f | test(review): cover semantically-gated batch review with audit |
| 49c438e | feat(review): request agent reevaluate with append-only history |
| 0456d00 | feat(events): surface record review events over SSE |
| 1589841 | feat(web): add records data api client and types（+ .gitignore /data/ 修复） |
| 5be94fd | feat(web): render data workspace tabs with live counts and search |
| dcf964e | feat(web): render record detail drawer with evidence and review actions |
| 6d0472d | feat(web): add gated batch review toolbar and deep-link query recovery |

## 7. 跨模块联动结果
- 上游 M-12 三分区/ValidationResult/allowed_actions：PASS（只消费，不重复建第二套分区）。
- 上游 M-04 Record/FieldEvidence：PASS（扩展 data_version + 覆写/审计表，证据不可变）。
- 上游 M-07 SSE：PASS（record.* 事件经任务流重放，payload 带 task_id）。
- 下游 M-14 Handoff：Quality 指标可下钻到 `/data`（status/review_type/source_type/extract_method/q Deep Link 契约固定）。
- 下游 M-15 Handoff：Data 页当前筛选快照可作导出范围（query 参数即 filter snapshot）。
- 安全：全部新表 user_id 边界 + owner-safe 404；record_review_actions append-only；覆写不触碰 PageSnapshot/FieldEvidence。

## 8. 完成结论
**M-13 = DONE**。Staging 轻量 Data/Review Acceptance 通过（真实 task 44 数据：查询/分区/详情/人工修正/通过/SSE 全链）。**M-14 = UNBLOCKED**。**DEPLOY-GATE-4 = NOT_REACHED**（M-13～M-15 完成后强制执行，本轮不进入）。
