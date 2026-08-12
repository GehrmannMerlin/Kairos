# M-15 模块执行记录

状态：**DONE_LOCAL**（2026-08-12）— 本地 scoped 验证全绿；Staging 部署与 FAST DEPLOY-GATE-4 见本记录第 8 节
负责人/Agent：Claude Code
Baseline SHA：`d2464c7`（M-14 DONE HEAD，migration 0011）
分支：`feature/M-15-artifacts-lifecycle`（pushed：NO）
依赖模块：M-04（Artifact/DomainEvent/状态机）、M-08（GENERATE_ARTIFACT Node Registry）、M-10（ObjectStorage/PageSnapshot content-hash）、M-11（FieldEvidence raw_snippet）、M-12（CompletionDecision/QualitySnapshot）、M-13（records query 契约）、M-14（Evidence ref）

## 1. 模块目标
完成 D-005/D-014/D-016/D-023/D-025/D-036/D-039/D-042/D-043/D-044/D-048/D-060/D-065/D-067/D-072 闭环：CSV Artifact（正式/待复核/审核完整、幂等导出、owner-safe 下载）、Chat 完成总结卡（NORMAL/PARTIAL、无假百分比）、Task 软删除/恢复（已删除视图）、永久删除（引用安全对象清理）、Retention 生命周期清理 job。不新增页面、无金额 UI、不重跑 M-09~M-14 全量回归、DEFERRED-DYNAMIC-E2E-01 不处理。

## 2. 实施计划
- 使用 superpowers:writing-plans 真实调用。
- Plan 文件：`docs/superpowers/plans/2026-08-12-m15-artifacts-deletion-retention.md`（8 个 macro task）。
- Spec Coverage / Placeholder Scan / Type Consistency 全部执行。
- **PROJECT SELF-APPROVAL：CHECK 1-21 全部 PASS。**
- **PLAN SELF-APPROVAL：PASS**（24 项全部 PASS）。
- 使用 superpowers:executing-plans 自动执行（Inline Execution，未启动大量 subagent）。

## 3. 实现清单
- **ArtifactService**（`app/artifacts/service.py`）：`export()` 幂等导出（dataset_version 数据状态指纹 + canonical filter snapshot + request_fingerprint 复用）、`download()` owner-safe、`list_for_task()`。
- **Export types**（`app/artifacts/contracts.py`）：`ExportType{formal,review,audit}`、`ExportScope{current,all}`、`ExportRequest/ExportFilter/ArtifactRef/ArtifactView/CompletionCardView/PermanentDeleteCommand`。
- **filter snapshot**：复用 M-13 `RecordListParams`/`ReviewRepository.query_records_all`（同语义不分页、record.id ASC 确定性）。
- **dataset_version**：`"ds-" + sha256(canonical_json([(record.id, partition, review_type, review_reason, data_version, final_fields) ...]))`，任何数据变化 → 新指纹 → 新 Artifact。
- **Artifact identity**：`(user_id, task_id, dataset_version, export_type, request_fingerprint)` + `content_hash` + `schema_version`。
- **ObjectStorage**：CSV bytes 存 `artifacts/u{user}/csv/{content_hash}.csv`（content-addressable，存在则跳过 put）；DB 只存 metadata/hash/ref。
- **download**：`GET /tasks/{id}/artifacts/{id}/download` 流式返回，`filename*=UTF-8''` sanitize，owner-safe 越权 404。
- **Completion Card**（`app/api/routes/completion.py` + `CompletionCard.vue`）：全部来自 DB facts（CompletionDecision + 分区计数 + URLResource 处理事实），NORMAL/PARTIAL，无假百分比，导出复用同一 Export Modal；稳定 `completion_id` 幂等渲染。
- **Soft Delete / Restore**（`TaskCommandService.delete_task/restore_task` + `DomainService.transition_task`）：`deleted_at` + `restore_state`（restore 回到软删除前终态，不破坏 Run execution facts）；`/tasks?view=deleted` 已删除视图；运行中任务删除 409 必须先 cancel。
- **Permanent Delete**（`app/artifacts/deletion.py` + `POST /tasks/{id}/permanent-delete`）：owner + state==DELETED + 二次强确认；manifest 显式删除 task-owned DB 行（不依赖 FK cascade）→ 跨表跨用户引用复查 → 最后一个引用才 `storage.delete`；幂等可恢复。
- **Retention**（`app/artifacts/retention.py` + `cli.py` + `infra/scripts/retention_cleanup.py`）：`RetentionPolicy/CleanupResult`（scanned/eligible/protected/deleted/failed/bytes_freed），只清理「到期 + 无保护引用」重型 PageSnapshot 对象；`FieldEvidence.snapshot_id` → PROTECTED；`raw_snippet/source_locator` 长期保留；`--dry-run` 安全。
- **设置 → 存储与数据**（`app/api/routes/settings_data.py` + SettingsView.vue）：存储概要 + retention 清理预览（dry-run），不暴露 MinIO。

## 4. Migration
`alembic/versions/0012_artifact_deletion_lifecycle.py`（expand-only，head = **0012**）：
- `tasks.restore_state`（软删除前终态）
- `artifacts.request_fingerprint/schema_version/row_count/size_bytes/filename/status` + index `(user_id, task_id, request_fingerprint)`
- 无表重建、无数据迁移。

## 5. 验收证据（M-15 scoped，未重跑历史全量 / Golden）
```bash
# backend（20 passed：models 2 + csv_builder 4 + artifact_service 3 + artifact_api 1 + completion 3 + soft_delete 2 + permanent_delete 3 + retention 2）
.venv/Scripts/python.exe -m pytest tests/artifacts -q          # .................... PASS
.venv/Scripts/python.exe -m ruff check app/artifacts app/api/routes/{artifacts,completion,settings_data,tasks}.py app/api/router.py app/domain/{task_commands,service,repository}.py app/review/repository.py app/infra/object_storage.py app/config.py tests/artifacts  # PASS
.venv/Scripts/python.exe -m mypy app/artifacts app/api/routes/{artifacts,completion,settings_data,tasks}.py app/domain/{task_commands,service,repository}.py app/review/repository.py app/infra/object_storage.py   # PASS（17 files）
.venv/Scripts/python.exe -m alembic heads                       # 0012 (head)
.venv/Scripts/python.exe -c "from app.main import create_app; create_app()"  # import PASS
# frontend（9 passed / 3 files：ExportModal 3 + CompletionCard 2 + DeletedView 4）
cd frontend && npx vitest run src/features/artifacts src/app/overlay/modals/ExportModal.test.ts src/features/tasks/DeletedView.test.ts  # PASS
cd frontend && npx vue-tsc --noEmit && npm run build            # PASS
```

## 6. 跨模块联动结果
- M-13 records query 契约：PASS（导出复用 `query_records_all`，filter snapshot 一致）。
- M-08 Node Registry：GENERATE_ARTIFACT 已注册（复用契约，未新增第二个 Node）。
- M-10 ObjectStorage：PASS（`delete` 方法扩展；content-addressable 复用，exists 检查幂等）。
- M-12 CompletionDecision/QualitySnapshot：PASS（Completion Card + dataset_version 消费既有事实，不重建）。
- M-14 Evidence 引用：PASS（Retention/永久删除以 DB 引用复查保护）。

## 7. Git 证据（feature/M-15-artifacts-lifecycle，基线 d2464c7，pushed NO）
| Commit | 内容 |
|---|---|
| 74a8559 | feat(artifact): add M-15 artifact lifecycle and task restore columns |
| 58f8aaf | feat(artifact): add deterministic csv builder and object delete |
| 73a980f | feat(artifact): add idempotent csv export with dataset fingerprint |
| f738d76 | feat(web): connect export modal and artifact download |
| ec92923 | feat(web): render normal/partial completion card from db facts |
| 5050ced | feat(task): add soft delete, restore and deleted view |
| 788c397 | feat(storage): add reference-safe permanent deletion |
| 975fb28 | feat(web): wire soft delete, restore and permanent delete confirm |
| 8ab0247 | fix(web): remove unused reset in delete confirm modal |
| 37e89b4 | feat(storage): add retention cleanup policy and dry-run job |
| 58caaa3 | fix(artifact): guard completion scope metadata and infra noqa |
| 3fe3114 | chore(artifact): apply ruff/mypy cleanup to M-15 files |
| 6c25e68 | docs(plan): record M-15 artifacts deletion retention plan |

## 8. Staging + FAST DEPLOY-GATE-4
见 `docs/implementation/DEPLOY-GATE-4-execution.md`（本阶段完成后创建）。
