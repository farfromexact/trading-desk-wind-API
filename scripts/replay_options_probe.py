"""Reproduce pricing diagnostics from saved Wind probes, without querying Wind.

These data do not contain synchronized index futures books. No candidate or
executable return is inferred from this replay.
"""
import hashlib
import json
import sqlite3
from time import perf_counter

from app.domain import ROOT, iso, now
from app.markets import market
from app.options_core import analyse
from app.options_wind import normalize_chain, OPTION_ROOTS


def matrix(packet):
    return [{f.lower():packet['data'][j][i] for j,f in enumerate(packet['fields'])} for i in range(len(packet['data'][0]))]


def main():
    chain_file=ROOT/'reports'/'options-capability-probe.json'
    quote_file=ROOT/'reports'/'options-quotes-probe.json'
    chains={x['label']:x for x in json.loads(chain_file.read_text(encoding='utf-8'))}
    packets={x['label']:x for x in json.loads(quote_file.read_text(encoding='utf-8'))}
    config=json.loads((ROOT/'options-config.json').read_text(encoding='utf-8'))
    db=sqlite3.connect((ROOT/'data'/'desk.sqlite3').as_uri()+'?mode=ro',uri=True)
    calendars={key.removeprefix('calendar_'):set(json.loads(value)) for key,value in db.execute("SELECT key,payload FROM settings WHERE key LIKE 'calendar_%'")}
    db.close()
    results={}
    for root in OPTION_ROOTS:
        underlying_packet=packets[root+' chain underlying']; packet=packets[root+' chain quotes']
        underlying=underlying_packet['codes'][0]
        all_contracts=normalize_chain(matrix(chains[root+' chain']),root,underlying)
        quotes=dict(zip(packet['codes'],matrix(packet)))
        contracts=[x for x in all_contracts if x['code'] in quotes]
        daily=packets[root+' chain daily']
        start=perf_counter()
        context,rows=analyse(root,underlying,contracts,quotes,matrix(underlying_packet)[0],{},
                             [dict(day=str(day)[:10],close=close) for day,close in zip(daily['times'],daily['data'][0])],
                             calendars[market(root)['calendar']],packet['started'],'交易中',config)
        results[root]=dict(discovered=len(all_contracts),quoted=len(contracts),qualified=context['qualified'],excluded=context['excluded'],
                           expiries=context['expiries'],calculation_ms=round((perf_counter()-start)*1000,2),asof=packet['started'],candidate_ready=False)
    report=dict(generated_at=iso(now()),scope=__doc__,results=results,
                input_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (chain_file,quote_file)})
    output=ROOT/'reports'/f'options-probe-replay_{now():%Y%m%d_%H%M%S%f}.json'
    with output.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(report=str(output),results=results),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
