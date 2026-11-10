from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.domain import Bar, TZ, bar_finished_at, completed_bar, dt, expected_end, iso, quote_problem, session
from app.service import Desk
from app.storage import Store
from app.wind_worker import serialize_quotes


def test_native_wind_label_is_not_end_of_formation():
    b=Bar('IM2612.CFE',5,'2026-11-10T09:35:00+08:00',100,102,99,101,10)
    assert not completed_bar(b,dt('2026-11-10T09:35:59+08:00'))
    assert not completed_bar(b,dt('2026-11-10T09:36:04+08:00'))
    assert completed_bar(b,dt('2026-11-10T09:36:05+08:00'))
    future=Bar('IM2612.CFE',5,'2026-11-10T09:45:00+08:00',100,102,99,101,10)
    assert not completed_bar(future,dt('2026-11-10T09:41:00+08:00'))


def test_session_end_caps_native_minute():
    for end in ('11:30:00','15:00:00'):
        b=Bar('IM2612.CFE',5,'2026-11-10T'+end+'+08:00',100,102,99,101,10)
        assert bar_finished_at(b)==dt(b.end)
        assert completed_bar(b,dt(b.end)+timedelta(seconds=5))


@pytest.mark.parametrize('time_,phase',[('09:29:59','盘前'),('09:30:00','交易中'),('11:30:00','午休'),('12:59:59','午休'),('13:00:00','交易中'),('15:00:00','已收盘')])
def test_sessions(time_,phase):
    assert session(dt('2026-11-10T'+time_+'+08:00'),{'2026-11-10'})==phase


def test_calendar_and_expected_labels():
    cal={'2026-11-09','2026-11-10'}
    assert expected_end(dt('2026-11-10T09:35:30+08:00'),5,cal)=='2026-11-09T15:00:00+08:00'
    assert expected_end(dt('2026-11-10T09:36:05+08:00'),5,cal)=='2026-11-10T09:35:00+08:00'
    assert expected_end(dt('2026-11-10T12:00:00+08:00'),15,cal)=='2026-11-10T11:30:00+08:00'
    assert expected_end(dt('2026-11-10T13:15:30+08:00'),15,cal)=='2026-11-10T11:30:00+08:00'
    assert session(dt('2026-11-14T10:00:00+08:00'),cal)=='非交易日'


def test_quote_quality_zero_old_stale_future():
    at=dt('2026-11-10T09:40:00+08:00')
    base={'rt_last':100,'rt_date':20261110,'rt_time':94000}
    assert quote_problem(base,at) is None
    assert quote_problem(dict(base,rt_last=0),at)
    assert quote_problem(dict(base,rt_date=20261109),at)
    assert quote_problem(dict(base,rt_time=93944),at)
    assert quote_problem(dict(base,rt_time=94004),at)


def test_partial_quote_merge_and_out_of_order(tmp_path):
    desk=Desk(tmp_path,offline=True)
    desk.watch={'IM':'IM2612.CFE'}
    def send(fields):
        desk.handle_quotes({'records':{'IM2612.CFE':fields},'received_at':'2026-11-10T09:40:00+08:00'})
    send({'rt_last':100,'rt_date':20261110,'rt_time':94000,'rt_bid1':99.8})
    send({'rt_ask1':100.2})
    assert desk.quotes['IM2612.CFE']['rt_last']==100
    assert desk.quotes['IM2612.CFE']['rt_ask1']==100.2
    send({'rt_time':93959,'rt_last':90})
    assert desk.quotes['IM2612.CFE']['rt_last']==100
    desk.stop()


def test_callback_only_emits_fields_present():
    class Result:
        ErrorCode=0
        Codes=['IM2612.CFE','IF2612.CFE']
        Fields=['RT_LAST']
        Data=[[100,200]]
    packet=serialize_quotes(Result())
    assert packet['records']=={'IM2612.CFE':{'rt_last':100},'IF2612.CFE':{'rt_last':200}}


def test_bar_revisions_and_events_are_append_only(tmp_path):
    store=Store(tmp_path/'test.sqlite3')
    first=Bar('IM2612.CFE',5,'2026-11-10T09:35:00+08:00',100,102,99,101,10)
    second=Bar(first.contract,5,first.end,100,102,99,100.8,11)
    store.save_bars([first],'2026-11-10T09:36:05+08:00')
    assert len(store.save_bars([second],'2026-11-10T09:37:05+08:00'))==1
    assert store.db.execute('SELECT count(*) FROM revisions').fetchone()[0]==1
    assert store.event({'id':'event1','kind':'opportunity','message':'original'})
    assert not store.event({'id':'event1','kind':'opportunity','message':'rewritten'})
    assert store.events()[0]['message']=='original'
    store.close()


def test_invalid_manual_contract_is_rejected(tmp_path):
    desk=Desk(tmp_path,offline=True)
    with pytest.raises(ValueError):
        desk.set_watch('IM','IF2612.CFE')
    with pytest.raises(ValueError):
        desk.set_watch('IM','IM.CFE')
    desk.stop()
