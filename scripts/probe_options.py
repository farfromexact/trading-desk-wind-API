"""Read-only, bounded Wind capability probe; run while the desk is stopped."""
import json
import sys
from datetime import datetime
from pathlib import Path

from WindPy import w

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / 'reports' / ('options-quotes-probe.json' if '--quotes' in sys.argv else 'options-capability-probe.json')
records = []


def query(label, method, *args):
    started = datetime.now().isoformat()
    r = getattr(w, method)(*args)
    row = dict(label=label, method=method, args=args, started=started,
               error=r.ErrorCode, fields=r.Fields, codes=r.Codes,
               times=[str(t) for t in r.Times], data=r.Data)
    records.append(row)
    out.write_text(json.dumps(records, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(label, r.ErrorCode, r.Fields, str(r.Data)[:800], flush=True)
    return r


if __name__ == '__main__':
    print('connect', w.start(waitTime=10), flush=True)
    try:
        day=datetime.now().strftime('%Y-%m-%d')
        if '--quotes' in sys.argv:
            original=json.loads((ROOT/'reports/options-capability-probe.json').read_text(encoding='utf-8'))
            for old in original:
                if not old['label'].endswith('chain'):
                    continue
                cols=dict(zip(old['fields'],old['data']))
                underlying=cols['us_code'][0]
                uq=query(old['label']+' underlying','wsq',underlying,'rt_last,rt_date,rt_time')
                spot=uq.Data[0][0]
                expiries=sorted(set(cols['last_tradedate']))[:2]
                chosen=[]
                for expiry in expiries:
                    ids=[i for i,e in enumerate(cols['last_tradedate']) if e==expiry]
                    strikes=sorted({cols['strike_price'][i] for i in ids},key=lambda k:abs(k-spot))[:21]
                    chosen += [cols['option_code'][i] for i in ids if cols['strike_price'][i] in strikes]
                query(old['label']+' quotes','wsq',','.join(chosen),'rt_last,rt_bid1,rt_ask1,rt_bsize1,rt_asize1,rt_vol,rt_oi,rt_date,rt_time')
                query(old['label']+' daily','wsd',underlying,'close','2026-09-01',day,'PriceAdj=U;Days=Trading;Fill=Blank')
                query(old['label']+' vendor','wss',chosen[len(chosen)//2],'us_impliedvol,delta,gamma','tradeDate='+day.replace('-',''))
            sys.exit(0)
        for root, underlying, exchange in [('IM','000852.SH','CFE'),('IF','000300.SH','CFE'),('IH','000016.SH','CFE'),('AG','AG2612.SHF','SHF'),('AU','AU2612.SHF','SHF'),('SC','SC2612.INE','INE')]:
            r=query(root+' chain','wset','optionchain',f'date={day};us_code={underlying};option_var=all;call_put=all')
            if r.ErrorCode:
                query(root+' basic','wset','optioncontractbasicinfo',f'exchange={exchange};windcode={underlying};status=trading')
        for code in ['MO2611-C-7000.CFE','AG2612C18000.SHF','AU2612C900.SHF','SC2612C500.INE']:
            query(code+' quote','wsq',code,'rt_last,rt_bid1,rt_ask1,rt_bsize1,rt_asize1,rt_vol,rt_oi,rt_date,rt_time')
            query(code+' meta','wss',code,'sec_name,exe_price,exe_enddate,exe_mode,exe_type,exe_ratio,underlyingwindcode,price_per_tick')
    finally:
        w.stop()
