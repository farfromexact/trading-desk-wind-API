param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$deskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $deskPython) -or -not (Test-Path -LiteralPath 'frontend\dist\index.html')) {
    throw 'Run install.ps1 first.'
}
try {
    $deskExisting = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/state' -TimeoutSec 2
    if ($deskExisting.rules.version) {
        if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8765' }
        Write-Output 'Dashboard already running: http://127.0.0.1:8765'
        exit 0
    }
} catch { }
New-Item -ItemType Directory -Force logs,data | Out-Null
$deskProcess = Start-Process -FilePath $deskPython -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8765','--no-access-log' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'logs\server-out.log') -RedirectStandardError (Join-Path $PSScriptRoot 'logs\server-error.log') -PassThru
Set-Content -Encoding ASCII -LiteralPath 'data\server.pid' -Value $deskProcess.Id
for ($deskAttempt=0; $deskAttempt -lt 30; $deskAttempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $null=Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/state' -TimeoutSec 2
        if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8765' }
        Write-Output 'Dashboard: http://127.0.0.1:8765'
        exit 0
    } catch { }
}
throw 'Dashboard startup failed. Check logs\server-error.log.'
