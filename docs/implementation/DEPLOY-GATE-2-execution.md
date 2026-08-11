# DEPLOY-GATE-2 执行记录：可交互 Task Workflow Staging

状态：**PENDING**（M-08 LOCAL 完成，Staging 部署待执行）
负责人/Agent：Claude Code — 2026-08-11
Staging：https://staging.kairos.ac.cn（复用 DEPLOY-GATE-1 环境，服务器 47.238.145.24）
Gate-1 稳定 release：作为 Gate-2 rollback target（待确认 image/digest）

## 1. 目标
验证 M-05～M-08 在服务器 Staging 的可交互 Agent Task Workflow 闭环：
注册/登录 → Model Provider → 建任务 → Goal Understanding → CollectionSpec 确认 →
Plan 生成/校验 → Temporal Workflow 启动 → SSE 状态变化 → 模拟高风险 Node → Approval →
暂停/恢复/取消 → 重启恢复 → 回滚就绪。

## 2. 部署信息
- Git SHA：（Gate-2 执行时填写当前 M-08 HEAD）
- Image tags/digests：web / api / worker
- Migration：0006（对 Staging DB 实跑 upgrade）
- Model Provider：provider / model / config version（无 Secret）
- Task ID / Spec Version / Plan Version / Run ID / Approval ID

## 3. 用户闭环（Gate-2 Smoke）
1. 注册/登录
2. 真实 Model Provider AVAILABLE
3. Workbench 创建 Task
4. Pydantic AI 生成 CollectionSpec
5. 确认 Spec
6. Pydantic AI 生成 Plan
7. Deterministic Validator PASS
8. PlanVersion 冻结
9. Temporal Workflow 启动
10. SSE 看到真实状态变化
11. 触发模拟高风险 Node（标准 Fetch + NON_PUBLIC/CREDENTIAL_ACCESS，fixture-only）
12. 生成真实 Approval
13. Chat Approval Card / Task Drawer / Deep Link 指向同一 Approval ID
14. 批准
15. Temporal 从 Approval wait 恢复
16. 暂停 → PAUSING → PAUSED
17. 恢复 → RUNNING
18. 取消 → CANCELLING → CANCELLED
19. 重启 api/worker 后 Task/Plan/Approval 仍在，SSE 恢复后状态一致

## 4. 约束
- 只部署 staging.kairos.ac.cn，不触碰 app.kairos.ac.cn / Production DB / Production Secret
- Gate-2 主任务使用 SPECIFIED_SOURCE，避免 Search Provider 无关阻塞；不在 Staging 搜索互联网
- 不跑全量 pytest；只跑真实用户闭环 + Approval + pause/resume/cancel + health/readiness +
  restart/recovery + secret leak + rollback readiness
- Model API Key / Credential plaintext 不得出现在 API Response / SSE / DomainEvent /
  Temporal History fixture / 应用日志 / release manifest

## 5. 结果
- HTTPS / health / ready / auth / provider / task / spec / plan / validator / PlanVersion /
  workflow / SSE / high-risk / approval / chat card / drawer / deep link / resolve /
  temporal resume / pause / resume / cancel / restart / secret / rollback：
  待执行后逐项填写 PASS/FAIL
- Deployment Record：待填写

## 6. 最终结论
待执行后填写：DEPLOY-GATE-2 = PASS / BLOCKED；M-08 = DEPLOYED / 保持 DONE；M-09 = UNBLOCKED / BLOCKED。
