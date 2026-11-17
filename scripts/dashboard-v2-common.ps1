# Lifecycle helpers for the presentation service only. Never stop the shared desk.
$v2Root = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$v2Data = Join-Path $v2Root 'data\dashboard-v2'
$v2Server = Join-Path $v2Root 'scripts\serve_dashboard_v2.py'
$v2Python = Join-Path $v2Root '.venv\Scripts\python.exe'
$v2PidFile = Join-Path $v2Data 'server.json'
$v2Url = 'http://127.0.0.1:8770'

function Open-V2ControlLock {
    [IO.Directory]::CreateDirectory($v2Data) | Out-Null
    try {
        return [IO.File]::Open((Join-Path $v2Data 'control.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    } catch {
        throw 'Another V2 start/stop is in progress. Please retry after it finishes.'
    }
}

function Get-DeskListener([int]$Port) {
    $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -eq $Port |
        Select-Object -Property OwningProcess -Unique)
    if ($listeners.Count -gt 1) { throw "Multiple processes own port $Port. No action taken." }
    if ($listeners.Count -eq 1) { return $listeners[0] }
    return $null
}

function Get-V2Health {
    try { return Invoke-RestMethod -Uri "$v2Url/v2-api/health" -TimeoutSec 2 } catch { return $null }
}

function Get-V2OwnedProcess($Listener) {
    $identity = Get-V2Health
    $saved = $null
    if ($identity) {
        if ($identity.service -ne 'wind-desk-v2' -or $identity.version -ne 2 -or $identity.root -ine $v2Root) {
            throw 'Port 8770 belongs to a different service or repository. Refusing to change it.'
        }
        $targetId = [int]$identity.pid
    } elseif (Test-Path -LiteralPath $v2PidFile) {
        $saved = Get-Content -Raw -LiteralPath $v2PidFile | ConvertFrom-Json
        if ($saved.root -ine $v2Root -or $saved.port -ne 8770 -or -not $saved.created) {
            throw 'Invalid V2 process record. No process was stopped.'
        }
        $targetId = [int]$saved.pid
    } else { return $null }
    if ($targetId -le 0) { throw 'Invalid V2 process ID.' }
    $owned = Get-CimInstance Win32_Process -Filter "ProcessId = $targetId"
    if (-not $owned) { return $null }
    if ($owned.CommandLine -notmatch ('(?:^|\s)"?' + [regex]::Escape($v2Server) + '"?(?:\s|$)') -or
        ($saved -and $owned.CreationDate.ToUniversalTime().ToString('o') -ne $saved.created) -or
        ($Listener -and $Listener.OwningProcess -ne $targetId)) {
        throw 'V2 process identity changed. Refusing to stop or reuse that process.'
    }
    return $owned
}

function Save-V2OwnedProcess($Owned) {
    @{pid = $Owned.ProcessId; created = $Owned.CreationDate.ToUniversalTime().ToString('o');
      root = $v2Root; port = 8770} | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath $v2PidFile
}

function Test-SharedDeskOwner($Listener) {
    # A venv launcher can have a system-Python child. Check both before reusing it.
    $candidateId = [int]$Listener.OwningProcess
    for ($generation = 0; $generation -lt 3 -and $candidateId -gt 0; $generation++) {
        $candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $candidateId"
        if (-not $candidate) { return $false }
        if ($candidate.CommandLine -match 'uvicorn\s+app\.main:app' -and
            $candidate.CommandLine -match [regex]::Escape($v2Python)) { return $true }
        $candidateId = [int]$candidate.ParentProcessId
    }
    return $false
}

function Start-SharedDeskIfNeeded {
    $listener = Get-DeskListener 8765
    if ($listener) {
        if (-not (Test-SharedDeskOwner $listener)) {
            throw 'Port 8765 is not the shared backend from this repository. No second collector was started.'
        }
        return
    }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $v2Root 'start.ps1') -NoBrowser
    if ($LASTEXITCODE -ne 0) { throw 'Shared backend startup failed. Check logs\server-error.log.' }
    $listener = Get-DeskListener 8765
    if (-not $listener -or -not (Test-SharedDeskOwner $listener)) {
        throw 'Shared backend ownership could not be verified. V2 was not started.'
    }
}
