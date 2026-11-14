import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.domain import dt, iso
from app.market_context import ContextBook, MarketContext, mcp_rows


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
    add_window(b,'000016.SH','2026-11-13T14:00:00+08:00',finish=100.5)
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
    assert service.error=='权限不足'
    assert service.book.updated_at=='2026-11-13T15:00:00+08:00'
    assert service.calls==1
