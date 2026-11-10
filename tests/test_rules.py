from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from app.domain import Bar, TZ, bar_finished_at, iso, load_config
from app.engine import Engine, classify

CODE='IM2612.CFE'


def candle(i, o=100,h=102,l=98,c=100,tf=5):
    stamp=datetime(2026,11,9,9,35,tzinfo=TZ)+timedelta(minutes=i*5)
    if stamp.hour==11 and stamp.minute>30 or stamp.hour==12:
        stamp+=timedelta(minutes=90)
    return Bar(CODE,tf,iso(stamp),o,h,l,c,100)


def mirror(b):
    return Bar(b.contract,b.timeframe,b.end,200-b.open,200-b.low,200-b.high,200-b.close,b.volume)


def seeded(state='bull',wide=False):
    e=Engine(CODE,load_config())
    for i in range(20):
        e.on_bar(candle(i,h=110 if wide else 102,l=90 if wide else 98))
    e.regime={'state':state,'label':state}
    e.events.clear()
    return e


def pullback(direction=1):
    e=seeded('bull' if direction==1 else 'bear')
    e.pivots[('internal','high' if direction==1 else 'low')]={'id':'known-level','price':100,'known_at':iso(bar_finished_at(e.bars[-1])),'occurred_at':e.bars[-4].end,'crossed':False}
    bars=[candle(20,100,104,99.8,103.8),candle(21,100.1,101,99.6,100.9)]
    for b in bars:
        b=b if direction==1 else mirror(b)
        e.on_bar(b,known_at=iso(bar_finished_at(b)+timedelta(seconds=5)),historical=False)
    return e,list(e.opportunities.values())[0]


@pytest.mark.parametrize('direction',[1,-1])
def test_pullback_confirm_and_live_touch(direction):
    e,op=pullback(direction)
    assert op['status']=='confirmed'
    assert op['rr']>=1.5
    assert op['entry']==pytest.approx(101.2 if direction==1 else 98.8)
    assert op['stop']==pytest.approx(99.4 if direction==1 else 100.6)
    original=deepcopy(op['source_bars'])
    stamp=iso(datetime.fromisoformat(op['confirmed_at'])+timedelta(seconds=2))
    e.on_quote(op['entry'],stamp,stamp)
    assert op['status']=='triggered'
    e.on_quote(op['stop'],stamp,stamp)
    assert op['status']=='invalidated'
    assert op['source_bars']==original


@pytest.mark.parametrize('direction',[1,-1])
def test_range_failure_needs_next_bar_followthrough(direction):
    e=seeded('range',wide=True)
    sweep=candle(20,109.8,110.4,109.2,109.4)
    follow=candle(21,109.4,109.6,108.8,108.9)
    # Original fixture is a short reversal at the upper edge.
    if direction==1:
        sweep,follow=mirror(sweep),mirror(follow)
    e.on_bar(sweep)
    op=list(e.opportunities.values())[0]
    assert op['status']=='watching'
    assert op['bounds']==(90,110)
    e.on_bar(follow)
    assert op['status']=='confirmed'
    assert op['direction']==direction
    assert op['targets'][0]['price']==100
    assert op['rr']>4


def test_failed_break_weak_followthrough_expires():
    e=seeded('range',wide=True)
    e.on_bar(candle(20,109.8,110.4,109.2,109.4))
    op=list(e.opportunities.values())[0]
    e.on_bar(candle(21,109.4,109.8,109.0,109.3))
    assert op['status']=='expired'
    assert op['entry'] is None


def test_true_break_does_not_become_failure():
    e=seeded('range',wide=True)
    e.on_bar(candle(20,109.8,112,109.2,111.5))
    op=list(e.opportunities.values())[0]
    e.on_bar(candle(21,111.5,113,111,112.8))
    assert op['status']=='expired'


def test_trend_blocks_countertrend_range_reversal():
    e=seeded('bull',wide=True)
    e.on_bar(candle(20,109.8,110.4,109.2,109.4))
    e.on_bar(candle(21,109.4,109.6,108.8,108.9))
    assert not any(o['setup']=='failed_break' for o in e.opportunities.values())


def test_bad_reward_is_visible_but_not_confirmed():
    e,op=pullback()
    op['status']='watching'
    e.confirm(op,e.bars[-1],[{'price':101.4,'name':'近端阻力'}])
    assert op['status']=='filtered'
    assert op['rr']<1.5


def test_confirmation_expires_after_three_completed_bars():
    e,op=pullback()
    for i in (22,23):
        e.on_bar(candle(i,100.2,100.8,99.8,100.4),historical=False)
        assert op['status']=='confirmed'
    e.on_bar(candle(24,100.2,100.8,99.8,100.4),historical=False)
    assert op['status']=='expired'


def test_historical_bar_cannot_invent_entry_stop_order():
    e,op=pullback()
    e.on_bar(candle(22,100,102,98,100),historical=True)
    assert op['status']=='uncertain'
    assert 'touch_at' not in op


def test_historical_confirmation_cannot_be_triggered_by_current_quote():
    e,op=pullback()
    op['historical']=True
    stamp=iso(datetime.fromisoformat(op['confirmed_at'])+timedelta(seconds=2))
    e.on_quote(102,stamp,stamp)
    assert op['status']=='confirmed'


def test_old_tick_does_not_trigger_new_confirmation():
    e,op=pullback()
    e.on_quote(102,e.bars[-1].end,op['confirmed_at'])
    assert op['status']=='confirmed'


def test_duplicate_bar_emits_no_duplicate_confirmation():
    e,op=pullback()
    prior=len(e.events)
    e.on_bar(e.bars[-1],historical=False)
    assert len(e.events)==prior
    assert sum(x['kind']=='opportunity' and x['opportunity']['status']=='confirmed' for x in e.events)==1


def test_no_future_context_and_confirmed_pivot_timing():
    e=Engine(CODE,load_config())
    for i,h in enumerate([101,102,110,104,103]):
        e.on_bar(candle(i,h=h))
        if i<4:
            assert ('internal','high') not in e.pivots
    pivot=e.pivots[('internal','high')]
    assert pivot['occurred_at']==candle(2).end
    assert pivot['known_at']==iso(bar_finished_at(candle(4)))
    e.on_bar(candle(10,tf=15))
    with pytest.raises(ValueError,match='超前'):
        e.on_bar(candle(6))


def test_prefix_events_unchanged_by_future_bars():
    e,op=pullback()
    prefix=deepcopy(e.events)
    for i in range(22,30):
        e.on_bar(candle(i),historical=True)
    assert e.events[:len(prefix)]==prefix


def test_end_of_day_expires_untriggered():
    e,op=pullback()
    e.expire_session(datetime(2026,11,9,15,0,tzinfo=TZ))
    assert op['status']=='expired'


@pytest.mark.parametrize('slope,expected',[(1,'bull'),(-1,'bear'),(0,'range')])
def test_market_regime_from_prices(slope,expected):
    bars=[candle(i,o=100+i*slope,h=101+i*slope,l=99+i*slope,c=100+i*slope,tf=15) for i in range(35)]
    result=classify(bars,load_config())
    assert result['state']==expected


def test_transition_does_not_generate_formal_setups():
    e=seeded('transition')
    e.pivots[('internal','high')]={'id':'level','price':100,'known_at':e.bars[-1].end,'crossed':False}
    e.on_bar(candle(20,100,104,99.8,103.8))
    assert not e.opportunities


def test_intrabar_range_proximity_is_preview_only_and_deduplicated():
    e=seeded('range',wide=True)
    stamp=iso(bar_finished_at(e.bars[-1])+timedelta(seconds=10))
    e.on_quote(109,stamp,stamp)
    assert len(e.previews)==1
    preview=list(e.previews.values())[0]
    assert preview['preview_only'] and preview['entry'] is None
    assert not e.opportunities
    count=len(e.events)
    e.on_quote(109.2,stamp,stamp)
    assert len(e.events)==count
    e.on_quote(100,stamp,stamp)
    assert preview['status']=='expired'


def test_last_bar_cannot_leave_new_candidate_active_overnight():
    e,op=pullback()
    e.on_bar(Bar(CODE,5,'2026-11-09T15:00:00+08:00',100,100.8,99.8,100.5,10),historical=True)
    assert op['status']=='expired'
    assert all(x['historical'] for x in e.events if x.get('opportunity',{}).get('status')=='expired')
