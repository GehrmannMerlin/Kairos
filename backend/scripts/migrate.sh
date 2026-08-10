#!/usr/bin/env bash
# Apply pending alembic migrations against the configured DATABASE_URL.
# Usage: from backend/ run `bash scripts/migrate.sh [revision]`
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -d .venv ]]; then
  PY=.venv/Scripts/python
  [[ -x "$PY" ]] || PY=.venv/bin/python
else
  PY=python
fi

REVISION="${1:-head}"
exec "$PY" -m alembic upgrade "$REVISION"
