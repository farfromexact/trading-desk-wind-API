import copy
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from scripts.serve_dashboard_v2 import Handler, LiveCache, ROOT


def fixtures():
    state = dict(server_time='2026-11-17T14:30:00+08:00', feed_state='Wind 已连接', offline=False,
                 instruments=[dict(root=r, contract=r+'2612.CFE', trading_day='2026-11-17',
                                   last_bar='2026-11-17T14:25:00+08:00', opportunities=[], structures=[], live_bars={})
                              for r in ('IM', 'IF')], options={'contexts': {'IM': {'ready': True}}, 'quotes': {'private': {}}})
    calls = []
    def read(path):
        calls.append(path)
        if path == '/api/state':
            return copy.deepcopy(state)
        if path == '/api/market-context':
            return {'breadths': [], 'monitoring': True}
        if path.startswith('/api/events'):
            return []
        if path.startswith('/api/bars?'):
            return [{'end': '2026-11-17T14:25:00+08:00', 'close': 100}]
        raise AssertionError(path)
    return state, calls, read


def test_live_preview_uses_only_cache_reads_and_shares_refresh_between_viewers():
    state, calls, read = fixtures()
    at = [0.0]
    cache = LiveCache(read, lambda: at[0])
    status, first = cache.get()
    assert status == 200 and first['mode'] == 'live'
    assert 'quotes' not in first['options'] and 'live_bars' not in first['state']['instruments'][0]
    assert len([p for p in calls if p.startswith('/api/bars')]) == 1
    count = len(calls)
    assert cache.get()[1]['sequence'] == first['sequence']
    assert len(calls) == count
    at[0] = 3
    second = cache.get()[1]
    assert len(second['bars']) == 2 and not second['transport']['bars_loading']
    assert all(p.startswith(('/api/state', '/api/market-context', '/api/events?', '/api/bars?')) for p in calls)
    assert not any('refresh' in p or 'monitor' in p or 'chart-interest' in p for p in calls)


def test_backend_outage_is_503_not_a_successful_replay_of_previous_snapshot():
    state, calls, read = fixtures()
    at = [0.0]
    failed = [False]
    def intermittent(path):
        if failed[0]:
            raise TimeoutError()
        return read(path)
    cache = LiveCache(intermittent, lambda: at[0])
    first = cache.get()[1]
    failed[0] = True
    at[0] = 3
    status, body = cache.get()
    assert status == 503 and 'state' not in body
    assert cache.get()[0] == 503
    failed[0] = False
    at[0] = 6
    state['server_time'] = '2026-11-17T14:30:06+08:00'
    assert cache.get()[1]['state']['server_time'] != first['state']['server_time']


def test_context_failure_is_marked_locally_while_main_quotes_continue():
    state, calls, read = fixtures()
    def partial(path):
        if path == '/api/market-context':
            raise TimeoutError()
        return read(path)
    cache = LiveCache(partial, lambda: 0)
    status, result = cache.get()
    assert status == 200 and result['state']['feed_state'] == 'Wind 已连接'
    assert result['context']['error'] and result['transport']['partial_errors']['context']


def test_contract_change_drops_the_old_chart_and_day_rollover_requires_new_bars():
    state, calls, read = fixtures()
    at = [0.0]
    cache = LiveCache(read, lambda: at[0])
    cache.get()
    state['instruments'][0]['contract'] = 'IM2701.CFE'
    at[0] = 3
    result = cache.get()[1]
    assert 'IM2612.CFE' not in result['bars']
    assert 'IM2701.CFE' in result['bars']
    state['instruments'][0]['trading_day'] = '2026-11-18'
    at[0] = 6
    result = cache.get()[1]
    assert 'IM2701.CFE' not in result['bars']  # the still-cold IF cache gets this turn
    assert 'IM2701.CFE' in result['transport']['bars_loading']


def test_concurrent_viewers_never_issue_parallel_cache_reads():
    _, calls, read = fixtures()
    entered, release = threading.Event(), threading.Event()
    def blocking(path):
        if path == '/api/state':
            entered.set()
            assert release.wait(timeout=5)
        return read(path)
    cache = LiveCache(blocking, lambda: 0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.get)
        assert entered.wait(timeout=5)
        second = pool.submit(cache.get)
        release.set()
        assert first.result()[1]['sequence'] == second.result()[1]['sequence'] == 1
    assert calls.count('/api/state') == 1


def test_health_identifies_the_process_without_touching_upstream_and_rejects_mutations():
    class UnusedCache:
        def get(self):
            raise AssertionError('Health and rejected routes must not read the backend')
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, cache=UnusedCache()))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        conn = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        conn.request('GET', '/v2-api/health')
        result = conn.getresponse()
        identity = json.loads(result.read())
        assert result.status == 200
        assert identity == dict(service='wind-desk-v2', version=2, root=str(ROOT), pid=os.getpid())
        for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            conn.request(method, '/api/market-context/monitor', body=b'{}')
            response = conn.getresponse()
            assert response.status == 405
            response.read()
        conn.request('GET', '/v2-api/health', headers={'Host': 'external.example'})
        response = conn.getresponse()
        assert response.status == 403
        response.read()
        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
