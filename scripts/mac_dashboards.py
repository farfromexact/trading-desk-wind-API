"""Open both Mac dashboards in one foreground session with one WindPy backend."""

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
from http.client import HTTPException
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

from scripts.serve_dashboard_v2 import ROOT, read_local_json

BACKEND, V2 = 8765, 8770
HOST = '127.0.0.1'


def url(port):
    return f'http://{HOST}:{port}/'


def v2_directory(path):
    if path == ROOT:
        return True
    # The published trial can still be serving 8770 on the first stable launch.
    if path != (ROOT / '.tmp/mac-v2').resolve() or not (path / '.git').is_file():
        return False
    prefix, _, git_dir = (path / '.git').read_text().strip().partition(': ')
    return prefix == 'gitdir' and (path / git_dir).resolve().parent == ROOT / '.git/worktrees'


def output(*args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode == 1 and not result.stderr.strip():  # lsof/ps: process absent
        return ''
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f'{args[0]} 查询失败')
    return result.stdout.strip()


@dataclass(frozen=True)
class Listener:
    pid: int
    directory: Path
    identity: str  # Includes owner, start time and complete command for rechecking PID reuse.


def listener(port):
    fields = output('/usr/sbin/lsof', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN', '-Fpn').splitlines()
    if not fields:
        return None
    pids = {int(line[1:]) for line in fields if line.startswith('p')}
    endpoints = {line[1:] for line in fields if line.startswith('n')}
    if len(pids) != 1 or endpoints != {f'{HOST}:{port}'}:
        raise RuntimeError(f'端口 {port} 已被其他服务占用')
    pid = pids.pop()
    identity = output('/bin/ps', '-ww', '-p', str(pid), '-o', 'uid=,lstart=,command=')
    files = output('/usr/sbin/lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn').splitlines()
    directories = [Path(line[1:]).resolve() for line in files if line.startswith('n')]
    if not identity or not directories:
        raise RuntimeError(f'端口 {port} 的进程已变化，请重新打开启动或停止入口')
    fields = identity.split(maxsplit=6)  # uid, the five lstart fields, full command
    if len(fields) != 7:
        raise RuntimeError(f'无法核对端口 {port} 的进程身份')
    owner, command = fields[0], f' {fields[6]} '
    directory = directories[0]
    if port == BACKEND:
        expected = (directory == ROOT and ' -m uvicorn app.main:app ' in command
                    and f' --host {HOST} ' in command and f' --port {port} ' in command)
    else:
        expected = v2_directory(directory) and ' scripts/serve_dashboard_v2.py ' in command
    if owner != str(os.getuid()) or not expected:
        raise RuntimeError(f'端口 {port} 的进程不属于本仓库看板')
    return Listener(pid, directory, identity)


@contextmanager
def launch_lock():
    directory = ROOT / '.tmp'
    directory.mkdir(exist_ok=True)
    with (directory / 'mac-dashboards.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def start_process(port):
    if port == V2:
        return subprocess.Popen([sys.executable, '-X', 'utf8', 'scripts/serve_dashboard_v2.py',
                                 '--port', str(port), '--build'], cwd=ROOT, start_new_session=True)
    # Claim the actual TCP port before uvicorn runs the app lifespan and starts Wind.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((HOST, port))
        sock.listen()
        return subprocess.Popen(
            [sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', HOST, '--port', str(port),
             '--fd', str(sock.fileno()), '--no-access-log', '--timeout-graceful-shutdown', '5'],
            cwd=ROOT, pass_fds=(sock.fileno(),), start_new_session=True)


def wait_ready(port, child, existing):
    deadline = time.monotonic() + 60
    path = '/openapi.json' if port == BACKEND else '/v2-api/health'
    while time.monotonic() < deadline:
        try:
            info = read_local_json(port, path, 1)
            valid = isinstance(info, dict)
            if valid and port == BACKEND:
                valid = isinstance(info.get('info'), dict) and info['info'].get('title') == 'Wind 实时机会工作台'
            elif valid:
                valid = (info.get('service') == 'wind-desk-v2' and info.get('version') == 2
                         and isinstance(info.get('root'), str) and v2_directory(Path(info['root'])))
                if valid and info.get('pid') != (existing.pid if existing else child.pid):
                    # A standalone V2 launch may have won the port while ours was starting.
                    actual = listener(port)
                    valid = existing is None and actual is not None and info.get('pid') == actual.pid
            if not valid:
                raise RuntimeError(f'端口 {port} 的看板身份不匹配')
            return
        except (OSError, HTTPException, ValueError):
            pass
        if child is not None and child.poll() is not None:
            raise RuntimeError(f'{port} 启动退出，请查看上方日志')
        time.sleep(.25)
    raise RuntimeError(f'{port} 在 60 秒内没有就绪，请查看启动日志')


def close_owned(children):
    for port, child in reversed(list(children.items())):
        if child.poll() is None:
            try:
                if port == V2:
                    os.killpg(child.pid, signal.SIGINT)  # Also stop a build still in progress.
                else:
                    child.terminate()  # Let uvicorn drain its own Wind worker.
            except ProcessLookupError:
                pass
    for child in children.values():
        child.wait()


def start(open_browser):
    children = {}
    pages = []
    try:
        with launch_lock():
            for port in (BACKEND, V2):
                try:
                    running = listener(port)
                    if running is None:
                        children[port] = start_process(port)
                    wait_ready(port, children.get(port), running)
                    pages.append(url(port))
                    print(f'{"复用" if running else "已启动"} {pages[-1]}', flush=True)
                except (OSError, RuntimeError) as exc:
                    if port == BACKEND:
                        raise
                    print(f'V2 暂不可用：{exc}。原版仍可使用。', file=sys.stderr, flush=True)
                    if port in children:
                        close_owned({port: children.pop(port)})
            if open_browser:
                result = subprocess.run(['/usr/bin/open', *pages], check=False)
                if result.returncode:
                    print('请在浏览器打开上方地址。', file=sys.stderr)
        if children:
            print('保持此终端打开。Control+C 关闭本窗口启动的服务。', flush=True)
            primary = children.get(BACKEND) or children[V2]
            if primary.wait() not in (0, -signal.SIGTERM, -signal.SIGINT):
                return 1
        return 0 if len(pages) == 2 else 1
    finally:
        close_owned(children)


def stop(check_only):
    with launch_lock():
        # Identify both independently. A foreign port must never receive a signal.
        failed = False
        for port in (V2, BACKEND):
            try:
                current = listener(port)
                if current is None:
                    print(f'{port} 已停止。')
                    continue
                print(f'已确认 {port} 看板，PID {current.pid}。', flush=True)
                if check_only:
                    continue
                confirmed = listener(port)
                if confirmed is None:
                    continue
                if confirmed != current:
                    raise RuntimeError('进程已变化，请重新检查')
                try:
                    os.kill(current.pid, signal.SIGINT if port == V2 else signal.SIGTERM)
                except ProcessLookupError:
                    continue
                deadline = time.monotonic() + 20
                while output('/bin/ps', '-ww', '-p', str(current.pid), '-o', 'uid=,lstart=,command=') == current.identity:
                    if time.monotonic() >= deadline:
                        raise RuntimeError('仍在退出，请查看终端日志')
                    time.sleep(.2)
                print(f'{port} 已停止。')
            except (OSError, RuntimeError) as exc:
                failed = True
                print(f'{port}：{exc}', file=sys.stderr)
        return int(failed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    actions.add_parser('start').add_argument('--no-browser', action='store_true')
    actions.add_parser('stop').add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.action == 'stop':
        return stop(args.check)

    def interrupt(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGHUP, interrupt)
    signal.signal(signal.SIGTERM, interrupt)
    try:
        return start(not args.no_browser)
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
