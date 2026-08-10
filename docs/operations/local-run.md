# 本地运行手册（M-01）

所有命令默认在**仓库根目录**执行。

## 1. 环境要求

- Docker Desktop 已启动（`docker info` 可见 Server 信息）。
- Python 3.11+、Node 20+（仅当你想在宿主机裸跑 API/Worker 时需要）。

## 2. 配置 .env

```bash
cp .env.example .env
```

`KAIROS_*` 是后端配置；`POSTGRES_*`/`MINIO_*` 供 compose 使用。默认 dev 值可直接使用，无需修改。
**`.env` 已被 .gitignore 忽略，禁止提交。**

## 3. 一键启动

```bash
bash infra/scripts/up.sh
# 等价于：
#   docker compose -f infra/compose/compose.yaml up -d --build
```

启动后：

- API: http://localhost:8000/api/health/live
- Web: http://localhost:5173
- Temporal UI: http://localhost:8088
- MinIO 控制台: http://localhost:9001

## 4. 停止

```bash
bash infra/scripts/down.sh
# 保留数据卷（postgres_data / minio_data）。
# 彻底删除数据：docker compose -f infra/compose/compose.yaml down -v
```

## 5. 运行 Migration

compose 启动时 `migrate` 服务会自动执行 `alembic upgrade head`。

宿主机方式（使用 backend/.venv）：

```bash
cd backend
.venv/Scripts/python -m alembic upgrade head   # 升级
.venv/Scripts/python -m alembic downgrade 0000  # 回滚（会丢 smoke_probe 表）
```

## 6. 启动 Worker

compose 已包含 `worker` 服务（`python -m app.worker`）。

宿主机方式：

```bash
cd backend
.venv/Scripts/python scripts/run_worker.py
```

## 7. 运行最小 Smoke

```bash
bash infra/scripts/smoke.sh
# 期望输出：SMOKE PASS
```

Smoke 链：script → Temporal `SmokeWorkflow` → `write_smoke_record` Activity
→ PostgreSQL 写 `smoke_probe` 行 + MinIO 写 `smoke/{workflow_id}.txt` → 读回校验。

## 8. 质量门禁

后端：

```bash
cd backend
.venv/Scripts/python -m ruff format . && .venv/Scripts/python -m ruff check .
.venv/Scripts/python -m mypy app scripts
.venv/Scripts/python -m pytest                       # 单元测试（不需要服务）
KAIROS_RUN_INTEGRATION=1 .venv/Scripts/python -m pytest -m integration   # 需要服务在线
```

前端：

```bash
cd frontend
npm run lint:check
npm run format:check
npm run type-check
npm run build
npm run test:unit
```

## 9. 常见故障判断

| 现象 | 原因与处理 |
|---|---|
| `api` 反复重启 / ready 503 | 看 `docker compose logs api`。常见：Temporal 未就绪、Migration 未完成。等 `temporal` healthy 后自动恢复。 |
| 端口占用 / bind forbidden | Windows Hyper-V/WSL 保留 7178-7277 段 → temporal 宿主机映射用 8233。本机 5432（系统 Postgres）与 5433（他项目容器）被占 → postgres 映射用 5434。其他冲突改 `.env` 对应端口。 |
| Web 打开 502 | vite 代理目标不可达：确认 `api` healthy（`curl localhost:8000/api/health/live`）。 |
| Smoke FAIL: temporal 连接 | Worker 未注册：`docker compose -f infra/compose/compose.yaml ps worker` 应显示 running。 |
| Smoke FAIL: postgres read-back | Migration 未跑：手动执行 `bash backend/scripts/migrate.sh`。 |
| Temporal UI 打不开 | temporal 尚未 healthy，等其 `service_healthy`。 |

## 10. 查看 Trace（OTel）

本地 collector 只打印到 stdout：

```bash
docker compose -f infra/compose/compose.yaml logs -f otel-collector
```

API 请求与 Smoke Workflow/Activity 的 span 会出现在这里（`service.name=kairos-api`）。
