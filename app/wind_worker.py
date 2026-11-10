"""Only this child process imports WindPy; callbacks never call Wind APIs."""
from __future__ import annotations

import math
import queue
import time
import traceback
import heapq
import threading
from datetime import timedelta

from .domain import Bar, dt, iso, now
from .options_wind import OPTION_FIELDS, dispatch


def serialize_quotes(result, channel='futures', snapshot=False):
    if result.ErrorCode:
        return {'type': 'option_feed_error' if channel=='options' else 'feed_error', 'message': f'WSQ错误 {result.ErrorCode}'}
    records = {}
    for i, code in enumerate(result.Codes):
        values = {}
        for field, column in zip(result.Fields, result.Data):
            if i < len(column):
                value = column[i]
                if isinstance(value, (int, float)) and math.isfinite(value):
                    values[field.lower()] = value
                elif channel=='options':
                    # Explicit invalidation is different from an omitted delta.
                    values[field.lower()] = None
        records[code] = values
    return {'type': 'option_quotes' if channel=='options' else 'quotes', 'records': records, 'snapshot':snapshot,'received_at': iso(now()), 'received_ms':time.time()*1000}


def worker_main(commands, results):
    from WindPy import w

    callbacks = queue.SimpleQueue()
    subscriptions = {}
    jobs=[]
    counter=0
    done=threading.Event()

    def relay():
        while not done.is_set():
            try:
                r,channel=callbacks.get(timeout=.1)
                results.put(serialize_quotes(r,channel))
            except queue.Empty:
                pass

    threading.Thread(target=relay,daemon=True).start()

    def check(r, label):
        if r.ErrorCode:
            raise RuntimeError(f'{label}: Wind错误 {r.ErrorCode}: {r.Data}')
        return r

    try:
        check(w.start(waitTime=10), '连接')
        results.put({'type': 'connected'})
        while True:
            try:
                cmd=commands.get(timeout=.01 if jobs else .05)
                counter+=1
                heapq.heappush(jobs,(-100 if cmd['op']=='stop' else 10 if cmd['op'].startswith('option_') else 0,counter,cmd))
                while True:
                    cmd=commands.get_nowait()
                    counter+=1
                    heapq.heappush(jobs,(-100 if cmd['op']=='stop' else 10 if cmd['op'].startswith('option_') else 0,counter,cmd))
            except queue.Empty:
                pass
            if not jobs:
                continue
            cmd=heapq.heappop(jobs)[2]
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
                    if subscriptions.get('futures'):
                        w.cancelRequest(subscriptions['futures'])
                    fields = 'rt_last,rt_bid1,rt_ask1,rt_vol,rt_date,rt_time'
                    codes = ','.join(cmd['codes'])
                    r = check(w.wsq(codes, fields, func=lambda r: callbacks.put((r,'futures'))), '订阅')
                    subscriptions['futures'] = r.RequestID
                    payload = {'request_id': r.RequestID}
                    results.put(serialize_quotes(check(w.wsq(codes, fields), '快照'),snapshot=True))
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
                elif op == 'option_snapshot':
                    results.put(serialize_quotes(check(w.wsq(','.join(cmd['codes']),OPTION_FIELDS),'期权快照'),'options',True))
                    payload=None
                elif op == 'option_subscribe':
                    if subscriptions.get('options'):
                        w.cancelRequest(subscriptions.pop('options'))
                    if cmd['codes']:
                        r=check(w.wsq(','.join(cmd['codes']),OPTION_FIELDS,func=lambda r:callbacks.put((r,'options'))),'期权订阅')
                        subscriptions['options']=r.RequestID
                    payload={'request_id':subscriptions.get('options')}
                elif op.startswith('option_'):
                    payload=dispatch(w,cmd,check)
                else:
                    raise ValueError('未知行情请求')
                results.put({'type': 'response', 'id': request_id, 'op': op, 'payload': payload, 'received_at': iso(now())})
            except Exception as exc:
                results.put({'type': 'response', 'id': request_id, 'op': op, 'error': str(exc)})
    except Exception as exc:
        results.put({'type': 'fatal', 'message': str(exc)})
    finally:
        done.set()
        try:
            for subscription in subscriptions.values():
                w.cancelRequest(subscription)
            w.stop()
        except Exception:
            pass
