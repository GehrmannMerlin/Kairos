$ErrorActionPreference = "Stop"
$appRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$composeFile = Join-Path $appRoot "infra\compose\compose.yaml"
docker compose -f $composeFile up -d
docker compose -f $composeFile ps

