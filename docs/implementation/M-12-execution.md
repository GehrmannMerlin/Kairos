# M-12 模块执行记录

状态：**DONE_LOCAL**（2026-08-11）
负责人/Agent：Claude Code
Baseline（M-11 DONE_LOCAL）SHA：`02c4677`（docs(extraction): note staging intermediate update and deployment fixes）
当前稳定 Staging release（运行镜像）：`f25a5378113a`（kairos-{web,api,worker}:staging-f25a5378113a）
分支：`feature/M-12-validation-quality`（pushed：NO）
依赖模块：M-04（Record/FieldEvidence/状态机/幂等/Checkpoint）、M-06（CollectionSpec 字段 Schema）、M-07（TaskWorkflow/SSE）、M-08（DEDUPLICATE/VALIDATE Node + register_node_executor seam）、M-11（EXTRACTED Record candidate + FieldEvidence）
目标环境：local（M-12 不部署；下一强制 Gate 为 DEPLOY-GATE-3）

## 1. 模块目标
实现 D-006/D-014 的数据可信闭环：把 M-11 产出的 EXTRACTED Record 候选转化为可正式导出的可信数据。验证顺序固定 structure/type → required → evidence → business → dedupe → conflict → sampling；结果固定三分区 PASSED/NEEDS_REVIEW/REJECTED；产出 QualityMetrics 与 CompletionDecision；挂入 M-08 的 Deduplicate/Validate Node 生产 executor。

## 2. 输入契约（只消费已有历史事实，不重新采集）
- M-11 `ExtractionRepository.records_for_task`（partition=EXTRACTED 的 Record candidate）+ `FieldEvidenceRepository` 证据链。
- M-06 冻结 `CollectionSpecVersion` 字段 Schema（`FieldSpec`/`FieldType`）。
- M-08 `NodeRegistry` DEDUPLICATE/VALIDATE 契约 + `register_node_executor` seam。
- M-07 `TaskWorkflow`（`execute_safe_unit` + `commit_checkpoint`）、`app.activities.execution_seam.ExecuteUnitResult`。
- M-04 `Record`（已有 `partition`/`business_key` 列）、`DomainEvent`、`stable_fingerprint`。

## 3. 本模块实现清单
- [x] 数据模型/迁移：migration `0010`（validation_results / dedupe_clusters / field_conflicts / quality_snapshots / completion_decisions + Record review_type/review_reason/validated_at）
- [x] typed contracts：`ValidationPartition` / `ReviewReason` / `AllowedReviewAction` / `ValidationIssue` / `ValidationResult` / `BusinessKeyPolicy` / `ConflictResolution` / `QualityMetrics` / `CompletionDecisionView`
- [x] 验证流水线：`ValidationPipeline`（structure→required→evidence→business→dedupe→conflict→partition）
- [x] 校验器：`StructureTypeValidator`（复用 M-11 `ExtractionSchemaValidator`，不重复第二套 parser）/ `RequiredFieldValidator` / `EvidenceValidator`（SYSTEM_DERIVED 显式例外）/ `BusinessRuleValidator`（typed 注册操作符，禁止 eval）
- [x] 去重：`BusinessUniqueKeyStrategy`（默认 key=全部必填字段）+ `DedupeEngine`（exact fingerprint 归组 + deterministic fuzzy threshold）
- [x] 冲突：`ConflictResolver`（source priority→evidence→method→rule→time→confidence；tie→NEEDS_REVIEW 不静默选值）
- [x] 三分区：`Partitioner`（PASSED/NEEDS_REVIEW/REJECTED + review_type/review_reason/allowed_actions）
- [x] 抽样：`StratifiedSampler`（source/method/rule_version/confidence 四维分层，hash 确定性稳定）
- [x] 质量：`QualityMetricsService`（全部来自数据库事实聚合，denominator 明确）
- [x] 完成判定：`CompletionDecisionService` + `SaturationTracker`（定向/探索饱和/部分完成，无金额条件）
- [x] Activity：`DeduplicateNodeExecutor` / `ValidateNodeExecutor`（单事务 + 幂等）+ `install_validation_executors()` 注册
- [x] Workflow：`resolve_completion` activity + `mark_partial`（RUNNING→PARTIALLY_COMPLETED）+ TaskWorkflow 完成分支
- [x] SSE：`validation.*` / `DEDUPE_COMPLETED` 聚合事件映射
- [x] 安全/用户隔离：全部新表 user_id 边界 + owner-safe find（越权返回 None）
- [x] 自动化测试：CORE TEST A~F + executor 幂等 + 2 条 integration marker
- [x] 文档：本执行记录 + writing-plans 计划文件

## 4. 明确不做（M-13+）
M-13 数据页 Tabs/Record Drawer/人工审核/批量操作 UI、M-14 Quality 页面、M-15 CSV 导出均不实现；只提供 `review_type/review_reason/allowed_actions` 后端契约与 `QualityMetrics` 后端快照。未执行正式 DEPLOY-GATE-3（NOT_REACHED）。

## 5. 验收证据
### scoped tests（M-12 专属，未重跑历史模块全量回归）
```bash
.venv/Scripts/python.exe -m pytest tests/validation -q
# 46 passed：contracts+persistence（migration 0010 全表建表 + 幂等 + owner 隔离）/
#   validators（CORE TEST A validation matrix：结构/必填/证据/SYSTEM_DERIVED/业务规则）/
#   dedupe（CORE TEST B：多来源合并保留证据、retry 稳定、fuzzy 阈值边界）/
#   conflict（CORE TEST C：source priority、证据强度、tie→NEEDS_REVIEW 不静默选值）/
#   partitioner（三分区矩阵）/
#   sampling+quality（CORE TEST D 指标 DB 一致性、CORE TEST E 分层代表性与稳定性）/
#   completion（CORE TEST F：定向/探索饱和/部分完成/无金额字段断言）/
#   executor_pipeline（Deduplicate/Validate 注册 + 单事务幂等 + Evidence gate 分区）
.venv/Scripts/python.exe -m pytest tests/integration/test_m12_validation_workflow.py -q
# 2 collected（marker=integration；本地无完整 Temporal+PG+MinIO 栈时收集跳过，同 M-09/M-10 先例）
```
### ruff / mypy / import
```bash
.venv/Scripts/python.exe -m ruff check app/validation app/activities/completion.py app/activities/task_execution.py app/workflows/task_workflow.py app/worker.py app/api/events.py tests/validation  # PASS
.venv/Scripts/python.exe -m ruff format --check app/validation                                            # PASS（15 files）
.venv/Scripts/python.exe -m mypy app/validation app/activities/completion.py app/activities/task_execution.py app/workflows/task_workflow.py  # PASS（18 files）
.venv/Scripts/python.exe -c "import app.worker; import app.validation.executors; import app.workflows.task_workflow"  # PASS
```
### Migration
```bash
.venv/Scripts/python.exe -m alembic heads        # 0010 (head)
.venv/Scripts/python.exe -m alembic upgrade head --sql   # validation_results/dedupe_clusters/field_conflicts/quality_snapshots/completion_decisions 均生成
```
### secret scan
`app/validation` 与 `app/activities/completion.py` 无 API Key/Cookie/密码明文；LLM 不作为最终 validator（只可能产生 candidate hint）；模型密钥仍只经 CredentialVault 执行期临时解密。

## 6. Git 证据（feature/M-12-validation-quality，基线 02c4677，pushed NO，8 commits + 1 docs commit）
| Commit | 内容 |
|---|---|
| 3220560 | feat(validation): add validation contracts and persistence（migration 0010 + contracts/policies/repository） |
| df19809 | feat(validation): add structure/type/required/evidence/business validators |
| 6269f05 | feat(validation): add deterministic business-key deduplication |
| bea4d35 | feat(validation): add cross-source conflict resolution |
| 33dfb95 | feat(validation): add three partitions and review reasons/actions |
| 706016d | feat(quality): add stratified sampling and quality metrics |
| 53b35d1 | feat(workflow): bind deduplicate and validate executors with completion |
| 2baf083 | test(validation): cover completion decision contracts |
| 1fed998 | docs(validation): record M-12 execution + writing-plans 计划文件 |

## 7. 跨模块联动结果
- 上游 M-04 Record/FieldEvidence/幂等/Checkpoint：PASS（复用 Record 增量扩展，ValidationResult 唯一约束 (record_id, validation_version) 幂等兜底；单事务 + Workflow checkpoint）
- 上游 M-06 CollectionSpec Schema：PASS（复用 `validate_spec_payload`/`FieldSpec`；business key 默认=全部必填字段，不硬编码企业例子）
- 上游 M-07 TaskWorkflow/SSE：PASS（`resolve_completion` 无更多单元时计算完成判定 → complete/mark_partial 分支；state machine 未破坏，mark_partial 走既有 RUNNING→PARTIALLY_COMPLETED 转换；pause/cancel 不受影响）
- 上游 M-08 Node Registry/executor seam：PASS（只注册 DEDUPLICATE/VALIDATE，无重复 Node Type）
- 上游 M-11 Extraction/Evidence：PASS（只消费 EXTRACTED candidate + FieldEvidence，不重新 Fetch/Extract）
- 下游 M-13 Handoff：稳定 Query Contract——Record partition/review_type/review_reason/allowed_actions + ValidationResult + counts + dataset_version
- 下游 M-14 Handoff：QualitySnapshot（immutable，绑定 task/run/spec/validation/sampling policy/dataset version）
- 下游 M-15 Handoff：PASSED-only 契约（正式 CSV 只消费 passed；本轮不实现 CSV）

## 8. 完成结论
**M-12 = DONE_LOCAL**。下一阶段：PHASE B（DEPLOY-GATE-3 真实网页采集 E2E Staging），Gate PASS 后 M-12=DEPLOYED、M-13=UNBLOCKED。
