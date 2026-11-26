$ErrorActionPreference='Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try { python -m unittest discover -s tests -v; if ($LASTEXITCODE -ne 0) { throw 'Tests failed' } }
finally { Pop-Location }
