"""Export local read-only observation evidence; never query Wind or place orders."""
import hashlib
import json
import sqlite3
from datetime import timedelta
from urllib.request import urlopen

from .domain import ROOT, Bar, dt, iso, now, quote_time
from .markets import label_window, market, trading_day


def collect():
    with urlopen('http://127.0.0.1:8765/api/state',timeout=5) as response:
        state=json.load(response)
    db=sqlite3.connect((ROOT/'data'/'desk.sqlite3').as_uri()+'?mode=ro',uri=True)
    db.execute('BEGIN')
    settings={k:json.loads(v) for k,v in db.execute('SELECT key,payload FROM settings')}
    runs={r['started_at']:r for r in settings.get('observation_runs',[])}
    runs[state['metrics']['started_at']]=state['metrics']
    accepted=[r for r in runs.values() if r.get('rule_version')==state['rules']['version']]
    first=min((r['first_live_at'] for r in accepted if r.get('first_live_at')),default=state['metrics']['started_at'])
    sums={k:sum(r.get(k,0) for r in accepted) for k in ('healthy_seconds','quote_updates','bar_batches','live_confirmations','restarts','ui_samples','ui_over_2s','source_ui_samples','source_ui_over_2s')}
    sums.update({k:max(r.get(k,0) for r in accepted) for k in ('ui_max_ms','source_ui_max_ms')})
    sums['healthy_minutes']=round(sums['healthy_seconds']/60,3)
    bars=[]
    day=state['server_time'][:10]
    for instrument in state['instruments']:
        code=instrument['contract']
        if not code:
            continue
        raw=[Bar(**json.loads(r[0])) for r in db.execute('SELECT payload FROM bars WHERE contract=? AND timeframe=1 AND end LIKE ? ORDER BY end',(code,day+'%'))]
        for tf in (5,15):
            rows=[Bar(**json.loads(r[0])) for r in db.execute('SELECT payload FROM bars WHERE contract=? AND timeframe=? AND end LIKE ? ORDER BY end',(code,tf,day+'%'))]
            for bar in rows:
                start=dt(bar.end)-timedelta(minutes=tf)
                window=label_window(dt(bar.end),code)
                include_open=window and start==window[0]
                seq=[x for x in raw if (dt(x.end)>=start if include_open else dt(x.end)>start) and x.end<=bar.end]
                if not seq:
                    continue
                aggregation=dict(open=seq[0].open,high=max(x.high for x in seq),low=min(x.low for x in seq),close=seq[-1].close,volume=sum(x.volume for x in seq))
                differences={k:{'native':getattr(bar,k),'from_1m':v} for k,v in aggregation.items() if abs(getattr(bar,k)-v)>.001}
                bars.append(dict(contract=code,timeframe=tf,label=bar.end,minute_rows=len(seq),differences=differences))
    lag=[]
    for code,received,payload in db.execute('SELECT contract,received_at,payload FROM quotes WHERE received_at>=?',(first,)):
        stamp=quote_time(json.loads(payload),code,set(settings.get('calendar_'+market(code)['calendar'],[])),dt(received))
        if stamp and stamp.date()==dt(received).date():
            lag.append(max(0,(dt(received)-stamp).total_seconds()))
    lag.sort()
    lag_stats={'samples':len(lag),'description':'SQLite约每秒采样；Wind时间字段与本机接收时间均精确到秒，含查询/切换/恢复阶段。'}
    if lag:
        lag_stats.update(p50_seconds=lag[len(lag)//2],p95_seconds=lag[int((len(lag)-1)*.95)],max_seconds=lag[-1])
    errors=[json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE kind='data_error' AND recorded_at>=? ORDER BY seq",(first,))]
    revisions=[dict(zip(('contract','timeframe','count'),r)) for r in db.execute('SELECT contract,timeframe,count(*) FROM revisions WHERE seen_at>=? GROUP BY contract,timeframe',(first,))]
    source_files=list((ROOT/'app').glob('*.py'))+list((ROOT/'tests').glob('*.py'))+list((ROOT/'frontend'/'src').glob('*'))+[ROOT/'config.json',ROOT/'requirements-lock.txt',ROOT/'frontend'/'package-lock.json']
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files if p.is_file()}
    db.close()
    return dict(generated_at=iso(now()),rules=state['rules'],observation_type='累计多段开发期盘中只读观察，包含主动重启；不是连续30分钟稳定性认证',
                first_corrected_live_at=first,aggregate=sums,runs=accepted,excluded_run_count=len(runs)-len(accepted),
                current=dict(server_time=state['server_time'],last_error=state['last_error'],instruments=[{k:x.get(k) for k in ('root','contract','status','market_time','last_bar','regime','note')} for x in state['instruments']]),
                quote_source_age=lag_stats,minute_crosschecks=bars,data_errors=errors,data_revisions=revisions,source_sha256=hashes)


def main():
    import sys
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    evidence=collect()
    output=ROOT/'reports'/f'observation_{now():%Y%m%d_%H%M%S%f}.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as stream:
        json.dump(evidence,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'report':str(output),'aggregate':evidence['aggregate'],'current':evidence['current'],'quote_source_age':evidence['quote_source_age'],'bar_crosschecks':len(evidence['minute_crosschecks']),'bar_mismatches':sum(bool(x['differences']) for x in evidence['minute_crosschecks']),'data_errors':len(evidence['data_errors']),'data_revisions':evidence['data_revisions']},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
