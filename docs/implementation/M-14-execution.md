# M-14 模块执行记录

状态：**IN_PROGRESS**（本地 scoped 验证通过，待轻量 Staging Acceptance）
负责人/Agent：Claude Code
Baseline SHA：`10e74c7`（docs(review): record M-13 DONE，M-13=DONE）
分支：`feature/M-14-quality-execution-evidence`（pushed：NO）
依赖模块：M-05（前端壳）、M-07（DomainEvent/SSE）、M-08（frozen PlanVersion）、M-10（PageSnapshot/ObjectStorage）、M-11（FieldEvidence）、M-12（QualitySnapshot/ValidationResult/三分区）、M-13（Data 页 + records query 契约）

## 1. 模块目标
完成 D-024 / D-048 二级页面闭环：`/tasks/:id/quality`（诊断 + Data 下钻）、`/tasks/:id/execution`（阶段摘要 + 脱敏时间线 + 只读 Plan DAG + Node Detail Drawer）、`/tasks/:id/evidence/:id`（历史快照查看器 + Quick Evidence + owner-safe 内容访问）。不新增页面、无金额 UI、不 live refetch。

## 2. 输入契约（只消费既有事实）
- M-12 `QualitySnapshot`/`ValidationResult`/`FieldConflict`/`Record.partition`/`URLResource`。
- M-04 `DomainEvent`/`Run`/`URLResource`/`PageSnapshot`/`Record`/`FieldEvidence`。
- M-08 `PlanVersion.payload.graph`（nodes/edges）+ `NodeRegistry`。
- M-10 `ObjectStorage`（`app.infra.deps.storage()`）。
- M-13 `RecordListParams` deep-link 契约（status/review_type/source_type/extract_method）。

## 3. 本模块实现清单
- [x] `app/quality/`：Quality Query API `GET /tasks/{id}/quality`（分区计数/字段完整性/来源覆盖/抽样/诊断，Metrics Version Boundary 绑定最新 QualitySnapshot；typed `QualityDrilldown` 与 M-13 contract 对齐）
- [x] M-13 `records` source_type 修复：真实记录 payload 无 source_type，改为 `URLResource` 关联解析（payload 兜底），保证 D-062 来源下钻准确（参数名不变，不新增 Data 无法解析的参数）
- [x] 前端 `features/quality/`：`TaskQualityView.vue` 真实诊断页 + `buildDataLink` 统一 Deep Link 生成（无编辑/审核动作）
- [x] `app/execution/`：`GET /tasks/{id}/execution`（阶段聚合：Goal/Plan/SourceDiscovery/Fetch/Extraction/Validation，来自 Run/DomainEvent/URLResource/Record）+ `GET /execution/timeline`（脱敏 allowlist DTO、稳定排序 occurred_at+id、category 过滤、after_id cursor 分页）
- [x] `GET /tasks/{id}/execution/dag`（frozen PlanVersion 图 + stage 映射 + resource class + 脱敏参数摘要）+ `GET /execution/nodes/{node_id}`（Node Detail 数据，无 Retry 命令保持只读）
- [x] `app/evidence/`：`GET /tasks/{id}/evidence/{snapshot_id}`（EvidenceView，display_mode 按 D-064：image→snapshot / snippet→text / 否则→raw）+ `GET /evidence/{snapshot_id}/content`（owner 校验后从 ObjectStorage 流式返回历史字节，`?download=1` 附件；绝不 live fetch source）
- [x] 前端 `features/evidence/` + `features/execution/`：`TaskEvidenceView.vue`（sandbox iframe + CSP meta 安全展示，DOMParser locator，搜索/复制/打开原始来源，Quick Evidence Drawer 接通）、`TaskExecutionView.vue`（阶段卡 + 时间线过滤器 + 只读 DAG + Node Detail Drawer）

## 4. 明确不做（M-15+ / 延期）
M-15 CSV/Artifact/Delete/Restore/Retention、Completion Card、DEFERRED-DYNAMIC-E2E-01（已登记技术债，本轮完全不处理）、DEPLOY-GATE-4（M-13~M-15 完成后强制执行）、M-17 Observability 平台。未新增任何页面类型；Node Retry 命令不存在故不实现。

## 5. 验收证据（M-14 scoped，未重跑历史全量 / Golden）
```bash
# backend（25 passed：quality 6 + execution 12 + evidence 5 + source_type 2）
.venv/Scripts/python.exe -m pytest tests/quality tests/execution tests/evidence tests/review/test_records_source_type.py -q
.venv/Scripts/python.exe -m ruff check app/quality app/execution app/evidence app/api/routes/quality.py app/api/routes/execution.py app/api/routes/evidence.py app/review/repository.py tests/{quality,execution,evidence} tests/review/test_records_source_type.py  # PASS
.venv/Scripts/python.exe -m mypy app/quality app/execution app/evidence app/api/routes/{quality,execution,evidence}.py app/review/repository.py   # PASS（16 files）
.venv/Scripts/python.exe -m alembic heads            # 0011 (head)，NO MIGRATION
.venv/Scripts/python.exe -c "from app.main import create_app; create_app()"  # PASS
# frontend（43 passed / 12 files：quality + execution + evidence + data + deepLinks）
cd frontend && npx vitest run src/features/quality src/features/execution src/features/evidence src/features/data src/features/tasks/Task{Quality,Execution,Evidence}View.test.ts src/app/overlay/drawers/{EvidenceQuickDrawer,NodeDetailDrawer}.test.ts src/app/router/deepLinks.test.ts
cd frontend && npm run build                         # vue-tsc + vite build PASS
```

## 6. 跨模块联动结果
- 上游 M-12 QualitySnapshot/ValidationResult/三分区：PASS（只消费，不重建第二套指标）。
- 上游 M-13 Data 查询契约：PASS（Quality drilldown 复用；source_type 解析修复使真实数据可下钻）。
- 上游 M-08 frozen PlanVersion / NodeRegistry：PASS（DAG 只读）。
- 上游 M-10 ObjectStorage：PASS（证据内容 owner-safe 读取，live_fetch_count=0 断言）。
- 下游 M-15 Handoff：Quality snapshot ref + Evidence ref + 查询 metadata 已稳定可复用。

## 7. 完成结论
**M-14 = IN_PROGRESS**（本地 scoped 全绿；轻量 Staging Acceptance 三条极小验证后置为 DONE）。
**M-15 = UNBLOCKED**。**DEPLOY-GATE-4 = NOT_REACHED**。
