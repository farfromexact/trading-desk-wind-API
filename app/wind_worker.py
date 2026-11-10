"""Only this child process imports WindPy; callbacks never call Wind APIs."""
from __future__ import annotations

import math
import queue
import time
import traceback
from datetime import timedelta

from .domain import Bar, dt, iso, now


def serialize_quotes(result):
    if result.ErrorCode:
        return {'type': 'feed_error', 'message': f'WSQ错误 {result.ErrorCode}'}
    records = {}
    for i, code in enumerate(result.Codes):
        values = {}
        for field, column in zip(result.Fields, result.Data):
            if i < len(column):
                value = column[i]
                if isinstance(value, (int, float)) and math.isfinite(value):
                    values[field.lower()] = value
        records[code] = values
    return {'type': 'quotes', 'records': records, 'received_at': iso(now()), 'received_ms':time.time()*1000}


def worker_main(commands, results):
    from WindPy import w

    callbacks = queue.SimpleQueue()
    subscription = None

    def check(r, label):
        if r.ErrorCode:
            raise RuntimeError(f'{label}: Wind错误 {r.ErrorCode}: {r.Data}')
        return r

    try:
        check(w.start(waitTime=10), '连接')
        results.put({'type': 'connected'})
        while True:
            while not callbacks.empty():
                results.put(callbacks.get_nowait())
            try:
                cmd = commands.get(timeout=0.05)
            except queue.Empty:
                continue
            request_id, op = cmd['id'], cmd['op']
            results.put({'type': 'request_started', 'id': request_id, 'op': op})
            try:
                if op == 'stop':
                    break
                if op == 'calendar':
                    r = check(w.tdays(cmd['start'], cmd['end'], 'TradingCalendar='+cmd.get('calendar','CFFEX')), '交易日历')
                    payload = [str(x)[:10] for x in r.Times]
                elif op == 'mapping':
                    aliases=cmd.get('aliases', ['IM.CFE','IF.CFE'])
                    r = check(w.wss(','.join(aliases), 'trade_hiscode', 'tradeDate=' + cmd['day'].replace('-', '')), '主力映射')
                    payload = dict(zip([code.split('.')[0] for code in aliases], r.Data[0]))
                elif op == 'subscribe':
                    if subscription:
                        w.cancelRequest(subscription)
                    fields = 'rt_last,rt_bid1,rt_ask1,rt_vol,rt_date,rt_time'
                    codes = ','.join(cmd['codes'])
                    r = check(w.wsq(codes, fields, func=lambda r: callbacks.put(serialize_quotes(r))), '订阅')
                    subscription = r.RequestID
                    payload = {'request_id': subscription}
                    results.put(serialize_quotes(check(w.wsq(codes, fields), '快照')))
                elif op == 'snapshot':
                    r = check(w.wsq(','.join(cmd['codes']), 'rt_last,rt_bid1,rt_ask1,rt_vol,rt_date,rt_time'), '快照')
                    results.put(serialize_quotes(r))
                    payload = None
                elif op == 'bars':
                    r = check(w.wsi(cmd['code'], 'open,high,low,close,volume', cmd['start'], cmd['end'], f"BarSize={cmd['timeframe']}"), '分钟线')
                    columns = {f.lower(): col for f, col in zip(r.Fields, r.Data)}
                    payload = []
                    for i, stamp in enumerate(r.Times):
                        try:
                            b = Bar(cmd['code'], cmd['timeframe'], iso(stamp), **{f: float(columns[f][i]) for f in ('open', 'high', 'low', 'close', 'volume')})
                            if b.valid():
                                payload.append(b.json())
                        except (ValueError, TypeError, IndexError, KeyError):
                            continue
                else:
                    raise ValueError('未知行情请求')
                results.put({'type': 'response', 'id': request_id, 'op': op, 'payload': payload, 'received_at': iso(now())})
            except Exception as exc:
                results.put({'type': 'response', 'id': request_id, 'op': op, 'error': str(exc)})
    except Exception as exc:
        results.put({'type': 'fatal', 'message': str(exc)})
    finally:
        try:
            if subscription:
                w.cancelRequest(subscription)
            w.stop()
        except Exception:
            pass
