"""WindPy-only background queries. Imported by the existing worker, never imports WindPy."""
from __future__ import annotations

import math
import queue
import time
from datetime import date, datetime

from .domain import iso, now


def clean(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def receipt(result):
    return {name: clean(getattr(result, name, [])) for name in ('Codes', 'Fields', 'Times', 'Data')}


def context_priority(op):
    return -100 if op == 'stop' else 20 if op == 'context' else 10 if op.startswith('option_') else 0


class ContextRequests:
    """One background call at a time, with platform-selected snapshot transport."""
    def __init__(self, w, check, results, timeout=10, callback_snapshots=True):
        self.w, self.check, self.results, self.timeout = w, check, results, timeout
        self.callback_snapshots = callback_snapshots
        self.inbox = queue.SimpleQueue()
        self.pending = {}

    def start(self, cmd):
        method = cmd['method']
        if method not in ('wsq', 'wsi', 'wsd', 'wss'):
            raise ValueError('不支持的背景查询')
        if time.monotonic() > cmd['expires']:
            raise ValueError('主行情繁忙，本轮背景查询已让行，请稍后刷新')
        started = time.monotonic()
        if method != 'wsq' or not self.callback_snapshots:
            return receipt(self.check(getattr(self.w, method)(*cmd['args']), '盘面 WindPy '+method))
        ident = cmd['id']
        result = self.check(self.w.wsq(*cmd['args'], func=lambda r: self.inbox.put((ident, r))), '盘面回调快照')
        self.pending[ident] = dict(subscription=result.RequestID, started=started,
                                  native_seconds=time.monotonic()-started, codes=set(cmd['args'][0].split(',')),
                                  fields=cmd['args'][1].lower().split(','), rows={})
        self.results.put(dict(type='request_deferred', id=ident))
        return None

    def poll(self):
        while True:
            try:
                ident, result = self.inbox.get_nowait()
            except queue.Empty:
                break
            job = self.pending.get(ident)
            if not job:
                continue
            if result.ErrorCode:
                job['error'] = f'WindPy 背景快照错误 {result.ErrorCode}'
                continue
            # Only use full fields from the same callback. Never combine unrelated delta timestamps.
            fields = [x.lower() for x in result.Fields]
            for i, code in enumerate(result.Codes):
                if code in job['codes'] and set(job['fields']) <= set(fields):
                    cols=dict(zip(fields,result.Data))
                    job['rows'][code] = [clean(cols[f][i]) if i < len(cols[f]) else None for f in job['fields']]
        for ident, job in list(self.pending.items()):
            if not job.get('error') and job['codes'] - job['rows'].keys() and time.monotonic()-job['started'] < self.timeout:
                continue
            started = time.monotonic()
            self.results.put(dict(type='request_started', id=ident, native_started=started))
            try:
                self.w.cancelRequest(job['subscription'])
            except Exception as exc:
                job['error'] = str(exc)
            codes = sorted(job['rows'])
            packet = dict(type='response', id=ident, op='context', received_at=iso(now()),
                          native_seconds=job['native_seconds']+time.monotonic()-started)
            if not job.get('error') and job['codes'] - job['rows'].keys():
                job['error'] = 'WindPy 背景快照10秒内未完整返回'
            if job.get('error'):
                packet['error'] = job['error']
            else:
                packet['payload'] = dict(Codes=codes, Fields=job['fields'], Times=[],
                                         Data=[list(col) for col in zip(*(job['rows'][c] for c in codes))])
            self.results.put(packet)
            del self.pending[ident]

    def close(self):
        for job in self.pending.values():
            self.w.cancelRequest(job['subscription'])
        self.pending.clear()
