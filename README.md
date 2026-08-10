# Kairos — 网页信息采集 Agent

Monorepo 工程，架构与流程见根目录五份权威文档。开发约束以 `CLAUDE.md` 为准。

```text
frontend/   Vue 3 + TypeScript strict
backend/    FastAPI + SQLAlchemy + Alembic + Temporal + MinIO + OpenTelemetry
infra/      本地 docker compose、otel collector、运维脚本
docs/       架构 / 运行 / 模块执行记录
```

## 快速开始（本地）

```bash
cp .env.example .env
docker compose -f infra/compose/compose.yaml up -d --build
```

详见 `docs/operations/local-run.md`。
