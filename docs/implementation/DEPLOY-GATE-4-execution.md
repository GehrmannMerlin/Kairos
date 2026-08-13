# DEPLOY-GATE-4 Execution（FAST DEVELOPMENT RC）

状态：**PASS_FAST_DEV**（2026-08-12）— M-15 增量验收全 PASS，复用既有 Staging PASS 证据，无 P0/P1
策略：DELTA RC ACCEPTANCE（M-13/M-14 既有真实 Staging PASS 直接作为 REUSED_PASS，不重跑全量回归）

## Release 信息
- 分支：`feature/M-15-artifacts-lifecycle`（pushed：NO）
- 本地 HEAD：`c0d76d2`
- Release SHA（M-15 HEAD，migration 0013 前基线 `58da9e0`）：`58da9e0eb26a`
- 不可变镜像：
  - `kairos-web:staging-58da9e0eb26a`
  - `kairos-api:staging-2794a95580e4`（含 dataset_version 扩宽 fix）
  - `kairos-worker:staging-cfda95b0bd5c`（M-15 未改 Worker Activity，保持既有 tag）
- Migration：0012（artifact lifecycle + task.restore_state）→ 0013（artifacts.dataset_version VARCHAR(100)）→ **head = 0013**

## 部署前 Preflight
- 磁盘：部署前 37G 可用（61%），无磁盘紧急。
- 部署后清理：移除被取代的 `staging-59428205a14e` / 首个 `staging-58da9e0eb26a` api 镜像，保留 current + rollback；`docker builder prune -f` 0B。
- 备份：
  - `/srv/kairos/backups/staging-m15-pre-20260812-172840.sql`（0012 前）
  - `/srv/kairos/backups/staging-m15-fix-20260812-173425.sql`（0013 前）
- Rollback 镜像保留：`kairos-api/worker:staging-cfda95b0bd5c`（M-14 稳定镜像）。

## Staging Health（全部 PASS）
- HTTPS `https://staging.kairos.ac.cn/` → 200
- `/api/health/live` → ok；`/api/health/ready` → ok（postgresql / temporal / object_storage 全 ok）
- api `staging-2794a95580e4` healthy；web `staging-58da9e0eb26a`；worker `staging-cfda95b0bd5c`
- postgres / temporal / minio / otel 全部 healthy
- migration head = **0013**

## REUSED_PASS（既有真实 Staging 证据，不重跑）
- Auth / Provider / Task / Workflow：Gate-3（gate3.a@kairos.test DeepSeek + Tavily AVAILABLE，真实探索任务）
- Data / Review / Quality / Execution / Evidence：M-13/M-14 Staging（task 44：Quality 38 记录 19/19、Execution 阶段/DAG、Evidence 完整追溯）
- Owner Isolation：M-14 Cross-user Evidence 404 PASS

## NEW_PASS（M-15 本轮新增验证，真实 task 44 + disposable draft）
| # | 项 | 结果 |
|---|---|---|
| 1 | M-15 Staging deployment healthy | PASS |
| 2 | Completion Card（task 44，真实终态任务） | PASS（PARTIALLY_COMPLETED，completion_id=12，passed 19 / needs_review 19 / rejected 0，url_processed 49，can_export_formal） |
| 3 | Formal CSV export | PASS（export 200，row_count=19 = passed count，CSV 列 `标题,发布日期,发文机关,文号,原文链接` = 冻结 Spec schema，仅 PASSED） |
| 4 | CSV download | PASS（200，text/csv，UTF-8 BOM，header+19 行） |
| 5 | Artifact idempotent reuse | PASS（相同 ExportRequest 两次 → 同一 artifact_id=1，content_hash 相同，不重复生成 Blob） |
| 6 | Task soft delete | PASS（disposable draft → DELETE → state=DELETED） |
| 7 | Deleted View | PASS（`/tasks?view=deleted` 可见被删任务） |
| 8 | Task restore | PASS（restore → 回到软删除前状态 DRAFT） |
| 9 | Retention dry-run Evidence protection | PASS（real task 44 数据：scanned=118，eligible=118（--days 0 合成窗口），**protected=81** 个被 FieldEvidence 引用的 snapshot 不会删除，dry-run deleted=0） |
| 10 | Backup / rollback metadata | PASS（2 份迁移前备份 + M-14 稳定镜像保留） |

注：Retention 以 `--days 0` 合成窗口证明保护机制（当前真实数据 <1 天，90 天策略下 eligible=0）；CLI `--days 0` 参数 bug 已在本地修复提交（`c0d76d2`，下一发布生效）。

## Deferred（本轮明确不验证/不实现）
- 完整 Reliability Matrix（暂停/恢复重复验证、Worker restart、高并发、资源池、大数据 Artifact、Retention 定时调度、Restore Drill、完整多用户矩阵）→ M-16 / M-17 / DEPLOY-GATE-5
- **DEFERRED-DYNAMIC-E2E-01**：继续登记技术债，不处理、不标记 PASS。
- Permanent Delete 真实 Staging 实验：不做（本地 scoped automated test 已验证，FAST Gate 不要求）。

## 结论
无 P0/P1 → **DEPLOY-GATE-4 = PASS_FAST_DEV**。
**M-15 = DONE**，**M-16 = UNBLOCKED**。
