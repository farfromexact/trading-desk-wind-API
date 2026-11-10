"""Only this child process imports WindPy; callbacks never call Wind APIs."""
from __future__ import annotations

import math
import queue
import time
import traceback
import heapq
import threading
from datetime import datetime, timedelta

from .domain import Bar, TZ, dt, iso, now
from .options_wind import OPTION_FIELDS, dispatch


def update_option_subscriptions(w, desired, groups, callback, check):
    """Keep unchanged subscription groups alive; only replace affected groups."""
    desired=set(desired)
    for codes,ident in list(groups.items()):
        if not codes<=desired:
            w.cancelRequest(ident)  # official macOS binding returns void
            del groups[codes]
    covered=set().union(*groups) if groups else set()
    added=sorted(desired-covered)
    for i in range(0,len(added),32):
        codes=frozenset(added[i:i+32])
        result=check(w.wsq(','.join(sorted(codes)),OPTION_FIELDS,func=callback),'期权订阅')
        groups[codes]=result.RequestID
    return list(groups.values())


def serialize_quotes(result, channel='futures', snapshot=False, received_ms=None):
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
    received_ms=time.time()*1000 if received_ms is None else received_ms
    return {'type': 'option_quotes' if channel=='options' else 'quotes', 'records': records, 'snapshot':snapshot,
            'received_at':iso(datetime.fromtimestamp(received_ms/1000,TZ)), 'received_ms':received_ms}


def worker_main(commands, results, quote_results=None):
    from WindPy import w

    quote_results=results if quote_results is None else quote_results

    callbacks = queue.SimpleQueue()
    subscriptions = {}
    option_subscriptions={}
    jobs=[]
    counter=0
    done=threading.Event()

    def relay():
        while not done.is_set():
            try:
                r,channel,received_ms=callbacks.get(timeout=.1)
                quote_results.put(serialize_quotes(r,channel,received_ms=received_ms))
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
            started=time.monotonic()
            results.put({'type': 'request_started', 'id': request_id, 'op': op,'native_started':started})
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
                    r = check(w.wsq(codes, fields, func=lambda r: callbacks.put((r,'futures',time.time()*1000))), '订阅')
                    subscriptions['futures'] = r.RequestID
                    payload = {'request_id': r.RequestID}
                    quote_results.put(serialize_quotes(check(w.wsq(codes, fields), '快照'),snapshot=True))
                elif op == 'snapshot':
                    r = check(w.wsq(','.join(cmd['codes']), 'rt_last,rt_bid1,rt_ask1,rt_vol,rt_date,rt_time'), '快照')
                    quote_results.put(serialize_quotes(r,snapshot=True))
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
                    quote_results.put(serialize_quotes(check(w.wsq(','.join(cmd['codes']),OPTION_FIELDS),'期权快照'),'options',True))
                    payload=None
                elif op == 'option_subscribe':
                    ids=update_option_subscriptions(w,cmd['codes'],option_subscriptions,
                        lambda r:callbacks.put((r,'options',time.time()*1000)),check)
                    payload={'request_ids':ids}
                elif op.startswith('option_'):
                    payload=dispatch(w,cmd,check)
                else:
                    raise ValueError('未知行情请求')
                results.put({'type': 'response', 'id': request_id, 'op': op, 'payload': payload, 'received_at': iso(now()),
                             'native_seconds':time.monotonic()-started})
            except Exception as exc:
                results.put({'type': 'response', 'id': request_id, 'op': op, 'error': str(exc),
                             'native_seconds':time.monotonic()-started})
    except Exception as exc:
        results.put({'type': 'fatal', 'message': str(exc)})
    finally:
        done.set()
        try:
            for subscription in list(subscriptions.values())+list(option_subscriptions.values()):
                w.cancelRequest(subscription)
            w.stop()
        except Exception:
            pass
