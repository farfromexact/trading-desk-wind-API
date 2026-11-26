$ErrorActionPreference = 'Stop'
$deskPidFile = Join-Path $PSScriptRoot 'data\server.pid'
Write-Output '正在检查原版看板（8765）…'

function Test-DeskPortListening {
    $network = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties()
    return $network.GetActiveTcpListeners().Port -contains 8765
}

$deskOwned = $null
if (Test-Path -LiteralPath $deskPidFile) {
    $deskPid = [int](Get-Content -LiteralPath $deskPidFile -Raw)
    $deskProcesses = @(Get-CimInstance Win32_Process)
    $deskOwned = $deskProcesses | Where-Object ProcessId -eq $deskPid
}
if (-not $deskOwned) {
    if (Test-DeskPortListening) {
        throw '8765 端口仍被占用，启动记录无法确认进程归属。未停止任何程序。'
    }
    Write-Output '看板当前没有运行（8765）。要打开看板，请双击 Start Dashboard.cmd。'
    exit 0
}

$deskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if ($deskOwned.CommandLine -notmatch 'uvicorn\s+app\.main:app(?:\s|$)' -or
    $deskOwned.CommandLine -notmatch [regex]::Escape($deskPython)) {
    throw '记录的进程号已用于其他程序。未停止任何程序。'
}

function Stop-DeskTree([int]$deskTarget) {
    foreach ($deskChild in $deskProcesses | Where-Object ParentProcessId -eq $deskTarget) {
        Stop-DeskTree $deskChild.ProcessId
    }
    $process = Get-Process -Id $deskTarget -ErrorAction SilentlyContinue
    if ($process) {
        try { Stop-Process -InputObject $process -ErrorAction Stop }
        catch { if (-not $process.HasExited) { throw } }
        if (-not $process.WaitForExit(5000)) { throw "等待看板进程 $deskTarget 退出超时。" }
    }
}
Stop-DeskTree $deskPid
if (Test-DeskPortListening) { throw '看板进程已退出，但 8765 端口仍被占用。' }
Write-Output '看板已停止，行情和历史记录已保留。'
