$ErrorActionPreference = "Stop"
$appRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$composeFile = Join-Path $appRoot "infra\compose\compose.yaml"
docker compose -f $composeFile down

