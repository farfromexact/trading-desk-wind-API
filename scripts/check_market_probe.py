"""Reproduce native OHLCV checks from the saved, read-only Wind API probe."""
import json
from pathlib import Path
import sys
from datetime import timedelta

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.domain import Bar, ROOT, dt, iso
from app.markets import label_window


def unpack(code, tf, result):
    bars=[]
    for i,stamp in enumerate(result['times']):
        values={f:column[i] for f,column in zip(result['fields'],result['data'])}
        bar=Bar(code,tf,iso(stamp),**values)
        if bar.valid():
            bars.append(bar)
    return bars


def check():
    source=ROOT/'reports'/'market-extension-probe.json'
    payload=json.loads(source.read_text(encoding='utf8'))
    report=[]
    for code in payload['mapping']['data'][0][2:]:
        raw=payload['bars'][code+':1']
        minutes=unpack(code,1,raw)
        for tf in (5,15):
            checks=0
            differences=[]
            for bar in unpack(code,tf,payload['bars'][code+':'+str(tf)]):
                if dt(bar.end)>dt(raw['times'][-1]):
                    continue
                start=dt(bar.end)-timedelta(minutes=tf)
                include_open=start==label_window(bar.end,code)[0]
                seq=[b for b in minutes if (dt(b.end)>=start if include_open else dt(b.end)>start) and b.end<=bar.end]
                if not seq:
                    continue
                aggregation=dict(open=seq[0].open,high=max(x.high for x in seq),low=min(x.low for x in seq),close=seq[-1].close,volume=sum(x.volume for x in seq))
                diff={k:dict(native=getattr(bar,k),from_1m=v) for k,v in aggregation.items() if abs(getattr(bar,k)-v)>.001}
                checks+=1
                if diff:
                    differences.append(dict(label=bar.end,differences=diff))
            report.append(dict(code=code,timeframe=tf,checks=checks,empty_minutes_ignored=len(raw['times'])-len(minutes),mismatches=differences))
    (ROOT/'reports'/'market-native-crosschecks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print('checks',sum(x['checks'] for x in report),'mismatches',sum(len(x['mismatches']) for x in report))
    return report


if __name__=='__main__':
    assert not any(x['mismatches'] for x in check())
