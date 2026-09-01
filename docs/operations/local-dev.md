# Local development

## Requirements

- Windows local development is supported by the current setup; Python 3.11, Node.js, npm, Docker Desktop, and `uv` are expected.
- Docker Desktop must be running before the infrastructure command.
- Current upstream lock and dependency rationale are in [../upstream-lock.md](../upstream-lock.md).

## Infrastructure

Start only the required local infrastructure:

```powershell
docker compose -f infra/compose/compose.yaml up -d
```

Services:

| Service | Host endpoint |
|---|---|
| PostgreSQL | `127.0.0.1:5434` |
| Temporal Server | `127.0.0.1:7233` |
| Temporal UI | `http://127.0.0.1:8088` |
| MinIO API | `http://127.0.0.1:9000` |
| MinIO console | `http://127.0.0.1:9001` |

Run `pwsh -File infra/scripts/health.ps1` to check PostgreSQL, Temporal, Temporal UI, and MinIO. The script fails when Docker is unavailable; do not interpret a stopped Docker daemon as a passing gate.

## Python setup

The backend uses local editable Pydantic AI/Harness source dependencies. Temporal uses the matching `1.32.0` wheel because the local SDK checkout needs a Rust/maturin native bridge and this Windows machine currently has no Rust toolchain. Run:

```powershell
uv sync --project backend --group dev
uv run --project backend python -c "import pydantic_ai, pydantic_ai_harness, temporalio; print(pydantic_ai.__file__); print(pydantic_ai_harness.__file__); print(temporalio.__file__)"
uv run --project backend alembic -c backend/alembic.ini upgrade head
```

## No secret in workflow data

Provider credentials are supplied to the worker through its process environment or a later CredentialVault adapter. Never place them in `.env` committed content, JSON Workflow input, AgentDeps, tool args, SQL event payloads, SSE, or logs.

