from datetime import timedelta
import queue

import pytest

from app.domain import Bar, completed_bar, dt, expected_end, iso, load_config, quote_problem, quote_time, session
from app.engine import Engine
from app.live_preview import LivePreview, forming_label
from app.markets import day_close, labels, market, root_of, trading_day, trading_windows, valid_contract
from app.replay import build_replay
from app.service import Desk
from app.storage import Store

CAL={'2026-11-05','2026-11-06','2026-11-09','2026-11-10','2026-11-11','2026-11-12','2026-11-13','2026-11-16'}


@pytest.mark.parametrize('code,tick,decimals',[('IH2612.CFE',.2,1),('AG2702.SHF',1,0),('AU2612.SHF',.02,2),('SC2612.INE',.1,1),('T2612.CFE',.005,3),('TL2612.CFE',.01,2)])
def test_tick_sizes_flow_into_rule_prices(code,tick,decimals):
    e=Engine(code,load_config())
    assert e.c['tick_size']==tick
    assert market(code)['decimals']==decimals
    assert e.rounded(100+tick*.1,1)==pytest.approx(100+tick)
    assert e.rounded(100+tick*.9,-1)==pytest.approx(100)


@pytest.mark.parametrize('code,valid',[('T2612.CFE',True),('TL2612.CFE',True),('AU2612.SHF',True),('SC2612.INE',True),('SC2612.SHF',False),('T2613.CFE',False),('TS2612.CFE',False),('AU.SHF',False)])
def test_contract_exchange_and_root_guard(code,valid):
    assert valid_contract(code)==valid
    assert root_of('TL2612.CFE')=='TL'


@pytest.mark.parametrize('stamp,code,phase',[
    ('2026-11-10 09:10','AU2612.SHF','交易中'),('2026-11-10 09:10','IH2612.CFE','盘前'),
    ('2026-11-10 10:20','AG2702.SHF','小节休息'),('2026-11-10 10:20','IM2612.CFE','交易中'),
    ('2026-11-10 13:10','SC2612.INE','午休'),('2026-11-10 13:10','T2612.CFE','交易中'),
    ('2026-11-10 15:05','T2612.CFE','交易中'),('2026-11-10 15:15','TL2612.CFE','已收盘'),
    ('2026-11-10 21:10','AU2612.SHF','交易中'),('2026-11-10 21:10','IF2612.CFE','已收盘'),
    ('2026-11-11 00:10','AG2702.SHF','交易中'),('2026-11-11 02:30','SC2612.INE','盘前'),
    ('2026-11-07 01:10','AG2702.SHF','交易中'),('2026-11-08 01:10','AG2702.SHF','非交易日')])
def test_exchange_phases(stamp,code,phase):
    assert session(dt(stamp),CAL,code)==phase


def test_night_ownership_and_no_holiday_eve_session():
    for stamp in ('2026-11-06 21:00','2026-11-07 00:05','2026-11-07 02:30','2026-11-09 10:00'):
        assert trading_day(dt(stamp),'AU2612.SHF',CAL)=='2026-11-09'
    holiday={'2026-09-30','2026-10-08','2026-10-09'}
    assert session(dt('2026-09-30 21:30'),holiday,'AU2612.SHF')!='交易中'
    assert len(trading_windows('2026-10-08','AU2612.SHF',holiday))==3
    assert session(dt('2026-10-08 21:30'),holiday,'AU2612.SHF')=='交易中'


def test_native_labels_around_night_and_breaks():
    code='AG2702.SHF'
    assert len(labels('2026-11-10',code,5,CAL))==111
    assert len(labels('2026-11-10',code,15,CAL))==37
    assert len(labels('2026-11-10',code,1,CAL))==559
    assert forming_label(dt('2026-11-10 23:59'),5,CAL,code)=='2026-11-11T00:00:00+08:00'
    assert forming_label(dt('2026-11-11 00:01'),5,CAL,code)=='2026-11-11T00:05:00+08:00'
    assert forming_label(dt('2026-11-10 10:30'),15,CAL,code)=='2026-11-10T10:45:00+08:00'
    assert forming_label(dt('2026-11-10 10:20'),5,CAL,code) is None
    assert expected_end(dt('2026-11-10 10:30:10'),15,CAL,code=code)=='2026-11-10T10:15:00+08:00'
    assert expected_end(dt('2026-11-10 21:05:59'),5,CAL,code=code)=='2026-11-10T15:00:00+08:00'
    assert expected_end(dt('2026-11-10 21:06:05'),5,CAL,code=code)=='2026-11-10T21:05:00+08:00'


def test_end_caps_and_unfinished_native_filter():
    for label in ('2026-11-10 02:30','2026-11-10 10:15','2026-11-10 11:30','2026-11-10 15:00'):
        b=Bar('AU2612.SHF',15,iso(label),100,102,99,101)
        assert completed_bar(b,dt(label)+timedelta(seconds=5),calendar=CAL)
    for code,label in [('AU2612.SHF','2026-11-10 21:15'),('TL2612.CFE','2026-11-10 15:00')]:
        b=Bar(code,15,iso(label),100,102,99,101)
        assert not completed_bar(b,dt(label)+timedelta(seconds=30),calendar=CAL)
        assert completed_bar(b,dt(label)+timedelta(seconds=65),calendar=CAL)
    b=Bar('SC2612.INE',5,iso('2026-11-10 10:20'),100,102,99,101)
    assert not completed_bar(b,dt('2026-11-10 11:00'),calendar=CAL)


def test_treasury_last_trading_day_morning_only():
    cal={'2026-12-10','2026-12-11','2026-12-14'}
    for code in ('T2612.CFE','TL2612.CFE'):
        assert session(dt('2026-12-10 15:05'),cal,code)=='交易中'
        assert session(dt('2026-12-11 13:05'),cal,code)=='已收盘'
        assert day_close('2026-12-11',code,cal)==dt('2026-12-11 11:30')


@pytest.mark.parametrize('raw_date,at',[ (20261109,'2026-11-06 21:12:00'),(20261106,'2026-11-06 21:12:00'),(20261109,'2026-11-07 00:12:00'),(20261107,'2026-11-07 00:12:00')])
def test_wind_night_civil_or_trading_date_quotes(raw_date,at):
    clock=dt(at)
    q={'rt_date':raw_date,'rt_time':int(clock.strftime('%H%M%S')),'rt_last':100}
    assert quote_time(q,'AU2612.SHF',CAL,clock)==clock
    assert quote_problem(q,clock,15,'AU2612.SHF',CAL) is None
    assert quote_problem(q,clock+timedelta(seconds=20),15,'AU2612.SHF',CAL)


def test_display_preview_survives_midnight():
    p=LivePreview()
    p.on_quote('AU2612.SHF',888,dt('2026-11-10 23:59:30'),CAL)
    p.on_quote('AU2612.SHF',888.02,dt('2026-11-11 00:00:10'),CAL)
    rows=p.snapshot('AU2612.SHF',dt('2026-11-11 00:00:10'))['5']
    assert len(rows)==1 and rows[0]['high']==888.02
    assert rows[0]['end']=='2026-11-11T00:00:00+08:00'


def test_candidate_continues_across_midnight_and_treasury_after_1500():
    for code,before,after in [('AU2612.SHF','2026-11-10 23:55','2026-11-11 00:00'),('T2612.CFE','2026-11-10 14:55','2026-11-10 15:00')]:
        e=Engine(code,load_config(),CAL)
        b=Bar(code,5,iso(before),100,102,98,101)
        e.on_bar(b,historical=False)
        op=e.start_op('pullback',1,{'id':'test','name':'BOS','price':100},4,(90,110),b)
        op.update(status='confirmed',entry=105,stop=95,confirmed_index=0,confirmation_bar=b.end,confirmed_at=iso(dt(before)+timedelta(minutes=1)))
        e.on_bar(Bar(code,5,iso(after),101,102,100,101),historical=False)
        assert op['status']=='confirmed'
        assert e.day==trading_day(after,code,CAL)


def test_single_market_failure_and_switch_dont_reset_others(tmp_path,monkeypatch):
    monkeypatch.setattr('app.service.now',lambda:dt('2026-11-10 10:01:10'))
    d=Desk(tmp_path)
    d.commands=queue.Queue()
    d.connected=d.calendar_ready=d.mapping_ready=True
    d.calendar=CAL
    d.calendars={'CFFEX':CAL,'SHFE':CAL,'INE':CAL}
    d.mapping={'IM':'IM2612.CFE','AU':'AU2612.SHF'}
    d.watch=dict(d.mapping)
    im=Engine('IM2612.CFE',d.config,CAL)
    d.engines['IM2612.CFE']=im
    d.quotes['IM2612.CFE']={'rt_date':20261110,'rt_time':100110,'rt_last':100}
    monkeypatch.setattr(d,'gap_problem',lambda code,at:None)
    d.loading.add('AU2612.SHF')
    d.root_errors['AU']='Wind 查询错误'
    assert d.health('IM2612.CFE')==('实时',True)
    assert not d.health('AU2612.SHF')[1]
    d.set_watch('AU','AU2702.SHF')
    assert d.engines['IM2612.CFE'] is im
    assert d.quotes['IM2612.CFE']['rt_last']==100
    assert d.watch['AU']=='AU2702.SHF'
    d.stop()


def test_replay_selects_entire_trading_day_including_friday_night(tmp_path):
    path=tmp_path/'replay.sqlite3'
    s=Store(path)
    s.set('calendar_SHFE',sorted(CAL))
    bars=[Bar('AU2612.SHF',5,iso(t),100,102,99,101) for t in ('2026-11-06 21:05','2026-11-07 00:05','2026-11-09 09:05','2026-11-09 21:05')]
    s.save_bars(bars,iso('2026-11-10 15:00'))
    s.close()
    replay=build_replay(path,'AU2612.SHF','2026-11-09')
    assert len(replay['frames'])==3
    assert replay['frames'][0]['label']==bars[0].end
    assert replay['frames'][-1]['label']==bars[2].end
    assert replay['market']['decimals']==2
