$ErrorActionPreference = "Stop"
$appRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$composeFile = Join-Path $appRoot "infra\compose\compose.yaml"

docker compose -f $composeFile ps
docker compose -f $composeFile exec -T postgres pg_isready -U kairos -d kairos
docker compose -f $composeFile exec -T temporal temporal operator cluster health --address 127.0.0.1:7233
docker compose -f $composeFile exec -T minio mc ready local
$ui = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8088/" -TimeoutSec 10
if ($ui.StatusCode -lt 200 -or $ui.StatusCode -ge 400) {
  throw "Temporal UI health check returned HTTP $($ui.StatusCode)"
}
Write-Host "PostgreSQL, Temporal, Temporal UI, and MinIO are healthy."

