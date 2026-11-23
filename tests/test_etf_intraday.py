import asyncio
import json
from types import SimpleNamespace

import pytest

from app.domain import dt
from app.etf_context import (ETF_FLOW_COMPONENTS, ETF_FLOW_CORE_FIELDS, ETF_FLOW_WSQ_FIELDS,
                             ETF_ROLLING_FIELD, ETF_SAMPLES, flow_records)
from app.market_context import ContextBook, MarketContext, number
from app.wind_context_client import WindPyContextClient


CODES = [s['code'] for s in ETF_SAMPLES.values()]
UNITS = dict.fromkeys(ETF_FLOW_COMPONENTS, '元')
AT = '2026-11-17T10:10:00+08:00'


def row(**overrides):
    return dict(code=CODES[0], source='WindPy WSQ', rt_date=20261117, rt_time=100958,
                rt_mf_amt_20d=-400, **dict(zip(ETF_FLOW_COMPONENTS, [100, 70, 50, 90]))) | overrides


def test_one_wsq_batch_has_no_daily_fallback_and_maps_reordered_fields():
    fields = list(reversed(ETF_FLOW_WSQ_FIELDS.split(',')))
    codes = list(reversed(CODES))
    raw = dict(Codes=codes, Fields=[f.upper() for f in fields], Times=[],
               Data=[[row()[f]]*len(codes) for f in fields])
    calls = []
    async def query(method, *args):
        calls.append((method, args))
        return raw
    envelope = asyncio.run(WindPyContextClient(SimpleNamespace(context_query=query)).call(
        'get_fund_price_indicators', dict(windcode=','.join(CODES))))
    assert calls == [('wsq', (','.join(CODES), ETF_FLOW_WSQ_FIELDS, 'unit=1'))]
    records = flow_records(envelope['rows'], envelope['units'], AT, number)
    assert set(records) == set(CODES)
    assert all(r['value'] == -10 for r in records.values())
    assert all(r['rolling_20d_total'] == -400 for r in records.values())
    assert records[CODES[0]]['time'] == '2026-11-17T10:09:58+08:00'
    assert records[CODES[0]]['frequency'] == 'intraday_cumulative'
    assert records[CODES[0]]['source'] == 'WindPy WSQ'
    async def denied(*args):raise ValueError('field denied')
    with pytest.raises(ValueError, match='field denied'):
        asyncio.run(WindPyContextClient(SimpleNamespace(context_query=denied)).call(
            'get_fund_price_indicators', dict(windcode=','.join(CODES))))


@pytest.mark.parametrize('update', [dict(rt_vip_buy_amt=None), dict(rt_vip_buy_amt=float('nan')),
    dict(rt_vip_sell_amt=-1), dict(rt_date=None), dict(rt_time=None), dict(rt_time=101001),
    dict(rt_date=20261116)])
def test_bad_component_or_clock_stays_missing_and_preserves_last_good(update):
    book = ContextBook()
    book.add_etf_flow([row()], UNITS, AT)
    previous = dict(book.etf_flow[CODES[0]])
    bad = book.add_etf_flow([row(**update)], UNITS, AT)[CODES[0]]
    assert bad['value'] is None and bad['reason']
    assert book.etf_flow[CODES[0]] == previous


def test_zero_and_units_and_out_of_order_time():
    zero = flow_records([row(**dict.fromkeys(ETF_FLOW_COMPONENTS, 0))], UNITS, AT, number)[CODES[0]]
    assert zero['value'] == 0 and not zero['reason']
    assert flow_records([row()], {}, AT, number)[CODES[0]]['value'] is None
    book = ContextBook()
    book.add_etf_flow([row()], UNITS, AT)
    book.add_etf_flow([row(rt_time=100900, rt_vip_buy_amt=999)], UNITS, AT)
    assert book.etf_flow[CODES[0]]['value'] == -10


@pytest.mark.parametrize('bad', [dict(Codes=[CODES[0], CODES[0], CODES[2]]),
    dict(Codes=CODES[:2]), dict(Data=[[0]]*6), dict(Fields=['rt_date']*6),
    dict(Fields=list(ETF_FLOW_CORE_FIELDS[:-1]) + ['unknown', ETF_ROLLING_FIELD])])
def test_incomplete_or_ambiguous_response_is_rejected(bad):
    fields = ETF_FLOW_WSQ_FIELDS.split(',')
    raw = dict(Codes=CODES, Fields=fields, Times=[], Data=[[0]*len(CODES) for f in fields])
    with pytest.raises(ValueError, match='不完整'):
        WindPyContextClient.flow_rows(raw | bad, CODES)


def test_missing_or_misaligned_optional_column_preserves_core_receipts():
    client = WindPyContextClient(SimpleNamespace())
    core = dict(Codes=CODES, Fields=list(ETF_FLOW_CORE_FIELDS), Times=[],
                Data=[[row()[f]] * len(CODES) for f in ETF_FLOW_CORE_FIELDS])
    short = core | dict(Fields=[ETF_ROLLING_FIELD, *core['Fields']], Data=[[-123], *core['Data']])
    for raw in (core, short):
        rows = client.flow_rows(raw, CODES)
        records = flow_records(rows, UNITS | {ETF_ROLLING_FIELD: '元'}, AT, number)
        assert all(r['value'] == -10 and r['rolling_20d_total'] is None for r in records.values())
    for units in (UNITS, UNITS | {ETF_ROLLING_FIELD: '万元'}):
        assert flow_records([row()], units, AT, number)[CODES[0]]['rolling_20d_total'] is None


def test_optional_indicator_refusal_does_not_interrupt_core_stream_or_repeat_every_poll(monkeypatch):
    calls = []
    async def query(method, codes, fields, options):
        calls.append(fields)
        if len(calls) == 1:
            raise ValueError('CWSQService: invalid indicators.')
        return dict(Codes=CODES, Fields=fields.split(','), Times=[],
                    Data=[[row()[f]] * len(CODES) for f in fields.split(',')])
    clock = [1000.0]
    # Patch this module's clock only; asyncio owns a separate event-loop clock.
    monkeypatch.setattr('app.wind_context_client.time', SimpleNamespace(monotonic=lambda: clock[0]))
    client = WindPyContextClient(SimpleNamespace(context_query=query))
    async def fetch():
        return await client.call('get_fund_price_indicators', {'windcode': ','.join(CODES)})
    first = asyncio.run(fetch())
    assert ETF_ROLLING_FIELD not in first['rows'][0]
    asyncio.run(fetch())
    clock[0] += 1801
    restored = asyncio.run(fetch())
    assert restored['rows'][0][ETF_ROLLING_FIELD] == -400
    assert calls == [ETF_FLOW_WSQ_FIELDS, ','.join(ETF_FLOW_CORE_FIELDS),
                     ','.join(ETF_FLOW_CORE_FIELDS), ETF_FLOW_WSQ_FIELDS]


def test_live_cache_migration_keeps_daily_subscriptions_but_drops_old_flow(tmp_path):
    valid = flow_records([row()], UNITS, AT, number)[CODES[0]]
    daily = dict(value=55, data_date='2026-11-16')
    data = dict(etf_flow={CODES[0]:valid, CODES[1]:dict(value=123, frequency='daily_snapshot'),
        CODES[2]:dict(value=456, frequency='intraday_cumulative', source='Wind MCP基金行情')},
        etf_daily={CODES[1]:daily})
    (tmp_path/'market-context-windpy.json').write_text(json.dumps(data), encoding='utf-8')
    service = MarketContext(SimpleNamespace(offline=False, watch={}, calendar=set()), tmp_path)
    assert service.book.etf_flow == {CODES[0]:valid}
    assert service.book.etf_daily == {CODES[1]:daily}
