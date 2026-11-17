param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\dashboard-v2-common.ps1')
Set-Location -LiteralPath $v2Root
$v2Lock = Open-V2ControlLock
try {
    $v2Listener = Get-DeskListener 8770
    $v2Owned = Get-V2OwnedProcess $v2Listener
    if ($v2Listener) {
        if (-not $v2Owned) { throw 'Port 8770 is occupied by an unrecognized service. Stop it yourself before starting V2.' }
        Start-SharedDeskIfNeeded
        Save-V2OwnedProcess $v2Owned
        if (-not $NoBrowser) { Start-Process $v2Url }
        Write-Output "V2 already running: $v2Url"
        exit 0
    }
    if ($v2Owned) { throw 'V2 is running without a listener. Run Stop Dashboard V2.cmd, then retry.' }
    $v2Vite = Join-Path $v2Root 'frontend\node_modules\vite\bin\vite.js'
    if (-not (Test-Path -LiteralPath $v2Python) -or -not (Test-Path -LiteralPath $v2Vite) -or
        -not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Run install.ps1 first. V2 needs the existing Python and Node dependencies.' }
    $deskListener = Get-DeskListener 8765
    if ($deskListener -and -not (Test-SharedDeskOwner $deskListener)) {
        throw 'Port 8765 is not the shared backend from this repository. No second collector was started.'
    }
    & node $v2Vite build --config (Join-Path $v2Root 'frontend\v2\vite.config.js')
    if ($LASTEXITCODE -ne 0) { throw 'V2 build failed. The original dashboard was not changed.' }
    Start-SharedDeskIfNeeded
    $v2Process = Start-Process -FilePath $v2Python -ArgumentList '-X','utf8',('"' + $v2Server + '"'),'--port','8770' -WorkingDirectory $v2Root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $v2Data 'server-out.log') -RedirectStandardError (Join-Path $v2Data 'server-error.log') -PassThru
    for ($v2Attempt = 0; $v2Attempt -lt 30; $v2Attempt++) {
        Start-Sleep -Milliseconds 300
        $v2Listener = Get-DeskListener 8770
        if ($v2Listener) {
            $v2Owned = Get-V2OwnedProcess $v2Listener
            if ($v2Owned) {
                Save-V2OwnedProcess $v2Owned
                if (-not $NoBrowser) { Start-Process $v2Url }
                Write-Output "V2: $v2Url (shared backend: http://127.0.0.1:8765)"
                exit 0
            }
        }
        if ($v2Process.HasExited) { break }
    }
    throw 'V2 startup failed. Check data\dashboard-v2\server-error.log. The shared backend was left running.'
} finally { $v2Lock.Dispose() }
