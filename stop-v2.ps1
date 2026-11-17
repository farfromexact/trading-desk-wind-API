$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scripts\dashboard-v2-common.ps1')
$v2Lock = Open-V2ControlLock
try {
    $v2Listener = Get-DeskListener 8770
    $v2Owned = Get-V2OwnedProcess $v2Listener
    if ($v2Listener -and -not $v2Owned) { throw 'Port 8770 belongs to an unrecognized process. Nothing was stopped.' }
    if ($v2Owned) {
        Stop-Process -Id $v2Owned.ProcessId -ErrorAction Stop
        Wait-Process -Id $v2Owned.ProcessId -Timeout 5 -ErrorAction SilentlyContinue
        Write-Output 'V2 stopped. The original dashboard and shared Wind collector are still running.'
    } else { Write-Output 'V2 is already stopped. The shared backend was not changed.' }
    if (Test-Path -LiteralPath $v2PidFile) { Remove-Item -LiteralPath $v2PidFile }
} finally { $v2Lock.Dispose() }
