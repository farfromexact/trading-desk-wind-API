from datetime import timedelta
import queue
import pytest
from app.domain import dt, expected_end
from app.service import Desk

AT=dt('2026-11-10T10:38:05+08:00')
CODE='IM2612.CFE'
CAL={'2026-11-09','2026-11-10'}


def test_minute_cache_audit_and_visible_chart_keep_distinct_budgets(tmp_path,monkeypatch):
    d=Desk(tmp_path,offline=True);d.calendar=CAL
    try:
        assert d.minute_refresh_due(CODE,AT)
        d.minute_refresh[CODE]=dict(target=expected_end(AT,1,CAL),at=AT)
        assert not d.minute_refresh_due(CODE,AT+timedelta(minutes=1))
        assert d.minute_refresh_due(CODE,AT+timedelta(seconds=300))
        monkeypatch.setattr('app.service.time.monotonic',lambda:100)
        d.minute_demand[CODE]=190
        assert not d.minute_refresh_due(CODE,AT)
        assert d.minute_refresh_due(CODE,AT+timedelta(minutes=1))
        monkeypatch.setattr('app.service.time.monotonic',lambda:191)
        assert not d.minute_refresh_due(CODE,AT+timedelta(minutes=1))
    finally:d.stop()


def test_chart_open_catches_up_once_and_coalesces_multiple_viewers(tmp_path,monkeypatch):
    monkeypatch.setattr('app.service.now',lambda:AT)
    d=Desk(tmp_path,offline=False);d.calendar=CAL;d.watch={'IM':CODE};d.connected=True;d.commands=queue.Queue()
    try:
        d.touch_minute_chart(CODE);d.touch_minute_chart(CODE)
        assert len(d.pending)==1
        cmd=next(iter(d.pending.values()))['cmd']
        assert cmd['timeframe']==1 and cmd['start']=='2026-11-10 09:53:05'
        assert len(d.refresh_pending)==1
    finally:d.stop()


@pytest.mark.parametrize('at',['10:38:05','11:30:05','21:06:05'])
def test_signal_periods_still_refresh_every_minute_when_auxiliary_cache_is_skipped(tmp_path,monkeypatch,at):
    clock=dt('2026-11-10T'+at+'+08:00');code='AG2612.SHF'
    monkeypatch.setattr('app.service.now',lambda:clock)
    d=Desk(tmp_path,offline=True);d.calendars={'SHFE':CAL|{'2026-11-11'}};d.watch={'AG':code};d.commands=queue.Queue()
    d.history_dates=lambda code:['2026-11-10','2026-11-11']
    d.minute_refresh[code]=dict(target=expected_end(clock,1,d.calendar_for(code),code=code),at=clock)
    try:
        d.schedule_refresh()
        assert [p['cmd']['timeframe'] for p in d.pending.values()]==[15,5]
        assert all(p['cmd']['start']==(clock-timedelta(minutes=45)).strftime('%Y-%m-%d %H:%M:%S') for p in d.pending.values())
    finally:d.stop()
