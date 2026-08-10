# 本地开发架构（M-01）

本文件说明 M-01 建立的本地工程底座：目录、组件、数据流与扩展点。
面向「下一个 Claude Code 会话 / 开发者」，不是长篇架构论文。

## 目录结构

```text
frontend/                  Vue 3 + TypeScript strict
  src/app/                 Router、App Shell、API Client、错误处理
  src/features/home/       最小首页（前端→API 连通性证明）
backend/                   FastAPI + Temporal + SQLAlchemy + MinIO + OTel
  app/api/routes/health.py /health/live、/health/ready
  app/config.py            Settings（全部来自环境变量，KAIROS_ 前缀）
  app/infra/               db / temporal / object_storage / telemetry / deps
  app/workflows/smoke.py   最小测试 Workflow（确定性）
  app/activities/smoke.py  副作用 Activity（写 PG + MinIO）
  app/storage/             SQLAlchemy models + smoke repo
  alembic/                 Migration（0001 smoke_probe）
  scripts/run_smoke.py     集成 Smoke 链
infra/
  compose/compose.yaml     本地全栈 compose
  otel/otel-collector.yaml 本地 OTel collector（debug 导出）
  scripts/                 up / down / smoke
docs/                      architecture / operations / implementation
```

## 服务与端口（本地 compose）

| 服务 | 端口 | 说明 |
|---|---|---|
| postgres | 5434 | 业务事实来源（本机 5432/5433 被占用，见 local-run.md） |
| temporal | 8233 | 长任务编排（宿主机映射；Windows 排除 7178-7277 段，故避开 7233） |
| temporal-ui | 8088 | Temporal Web UI |
| minio | 9000 / 9001 | 对象存储 API / 控制台 |
| otel-collector | 4317 / 4318 | OTLP 接收 |
| api | 8000 | FastAPI |
| worker | – | Temporal Worker（smoke queue） |
| web | 5173 | Vite dev server |

## 依赖方向

```text
web (Vite, 只走 /api 代理)
  → api (FastAPI Route，只做认证/校验/命令/查询)
    → Temporal Workflow（确定性，无外部副作用）
      → Activity（全部网络/DB/文件副作用）
    → PostgreSQL / MinIO（副作用事实）
  → otel-collector（Trace 汇聚）
```

## 关键设计决策（M-01）

- **单仓库 Monorepo + 模块化单体**：前端、后端、Worker、基础设施统一版本管理（I-001）。
- **Route 保持轻量**：health 路由只调用检查函数，不堆基础设施逻辑。
- **Workflow 确定性**：`SmokeWorkflow` 内无网络/时间/随机副作用，DB/MinIO 写入全部在 Activity。
- **Storage 抽象**：业务依赖 `ObjectStorage` Protocol，M-01 提供 MinIO Adapter；后续可换 S3。
- **Trace 关联**：FastAPI 埋点 + Temporal `TracingInterceptor`，API 请求 → Workflow/Activity 共享 trace。
- **配置即环境变量**：`KAIROS_*`，`.env.example` 只含 dev 占位值，无真实 Secret。

## 扩展点（后续模块使用，不在 M-01 实现）

- `app/config.py Settings` → 新配置字段直接追加。
- `app/infra/deps.py get_session_factory / get_object_storage` → 仓库/存储复用。
- `app/infra/temporal.py create_smoke_worker` → 注册更多 Workflow/Activity、多 Task Queue。
- `app/storage/models.py Base` → 新业务表（User/Task/Spec... 属 M-02+）。

## 环境要求

- Docker Desktop（含 Compose v2）已启动。
- Python 3.11+（后端裸跑时用 `backend/.venv`）。
- Node 20+（前端 npm/pnpm）。
