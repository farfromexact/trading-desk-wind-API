import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.domain import dt, iso
from app.market_context import ContextBook, MarketContext, mcp_rows, completed_cash_bar, cash_bar_labels, INDICES


def add_window(book, code, end, amount=100, start=100, finish=101):
    end=dt(end)
    for i in (3,2,1,0):
        stamp=iso(end-timedelta(minutes=i*5))
        book.bars.setdefault(code,{})[stamp]={'time':stamp,'close':start if i else finish,'amount':amount}


def test_breadth_rejects_wrong_universe_and_preserves_missing():
    b=ContextBook()
    b.add_snapshots([{'Wind代码':'000852.SH','交易时间':'2026-11-13T10:00:00+08:00',
                      '上涨家数':'126','下跌家数':'859','平盘家数':'15'}],{})
    assert b.view()['breadth']['value']==pytest.approx(126/985*100)
    b.add_snapshots([{'Wind代码':'000852.SH','交易时间':'2026-11-13T10:01:00+08:00',
                      '上涨家数':'126','下跌家数':'1200','平盘家数':'15'}],{})
    assert b.view()['breadth']['value'] is None
    assert b.view()['breadth']['count_total']==1341


def test_window_rejects_lunch_gap_missing_bar_and_unknown_amount():
    b=ContextBook()
    add_window(b,'000852.SH','2026-11-13T13:10:00+08:00')
    assert b.window('000852.SH','2026-11-13T13:10:00+08:00') is None
    add_window(b,'000852.SH','2026-11-13T14:00:00+08:00')
    assert b.window('000852.SH','2026-11-13T14:00:00+08:00')['amount']==300
    del b.bars['000852.SH']['2026-11-13T13:55:00+08:00']
    assert b.window('000852.SH','2026-11-13T14:00:00+08:00') is None


def test_same_clock_baseline_is_prior_only_and_requires_ten_days():
    b=ContextBook()
    end=dt('2026-11-13T14:00:00+08:00')
    for i in range(1,10):
        add_window(b,'000852.SH',end-timedelta(days=i),amount=100)
    add_window(b,'000852.SH',end,amount=200)
    assert b.view(end+timedelta(minutes=2))['activity']['IM']['value'] is None
    add_window(b,'000852.SH',end-timedelta(days=10),amount=100)
    result=b.view(end+timedelta(minutes=2))['activity']['IM']
    assert result['value']==2 and result['baseline_days']==10
    add_window(b,'000852.SH',end+timedelta(days=1),amount=99999)
    assert b.view(end+timedelta(minutes=2))['activity']['IM']['value']==2


def test_minute_ingestion_rejects_forming_bars_and_does_not_guess_units():
    b=ContextBook()
    rows=[{'TIME':'2026-11-13T14:00:00+08:00','MATCH':'100','TURNOVER':'123'}]
    b.add_bars('000852.SH',rows,{},'2026-11-13T14:01:00+08:00')
    assert not b.bars['000852.SH']
    b.add_bars('000852.SH',rows,{},'2026-11-13T14:01:05+08:00')
    assert next(iter(b.bars['000852.SH'].values()))['amount'] is None
    b.add_bars('000852.SH',rows,{'TURNOVER 单位：':'元'},'2026-11-13T14:01:05+08:00')
    assert next(iter(b.bars['000852.SH'].values()))['amount']==123


def test_style_uses_common_end_and_breadth_never_crosses_lunch():
    b=ContextBook()
    add_window(b,'000852.SH','2026-11-13T14:00:00+08:00',finish=102)
    add_window(b,'000300.SH','2026-11-13T14:00:00+08:00',finish=100.5)
    add_window(b,'000852.SH','2026-11-13T14:05:00+08:00',finish=110)
    assert b.view('2026-11-13T14:10:00+08:00')['style']['time']=='2026-11-13T14:00:00+08:00'
    for stamp,up in [('2026-11-13T11:30:00+08:00',300),('2026-11-13T13:05:00+08:00',400)]:
        b.add_snapshots([{'Wind代码':'000852.SH','交易时间':stamp,'上涨家数':up,'下跌家数':1000-up,'平盘家数':0}],{})
    assert b.view()['breadth']['change_pp'] is None


def test_mcp_errors_and_schema_changes_fail_closed():
    with pytest.raises(ValueError):mcp_rows({'isError':True})
    with pytest.raises(ValueError):mcp_rows({'content':[{'type':'text','text':'没有数据'}]})


def test_future_breadth_is_not_visible_before_source_timestamp():
    b=ContextBook()
    b.add_snapshots([{'Wind代码':'000852.SH','交易时间':'2026-11-13T10:05:00+08:00',
                      '上涨家数':500,'下跌家数':500,'平盘家数':0}],{})
    assert b.view('2026-11-13T10:00:00+08:00')['breadth']['value'] is None


def test_offline_never_queries_and_refresh_has_single_flight_cooldown(tmp_path):
    desk=SimpleNamespace(offline=True,watch={},calendar=set())
    service=MarketContext(desk,tmp_path)
    with pytest.raises(ValueError):service.request_refresh()
    assert service.calls==0
    async def check():
        service.disabled=False
        gate=asyncio.Event()
        async def blocked():await gate.wait()
        service.refresh=blocked
        assert service.request_refresh()
        assert not service.request_refresh()
        gate.set();await service.task
        assert not service.request_refresh()
        await service.close()
    asyncio.run(check())


def test_query_failure_keeps_old_source_time(tmp_path):
    class FailedClient:
        async def call(self,*args):raise ValueError('权限不足')
    desk=SimpleNamespace(offline=False,watch={},calendar=set())
    service=MarketContext(desk,tmp_path,client=FailedClient())
    service.book.updated_at='2026-11-13T15:00:00+08:00'
    asyncio.run(service.refresh())
    assert service.error=='指数广度：权限不足'
    assert service.book.updated_at=='2026-11-13T15:00:00+08:00'
    assert service.calls==3  # Independent ETF sections still run when breadth fails.
    assert set(service.etf_errors)=={'flow','daily'}


def test_schedule_crosses_confirmation_boundary_without_waiting_five_minutes():
    day='2026-11-16'
    assert completed_cash_bar(day,day+'T09:36:04+08:00') is None
    assert completed_cash_bar(day,day+'T09:51:04+08:00').endswith('09:45:00+08:00')
    assert completed_cash_bar(day,day+'T09:51:05+08:00').endswith('09:50:00+08:00')
    assert completed_cash_bar(day,day+'T12:30:00+08:00').endswith('11:30:00+08:00')


def test_refresh_retries_delayed_complete_bar_and_does_not_repeat_healthy_indices(tmp_path,monkeypatch):
    at=dt('2026-11-16T09:50:58+08:00')
    monkeypatch.setattr('app.market_context.now',lambda:at)
    calls=[]
    class Client:
        async def call(self,tool,params):
            calls.append((tool,params))
            if tool=='get_index_price_indicators':
                rows=[{'Wind代码':code,'最新交易日':'20261116','交易时间':iso(at),
                       '上涨家数':1,'下跌家数':999,'平盘家数':0} for code in INDICES.values()]
            else:
                code=params['windcode']
                labels=['09:35','09:40','09:45','09:50']
                # One provider remains delayed for the first post-confirmation attempt.
                if at.minute==51 and code==INDICES['IF']:labels=labels[:-1]
                rows=[dict(TIME=f'2026-11-16T{t}:00+08:00',MATCH='100',TURNOVER='10') for t in labels]
            return {'content':[{'type':'text','text':__import__('json').dumps({'data':rows})}]}
    service=MarketContext(SimpleNamespace(offline=False,watch={},calendar=set()),tmp_path,Client())
    async def no_extra(*args):pass
    service.refresh_etfs=no_extra
    service.refresh_history=no_extra
    asyncio.run(service.refresh())
    assert service.book.view(at)['style']['time'].endswith('09:45:00+08:00')
    at=dt('2026-11-16T09:51:06+08:00')
    asyncio.run(service.refresh())
    assert service.book.view(at)['style']['time'].endswith('09:45:00+08:00')
    at=dt('2026-11-16T09:52:06+08:00')
    asyncio.run(service.refresh())
    assert service.book.view(at)['style']['value']==0
    assert service.book.view(at)['style']['time'].endswith('09:50:00+08:00')
    snapshots=[p['windcode'] for t,p in calls if t=='get_index_price_indicators']
    assert all(set(c.split(','))=={'000016.SH','000300.SH','000852.SH','000688.SH','399006.SZ'} for c in snapshots)
    minute_codes=[p['windcode'] for t,p in calls if t=='get_index_kline']
    assert set(minute_codes)==set(INDICES.values())  # No new board WSI or membership lookups.
    assert {t for t,p in calls}=={'get_index_price_indicators','get_index_kline'}
    assert minute_codes.count(INDICES['IM'])==2
    assert minute_codes.count(INDICES['IF'])==3


def test_history_backfill_is_bounded_prior_only_one_index_per_refresh_and_reuses_cache(tmp_path,monkeypatch):
    at=dt('2026-11-16T10:00:00+08:00')
    monkeypatch.setattr('app.market_context.now',lambda:at)
    days=[(at-timedelta(days=i)).date().isoformat() for i in range(1,21)]
    desk=SimpleNamespace(offline=False,watch={},calendar=set(days+[at.date().isoformat(),'2026-11-17']))
    service=MarketContext(desk,tmp_path)
    calls=[]
    async def query(tool,params):
        calls.append(params)
        assert params['begin_date']==min(days) and params['end_date']==max(days)
        return [dict(TIME=t,MATCH='100',TURNOVER='100') for day in days for t in cash_bar_labels(day)],{'TURNOVER':'元'}
    async def check():
        for expected in (1,2,3,3):
            await service.refresh_history(query,at.date().isoformat())
            assert len(calls)==expected
    asyncio.run(check())
    assert not service.history_errors
    for code in INDICES.values():
        add_window(service.book,code,'2026-11-16T09:50:00+08:00',amount=200)
    assert service.book.view(at)['activity']['IM']['value']==2
    # A new service can reuse persisted history without new requests.
    import json
    service.path.write_text(json.dumps(service.book.json()),encoding='utf-8')
    restored=MarketContext(desk,tmp_path)
    asyncio.run(restored.refresh_history(query,at.date().isoformat()))
    assert len(calls)==3
    at=at+timedelta(days=1)
    async def next_day_query(tool,params):
        calls.append(params)
        assert params['begin_date']==params['end_date']=='2026-11-16'
        return [dict(TIME=t,MATCH='100',TURNOVER='100') for t in cash_bar_labels('2026-11-16')],{'TURNOVER':'元'}
    asyncio.run(restored.refresh_history(next_day_query,'2026-11-17'))
    assert len(calls)==4


def test_history_failure_preserves_live_data_and_backs_off(tmp_path,monkeypatch):
    at=dt('2026-11-16T10:00:00+08:00')
    monkeypatch.setattr('app.market_context.now',lambda:at)
    days={(at-timedelta(days=i)).date().isoformat() for i in range(1,21)}
    service=MarketContext(SimpleNamespace(offline=False,watch={},calendar=days),tmp_path)
    add_window(service.book,INDICES['IM'],'2026-11-16T09:50:00+08:00')
    calls=[]
    async def query(tool,params):
        calls.append(params['windcode'])
        # Out-of-range data and unknown units must not create a baseline.
        return [dict(TIME='2026-11-16T09:50:00+08:00',MATCH='999',TURNOVER='99')],{}
    async def check():
        for _ in range(4):await service.refresh_history(query,'2026-11-16')
    asyncio.run(check())
    assert calls==list(INDICES.values())
    assert set(service.history_errors)==set(INDICES)
    assert service.error is None and service.history_loading is None
    assert service.book.window(INDICES['IM'],'2026-11-16T09:50:00+08:00')['amount']==300


def snapshot(book, code, stamp, up, down, same=0):
    book.add_snapshots([{'Wind代码':code,'交易时间':stamp,'上涨家数':up,
                         '下跌家数':down,'平盘家数':same}],{})


def test_five_universes_are_independent_and_change_uses_source_five_minutes():
    from app.market_context import COUNTS
    b=ContextBook()
    for code,total in COUNTS.items():
        total=total or {'000688.SH':619,'399006.SZ':1370}[code]
        snapshot(b,code,'2026-11-16T09:59:30+08:00',10,total-10)
        snapshot(b,code,'2026-11-16T10:00:00+08:00',20,total-20)
        snapshot(b,code,'2026-11-16T10:05:00+08:00',30,total-30)
    view=b.view('2026-11-16T10:06:00+08:00')
    assert len(view['breadths'])==5
    for row in view['breadths']:
        assert row['change_pp']==pytest.approx(10/(row['expected'] or row['count_total'])*100)
        assert row['previous_time']=='2026-11-16T10:00:00+08:00'
    snapshot(b,'000688.SH','2026-11-16T10:06:00+08:00',30,-1)
    rows={r['code']:r for r in b.view('2026-11-16T10:07:00+08:00')['breadths']}
    assert rows['000688.SH']['value'] is None
    assert rows['399006.SZ']['value']==pytest.approx(30/1370*100)


@pytest.mark.parametrize('previous,current',[
    ('2026-11-16T09:58:29+08:00','2026-11-16T10:05:00+08:00'),
    ('2026-11-16T10:00:01+08:00','2026-11-16T10:05:00+08:00'),
    ('2026-11-16T11:30:00+08:00','2026-11-16T13:05:00+08:00'),
    ('2026-11-13T10:00:00+08:00','2026-11-16T10:05:00+08:00'),
])
def test_five_minute_breadth_does_not_substitute_wrong_time(previous,current):
    b=ContextBook()
    snapshot(b,'000852.SH',previous,200,800)
    snapshot(b,'000852.SH',current,300,700)
    assert b.view(current)['breadth']['change_pp'] is None


def test_breadth_gap_requires_synchronized_current_and_previous_pairs():
    b=ContextBook()
    for time,small,large in [('10:00:00',500,150),('10:05:00',600,120)]:
        snapshot(b,'000852.SH','2026-11-16T'+time+'+08:00',small,1000-small)
        snapshot(b,'000300.SH','2026-11-16T'+time+'+08:00',large,300-large)
    gap=b.view('2026-11-16T10:06:00+08:00')['rotation']['breadth_gap']
    assert gap['value']==20 and gap['change_pp']==20
    snapshot(b,'000300.SH','2026-11-16T10:05:11+08:00',120,180)
    assert b.view('2026-11-16T10:06:00+08:00')['rotation']['breadth_gap']['value'] is None


def test_strength_five_minutes_and_turnover_paired_ratio_baseline():
    b=ContextBook();end=dt('2026-11-16T14:00:00+08:00')
    for i in range(1,11):
        for code,amount in [('000852.SH',100),('000300.SH',200)]:
            add_window(b,code,end-timedelta(days=i),amount=amount)
    add_window(b,'000852.SH',end,amount=300,finish=102)
    add_window(b,'000300.SH',end,amount=200,finish=101)
    rotation=b.view(end+timedelta(seconds=65))['rotation']
    assert rotation['strength']['value']==pytest.approx(1)
    assert rotation['turnover']['value']==3
    assert rotation['turnover']['baseline_days']==10
    # Incomplete paired coverage cannot borrow the other index's unmatched day.
    del b.bars['000300.SH'][iso(end-timedelta(days=10))]
    assert b.view(end+timedelta(seconds=65))['rotation']['turnover']['value'] is None
    # Neither a future day nor today's amount is a baseline sample.
    add_window(b,'000300.SH',end+timedelta(days=1))
    add_window(b,'000852.SH',end+timedelta(days=1))
    assert b.view(end+timedelta(seconds=65))['rotation']['turnover']['baseline_days']==9


def test_latest_complete_strength_rejects_gaps_and_five_minute_lunch_crossing():
    b=ContextBook()
    for code in ('000852.SH','000300.SH'):
        add_window(b,code,'2026-11-16T14:00:00+08:00')
    assert b.view('2026-11-16T14:01:04+08:00')['style']['time'].endswith('13:55:00+08:00')
    del b.bars['000300.SH']['2026-11-16T13:55:00+08:00']
    assert b.view('2026-11-16T14:01:05+08:00')['style']['value'] is None
    assert b.window('000852.SH','2026-11-16T13:00:00+08:00',5) is None


def test_repeated_close_snapshot_does_not_create_history():
    b=ContextBook()
    for _ in range(3):snapshot(b,'000852.SH','2026-11-16T15:00:06+08:00',295,686,19)
    row=b.view('2026-11-16T21:00:00+08:00')['breadth']
    assert len(row['trend'])==1 and row['change_pp'] is None



def test_board_vendor_scope_is_explicit_and_never_claims_index_coverage():
    b=ContextBook();stamp='2026-11-16T15:00:03+08:00'
    snapshot(b,'000688.SH',stamp,39,573,7)
    row=b.breadth_view('000688.SH',dt(stamp))
    assert row['value']==pytest.approx(39/612*100)
    assert row['expected'] is None and row['coverage']=='vendor_reported'
    assert b.snapshots['000688.SH'][stamp]['scope_ok'] is None
    snapshot(b,'000852.SH',stamp,39,573,7)
    assert b.breadth_view('000852.SH',dt(stamp))['value'] is None
    snapshot(b,'000688.SH',stamp,39,-1,7)
    assert b.breadth_view('000688.SH',dt(stamp))['value'] is None


def test_faster_breadth_does_not_double_etf_flow_requests(tmp_path, monkeypatch):
    import app.market_context as context
    tick=[100.]
    monkeypatch.setattr(context, 'time', SimpleNamespace(monotonic=lambda:tick[0]))
    calls=[]
    class Client:
        async def call(self,tool,params):
            calls.append(tool)
            raise ValueError('暂缺')
    service=MarketContext(SimpleNamespace(offline=False,watch={},calendar=set()),tmp_path,client=Client())
    async def run():
        assert service.snapshot()['interval_seconds']==30
        assert service.request_refresh()
        await service.task
        tick[0]+=29
        assert not service.request_refresh()
        tick[0]+=1
        assert service.request_refresh()
        assert not service.request_refresh()  # still single flight
        await service.task
        assert calls.count('get_index_price_indicators')==2
        assert calls.count('get_fund_price_indicators')==1
        tick[0]+=30
        assert service.request_refresh()
        await service.task
        assert calls.count('get_index_price_indicators')==3
        assert calls.count('get_fund_price_indicators')==2
        assert calls.count('fund_get_listed_historical_price')==1
        await service.close()
    asyncio.run(run())
