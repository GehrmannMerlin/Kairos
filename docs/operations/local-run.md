# Local run and smoke checks

1. Start infrastructure:

   ```powershell
   docker compose -f infra/compose/compose.yaml up -d
   pwsh -File infra/scripts/health.ps1
   ```

2. Apply schema:

   ```powershell
   uv run --project backend alembic -c backend/alembic.ini upgrade head
   ```

3. Start the worker, API, and Vue development server in separate terminals:

   ```powershell
   uv run --project backend python -m app.worker
   uv run --project backend uvicorn app.api:app --reload --port 8000
   npm --prefix frontend install
   npm --prefix frontend run dev
   ```

4. Open the Vue page, create/select a workspace with an absolute local path, choose its permission, type an instruction, and start the run. The page subscribes to the task's SSE stream. Inspect the same Workflow in Temporal UI to verify model request → tool Activity → model continuation.

5. For EXPLORATORY or HYBRID collection, set `DEEPSEEK_API_KEY` and `TAVILY_API_KEY` only in the API/Worker process environment, then run the real checks:

   ```powershell
   uv run --project backend python tests/smoke/search_provider_preflight.py
   uv run --project backend python tests/smoke/exploratory_collection_smoke.py
   ```

   The smoke is an HTTP-to-Temporal vertical check: the script creates the task/spec, starts one run, waits for terminal SSE, queries persisted outputs, and verifies MinIO snapshot objects plus bounded Temporal history. It does not call SearchProvider, fetch, or extraction functions directly.

6. Run targeted checks:

   ```powershell
   uv run --project backend pytest backend/tests -q
   uv run --project backend ruff check backend/app backend/tests
   npm --prefix frontend run typecheck
   npm --prefix frontend run build
   ```

The real-model and collection vertical-slice smoke commands must only be called a PASS when real credentials and a live Temporal worker are present. Without valid credentials, record `BLOCKED` and the safe reason emitted by the script; never print or commit a key.
