from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import json
import queue

import pytest

from app.domain import Bar, dt, iso, load_config
from app.engine import Engine
from app.replay import build_replay
from app.service import Desk
from app.storage import Store


def test_replay_frames_are_independent_and_match_prefix(tmp_path):
    path=tmp_path/'bars.sqlite3'
    store=Store(path)
    bars=[]
    stamp=dt('2026-11-09T09:35:00+08:00')
    for i in range(12):
        end=stamp+timedelta(minutes=5*i)
        c=100+(i%4)
        bars.append(Bar('IM2612.CFE',5,iso(end),c,c+2,c-2,c+1,50))
        if end.minute%15==0:
            bars.append(Bar('IM2612.CFE',15,iso(end),c,c+3,c-3,c+1,150))
    store.save_bars(bars,iso(stamp+timedelta(hours=2)))
    store.close()
    result=build_replay(path,'IM2612.CFE','2026-11-09')
    assert len(result['frames'])==12
    assert result['frames'][0]['snapshot']['last_bar']==bars[0].end
    assert result['frames'][-1]['snapshot']['last_bar']!=result['frames'][0]['snapshot']['last_bar']
    assert all(e['historical'] for f in result['frames'] for e in f['events'])
    assert 'source_sha256' in result
    assert result['mode']=='final_bar_replay'


def test_gap_blocks_ready_until_rebuilt(tmp_path,monkeypatch):
    desk=Desk(tmp_path)
    code='IM2612.CFE'
    desk.calendar={'2026-11-10'}
    desk.watch={'IM':code}
    desk.connected=True
    desk.bootstrap=False
    clock=dt('2026-11-10T10:01:10+08:00')
    monkeypatch.setattr('app.service.now',lambda:clock)
    engine=Engine(code,load_config())
    engine.regime={'state':'range','label':'震荡'}
    # Native 09:45 is missing despite the latest 10:00 label being present.
    for label in ('09:35','09:40','09:50','09:55','10:00'):
        engine.bars.append(Bar(code,5,'2026-11-10T'+label+':00+08:00',100,102,98,100,10))
    for label in ('09:45','10:00'):
        engine.context.append(Bar(code,15,'2026-11-10T'+label+':00+08:00',100,102,98,100,10))
    desk.engines[code]=engine
    desk.quotes[code]={'rt_date':20261110,'rt_time':100110,'rt_last':100}
    assert '缺失' in desk.health(code)[0]
    assert not desk.health(code)[1]
    desk.gap_cache.clear()
    engine.bars.insert(2,Bar(code,5,'2026-11-10T09:45:00+08:00',100,102,98,100,10))
    assert desk.health(code)==('实时',True)
    desk.stop()


def test_historical_confirmations_never_audible_and_ids_deduplicate(tmp_path):
    desk=Desk(tmp_path,offline=True)
    e=Engine('IM2612.CFE',load_config())
    event={'id':'confirmed1','contract':e.contract,'kind':'opportunity','historical':True,'opportunity':{'status':'confirmed'}}
    e.events=[deepcopy(event)]
    desk.drain_engine(e,allow_sound=True)
    assert desk.store.events()[0]['audible'] is False
    event.update(id='confirmed2',historical=False)
    e.events=[deepcopy(event)]
    desk.drain_engine(e,allow_sound=True)
    assert desk.metrics['live_confirmations']==1
    e.events=[deepcopy(event)]
    desk.drain_engine(e,allow_sound=True)
    assert desk.metrics['live_confirmations']==1
    desk.stop()


def test_manual_selection_keeps_real_contract_and_expires_old_plans(tmp_path,monkeypatch):
    desk=Desk(tmp_path,offline=True)
    desk.offline=False
    desk.connected=desk.calendar_ready=desk.mapping_ready=True
    desk.mapping={'IM':'IM2612.CFE','IF':'IF2612.CFE'}
    desk.commands=queue.Queue()
    desk.calendar={'2026-11-09','2026-11-10'}
    monkeypatch.setattr(desk,'finish_bootstrap',lambda:None)
    desk.set_watch('IM','IM2611.CFE')
    assert desk.watch=={'IM':'IM2611.CFE','IF':'IF2612.CFE'}
    assert desk.store.get('choices')['IM']=='IM2611.CFE'
    desk.set_watch('IM',None)
    assert desk.watch['IM']=='IM2612.CFE'
    desk.stop()


def test_daily_primary_is_pinned_within_day(tmp_path,monkeypatch):
    desk=Desk(tmp_path,offline=True)
    desk.commands=queue.Queue()
    desk.calendar={'2026-11-10'}
    desk.mapping={'IM':'IM2612.CFE','IF':'IF2612.CFE'}
    monkeypatch.setattr(desk,'finish_bootstrap',lambda:None)
    desk.begin_watch()
    desk.mapping={'IM':'IM2611.CFE','IF':'IF2611.CFE'}
    desk.begin_watch()
    assert desk.watch=={'IM':'IM2612.CFE','IF':'IF2612.CFE'}
    desk.stop()


def test_query_error_clears_only_after_retries_complete(tmp_path):
    desk=Desk(tmp_path,offline=True)
    desk.bootstrap=False
    desk.last_error='temporary Wind RPC error'
    desk.retry=[({'op':'bars'},False)]
    desk.pending['a']={'cmd':{'op':'snapshot'},'generation':desk.generation}
    desk.handle({'type':'response','id':'a','payload':None})
    assert desk.last_error
    desk.retry.clear()
    desk.refresh_pending.add('b')
    desk.pending['a']={'cmd':{'op':'snapshot'},'generation':desk.generation}
    desk.handle({'type':'response','id':'a','payload':None})
    assert desk.last_error
    desk.pending['b']={'cmd':{'op':'snapshot'},'generation':desk.generation}
    desk.handle({'type':'response','id':'b','payload':None})
    assert desk.last_error is None
    assert '重试完成' in desk.store.events()[0]['message']
    desk.stop()
