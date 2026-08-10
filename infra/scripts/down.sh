#!/usr/bin/env bash
# Stop the local stack (keeps named volumes / data). Run from the repository root.
set -euo pipefail
cd "$(dirname "$0")/../.."
docker compose -f infra/compose/compose.yaml down
