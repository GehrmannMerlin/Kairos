#!/usr/bin/env bash
# Run the M-01 integration smoke chain (script -> Temporal -> PG + MinIO -> read back).
# Requires the stack up (api + worker + infra). Run from the repository root.
set -euo pipefail
cd "$(dirname "$0")/../.."

if [[ ! -f .env ]]; then
  echo "Missing .env — run infra/scripts/up.sh first."
  exit 1
fi

if [[ -d backend/.venv ]]; then
  PY=backend/.venv/Scripts/python
  [[ -x "$PY" ]] || PY=backend/.venv/bin/python
else
  PY=python
fi

cd backend
"$PY" scripts/run_smoke.py
