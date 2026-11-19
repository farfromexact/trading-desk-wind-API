import asyncio
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from app.domain import dt
from app.etf_context import ETF_FLOW_COMPONENTS
from app.etf_flow_history import EtfFlowHistory
from app.market_context import MarketContext, etf_session
from app.wind_context_client import WindPyContextClient


CODE = '159915.SZ'
DAY = '2026-11-19'
DAYS = sorted((date.fromisoformat(DAY)-timedelta(days=i)).isoformat()
              for i in range(1, 30) if (date.fromisoformat(DAY)-timedelta(days=i)).weekday() < 5)[-20:]


def point(day, clock, value):
    return dict(code=CODE, time=f'{day}T{clock}+08:00', value=value, unit='元',
                frequency='intraday_cumulative', source='WindPy WSQ')


def test_same_clock_uses_prior_calendar_days_arithmetic_mean_and_signed_excess():
    history = EtfFlowHistory()
    current = point(DAY, '10:05:05', -50)
    at = dt(current['time'])
    for day in DAYS:
        history.record(point(day, '10:00:05', -200))
        history.record(point(day, '10:05:05', -100))
        history.record(point(day, '10:05:06', 99999))  # later than the comparison time
    history.record(point(DAY, '10:00:05', 0))
    history.record(current)
    closes = dict.fromkeys(DAYS, 1000)
    result = history.compare(CODE, current, at, DAYS, closes)
    assert (result['days'], result['mean'], result['excess']) == (20, -100, 50)
    assert (result['speed'], result['speed_mean'], result['speed_days']) == (-10, 20, 20)
    assert result['full_day_mean'] == 1000
    history.record(point(DAYS[-1], '10:05:05', 0))
    assert history.compare(CODE, current, at, DAYS, closes)['mean'] == -95
    partial = history.compare(CODE, current, at, ['2000-01-03', *DAYS[1:]], closes)
    assert partial['days'] == 19 and partial['sample_mean'] is not None
    assert partial['mean'] is partial['excess'] is partial['speed_mean'] is partial['full_day_mean'] is None


def test_no_lunch_or_overnight_rate_and_no_use_of_sparse_or_wrong_source():
    history = EtfFlowHistory()
    history.record(point(DAY, '11:30:38', 1000))
    current = point(DAY, '13:02:00', -110)
    history.record(current)
    assert history.compare(CODE, current, dt(current['time']), DAYS, {})['speed'] is None
    late = point(DAY, '13:15:05', -310)
    history.record(point(DAY, '13:08:45', 400))  # 80s before the baseline target, too old
    history.record(point(DAY, '13:10:05', 400) | {'source': 'WindPy WSD'})
    assert history.compare(CODE, late, dt(late['time']), DAYS, {})['speed'] is None
    assert history.compare(CODE, current, dt('2026-11-20T13:02:00'), DAYS, {})['speed'] is None
    assert etf_session(dt('2026-11-19T12:00:00'), {DAY}) == '午休'


def test_rolling_mean_uses_vendor_total_independently_of_observed_days_and_daily_closes():
    history = EtfFlowHistory()
    current = point(DAY, '13:34:18', -50)
    at = dt(current['time'])
    daily = dict.fromkeys(DAYS, 900)
    result = history.compare(CODE, current | {'rolling_20d_total': -2000}, at, DAYS, daily)
    assert (result['rolling_20d_mean'], result['rolling_20d_excess']) == (-100, 50)
    assert result['days'] == 0 and result['sample_mean'] is None and result['full_day_mean'] == 900
    history.record(point(DAYS[-1], '13:34:11', 150))
    zero = history.compare(CODE, current | {'rolling_20d_total': 0}, at, DAYS, daily)
    assert (zero['rolling_20d_mean'], zero['rolling_20d_excess'], zero['sample_mean']) == (0, -50, 150)
    for change in ({}, {'rolling_20d_total': float('nan')},
                   {'time': DAYS[-1]+'T13:34:18+08:00', 'rolling_20d_total': 100}):
        result = history.compare(CODE, current | change, at, DAYS, daily)
        assert result['rolling_20d_mean'] is result['rolling_20d_excess'] is None


def test_restore_reuses_archived_wsq_receipts_including_closing_minutes(tmp_path, monkeypatch):
    at = dt(DAY+'T12:05:00')
    monkeypatch.setattr('app.market_context.now', lambda: at)
    archive = tmp_path/'market-context-windpy-observed-20261119.jsonl'
    def receipt(clock, value):
        raw = dict(code=CODE, source='WindPy WSQ', rt_date=20261119, rt_time=clock,
                   **dict.fromkeys(ETF_FLOW_COMPONENTS, 0))
        raw['rt_vip_buy_amt'] = value
        return dict(tool='get_fund_price_indicators', observed_at=at.isoformat(), response=dict(
            provider='WindPy', rows=[raw], units=dict.fromkeys(ETF_FLOW_COMPONENTS, '元')))
    archive.write_text('\n'.join(json.dumps(receipt(t, v)) for t, v in [(112500, 100), (113038, 200)])+'\n{',
                       encoding='utf-8')
    service = MarketContext(SimpleNamespace(offline=False, calendar={DAY}), tmp_path)
    item = next(x for x in service.snapshot()['etfs'] if x['code'] == CODE)
    assert item['flow']['value'] == 200 and item['flow']['time'][11:19] == '11:30:38'
    assert item['comparison']['speed'] == 20


def test_wsd_full_day_table_requires_exact_codes_dates_field_and_matrix():
    codes = [CODE, '588000.SH']
    raw = dict(Codes=codes[::-1], Times=DAYS, Fields=['MFD_INFLOW_M'], Data=[[-3]*20, [5]*20])
    async def query(method, *args):
        assert method == 'wsd' and args == (','.join(codes), 'mfd_inflow_m', DAYS[0], DAYS[-1], 'unit=1;Fill=Blank')
        return raw
    client = WindPyContextClient(SimpleNamespace(context_query=query))
    values = asyncio.run(client.etf_daily_flows(codes, DAYS))
    assert values[CODE][DAYS[0]] == 5 and values[codes[1]][DAYS[-1]] == -3
    for change in [dict(Codes=codes[:1]), dict(Times=DAYS[1:]), dict(Fields=['AMOUNT']), dict(Data=[[1]*20])]:
        original = dict(raw)
        raw.update(change)
        with pytest.raises(ValueError):
            asyncio.run(client.etf_daily_flows(codes, DAYS))
        raw.update(original)
