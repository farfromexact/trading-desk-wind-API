from datetime import timedelta

import pytest

from app.domain import Bar, dt, iso, load_config
from app.engine import Engine
from app.live_preview import LivePreview, forming_label
from app.service import Desk

DAY='2026-11-10'
CAL={DAY}
CODE='IM2612.CFE'


def stamp(t):
    return dt(DAY+'T'+t+'+08:00')


@pytest.mark.parametrize('at,tf,label',[
    ('09:30:02',5,'09:35:00'),('09:35:59',5,'09:35:00'),('09:36:00',5,'09:40:00'),
    ('10:40:40',5,'10:40:00'),('10:41:00',5,'10:45:00'),('10:40:40',15,'10:45:00'),
    ('10:40:40',1,'10:40:00'),('11:30:00',5,None),('12:59:59',5,None),
    ('13:00:00',15,'13:15:00'),('13:16:00',15,'13:30:00'),('15:00:00',1,None)])
def test_live_period_follows_native_labels_and_breaks(at,tf,label):
    assert forming_label(stamp(at),tf,CAL)==(iso(stamp(label)) if label else None)


def test_native_seed_restores_true_open_and_later_quote_updates_current_candle():
    p=LivePreview()
    p.on_quote(CODE,107,stamp('10:39:32'),CAL)
    native=Bar(CODE,5,iso(stamp('10:40:00')),100,105,99,104,12)
    p.on_native(native,stamp('10:39:30'),stamp('10:39:33'),CAL)
    row=p.snapshot(CODE,stamp('10:39:33'))['5'][-1]
    assert row['open']==100 and row['high']==107 and row['close']==107
    assert row['source']=='wind_partial' and row['forming']
    p.on_quote(CODE,98,stamp('10:39:34'),CAL)
    row=p.snapshot(CODE,stamp('10:39:34'))['5'][-1]
    assert row['low']==98 and row['close']==98
    p.on_quote(CODE,999,stamp('10:39:31'),CAL)
    assert p.snapshot(CODE,stamp('10:39:35'))['5'][-1]['high']==107


def test_preview_does_not_enter_signal_engine_cache_or_completed_api(tmp_path,monkeypatch):
    clock=stamp('10:40:30')
    monkeypatch.setattr('app.service.now',lambda:clock)
    desk=Desk(tmp_path,offline=True)
    desk.calendar=CAL
    desk.watch={'IM':CODE}
    desk.bootstrap=False
    desk.engines[CODE]=Engine(CODE,desk.config)
    closed=Bar(CODE,5,iso(stamp('10:35:00')),100,102,99,101,10)
    forming=Bar(CODE,5,iso(stamp('10:40:00')),101,103,100,102,11)
    impossible_future=Bar(CODE,5,iso(stamp('10:45:00')),102,104,101,103,12)
    def receive(ident,rows):
        desk.pending[ident]={'cmd':{'op':'bars','code':CODE,'timeframe':5,'start':iso(stamp('09:29:00'))},'generation':desk.generation}
        desk.handle({'type':'response','id':ident,'payload':[b.json() for b in rows],'received_at':iso(clock)})
    receive('a',[closed,forming,impossible_future])
    assert desk.engines[CODE].bars==[closed]
    assert desk.available_bars(CODE,5)==[closed]
    preview=desk.snapshot()['instruments'][0]['live_bars']['5']
    assert len(preview)==1 and preview[0]['end']==forming.end
    assert preview[0]['provisional'] is True
    assert desk.store.events()==[]
    # Crossing the completion boundary replaces display data with native final OHLC.
    clock=stamp('10:41:06')
    receive('b',[forming])
    assert desk.engines[CODE].bars==[closed,forming]
    assert desk.preview.snapshot(CODE,clock)['5']==[]
    desk.stop()


def test_quote_only_preview_is_labelled_partial_and_no_lunch_candle():
    p=LivePreview()
    p.on_quote(CODE,100,stamp('11:29:50'),CAL)
    before=p.snapshot(CODE,stamp('11:29:50'))
    assert before['5'][-1]['source']=='quotes_only'
    assert before['5'][-1]['forming']
    p.on_quote(CODE,999,stamp('12:00:00'),CAL)
    after=p.snapshot(CODE,stamp('12:00:00'))
    assert after['5'][-1]['close']==100 and not after['5'][-1]['forming']
    assert forming_label(dt('2026-11-14T10:00:00+08:00'),5,CAL) is None
