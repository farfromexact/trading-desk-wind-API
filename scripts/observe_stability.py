"""Read-only local stability audit; bypass proxies and retain timestamped samples."""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import statistics
import time
import urllib.request


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=600)
    parser.add_argument('--interval',type=float,default=2)
    parser.add_argument('--mode',choices=['futures-only','options-on'],required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.seconds<1 or args.interval<=0:parser.error('Positive duration and interval required')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    rows=[]; start=time.monotonic(); last_print=-60; warm_until=None; initial=None
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.with_suffix('.jsonl').open('x',encoding='utf-8') as log:
        while True:
            before=time.monotonic()
            try:
                with opener.open('http://127.0.0.1:8765/api/state',timeout=5) as response:s=json.load(response)
                active=[i['root'] for i in s['instruments'] if i['phase']=='交易中' and i['root'] in ('AG','AU','SC','IM','IF','IH')]
                contexts=s['options']['contexts']
                mode_ok=all(contexts[r].get('status')=='期权模块已关闭' for r in active) if args.mode=='futures-only' else bool(active) and all(contexts[r].get('qualified',0)>0 for r in active)
                if warm_until is None and not s['bootstrap_remaining'] and mode_ok:
                    warm_until=time.monotonic(); initial=s['metrics']['restarts']
                row=dict(at=s['server_time'],response_ms=round((time.monotonic()-before)*1000,2),
                         measured=warm_until is not None,started_at=s['metrics']['started_at'],
                         restarts=s['metrics']['restarts'],feed=s['feed_state'],error=s['last_error'],
                         active=active,options={r:{k:contexts[r].get(k) for k in ('qualified','ready','status','excluded','market_time','generated_at','expiries')} for r in active},
                         pending=s.get('acquisition',{}).get('pending',[]),
                         quote_lag_ms=s.get('acquisition',{}).get('quote_processing_lag_ms'),
                         native_requests=s.get('acquisition',{}).get('native_requests'),quote_updates=s['metrics']['quote_updates'])
            except Exception as exc:
                row=dict(at=datetime.now().astimezone().isoformat(),measured=warm_until is not None,error=repr(exc))
            rows.append(row);log.write(json.dumps(row,ensure_ascii=False)+'\n');log.flush()
            elapsed=time.monotonic()-start
            if elapsed-last_print>=60:
                print(json.dumps(dict(elapsed_seconds=round(elapsed),sample=row),ensure_ascii=False),flush=True);last_print=elapsed
            if warm_until is None and elapsed>240:
                break
            if warm_until is not None and time.monotonic()-warm_until>=args.seconds:
                break
            time.sleep(max(.01,args.interval-(time.monotonic()-before)))
    measured=[r for r in rows if r.get('measured')]
    ok=[r for r in measured if 'response_ms' in r]
    roots=sorted({x for r in ok for x in r['active']})
    result=dict(mode=args.mode,requested_seconds=args.seconds,warmup_completed=warm_until is not None,
                measurement_seconds=round(time.monotonic()-warm_until,2) if warm_until else 0,
                samples=len(measured),http_failures=len(measured)-len(ok),
                started_at=ok[0]['started_at'] if ok else None,
                distinct_server_starts=sorted({r['started_at'] for r in ok}),
                first_at=ok[0]['at'] if ok else None,last_at=ok[-1]['at'] if ok else None,
                restarts_during_measurement=max((r['restarts'] for r in ok),default=initial or 0)-(initial or 0),
                quote_lag_max_ms=max((r.get('quote_lag_ms') or 0 for r in ok),default=None),
                native_requests=ok[-1].get('native_requests') if ok else None,
                response_p50_ms=statistics.median(r['response_ms'] for r in ok) if ok else None,
                response_max_ms=max((r['response_ms'] for r in ok),default=None),roots={})
    for root in roots:
        samples=[r['options'][root] for r in ok if root in r['options']]
        result['roots'][root]=dict(samples=len(samples),zero_qualified=sum(not x.get('qualified') for x in samples),
                                  not_ready=sum(not x.get('ready') for x in samples),
                                  statuses=dict(Counter(x['status'] for x in samples)),
                                  qualified_min=min((x.get('qualified') or 0 for x in samples),default=0))
    result['continuous_qualified']=all(v['zero_qualified']==0 for v in result['roots'].values()) if args.mode=='options-on' and roots else None
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0 if result['warmup_completed'] and result['http_failures']==0 and result['restarts_during_measurement']==0 and len(result['distinct_server_starts'])==1 and result['continuous_qualified'] is not False else 1


if __name__=='__main__':
    raise SystemExit(main())
