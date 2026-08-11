# M-11 模块执行记录

状态：**DONE_LOCAL**（2026-08-11）
负责人/Agent：Claude Code
Baseline（M-10 DONE_LOCAL）SHA：`9e82191`（docs(fetch): record M-10 execution）
分支：`feature/M-11-extraction-evidence`（pushed：NO）
依赖模块：M-03（ModelProvider/ModelInferenceClient/CredentialVault）、M-04（FieldEvidence/Record/幂等/Checkpoint）、M-06（CollectionSpec 字段 Schema）、M-07（TaskWorkflow/SSE）、M-08（EXTRACT/NORMALIZE Node + register_node_executor seam）、M-10（immutable PageSnapshot/PageSnapshotRef/ObjectStorage）
目标环境：local（M-11 不部署；下一强制 Gate 为 M-09～M-12 后的 DEPLOY-GATE-3）

## 1. 模块目标
实现 D-010 规则优先提取：JSON-LD/Meta/Table → 已验证 CSS/XPath 站点规则 → LLM typed fallback；
LLM 只处理 unresolved 字段，输出必须经过证据接地 + 统一 Schema 校验；每个有效字段持久化
`FieldEvidence`（snapshot/URL/locator/snippet/method/version/confidence），并形成 `EXTRACTED`
Record candidate 交给 M-12（不做去重/冲突/最终分区）。

## 2. 契约
- `app/extraction/contracts.py`：`ExtractorMethod`（json_ld/meta/table/css/xpath/rule/llm）、
  `CandidateValidationStatus`（valid/invalid/unresolved）、`RecordPartition.EXTRACTED`、
  `ExtractionCandidate`（field/raw/normalized/type/method/confidence/version/rule_version/
  model_config_id/locator/snippet/validation_status/issue_code/evidence_ref）、
  `ExtractionResult`（统一 extractor 返回）、`ExtractionIssue`、`ExtractionSettings`（集中阈值）
- `app/extraction/protocol.py`：`Extractor` protocol + `ExtractionContext`（有界上下文 + user_id）
- `app/extraction/normalize.py` / `schema_validator.py` / `confidence.py`：字段级 canonicalization、
  统一 Schema 校验（LLM 无 bypass）、确定性系统置信度
- `app/extraction/context.py`：`ExtractionContextBuilder`（从 immutable PageSnapshot 生成有界安全上下文）
- `app/extraction/structured.py`：`JsonLdExtractor` / `MetaExtractor` / `TableExtractor`（确定性）
- `app/extraction/site_rules.py`：`SiteRuleExtractor` + 注册安全 `RULE_TRANSFORMS`（禁止 eval）
- `app/extraction/llm.py`：`SemanticExtractionAgent`（Pydantic AI FunctionModel 包装 M-03
  ModelInferenceClient，typed 输出 + 一次 repair）
- `app/extraction/grounding.py`：`evidence_is_grounded`（幻觉证据拒绝）
- `app/extraction/rule_learning.py`：`RuleCandidate` / `RuleValidationResult` / `RuleLearningService`
  （LLM 只提出候选 → 代表性页面验证 → 质量阈值 → ACTIVE / 回退）
- `app/extraction/pipeline.py`：`ExtractionPipeline`（阶梯编排 + 字段级 fallback）
- `app/extraction/repository.py`：`ExtractionRepository` / `FieldEvidenceRepository` /
  `ExtractorRuleRepository`（flush 单事务）
- `app/extraction/model_resolver.py`：`ExtractionModelResolver`（冻结 PlanVersion 模型解析，Secret 不落盘）
- `app/extraction/executor.py`：`ExtractNodeExecutor` / `NormalizeNodeExecutor`
- `app/extraction/executors.py`：`install_extraction_executors()`（EXTRACT + NORMALIZE 注册）
- `app/api/events.py`：`extraction.*` / `normalize.completed` SSE 映射
- Migration：`0009_extract_evidence_rules.py`（field_evidence 扩展 + extractor_rules 表）

## 3. 行为
- 提取阶梯：Structured（JSON-LD→Meta→Table）→ Verified Site Rules → LLM fallback；只有 unresolved
  字段继续下发，确定性已验证值不被低优先级 extractor 静默覆盖（字段级 fallback，非页面级重发）。
- LLM 输入最小化：冻结 Spec 的 unresolved 字段 + 有界正文/上下文 + 确定性摘要；Secret 绝不进 prompt。
- LLM typed 输出（SemanticExtractionResult）；evidence quote 必须在页面上下文中存在，
  schema validation 与 grounding 全部通过才成为有效候选；一次 repair，不无限调用。
- 规则只消费 ACTIVE `ExtractorRuleVersion`；RULE_MISMATCH 记失败并回退下一层，不无限使用；
  规则版本不可变，v1→v2 不 UPDATE，支持回滚 v1；历史 Evidence 永不改写。
- 规则学习：LLM 只提出 RuleCandidate，程序对代表快照验证 precision/coverage/样本数阈值后
  Promote 为 ACTIVE；阈值不过保持 DRAFT/不 Promote。
- EXTRACT executor：消费 `pending_snapshots`（无 Record 的 snapshot），单事务写入
  Record(EXTRACTED) + FieldEvidence + extraction.* 事件后 commit；Checkpoint 由 Workflow 在
  `execute_safe_unit` 返回后提交。NORMALIZE 只做字段级 canonicalization。
- 幂等：同一 snapshot/spec/schema/extractor-version 重跑不重复产生 candidate/evidence；
  rule/spec/extractor 版本变化允许新 extraction identity（record 幂等 identity 包含
  snapshot content_hash + spec_version + values fingerprint）。

## 4. 明确不做（M-12+）
业务去重 / 跨来源最终冲突裁决 / PASSED / NEEDS_REVIEW / REJECTED 最终分区 / QualityMetrics /
CompletionDecision / 分层抽样 / CSV / Record Review UI / Evidence Viewer / 浏览器 Agent。
未部署 Staging（DEPLOY-GATE-3 NOT_REACHED）。真实外部 LLM 不在本地门禁内（Fake 模型验证）。

## 5. 验收证据
### scoped tests
```bash
.venv/Scripts/python.exe -m pytest tests/extraction -q
# 37 passed：contracts / evidence_persistence / schema_validator / structured（JSON-LD/Meta/Table）/
#   site_rules（CSS/XPath + transform + RULE_MISMATCH）/ llm_fallback（typed + grounding +
#   4 类 invalid LLM 拒绝）/ rule_learning（promote PASS + threshold FAIL）/ pipeline（字段级 fallback +
#   结构化无 LLM）/ idempotency（双跑无重复）/ fixtures（A structured no-LLM / B site rule + rollback /
#   C LLM fallback unresolved-only / 证据在快照清理后保留）/ normalize / executor_binding /
#   M-10→M-11 handoff（READY_FOR_FETCH→Fetch→Snapshot→Extract）
```
### ruff / mypy / import
```bash
.venv/Scripts/python.exe -m ruff check app/extraction app/worker.py app/api/events.py tests/extraction   # PASS
.venv/Scripts/python.exe -m ruff format --check app/extraction                                            # PASS
.venv/Scripts/python.exe -m mypy app/extraction                                                            # PASS（18 files）
.venv/Scripts/python.exe -c "import app.worker; import app.extraction.executors"                          # PASS
```
### Migration
```bash
.venv/Scripts/python.exe -m alembic heads            # 0009 (head)
.venv/Scripts/python.exe -m alembic upgrade head --sql   # field_evidence 扩展 + extractor_rules 生成
```
### secret scan
固定测试 secret 未出现在 app/extraction；模型 API Key 仅经 CredentialVault 执行期临时解密，
`model_config_id` 只是引用，不进入 prompt/日志/Evidence/DomainEvent。

## 6. Git 证据（feature/M-11-extraction-evidence，基线 9e82191，pushed NO）
| Commit | 内容 |
|---|---|
| b9873f1 | feat(extraction): add typed extraction contracts and evidence persistence（migration 0009） |
| 9d4edfa | feat(extraction): add schema validator and bounded context builder |
| 6344424 | feat(extraction): add deterministic json-ld meta and table extractors |
| c777264 | style(extraction): satisfy ruff on structured extractors and fixtures |
| 91cc163 | feat(extraction): add validated css and xpath site rule extraction |
| aabd295 | feat(extraction): add grounded llm typed fallback and rule learning |
| 908d653 | feat(workflow): bind extract and normalize activities with pipeline and sse |
| e6a02e5 | test(extraction): cover three fixture classes and m10 handoff |
| （+ normalize test / docs） | test(extraction): cover normalize executor；docs(extraction): record M-11 execution（本记录） |

## 7. 跨模块联动结果
- 上游 M-03 ModelInferenceClient / CredentialVault：PASS（SemanticExtractionAgent 复用
  FunctionModel 包装 M-03，不引入第二套 SDK；ExtractionModelResolver 从冻结 PlanVersion 解析）
- 上游 M-04 FieldEvidence / Record / Idempotency / Checkpoint：PASS（扩展 FieldEvidence 链，
  EXTRACTED 分区，单事务 + Workflow checkpoint）
- 上游 M-06 CollectionSpec 字段 Schema：PASS（只消费冻结 Spec 字段，extractor 不动态改 Schema）
- 上游 M-07 TaskWorkflow / SSE：PASS（EXTRACT/NORMALIZE 走 execute_safe_unit + commit_checkpoint；
  extraction.* 聚合事件）
- 上游 M-08 Node Registry / executor seam：PASS（只注册 EXTRACT/NORMALIZE，无重复 Node）
- 上游 M-10 PageSnapshot / PageSnapshotRef / ObjectStorage：PASS（只读 snapshot，不重新实时抓取）
- 下游 M-12 Handoff：Record(EXTRACTED) + extraction_candidates（FieldEvidence rows）+ field
  issues + rule/model metadata + snapshot ref + spec version

## 8. 完成结论
**M-11 = DONE_LOCAL**。下一阶段：M-12（验证/去重/冲突/三类结果/质量指标）；DEPLOY-GATE-3 在
M-09～M-12 全部完成后（NOT_REACHED）。
