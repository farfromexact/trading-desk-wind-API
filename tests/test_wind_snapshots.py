import queue
from types import SimpleNamespace
from app.wind_snapshots import AsyncOptionSnapshots
from app.wind_worker import serialize_quotes
from app.options_wind import OPTION_FIELDS
from app.service import Desk


class Wind:
    def __init__(self):self.calls=[];self.cancelled=[]
    def wsq(self,codes,fields,func):
        self.calls.append((codes,func));return SimpleNamespace(ErrorCode=0,RequestID=len(self.calls))
    def cancelRequest(self,ident):self.cancelled.append(ident)


def result(codes,fields=OPTION_FIELDS.split(',')):
    return SimpleNamespace(ErrorCode=0,Codes=codes,Fields=fields,Data=[[1]*len(codes) for _ in fields])


def make(monkeypatch):
    clock=[100.]
    monkeypatch.setattr('app.wind_snapshots.time.monotonic',lambda:clock[0])
    w=Wind();responses=queue.Queue();quotes=queue.Queue()
    snapshots=AsyncOptionSnapshots(w,lambda r,label:r,serialize_quotes,responses,quotes)
    return clock,w,snapshots,responses,quotes


def test_callback_snapshot_returns_control_and_requires_full_individual_records(monkeypatch):
    clock,w,s,responses,quotes=make(monkeypatch)
    s.start(dict(id='a',codes=['A','B']))
    assert responses.get_nowait()['type']=='request_deferred'
    callback=w.calls[0][1]
    callback(result(['A'],['rt_time']))
    assert quotes.empty() and not w.cancelled # callback itself does no SDK work
    s.poll()
    assert quotes.get_nowait()['snapshot'] is True
    assert s.pending['a']['missing']=={'A','B'}
    callback(result(['A']));s.poll()
    assert s.pending['a']['missing']=={'B'} and not w.cancelled
    callback(result(['B']));s.poll()
    assert w.cancelled==[1] and not s.pending
    assert responses.get_nowait()['type']=='request_started'
    completed=responses.get_nowait()
    assert completed['payload']['missing_codes']==[] and 'error' not in completed
    callback(result(['A']));s.poll()
    assert quotes.qsize()==2 # late callback never adds a new packet


def test_missing_snapshot_times_out_without_native_reconnect_or_fake_quotes(monkeypatch):
    clock,w,s,responses,quotes=make(monkeypatch)
    s.start(dict(id='a',codes=['A']))
    clock[0]=109;s.poll();assert s.pending
    clock[0]=110;s.poll()
    assert not s.pending and quotes.empty() and w.cancelled==[1]
    packets=list(responses.queue)
    assert [p['type'] for p in packets]==['request_deferred','request_started','response']
    assert packets[-1]['payload']['missing_codes']==['A']
    assert packets[-1]['payload']['wait_seconds']==10
    assert packets[-1]['native_seconds']==0
    assert 'error' not in packets[-1] # missing quotes stay missing; no fatal event


def test_callback_error_and_shutdown_cancel_only_temporary_subscriptions(monkeypatch):
    clock,w,s,responses,quotes=make(monkeypatch)
    s.start(dict(id='a',codes=['A']))
    w.calls[0][1](SimpleNamespace(ErrorCode=-1))
    s.poll()
    assert list(responses.queue)[-1]['error']=='WSQ错误 -1'
    s.start(dict(id='b',codes=['B']))
    s.close();assert w.cancelled==[1,2] and not s.pending


def test_deferred_wait_does_not_disable_watchdog_for_native_calls(tmp_path):
    d=Desk(tmp_path,offline=True)
    try:
        d.pending['a']={'cmd':{'op':'option_snapshot'},'started':100}
        d.pending['b']={'cmd':{'op':'bars'},'started':100}
        d.handle({'type':'request_deferred','id':'a'})
        assert d.pending['a']['started'] is None and d.pending['a']['waiting_callback']
        assert d.pending['b']['started']==100
        d.handle({'type':'request_started','id':'a','native_started':200})
        assert d.pending['a']['started']==200 and not d.pending['a']['waiting_callback']
    finally:d.stop()


def test_worker_uses_mac_callbacks_and_keeps_windows_snapshot_path(monkeypatch):
    import sys
    import app.wind_worker as worker
    class Commands:
        def __init__(self):self.items=iter([dict(id='a',op='option_snapshot',codes=['A']),dict(id='stop',op='stop')])
        def get(self,timeout=None):return next(self.items)
        def get_nowait(self):raise queue.Empty
    class SDK(Wind):
        def __init__(self):super().__init__();self.async_calls=[];self.stopped=False
        def start(self,**kwargs):return SimpleNamespace(ErrorCode=0)
        def stop(self):self.stopped=True
        def wsq(self,codes,fields,func=None):
            self.async_calls.append(func is not None)
            r=result([codes]);r.RequestID=1
            if func:func(r)
            return r
    for platform,async_call in [('Darwin',True),('Windows',False)]:
        sdk=SDK();responses=queue.Queue();quotes=queue.Queue()
        monkeypatch.setitem(sys.modules,'WindPy',SimpleNamespace(w=sdk))
        monkeypatch.setattr(worker.platform,'system',lambda:platform)
        worker.worker_main(Commands(),responses,quotes)
        assert sdk.async_calls==[async_call] and sdk.stopped
        assert sdk.cancelled==([1] if async_call else [])
        packets=list(responses.queue)
        assert len([p for p in packets if p['type']=='response' and p['id']=='a'])==1
        quote=quotes.get_nowait()
        assert quote['snapshot'] and set(quote['records']['A'])==set(OPTION_FIELDS.split(','))
