"""Import explicitly supplied, hashed research receipts into an isolated preview cache."""
import argparse
import hashlib
import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.domain import Bar, dt, iso, load_config, now
from app.engine import Engine
from app.market_context import ContextBook, INDICES, mcp_rows, number
from app.etf_context import daily_etf_rows
from app.markets import market, root_of


def prepare(history, snapshots, cross, output, etf_flow=None, etf_daily=None):
    manifest=json.loads(history.read_text(encoding='utf-8'))
    book=ContextBook()
    inputs={str(history):hashlib.sha256(history.read_bytes()).hexdigest()}
    instruments=[]
    native_bars={}
    events=[]
    config=load_config()
    for request in manifest['requests']:
        role=request.get('role','')
        if request.get('status')!='ok' or not (role.startswith('cash-') or role.startswith('futures-')):
            continue
        source=history.parent/request['filename']
        digest=hashlib.sha256(source.read_bytes()).hexdigest()
        if digest!=request['sha256']:raise ValueError('Source hash mismatch: '+source.name)
        inputs[str(source)]=digest
        result=json.loads(source.read_text(encoding='utf-8'))['result']
        fields={k.lower():v for k,v in zip(result['Fields'],result['Data'])}
        code=request['arguments'][0]
        if role.startswith('cash-') and code in INDICES.values():
            rows=[dict(TIME=t,MATCH=fields['close'][i],TURNOVER=fields['amount'][i]) for i,t in enumerate(result['Times'])]
            book.add_bars(code,rows,{'TURNOVER':'元'},manifest['closed_at'])
    # Replay the existing engine to obtain actual historical structure, never synthetic levels.
    for code in manifest['plan']['futures']:
        bars=[]
        for request in manifest['requests']:
            if request.get('role','').startswith('futures-'+code) and request.get('status')=='ok':
                result=json.loads((history.parent/request['filename']).read_text(encoding='utf-8'))['result']
                columns={k.lower():v for k,v in zip(result['Fields'],result['Data'])}
                tf=int(request['arguments'][-1].split('=')[-1])
                bars.extend(Bar(code,tf,iso(t),**{k:columns[k][i] for k in ('open','high','low','close','volume')}) for i,t in enumerate(result['Times']))
        calendar={b.end[:10] for b in bars}
        engine=Engine(code,config,calendar=calendar)
        sample_day=max(calendar)
        for bar in sorted(bars,key=lambda b:(b.end,-b.timeframe)):
            engine.on_bar(bar,historical=True)
            events.extend(dict(e,audible=False) for e in engine.events if str(e.get('known_at','')).startswith(sample_day))
            engine.events.clear()
        last=next(b for b in reversed(sorted(bars,key=lambda b:b.end)) if b.timeframe==5)
        engine.expire_session(dt(last.end)+timedelta(seconds=65),historical=True)
        events.extend(dict(e,audible=False) for e in engine.events)
        view=engine.snapshot()
        instruments.append(dict(**view,root=root_of(code),contract=code,snapshot_mode='historical',ready=False,phase='已收盘',
                                market_time=iso(dt(last.end)+timedelta(seconds=65)),
                                trading_day=last.end[:10],status='历史快照',live_bars={},
                                quote={'rt_last':last.close},market=market(code,config)))
        native_bars[code]={str(tf):[b.json() for b in sorted(bars,key=lambda b:b.end) if b.timeframe==tf] for tf in (5,15)}
    rows,units=mcp_rows(json.loads(snapshots.read_text(encoding='utf-8')))
    book.add_snapshots(rows,units)
    rows,_=mcp_rows(json.loads(cross.read_text(encoding='utf-8')))
    book.cross=[dict(code=r['windcode'],name=r.get('中文简称'),time=r['交易时间'],trading_day=r['最新交易日'],
                     change_pct=number(r.get('涨跌幅')),amount=number(r.get('成交额')) if r.get('indicator_units',{}).get('成交额')=='元' else None)
                for r in rows if r['windcode'].startswith(('AU','AG','SC','T2','TL'))]
    # Other original watch cards use only actual available snapshots; no invented OHLC/quotes.
    for root in config['watchlist']:
        if any(x['root']==root for x in instruments):
            continue
        sample=next((r for r in rows if root_of(r.get('windcode'))==root),None)
        code=sample['windcode'] if sample else None
        stamp=sample.get('交易时间') if sample else None
        instruments.append(dict(root=root,contract=code,market=market(root,config),snapshot_mode='historical',
            ready=False,phase='已收盘',status='历史快照' if sample else '样本未载入',market_time=stamp,
            trading_day=(str(sample.get('最新交易日'))[:4]+'-'+str(sample.get('最新交易日'))[4:6]+'-'+str(sample.get('最新交易日'))[6:]) if sample else None,
            quote={'rt_last':number(sample.get('最新成交价'))} if sample else {},live_bars={},
            regime={'state':'warming','label':'未载入结构'},note='只读历史快照；本样本未载入该合约K线',
            levels=[],structures=[],opportunities=[]))
    instruments.sort(key=lambda x:config['watchlist'].index(x['root']))
    for source, mode in ((etf_flow,'flow'),(etf_daily,'daily')):
        if source is None:
            continue
        receipt_path=source.parent/'receipt.json'
        receipt=json.loads(receipt_path.read_text(encoding='utf-8'))
        digest=hashlib.sha256(source.read_bytes()).hexdigest()
        if digest!=receipt['stdout_sha256']:
            raise ValueError('ETF source hash mismatch: '+source.name)
        inputs[str(source)]=digest
        inputs[str(receipt_path)]=hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        envelope=json.loads(source.read_text(encoding='utf-8'))
        received=receipt['finished_at']
        if mode=='flow':
            rows,units=mcp_rows(envelope)
            book.add_etf_flow(rows,units,received)
        else:
            request_path=source.parent/'request.json'
            request=json.loads(request_path.read_text(encoding='utf-8'))
            inputs[str(request_path)]=hashlib.sha256(request_path.read_bytes()).hexdigest()
            rows,units=daily_etf_rows(envelope,request['tradeDate'])
            book.add_etf_daily(rows,units,received)
    book.updated_at=iso(now())
    output.mkdir(parents=True,exist_ok=True)
    (output/'market-context.json').write_text(json.dumps(book.json(),ensure_ascii=False,allow_nan=False),encoding='utf-8')
    (output/'preview-instruments.json').write_text(json.dumps(instruments,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    contexts={r:dict(root=r,ready=False,status='历史预览 · 未载入期权盘口',expiries=[],
                    readonly_preview=True,notes=['本历史样本没有可验证的期权双边盘口，IV和候选保持缺失']) for r in config['watchlist']}
    ordered=sorted({e['id']:e for e in events}.values(),key=lambda e:e.get('known_at',''))
    archived_events=[dict(e,seq=i+1) for i,e in enumerate(ordered)]
    state=dict(server_time=iso(now()),preview_day=manifest['plan']['history_end'],offline=True,
               mode='historical_preview',phase='11月13日历史样本',feed_state='只读布局预览 · 未连接实时采集',
               instruments=instruments,last_error=None,bootstrap_remaining=0,
               metrics={'bar_batches':0},rules={'version':config['version'],'hash':config['hash']},
               options={'contexts':contexts,'quotes':{}})
    (output/'desk-preview.json').write_text(json.dumps(dict(state=state,bars=native_bars,events=list(reversed(archived_events))),
                                                     ensure_ascii=False,allow_nan=False),encoding='utf-8')
    inputs.update({str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (snapshots,cross)})
    (output/'provenance.json').write_text(json.dumps(dict(inputs=inputs,mode='historical_preview',
        limitations='Final historical bars; not historical arrival logs or a trading signal. Native amt imported as yuan from the audited WSI amt request.'),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(output),bars={k:len(v) for k,v in book.bars.items()},instruments=len(instruments)),ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('history','snapshots','cross','output'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--etf-flow',type=Path)
    parser.add_argument('--etf-daily',type=Path)
    args=parser.parse_args()
    prepare(args.history,args.snapshots,args.cross,args.output,args.etf_flow,args.etf_daily)
