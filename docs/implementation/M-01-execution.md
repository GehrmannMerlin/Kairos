# M-01 模块执行记录

状态：DONE
负责人/Agent：Claude Code — 2026-08-10
基线 Commit：`a48dac1`（docs: add project baseline documents）
依赖模块：无（M-01 为第一个模块）
目标环境：local

## 1. 本模块目标

> 建立后续所有模块共同依赖的可运行工程骨架，使新 Agent 在本机可以一条命令启动 Vue、FastAPI、PostgreSQL、Temporal、MinIO 和 OpenTelemetry，并有统一测试/格式化/迁移机制。（摘自 agent-project-implementation-plan.md 的 M-01 章节）

## 2. 输入契约

- 上游数据模型：无。
- API/Workflow 契约（本模块产出，供后续模块消费）：
  - `GET /api/health/live`、`GET /api/health/ready`（ready 检查 postgresql/temporal/object_storage）
  - `SmokeWorkflow`（name=`smoke_workflow`）→ `write_smoke_record` Activity，task queue=`kairos-smoke`
  - `ObjectStorage` Protocol（put/get/exists/head/ensure_bucket），MinIO Adapter
  - `app.config.Settings`（`KAIROS_*` 环境变量，pydantic-settings）
- 使用的已有页面/Drawer：无。

## 3. 本模块实现清单

- [x] 数据模型/迁移：`smoke_probe` 表（alembic revision 0001，upgrade/downgrade 验证）
- [x] 领域服务：`smoke_repo` 最小 repository
- [x] API/Workflow/Activity：health 路由、SmokeWorkflow、write_smoke_record（typed input/output）
- [x] 前端交互：Vue App Shell + 首页连通性检查（前端 → API 代理连通）
- [x] 安全/用户隔离：M-01 无业务用户数据；配置仅环境变量注入，无 Secret 提交（git 校验通过）
- [x] 幂等/错误路径：A-Lite（health 降级 503、ApiError、Activity retry policy maximum_attempts=2）
- [x] 自动化测试：后端 6 单测 + 2 集成；前端 3 单测
- [x] 联动测试：Smoke 链 PASS（见 §6）
- [x] 文档：local-dev.md、local-run.md、本记录

## 4. 明确不做

M-02 注册登录 / M-03 Provider / M-04 状态机；CollectionSpec / Agent Chat / 真实 Task Workflow / 暂停恢复 / Search / Scrapy / Playwright / 抓取 / 提取 / Evidence / Record / CSV / Quality；生产部署 / 香港服务器 / 域名 / HTTPS / Production CI-CD；Kubernetes / Redis / 消息队列；计费 UI。DEPLOY-GATE-1 未执行（要求 M-01～M-04 完成后）。

## 5. 验收命令与证据

| 验收项 | 命令 | 预期 | 实际结果 |
|---|---|---|---|
| 前端 lint | `npm run lint:check` | PASS | PASS |
| 前端 format | `npm run format:check` | PASS | PASS |
| 前端 type-check | `npm run type-check` | PASS | PASS |
| 前端 build | `npm run build` | PASS | PASS（vite 6.4.3 built in 547ms） |
| 前端单测 | `npm run test:unit` | 3 passed | PASS（3 passed） |
| 后端 ruff | `ruff check . && ruff format --check .` | PASS | PASS |
| 后端 mypy | `mypy app scripts tests` | PASS | PASS（30 source files） |
| 后端单测 | `python -m pytest` | 6 passed, 2 skipped | PASS |
| migration upgrade | `alembic upgrade head`（compose migrate 服务） | 0001 applied | PASS（migrate Exited 0） |
| 集成测试 | `KAIROS_RUN_INTEGRATION=1 python -m pytest -m integration` | 2 passed | PASS |
| 本地 Smoke | `python scripts/run_smoke.py` | SMOKE PASS | PASS（record_id=2 + minio 读回一致） |
| 一键启动 | `docker compose -f infra/compose/compose.yaml up -d --build` | 8 服务 up + api healthy | PASS（8/8 up，api/temporal/minio/postgres healthy） |
| /health/live | `curl localhost:8000/api/health/live` | 200 ok | PASS |
| /health/ready | `curl localhost:8000/api/health/ready` | 200 + 3 checks ok | PASS（3 checks all ok） |
| Web 访问 | `curl localhost:5173/` + `curl localhost:5173/api/health/live` | 200 | PASS（web→api 代理连通） |
| OTel | otel-collector debug exporter | 收到 span | PASS（持续收到 traces，含 smoke 的 6/9 spans 批次） |

## 6. 跨模块联动结果

- 上游兼容：PASS（无上游依赖）
- 下游契约测试：PASS — Smoke 链（script → `smoke_workflow` → `write_smoke_record` → PostgreSQL `smoke_probe` 行 + MinIO `smoke/{workflow_id}.txt` → 两端读回一致）即后续模块共享的基础契约验证。

## 7. 部署结果

- 本模块非 Deploy Gate。DEPLOY-GATE-1（M-01～M-04 后首次 Staging）未执行，符合实施计划。

## 8. 完成结论

- 全部门禁 PASS。M-01 = DONE。本轮产生的本地端口约定：postgres 宿主 5434（本机 5432/5433 被占用）、temporal 宿主 8233（Windows 排除 7178-7277）、minio 9000/9001、api 8000、web 5173、otel 4317。
