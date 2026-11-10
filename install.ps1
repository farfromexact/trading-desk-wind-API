$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
New-Item -ItemType Directory -Force .tmp | Out-Null
$env:TEMP=(Resolve-Path -LiteralPath .tmp).Path
$env:TMP=$env:TEMP
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python venv setup failed.' }
}
& '.\.venv\Scripts\python.exe' scripts/configure_wind.py
if ($LASTEXITCODE -ne 0) { throw 'Wind SDK configuration failed. Set WIND_PYTHON_DIR to the official SDK directory.' }
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
Push-Location -LiteralPath frontend
try {
    & npm.cmd ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
Write-Output 'Installed. Log into Wind, then run start.ps1.'
