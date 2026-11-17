"""Wind Desk V2: local read-only presentation server sharing the 8765 cache."""
import argparse
import copy
import json
import os
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'data' / 'dashboard-v2' / 'site'


def read_cache(path):
    with urlopen('http://127.0.0.1:8765' + path, timeout=10 if path == '/api/state' else 2) as response:
        return json.load(response)


class LiveCache:
    """One refresh at a time, shared across viewers; no polling without a viewer."""
    def __init__(self, read=read_cache, monotonic=time.monotonic):
        self.read = read
        self.clock = monotonic
        self.lock = threading.Lock()
        self.last_attempt = float('-inf')
        self.snapshot = None
        self.failure = None
        self.context = {}
        self.events = []
        self.events_at = float('-inf')
        self.bars = {}
        self.bar_versions = {}
        self.bar_attempts = {}
        self.day_versions = {}
        self.sequence = 0

    def get(self):
        with self.lock:
            now = self.clock()
            if now - self.last_attempt < 2.5:
                return (503, {'error': self.failure}) if self.failure else (200, self.snapshot)
            self.last_attempt = now
            try:
                state = self.read('/api/state')
                if not state.get('server_time') or not isinstance(state.get('instruments'), list):
                    raise ValueError('后台状态格式不完整')
            except Exception:
                self.failure = '无法连接原看板后台，正在重试'
                self.last_attempt = self.clock()
                return 503, {'error': self.failure}
            self.failure = None
            partial_errors = {}
            try:
                context = self.read('/api/market-context')
                if not isinstance(context, dict) or 'breadths' not in context:
                    raise ValueError('Invalid market context')
                self.context = context
            except Exception:
                partial_errors['context'] = '盘面背景缓存读取失败'
            if now - self.events_at >= 6:
                try:
                    self.events = self.read('/api/events?limit=1000')
                    self.events_at = now
                except Exception:
                    partial_errors['events'] = '事件缓存读取失败'
            instruments = copy.deepcopy(state['instruments'])
            codes = {i['contract'] for i in instruments if i.get('contract')}
            for code in list(self.bars):
                if code not in codes:
                    self.bars.pop(code, None)
                    self.bar_versions.pop(code, None)
                    self.bar_attempts.pop(code, None)
                    self.day_versions.pop(code, None)
            # One bar-cache request per refresh prevents a cold start or a new 5m
            # candle from delaying the live quote feed behind eight history reads.
            due = []
            for i in instruments:
                code = i.get('contract')
                if not code:
                    continue
                if self.day_versions.get(code) != i.get('trading_day'):
                    self.bars.pop(code, None)
                    self.bar_versions.pop(code, None)
                    self.day_versions[code] = i.get('trading_day')
                version = i.get('last_bar')
                attempted = self.bar_attempts.get(code, float('-inf'))
                if code not in self.bars or self.bar_versions.get(code) != version or now - attempted >= 60:
                    due.append((attempted, code, version))
            if due:
                _, code, version = min(due, key=lambda entry: entry[0])
                self.bar_attempts[code] = now
                try:
                    rows = self.read('/api/bars?' + urlencode(dict(contract=code, timeframe=5)))
                    if not isinstance(rows, list):
                        raise ValueError('Invalid bars')
                    self.bars[code] = rows
                    self.bar_versions[code] = version
                except Exception:
                    partial_errors['bars'] = f'{code} 分钟线读取失败'
            for i in instruments:
                i.pop('live_bars', None)
                i['structures'] = i.get('structures', [])[-200:]
                i['opportunities'] = [o for o in i.get('opportunities', []) if o.get('trading_day') == i.get('trading_day')]
            self.sequence += 1
            self.snapshot = dict(
                mode='live', captured_at=state['server_time'], sequence=self.sequence,
                source='共享原看板 WindPy 缓存',
                state={k: state.get(k) for k in ('server_time', 'feed_state', 'last_error', 'offline', 'bootstrap_remaining')},
                context=copy.deepcopy(self.context), events=copy.deepcopy(self.events),
                options=state.get('options', {}).get('contexts', {}),
                bars={code: rows for code, rows in self.bars.items() if code in codes},
                transport=dict(upstream_ok=True, partial_errors=partial_errors,
                               bars_loading=[code for code in codes if code not in self.bars]),
            )
            self.snapshot['state']['instruments'] = instruments
            if 'context' in partial_errors:
                self.snapshot['context']['error'] = partial_errors['context']
            self.last_attempt = self.clock()
            return 200, self.snapshot


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, cache, **kwargs):
        self.cache = cache
        super().__init__(*args, directory=str(SITE), **kwargs)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        super().end_headers()

    def do_GET(self):
        host = self.headers.get('Host', '').split(':')[0]
        if host not in ('127.0.0.1', 'localhost'):
            self.send_error(403)
            return
        path = urlsplit(self.path).path
        if path in ('/v2-api/live', '/v2-api/health'):
            status, result = ((200, dict(service='wind-desk-v2', version=2, root=str(ROOT), pid=os.getpid()))
                              if path == '/v2-api/health' else self.cache.get())
            body = json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
        elif path in ('/', '/index.html', '/favicon.ico') or path.startswith('/assets/'):
            super().do_GET()
        else:
            self.send_error(404)

    def do_POST(self):
        self.send_error(405, 'Read-only V2')

    do_PUT = do_DELETE = do_PATCH = do_POST

    def list_directory(self, path):
        self.send_error(404)
        return None

    def log_message(self, format, *args):
        if args and str(args[1] if len(args) > 1 else '').startswith(('4', '5')):
            super().log_message(format, *args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8770)
    args = parser.parse_args()
    if not (SITE / 'index.html').is_file():
        parser.error('Build frontend/v2 first, or use Start Dashboard V2.cmd')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), partial(Handler, cache=LiveCache()))
    print(f'Wind Desk V2: http://127.0.0.1:{args.port}/ (read-only 8765 cache)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
