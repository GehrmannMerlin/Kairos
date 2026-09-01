$ErrorActionPreference = "Stop"
$appRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
& (Join-Path $PSScriptRoot "health.ps1")
Push-Location $appRoot
try {
  uv run --project backend python -c "import pydantic_ai, pydantic_ai_harness, temporalio; print(pydantic_ai.__file__); print(pydantic_ai_harness.__file__); print(temporalio.__file__)"
  uv run --project backend alembic -c backend/alembic.ini upgrade head
} finally {
  Pop-Location
}

