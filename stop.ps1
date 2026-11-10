$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath 'data\server.pid')) { Write-Output 'No owned dashboard process.'; exit 0 }
$deskPid=[int](Get-Content -LiteralPath 'data\server.pid')
$deskProcesses=@(Get-CimInstance Win32_Process)
$deskOwned=$deskProcesses | Where-Object ProcessId -eq $deskPid
if (-not $deskOwned) { Write-Output 'Dashboard already stopped.'; exit 0 }
if ($deskOwned.CommandLine -notmatch 'uvicorn.*app\.main:app' -or $deskOwned.CommandLine -notlike ('*'+$PSScriptRoot+'*')) {
    throw 'Saved PID no longer identifies this dashboard. Refusing to stop it.'
}
function Stop-DeskTree([int]$deskTarget) {
    foreach ($deskChild in $deskProcesses | Where-Object ParentProcessId -eq $deskTarget) { Stop-DeskTree $deskChild.ProcessId }
    Stop-Process -Id $deskTarget -ErrorAction SilentlyContinue
}
Stop-DeskTree $deskPid
Write-Output 'Dashboard stopped. Local data and audit records retained.'
