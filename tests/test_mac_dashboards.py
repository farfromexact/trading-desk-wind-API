import socket
from unittest.mock import Mock

import pytest

pytest.importorskip('fcntl', reason='macOS foreground launcher uses POSIX file locking')
from scripts import mac_dashboards as desk


@pytest.mark.parametrize('running', [(), (8765,), (8770,), (8765, 8770)])
def test_start_opens_both_pages_and_only_starts_missing_services(monkeypatch, tmp_path, running):
    monkeypatch.setattr(desk, 'ROOT', tmp_path)
    start = Mock(side_effect=lambda port: Mock(pid=port, wait=Mock(return_value=0)))
    open_pages = Mock(return_value=Mock(returncode=0))
    cleanup = Mock()
    monkeypatch.setattr(desk, 'listener', lambda port: port if port in running else None)
    monkeypatch.setattr(desk, 'start_process', start)
    monkeypatch.setattr(desk, 'wait_ready', Mock())
    monkeypatch.setattr(desk.subprocess, 'run', open_pages)
    monkeypatch.setattr(desk, 'close_owned', cleanup)

    assert desk.start(True) == 0
    missing = [p for p in (8765, 8770) if p not in running]
    assert [call.args[0] for call in start.call_args_list] == missing
    open_pages.assert_called_once_with(['/usr/bin/open', desk.url(8765), desk.url(8770)], check=False)
    assert list(cleanup.call_args.args[0]) == missing


def test_v2_conflict_keeps_original_page_available(monkeypatch, tmp_path):
    monkeypatch.setattr(desk, 'ROOT', tmp_path)
    monkeypatch.setattr(desk, 'listener', Mock(side_effect=[object(), RuntimeError('foreign 8770')]))
    monkeypatch.setattr(desk, 'wait_ready', Mock())
    start = Mock()
    open_pages = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(desk, 'start_process', start)
    monkeypatch.setattr(desk.subprocess, 'run', open_pages)
    assert desk.start(True) == 1
    start.assert_not_called()
    open_pages.assert_called_once_with(['/usr/bin/open', desk.url(8765)], check=False)


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
