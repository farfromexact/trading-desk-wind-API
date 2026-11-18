"""Open the integrated Mac dashboard and stop verified dashboard processes."""

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
from http.client import HTTPConnection, HTTPException
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
BACKEND, V2 = 8765, 8770
HOST = '127.0.0.1'


def url(port):
    return f'http://{HOST}:{port}/'


def v2_directory(path):
    if path == ROOT:
        return True
    # Older installs may still have a standalone V2 process to stop.
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
    # Claim the actual TCP port before uvicorn runs the app lifespan and starts Wind.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((HOST, port))
        sock.listen()
        return subprocess.Popen(
            [sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', HOST, '--port', str(port),
             '--fd', str(sock.fileno()), '--no-access-log', '--timeout-graceful-shutdown', '5'],
            cwd=ROOT, pass_fds=(sock.fileno(),), start_new_session=True)


def wait_ready(port, child):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        connection = HTTPConnection(HOST, port, timeout=1)
        try:
            connection.request('GET', '/openapi.json')
            response = connection.getresponse()
            if response.status == 200:
                schema = json.load(response)
                info = schema.get('info') if isinstance(schema, dict) else None
                if not isinstance(info, dict) or info.get('title') != 'Wind 实时机会工作台':
                    raise RuntimeError(f'端口 {port} 的看板身份不匹配')
                return
        except (OSError, HTTPException, ValueError):
            pass
        finally:
            connection.close()
        if child is not None and child.poll() is not None:
            raise RuntimeError(f'{port} 启动退出，请查看上方日志')
        time.sleep(.25)
    raise RuntimeError(f'{port} 在 60 秒内没有就绪，请查看启动日志')


def start(open_browser):
    child = None
    try:
        with launch_lock():
            running = listener(BACKEND)
            if running is None:
                child = start_process(BACKEND)
            wait_ready(BACKEND, child)
            page = url(BACKEND)
            print(f'{"复用" if running else "已启动"} {page}', flush=True)
            if open_browser and subprocess.run(['/usr/bin/open', page], check=False).returncode:
                print(f'请在浏览器打开 {page}', file=sys.stderr)
        if child is None:
            return 0
        print('保持此终端打开。Control+C 关闭本窗口启动的服务。', flush=True)
        return int(child.wait() not in (0, -signal.SIGTERM, -signal.SIGINT))
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            child.wait()


def stop(check_only, ports=(V2, BACKEND)):
    with launch_lock():
        # Identify both independently. A foreign port must never receive a signal.
        failed = False
        for port in ports:
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
