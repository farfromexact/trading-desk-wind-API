"""Replay final cached bars, explicitly distinct from actual arrival-time audit events."""
import argparse
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

from .domain import ROOT, Bar, bar_finished_at, completed_bar, dt, iso, load_config, now
from .engine import Engine
from .markets import market, trading_day


def build_replay(db_path, contract, day, config=None):
    date.fromisoformat(day)
    config=config or load_config()
    db=sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True)
    try:
        setting=db.execute('SELECT payload FROM settings WHERE key=?',('calendar_'+market(contract)['calendar'],)).fetchone()
        calendar=set(json.loads(setting[0])) if setting else None
        rows=db.execute('SELECT payload FROM bars WHERE contract=? AND timeframe IN (5,15) AND substr(end,1,10)<=? ORDER BY end,timeframe DESC',(contract,day)).fetchall()
    finally:
        db.close()
    bars=[Bar(**json.loads(r[0])) for r in rows]
    # A rolling live calendar must not discard older, already cached replay dates.
    bars=[b for b in bars if completed_bar(b,now(),config['bar_grace_seconds']) and trading_day(b.end,contract,calendar)<=day]
    days=sorted({trading_day(b.end,contract,calendar) for b in bars})[-config['history_days']:]
    bars=[b for b in bars if trading_day(b.end,contract,calendar) in days]
    digest=hashlib.sha256(json.dumps([b.json() for b in bars],sort_keys=True).encode()).hexdigest()
    engine=Engine(contract,config,calendar)
    frames=[]
    for b in bars:
        available=bar_finished_at(b)+timedelta(seconds=config['bar_grace_seconds'])
        engine.on_bar(b,known_at=iso(available),historical=True)
        if b.timeframe==5 and trading_day(b.end,contract,calendar)==day:
            view=deepcopy(engine.snapshot())
            view['opportunities']=[op for op in view['opportunities'] if op['trading_day']==day]
            view['structures']=view['structures'][-80:]
            frames.append({'label':b.end,'available_after':iso(available),'snapshot':view,'events':deepcopy(engine.events)})
        engine.events.clear()
    return {'mode':'final_bar_replay','description':'终态K线回放；确认时间为按Wind标签推算的最早可用时刻，并非当时实际收到数据的时刻。实际发布记录请查看事件日志。',
            'contract':contract,'market':market(contract,config),'day':day,'rule_version':config['version'],'rule_hash':config['hash'],'source_sha256':digest,
            'bars':[b.json() for b in bars if b.timeframe==5],'frames':frames}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--contract',required=True)
    parser.add_argument('--day',required=True)
    parser.add_argument('--database',default=str(ROOT/'data'/'desk.sqlite3'))
    parser.add_argument('--output')
    args=parser.parse_args()
    result=build_replay(args.database,args.contract,args.day)
    output=Path(args.output) if args.output else ROOT/'reports'/f'replay_{args.contract}_{args.day}_{now():%H%M%S%f}.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as file:
        json.dump(result,file,ensure_ascii=False,indent=2)
    print(f'{len(result["frames"])} frames; source SHA256 {result["source_sha256"]}; {output}')


if __name__=='__main__':
    main()
