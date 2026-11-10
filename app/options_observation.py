"""Read-only acceptance evidence from the running service. Never starts Wind."""
import argparse
import hashlib
import json
from time import monotonic, sleep
from urllib.request import urlopen

from .domain import ROOT, now, iso


def read(path):
    start=monotonic()
    with urlopen('http://127.0.0.1:8765/api/'+path,timeout=8) as response:
        data=json.load(response)
    return data,round((monotonic()-start)*1000,2)


def collect(minutes=0,session='day'):
    started=iso(now()); deadline=monotonic()+minutes*60
    roots=['AG','AU','SC'] if session=='night' else ['IM','IF','IH','AG','AU','SC']
    samples=[]; longest=continuous=0.; previous=monotonic(); last_progress=0
    while True:
        stamp=monotonic()
        try:
            state,latency=read('state')
            items={x['root']:x for x in state['instruments']}
            contexts=state.get('options',{}).get('contexts',{})
            healthy=all(items[r]['ready'] and contexts.get(r,{}).get('ready') for r in roots)
            sample=dict(at=state['server_time'],http_response_ms=latency,healthy=healthy,roots={r:dict(future_status=items[r]['status'],
                option_status=contexts.get(r,{}).get('status'),qualified=contexts.get(r,{}).get('qualified'),option_time=contexts.get(r,{}).get('market_time')) for r in roots})
            continuous=continuous+min(stamp-previous,2) if healthy else 0.
            longest=max(longest,continuous)
        except Exception as exc:
            sample=dict(at=iso(now()),error=f'{type(exc).__name__}: {exc}',healthy=False);continuous=0.
        samples.append(sample);previous=stamp
        if minutes and stamp-last_progress>=30:
            print(json.dumps(dict(at=sample['at'],samples=len(samples),continuous_seconds=round(continuous,1)),ensure_ascii=False),flush=True)
            last_progress=stamp
        if monotonic()>=deadline:
            break
        sleep(min(1,max(0,deadline-monotonic())))
    chains={}
    for root in ('IM','IF','IH','AG','AU','SC','T','TL'):
        payload,latency=read('options/chain?root='+root)
        comparison,_=read('options/candidates?root='+root)
        chains[root]=dict(context=payload['context'],discovered=payload['metadata_count'],http_response_ms=latency,
                          rows=len(payload['rows']),candidate_count=len(comparison['candidates']),reasons=comparison['reasons'])
    research,_=read('options/research')
    files=list((ROOT/'app').glob('*.py'))+list((ROOT/'tests').glob('*.py'))+list((ROOT/'frontend'/'src').glob('*'))+[ROOT/'options-config.json',ROOT/'requirements-lock.txt']
    return dict(started_at=started,ended_at=iso(now()),requested_minutes=minutes,session=session,samples=samples,
                longest_continuous_seconds=round(longest,2),chains=chains,research=research,
                scope='本机HTTP与数据同步观察。HTTP耗时不是浏览器渲染延迟；页面可见时的SSE渲染样本单独记录在metrics。未达到30分钟或非交易时段不能算盘中验收。',
                source_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--minutes',type=float,default=0)
    parser.add_argument('--session',choices=('day','night'),default='day')
    args=parser.parse_args()
    if not 0<=args.minutes<=120:
        parser.error('minutes must be between 0 and 120')
    report=collect(args.minutes,args.session)
    output=ROOT/'reports'/f'options-observation_{now():%Y%m%d_%H%M%S%f}.json'
    with output.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(report=str(output),continuous_seconds=report['longest_continuous_seconds'],
                         phase={r:c['context']['status'] for r,c in report['chains'].items()}),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
