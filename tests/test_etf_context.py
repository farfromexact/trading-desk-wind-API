import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.domain import dt, iso
from app.etf_context import DAILY_FIELD, ETF_SAMPLES, daily_etf_rows
from app.market_context import ContextBook, MarketContext


def daily_envelope(day='2026-11-13', value='-795465360', header='申赎净流入额（元）'):
    return {'content':[{'type':'text','text':f'已返回1只基金 {day}的行情数据。\n'
        f'| 序号 | 基金代码 | 基金简称 | {header} |\n|---|---|---|---|\n'
        f'| 1 | 510300.SH | 沪深300ETF华泰柏瑞 | {value} |'}]}


def flow_row(value='0', stamp='2026-11-13T15:30:17+08:00'):
    return {'Wind代码':'510300.SH','交易时间':stamp,'最新交易日':'20261113','当日主力净流入额':value}


def test_daily_requires_actual_date_and_exact_subscription_field_in_yuan():
    rows,units=daily_etf_rows(daily_envelope(),'2026-11-15')
    assert rows[0]['data_date']=='2026-11-13'  # Non-trading-day query may roll back.
    assert rows[0]['field']==DAILY_FIELD and units=={DAILY_FIELD:'元'}
    with pytest.raises(ValueError):daily_etf_rows(daily_envelope(day='2026-11-16'),'2026-11-15')
    with pytest.raises(ValueError):daily_etf_rows(daily_envelope(header='净流入额（元）'),'2026-11-15')
    with pytest.raises(ValueError):daily_etf_rows(daily_envelope(header='申赎净流入额（万元）'),'2026-11-15')
    with pytest.raises(ValueError):daily_etf_rows({'content':[{'type':'text','text':'申赎净流入额为100'}]},'2026-11-15')


def test_daily_missing_is_not_zero_and_published_time_is_not_invented():
    book=ContextBook()
    rows,units=daily_etf_rows(daily_envelope(value='--'),'2026-11-13')
    book.add_etf_daily(rows,units,'2026-11-14T14:00:00+08:00')
    record=book.etf_daily['510300.SH']
    assert record['value'] is None and record['published_at'] is None
    assert record['data_date']=='2026-11-13' and record['observed_at'].startswith('2026-11-14')
    rows,units=daily_etf_rows(daily_envelope(value='0'),'2026-11-13')
    book.add_etf_daily(rows,units,'2026-11-14T14:01:00+08:00')
    assert book.etf_daily['510300.SH']['value']==0


def test_intraday_and_daily_values_remain_separate_and_units_fail_closed():
    book=ContextBook()
    book.add_etf_flow([flow_row('-100')],{'当日主力净流入额':'元'},'2026-11-14T14:00:00+08:00')
    rows,units=daily_etf_rows(daily_envelope(value='200'),'2026-11-13')
    book.add_etf_daily(rows,units,'2026-11-14T14:00:00+08:00')
    result=next(x for x in book.view('2026-11-14T14:01:00+08:00')['etfs'] if x['root']=='IF')
    assert result['flow']['value']==-100 and result['daily']['value']==200
    assert 'total' not in result and result['flow']['frequency']!='daily'
    book.add_etf_flow([flow_row('99')],{},'2026-11-14T14:02:00+08:00')
    assert book.etf_flow['510300.SH']['value'] is None
    assert book.etf_daily['510300.SH']['value']==200


def test_future_data_and_same_day_daily_values_cannot_leak_into_past_views():
    book=ContextBook()
    book.add_etf_flow([flow_row()],{'当日主力净流入额':'元'},'2026-11-14T14:00:00+08:00')
    rows,units=daily_etf_rows(daily_envelope(),'2026-11-13')
    with pytest.raises(ValueError):book.add_etf_daily(rows,units,'2026-11-13T16:00:00+08:00')
    book.add_etf_daily(rows,units,'2026-11-14T14:00:00+08:00')
    result=next(x for x in book.view('2026-11-13T15:31:00+08:00')['etfs'] if x['root']=='IF')
    assert result['flow'] is None and result['daily'] is None


def test_out_of_order_fund_snapshots_do_not_replace_newer_observations():
    book=ContextBook()
    book.add_etf_flow([flow_row('1')],{'当日主力净流入额':'元'},'2026-11-14T14:00:00+08:00')
    book.add_etf_flow([flow_row('2','2026-11-13T15:29:17+08:00')],{'当日主力净流入额':'元'},'2026-11-14T14:01:00+08:00')
    assert book.etf_flow['510300.SH']['value']==1


def test_daily_success_is_cached_once_per_day_and_flow_failure_is_isolated(tmp_path,monkeypatch):
    at=dt('2026-11-14T14:00:00+08:00')
    monkeypatch.setattr('app.market_context.now',lambda:at)
    service=MarketContext(SimpleNamespace(offline=False,watch={},calendar=set()),tmp_path)
    calls=[]
    async def query(tool,params,parser=None):
        calls.append(tool)
        if tool=='get_fund_price_indicators':raise ValueError('ETF流向无权限')
        assert params['tradeDate']=='2026-11-13' and params['includeFields']==[DAILY_FIELD]
        return [dict(code=s['code'],data_date='2026-11-13',value='0') for s in ETF_SAMPLES.values()],{DAILY_FIELD:'元'}
    async def check():
        await service.refresh_etfs(query)
        await service.refresh_etfs(query)
    asyncio.run(check())
    assert calls.count('fund_get_listed_historical_price')==1
    assert calls.count('get_fund_price_indicators')==2
    assert 'flow' in service.etf_errors and 'daily' not in service.etf_errors
    assert all(v['value']==0 for v in service.book.etf_daily.values())
    assert service.error is None


def test_delayed_daily_publication_keeps_old_date_and_retries_after_backoff(tmp_path,monkeypatch):
    monkeypatch.setattr('app.market_context.now',lambda:dt('2026-11-17T10:00:00+08:00'))
    service=MarketContext(SimpleNamespace(offline=False,watch={},calendar={'2026-11-13','2026-11-16','2026-11-17'}),tmp_path)
    daily_calls=[]
    async def query(tool,params,parser=None):
        if tool=='get_fund_price_indicators':return [],{}
        daily_calls.append(tool)
        return [dict(code=s['code'],data_date='2026-11-13',value='1') for s in ETF_SAMPLES.values()],{DAILY_FIELD:'元'}
    async def check():
        await service.refresh_etfs(query)
        assert 'daily' in service.etf_errors
        await service.refresh_etfs(query)
        assert len(daily_calls)==1
        service.last_daily_attempt-=1801
        await service.refresh_etfs(query)
    asyncio.run(check())
    assert len(daily_calls)==2
    assert all(x['data_date']=='2026-11-13' for x in service.book.etf_daily.values())
    assert 'daily' in service.etf_errors
