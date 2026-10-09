# Kairos Agent Foundation

This repository is the first real Kairos application foundation. It contains one durable Agent path:

```text
Vue 3 → FastAPI → Temporal Workflow → Pydantic AI Agent
      → TemporalDurability Activities → real HTTP/Harness tools
      → bounded result → Agent continuation → PostgreSQL event envelope → SSE → Vue
```

Kairos does not implement a second Agent loop, tool dispatcher, browser framework, planning executor, filesystem sandbox, shell framework, or Temporal tool bridge. The durable Agent is `kairos-agent-v1`; web tools use `kairos-web-v1`; the workspace dynamic toolset uses `kairos-workspace-v1`.

## Local quick start

From this directory:

```powershell
docker compose -f infra/compose/compose.yaml up -d
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend uvicorn app.api:app --reload --port 8000
```

In a second terminal, start the real Temporal worker:

```powershell
uv run --project backend python -m app.worker
```

In a third terminal, start the Vue app:

```powershell
npm --prefix frontend install
npm --prefix frontend run dev
```

Open [http://127.0.0.1:5173](http://127.0.0.1:5173). Temporal UI is at [http://127.0.0.1:8088](http://127.0.0.1:8088), MinIO console is at [http://127.0.0.1:9001](http://127.0.0.1:9001), and the API health endpoint is [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health).

Copy `.env.example` to `.env` only for non-secret local settings. A real provider key is injected into the worker process environment; it is never sent in a Workflow input or AgentDeps. If no valid real credential is configured, the real-model acceptance remains blocked and TestModel is used only by targeted unit tests.

## Phase 3B discovery collection

The collection panel supports `SPECIFIED_SOURCE`, `EXPLORATORY`, and `HYBRID` modes. Exploratory and hybrid runs use the configured SearchProvider through the durable Agent toolset `kairos-search-v1`; search snippets are discovery metadata only. `fetch_source` and `inspect_snapshot` must produce a persisted PageSnapshot before `commit_extraction` can create records or evidence. PostgreSQL stores search rounds, source provenance, canonical record identity, duplicate observations, and deterministic completion decisions.

For a real acceptance run, inject both credentials into the API/Worker environment without committing them:

```powershell
$env:DEEPSEEK_API_KEY = "<real model credential>"
$env:TAVILY_API_KEY = "<real search credential>"
uv run --project backend python tests/smoke/search_provider_preflight.py
uv run --project backend python tests/smoke/exploratory_collection_smoke.py
```

The smoke scripts return `BLOCKED` when either real credential, the API, the Temporal Worker, PostgreSQL, or MinIO is unavailable. The exploratory smoke creates the task and spec through HTTP, starts one workflow, waits on SSE, then reads the persisted task/spec/progress/search rounds/records/evidence/snapshots and verifies the corresponding MinIO objects and bounded Temporal history. It does not call the search, fetch, or extraction implementation directly.

## Architecture boundaries

- PostgreSQL is business truth: owner-scoped Task, TaskRun, Workspace, and committed Agent event metadata.
- Temporal is execution truth: Workflow History, retries, and Activity execution.
- Workspace files remain in the user-authorized local directory; bounded tool results are the only values sent back through the Agent loop.
- Workspace resolution is a construction-time stable `DynamicToolset`; the resolver and actual FileSystem/Shell I/O run in Temporal Activities.
- The durable main Agent does not mount Playwright. Harness explicitly rejects live browser state with TemporalDurability; a future browser path must use a separate non-durable Browser Agent Activity.
- Historical static Plan/DAG code is not the execution core. Pydantic AI owns the model→tool→observe continuation loop.

See [docs/architecture/agent-runtime.md](docs/architecture/agent-runtime.md), [docs/architecture/workspace.md](docs/architecture/workspace.md), [docs/operations/local-dev.md](docs/operations/local-dev.md), and [docs/operations/local-run.md](docs/operations/local-run.md).
