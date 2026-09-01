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

5. Run targeted checks:

   ```powershell
   uv run --project backend pytest backend/tests -q
   uv run --project backend ruff check backend/app backend/tests
   npm --prefix frontend run typecheck
   npm --prefix frontend run build
   ```

The real-model and web vertical-slice smoke commands must only be called a PASS when a real provider credential and live Temporal worker are present. Without a valid credential, record exactly: `real-model acceptance blocked by credential`.

