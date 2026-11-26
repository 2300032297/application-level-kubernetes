param([int]$Port=8090,[switch]$Minio)
$ErrorActionPreference='Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    if ($Minio) { & ./scripts/setup-minio.ps1 }
    python -m kube_recover console --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'Console exited with an error' }
}
finally { Pop-Location }
