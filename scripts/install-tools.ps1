$ErrorActionPreference='Stop'
$root=Join-Path (Split-Path $PSScriptRoot -Parent) 'runtime/tools'
New-Item -ItemType Directory -Force $root | Out-Null
$kind=Join-Path $root 'kind.exe'
if (-not (Test-Path -LiteralPath $kind)) {
    Invoke-WebRequest 'https://github.com/kubernetes-sigs/kind/releases/download/v0.30.0/kind-windows-amd64' -OutFile $kind
}
$kindChecksum=Join-Path $root 'kind.sha256'
Invoke-WebRequest 'https://github.com/kubernetes-sigs/kind/releases/download/v0.30.0/kind-windows-amd64.sha256sum' -OutFile $kindChecksum
$expected=((Get-Content $kindChecksum -Raw) -split '\s+')[0]
if ((Get-FileHash $kind -Algorithm SHA256).Hash -ne $expected) { throw 'Kind checksum verification failed' }
$archive=Join-Path $root 'velero.tar.gz'
$veleroDir=Join-Path $root 'velero-v1.18.2-windows-amd64'
if (-not (Test-Path -LiteralPath (Join-Path $veleroDir 'velero.exe'))) {
    Invoke-WebRequest 'https://github.com/vmware-tanzu/velero/releases/download/v1.18.2/velero-v1.18.2-windows-amd64.tar.gz' -OutFile $archive
    $checksums=Join-Path $root 'velero-checksums'
    Invoke-WebRequest 'https://github.com/vmware-tanzu/velero/releases/download/v1.18.2/CHECKSUM' -OutFile $checksums
    $line=Get-Content $checksums | Where-Object { $_ -match 'velero-v1\.18\.2-windows-amd64\.tar\.gz$' }
    $expected=($line -split '\s+')[0]
    if ((Get-FileHash $archive -Algorithm SHA256).Hash -ne $expected) { throw 'Velero checksum verification failed' }
    tar -xzf $archive -C $root
    if ($LASTEXITCODE -ne 0) { throw 'Velero extraction failed' }
}
$env:PATH="$root;$veleroDir;$env:PATH"
$kubectl=Join-Path $root 'kubectl.exe'
if (-not (Test-Path -LiteralPath $kubectl)) {
    Invoke-WebRequest 'https://dl.k8s.io/release/v1.34.0/bin/windows/amd64/kubectl.exe' -OutFile $kubectl
}
$kubectlChecksum=Join-Path $root 'kubectl.sha256'
Invoke-WebRequest 'https://dl.k8s.io/release/v1.34.0/bin/windows/amd64/kubectl.exe.sha256' -OutFile $kubectlChecksum
$expected=((Get-Content $kubectlChecksum -Raw) -split '\s+')[0]
if ((Get-FileHash $kubectl -Algorithm SHA256).Hash -ne $expected) { throw 'kubectl checksum verification failed' }
Write-Host 'Workspace-local kubectl 1.34, Kind and Velero tools are ready in this terminal.'
