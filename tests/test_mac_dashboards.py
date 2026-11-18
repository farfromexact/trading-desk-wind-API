import socket
from unittest.mock import Mock

import pytest

pytest.importorskip('fcntl', reason='macOS foreground launcher uses POSIX file locking')
from scripts import mac_dashboards as desk


@pytest.mark.parametrize('running', [False, True])
def test_start_only_opens_the_integrated_page(monkeypatch, tmp_path, running):
    monkeypatch.setattr(desk, 'ROOT', tmp_path)
    child = Mock(wait=Mock(return_value=0), poll=Mock(return_value=0))
    launch = Mock(return_value=child)
    identify = Mock(return_value=object() if running else None)
    open_page = Mock(return_value=Mock(returncode=0))
    ready = Mock()
    monkeypatch.setattr(desk, 'listener', identify)
    monkeypatch.setattr(desk, 'start_process', launch)
    monkeypatch.setattr(desk, 'wait_ready', ready)
    monkeypatch.setattr(desk.subprocess, 'run', open_page)

    assert desk.start(True) == 0
    identify.assert_called_once_with(8765)
    if running:
        launch.assert_not_called()
    else:
        launch.assert_called_once_with(8765)
    ready.assert_called_once_with(8765, None if running else child)
    open_page.assert_called_once_with(['/usr/bin/open', desk.url(8765)], check=False)


def test_failed_start_cleans_up_its_own_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(desk, 'ROOT', tmp_path)
    child = Mock(poll=Mock(return_value=None))
    monkeypatch.setattr(desk, 'listener', lambda port: None)
    monkeypatch.setattr(desk, 'start_process', lambda port: child)
    monkeypatch.setattr(desk, 'wait_ready', Mock(side_effect=RuntimeError('not ready')))
    with pytest.raises(RuntimeError, match='not ready'):
        desk.start(False)
    child.terminate.assert_called_once()
    child.wait.assert_called_once()


def test_backend_claims_port_before_spawning_a_collector(monkeypatch):
    spawn = Mock()
    monkeypatch.setattr(desk.subprocess, 'Popen', spawn)
    with socket.socket() as busy:
        busy.bind(('127.0.0.1', 0))
        busy.listen()
        with pytest.raises(OSError):
            desk.start_process(busy.getsockname()[1])
    spawn.assert_not_called()


def test_trial_reuse_requires_this_repository_worktree(monkeypatch, tmp_path):
    monkeypatch.setattr(desk, 'ROOT', tmp_path)
    trial = tmp_path / '.tmp/mac-v2'
    trial.mkdir(parents=True)
    assert desk.v2_directory(tmp_path)
    assert not desk.v2_directory(trial)
    (trial / '.git').write_text('gitdir: /other/repo/.git/worktrees/mac-v2')
    assert not desk.v2_directory(trial)
    (trial / '.git').write_text(f'gitdir: {tmp_path}/.git/worktrees/mac-v2')
    assert desk.v2_directory(trial)


def test_stop_rechecks_process_identity_and_leaves_a_replacement_alone(monkeypatch, tmp_path):
    monkeypatch.setattr(desk, 'ROOT', tmp_path)
    v2 = desk.Listener(101, tmp_path, 'v2')
    backend = desk.Listener(102, tmp_path, 'original start time')
    replaced = desk.Listener(102, tmp_path, 'different start time')
    identify = Mock(side_effect=[v2, backend, v2, v2, backend, replaced])
    monkeypatch.setattr(desk, 'listener', identify)
    monkeypatch.setattr(desk, 'output', lambda *args: '')
    send = Mock()
    monkeypatch.setattr(desk.os, 'kill', send)

    assert desk.stop(check_only=True) == 0
    send.assert_not_called()
    assert desk.stop(check_only=False) == 1
    send.assert_called_once_with(v2.pid, desk.signal.SIGINT)
