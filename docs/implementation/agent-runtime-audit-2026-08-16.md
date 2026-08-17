# Agent Runtime 跨模块审计与根因修复（2026-08-16）

> 任务入口：用户报告「采集上海市人民政府官网最近一个月发布的干部任前公示信息」
> （HYBRID）在 Goal Understanding / CollectionSpec 成功后，计划阶段失败：
> `模型未能生成通过确定性校验的计划`。

## 1. 现象

- Goal Understanding 正常输出 `task_type=HYBRID`、字段与来源约束，CollectionSpec 成功冻结。
- 进入计划生成后，`PlanGenerationService._repair_loop`（初始生成 + 1 次 repair）仍无法产出
  通过确定性校验的 Plan，最终抛出 `PlanValidationFailure("模型未能生成通过确定性校验的计划")`。

## 2. Root Cause（唯一，已实证）

失败不在模型能力，而在一次**误回退**：

- commit `6299223 revert: revert Golden C dynamic pipeline experimental commits` 的目标是回退
  5 个「Golden C（真实动态网页 HTTP → Playwright E2E）」实验 commit。
- 但这 5 个 commit 中混入了 **2 个属于 Golden B（探索式真实搜索）的非实验修复**，被一并回退：

| 被误回退 commit | 性质 | 影响 |
| --- | --- | --- |
| `903b9d8 fix(plan): allow candidate edges into url-consuming discovery nodes` | Golden B | 计划校验失败 |
| `6f53936 fix(crawl): fetch consumes access-allowed urls without link_discovery` | Golden B | 执行静默产出 0 结果 |

其中 `903b9d8` 是「模型未能生成通过确定性校验的计划」的直接根因：

- `source_search` 节点的 `output_contract` 只声明 `CANDIDATE`，但 M-09 executor 执行时把搜索
  结果 URL 物化为 URL Frontier 资源，实际下游消费的是 `url`。
- D-068 标准探索/混合管线 `SourceSearch → AccessRulesCheck → LinkDiscovery → Fetch` 中，
  `source_search` 边携带 `kind=candidate` 流向消费 `url` 的 `access_rules_check`。
- 误回退后，validator 的资源边检查退化回「`ref.kind` 必须同时 ∈ from.output 与 to.input」，
  于是 `candidate` 边恒被判 `RESOURCE_EDGE_INCOMPATIBLE`。
- 该结构形状是模型按系统提示词（规则 7/8）生成的标准正确形状，单次 repair 无法修正 →
  HYBRID/EXPLORATORY 任务永远无法通过确定性校验。

实证（本地复现）：修复前，一个合法的完整 HYBRID 管线
（`source_search→access_rules_check→link_discovery→fetch→extract→normalize→validate→generate_artifact`，
candidate/url/snapshot/record 边）被 `validate_plan` 判为 `INVALID`，唯一 issue 为
`RESOURCE_EDGE_INCOMPATIBLE: candidate 不能从 n1 流向 n2`。

## 3. 为什么之前看起来「已经做完」

- `903b9d8` 与 `6f53936` 的修复及其回归测试曾存在，且当时全绿。
- `6299223` 的回退清单明确列入了这两个 commit，但 commit message 笼统标注为「Golden C 动态
  流水线实验」，未区分 Golden B / Golden C；回退验证只确认了「与 c9c88e4 一致」与 scoped 测试，
  未覆盖「探索式真实搜索管线」这一 Golden B 场景，故两个 Golden B 修复被静默回退而未被察觉。

## 4. 修复内容

仅**恢复**两个被误回退的 Golden B 修复，**不回退/不恢复** Golden C 动态网页实验代码：

1. `fix(plan): restore candidate edges into url-consuming discovery nodes`
   - `app/plan/validator.py`：资源边检查中，`CANDIDATE` 可被 input 契约含 `URL` 的发现节点接收
     （`src_ok`/`dst_ok` + candidate 特例）。
   - `tests/plan/test_plan_fixtures.py`：新增 D-068 完整探索式管线回归 fixture。
2. `fix(crawl): restore fetch consuming access-allowed urls`
   - `app/discovery/frontier.py`：`list_ready_for_fetch` 同时返回 `READY_FOR_FETCH` 与 `ACCESS_ALLOWED`。
   - `tests/discovery/test_frontier.py`：新增 ALLOWED/READY 都返回、BLOCKED 不返回回归测试。
3. `test(plan): pass required plan identifiers in structured plan tests`
   - 补齐 `a30d9d2` 改 `build_input`/`PlanInput` 签名为必填 `task_id`/`spec_version` 后遗漏的
     三处调用方（structured-plan 验收夹具、inference_factory、inference_telemetry），恢复本地
     完整测试套件全绿。此项为独立于本次根因的既有测试缺口修复。

## 5. 未修改内容

- Golden C 动态网页实验代码（`BROWSER_REQUIRES_FETCH`、`render_if_empty` Playwright 渲染、
  Dockerfile 安装 Chromium）**保持回退状态**，不属于本次范围。
- 未新增 Migration、未改状态机、未改认证/隔离、未引入新依赖。
- 生产环境未做任何变更。

## 6. 审计矩阵（本次重点结论）

| 链路 | 结论 |
| --- | --- |
| Goal Understanding | REAL（pydantic-ai `FunctionModel` → `ModelInferenceClient` → DeepSeek HTTP） |
| CollectionSpec 冻结 | REAL |
| Plan Generation | REAL（Pydantic AI `Agent(output_type=PlanGraphDraft)`） |
| Plan Deterministic Validation | REAL（18 步确定性校验，此前 candidate 边误回退导致 broken） |
| Bounded Repair | REAL（初始 + 1 次 repair） |
| Workflow/Run/NodeRun | REAL（Task 25 事故已证明 Temporal 真实调度） |
| Search Provider | REAL（Tavily 真实 Provider Adapter） |
| Fetch（ACCESS_ALLOWED 消费） | REAL（本次恢复 `6f53936`） |

## 7. Scoped Test Evidence

```text
pytest tests/plan/ tests/discovery/ tests/crawling/ tests/agents/   → 177 passed
pytest tests/ops/test_structured_plan_acceptance_contract.py tests/providers/test_inference_factory.py tests/providers/test_inference_telemetry.py → 15 passed
ruff check / ruff format --check（改动文件） → clean
mypy app/plan/validator.py app/discovery/frontier.py → clean
```

本地完整测试套件（`-m "not integration and not browser"`）全绿。

## 8. Remaining Gaps

- **Golden C（真实动态网页 HTTP → Playwright 升级）仍为 DEFERRED_FAST_DEV**（`DEFERRED-DYNAMIC-E2E-01`），
  本轮按既有决策不回退。
- 完整 Vertical Slice 真实验收需在 Staging 执行（上海市政府官网 HYBRID 任务），本审计文档仅记录
  代码层根因与修复；Staging 运行时证据见后续部署记录。

## 9. Production Status

UNCHANGED（本轮明确禁止 Production 部署；Production 若仍运行 `main` 则同样受 `6299223` 误回退
影响，HYBRID/EXPLORATORY 计划生成在 Production 同样 broken，需在后续受控发布中随本修复一并修复）。
