param([switch]$SkipBuild)
$ErrorActionPreference='Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    New-Item -ItemType Directory -Force runtime | Out-Null
    $envFile=Join-Path $PWD 'runtime/minio.env'
    if (-not (Test-Path -LiteralPath $envFile)) {
        $access='kr'+[guid]::NewGuid().ToString('N').Substring(0,18)
        $secret=[guid]::NewGuid().ToString('N')+[guid]::NewGuid().ToString('N')
        [IO.File]::WriteAllText($envFile,"MINIO_ROOT_USER=$access`nMINIO_ROOT_PASSWORD=$secret`nMINIO_REGION_NAME=us-east-1`n")
    }
    $values=@{}
    foreach ($line in Get-Content -LiteralPath $envFile) {
        if ($line -match '^([^=]+)=(.*)$') { $values[$Matches[1]]=$Matches[2] }
    }
    $config=@{provider='minio';endpoint='http://127.0.0.1:9000';console_url='http://127.0.0.1:9001';bucket='application-backups';region='us-east-1';access_key=$values['MINIO_ROOT_USER'];secret_key=$values['MINIO_ROOT_PASSWORD']}
    $config | ConvertTo-Json | Set-Content -LiteralPath runtime/storage.json -Encoding utf8
    if (-not $SkipBuild) {
        docker compose -f deploy/minio.yaml build
        if ($LASTEXITCODE -ne 0) { throw 'MinIO source build failed; inspect the build output' }
    }
    docker compose -f deploy/minio.yaml up -d --wait --wait-timeout 120
    if ($LASTEXITCODE -ne 0) { throw 'MinIO did not become healthy' }
    Write-Host 'MinIO API: http://127.0.0.1:9000'
    Write-Host 'MinIO Console: http://127.0.0.1:9001'
    Write-Host 'Start the application, open Object storage, then use Reveal credentials to view your generated local login.'
    Write-Host 'Run Connect & verify to create and validate the application-backups bucket.'
} finally { Pop-Location }
