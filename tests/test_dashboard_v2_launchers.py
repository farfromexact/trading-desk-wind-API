"""Run Windows launchers with mocked OS processes; never start a Wind collector."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows PowerShell lifecycle tests')


def run_launcher(tmp_path, mode, action='start', saved=False):
    repo = tmp_path / '仓库 with spaces'
    (repo / 'scripts').mkdir(parents=True)
    for name in ('start-v2.ps1', 'stop-v2.ps1', 'scripts/dashboard-v2-common.ps1'):
        shutil.copyfile(ROOT / name, repo / name)
    for name in ('.venv/Scripts/python.exe', 'frontend/node_modules/vite/bin/vite.js'):
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode != 'missing':
            target.touch()
    data = repo / 'data/dashboard-v2'
    data.mkdir(parents=True)
    if saved:
        (data / 'server.json').write_text(json.dumps(dict(
            pid=4242, root=str(repo), port=8770, created='2026-11-17T00:00:00.0000000Z'
        )), encoding='utf-8')
    # Functions shadow only external commands, so the real ownership and lifecycle
    # branches execute, including PowerShell quoting for a Unicode, spaced path.
    driver = r'''
$ErrorActionPreference = 'Stop'
$global:MockRoot = $PSScriptRoot
$global:MockMode = '__MODE__'
$global:MockV2 = $MockMode -in @('existing','existing-no-backend','stop')
$global:MockBackend = $MockMode -notin @('cold','existing-no-backend')
function global:Record($Message) { Add-Content -LiteralPath (Join-Path $MockRoot 'calls.txt') -Value $Message }
function global:Get-NetTCPConnection {
    if ($MockMode -eq 'network-error') { throw 'Cannot enumerate listeners' }
    if ($MockBackend) { [pscustomobject]@{LocalPort=8765;OwningProcess=19132} }
    if ($MockV2) { [pscustomobject]@{LocalPort=8770;OwningProcess=4242} }
    if ($MockMode -eq 'occupied') { [pscustomobject]@{LocalPort=8770;OwningProcess=8888} }
}
function global:Invoke-RestMethod {
    if ($MockV2) { return [pscustomobject]@{service='wind-desk-v2';version=2;root=$MockRoot;pid=4242} }
    throw 'Not running'
}
function global:Get-CimInstance {
    param($ClassName,$Filter)
    if ($Filter -eq 'ProcessId = 19132') {
        return [pscustomobject]@{ProcessId=19132;ParentProcessId=0;CommandLine=('"'+$MockRoot+'\.venv\Scripts\python.exe" -m uvicorn app.main:app --port 8765')}
    }
    if ($Filter -eq 'ProcessId = 4242') {
        $command = '"'+$MockRoot+'\.venv\Scripts\python.exe" -X utf8 "'+$MockRoot+'\scripts\serve_dashboard_v2.py" --port 8770'
        if ($MockMode -eq 'stale') { $command = 'python unrelated.py' }
        $created = [datetime]'2026-11-17T00:00:00Z'
        if ($MockMode -eq 'reused') { $created = $created.AddDays(1) }
        return [pscustomobject]@{ProcessId=4242;ParentProcessId=4241;CommandLine=$command;CreationDate=$created}
    }
    return $null
}
function global:node { Record 'build'; $global:LASTEXITCODE=0 }
function global:powershell.exe { Record 'backend-start'; $global:MockBackend=$true; $global:LASTEXITCODE=0 }
function global:Start-Process {
    param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,$RedirectStandardOutput,$RedirectStandardError,[switch]$PassThru)
    if ($WindowStyle -ne 'Hidden') { throw 'Background process must be hidden' }
    if ($ArgumentList[2] -ne ('"'+$MockRoot+'\scripts\serve_dashboard_v2.py"')) { throw 'Script path was not quoted' }
    Record 'v2-start'
    $global:MockV2=$true
    [pscustomobject]@{Id=4241;HasExited=$false}
}
function global:Start-Sleep { }
function global:Stop-Process { param($Id,$ErrorAction); Record "stop:$Id"; $global:MockV2=$false }
function global:Wait-Process { }
& (Join-Path $MockRoot '__ACTION__-v2.ps1') __ARGS__
'''.replace('__MODE__', mode).replace('__ACTION__', action).replace('__ARGS__', '-NoBrowser' if action == 'start' else '')
    driver_path = repo / 'driver.ps1'
    driver_path.write_text(driver, encoding='utf-8-sig')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(driver_path)],
                            capture_output=True, timeout=20)
    calls = (repo / 'calls.txt').read_text().splitlines() if (repo / 'calls.txt').exists() else []
    return result, calls, data


def test_cold_start_builds_first_then_starts_one_shared_backend_and_hidden_v2(tmp_path):
    result, calls, data = run_launcher(tmp_path, 'cold')
    assert result.returncode == 0, result.stderr
    assert calls == ['build', 'backend-start', 'v2-start']
    assert json.loads((data / 'server.json').read_text(encoding='utf-8-sig'))['pid'] == 4242


def test_repeated_start_reuses_existing_v2_without_build_or_collector(tmp_path):
    result, calls, _ = run_launcher(tmp_path, 'existing')
    assert result.returncode == 0, result.stderr
    assert calls == []


def test_existing_v2_can_restart_a_stopped_shared_backend(tmp_path):
    result, calls, _ = run_launcher(tmp_path, 'existing-no-backend')
    assert result.returncode == 0, result.stderr
    assert calls == ['backend-start']


def test_stop_only_targets_the_verified_v2_process(tmp_path):
    result, calls, data = run_launcher(tmp_path, 'stop', action='stop', saved=True)
    assert result.returncode == 0, result.stderr
    assert calls == ['stop:4242']
    assert not (data / 'server.json').exists()


@pytest.mark.parametrize('mode', ['occupied', 'missing', 'network-error'])
def test_failed_preconditions_do_not_start_or_stop_anything(tmp_path, mode):
    result, calls, _ = run_launcher(tmp_path, mode)
    assert result.returncode != 0
    assert calls == []


@pytest.mark.parametrize('mode', ['stale', 'reused'])
def test_stale_pid_never_stops_an_unrelated_process(tmp_path, mode):
    result, calls, _ = run_launcher(tmp_path, mode, action='stop', saved=True)
    assert result.returncode != 0
    assert calls == []
