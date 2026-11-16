import asyncio
import queue
import time
from types import SimpleNamespace

import pytest

from app.context_wind import ContextRequests, context_priority
from app.wind_context_client import WindPyContextClient
from app.market_context import MarketContext
from app.etf_context import flow_records


def result(fields, data, codes=('000852.SH',), error=0):
    return SimpleNamespace(Fields=fields, Data=data, Codes=list(codes), Times=[], ErrorCode=error, RequestID=11)


def test_callback_partial_packets_cannot_masquerade_as_full_snapshot():
    callbacks=[];cancelled=[];out=queue.Queue()
    w=SimpleNamespace(wsq=lambda *a,func:callbacks.append(func) or result([],[]), cancelRequest=cancelled.append)
    bridge=ContextRequests(w,lambda r,l:r,out,timeout=0)
    bridge.start(dict(id='1',method='wsq',args=['000852.SH','rt_date,rt_time'],expires=time.monotonic()+1))
    callbacks[0](result(['rt_date'],[[20261116]]))
    callbacks[0](result(['rt_time'],[[150000]]))
    bridge.poll()
    packets=list(out.queue)
    assert packets[0]['type']=='request_deferred'
    assert 'error' in packets[-1] and 'payload' not in packets[-1]
    assert cancelled==[11]


def test_complete_callbacks_keep_field_order_and_invalid_values():
    callbacks=[];out=queue.Queue()
    w=SimpleNamespace(wsq=lambda *a,func:callbacks.append(func) or result([],[]),cancelRequest=lambda x:None)
    bridge=ContextRequests(w,lambda r,l:r,out)
    bridge.start(dict(id='1',method='wsq',args=['000852.SH','rt_date,rt_time'],expires=time.monotonic()+1))
    callbacks[0](result(['RT_TIME','RT_DATE'],[[float('nan')],[20261116]]))
    bridge.poll()
    assert list(out.queue)[-1]['payload']['Data']==[[20261116],[None]]
    assert not bridge.pending


def test_background_priority_and_expired_job_never_calls_sdk():
    assert context_priority('bars')<context_priority('option_snapshot')<context_priority('context')
    assert context_priority('stop')<context_priority('bars')
    bridge=ContextRequests(SimpleNamespace(),None,None)
    with pytest.raises(ValueError,match='让行'):
        bridge.start(dict(method='wsi',expires=time.monotonic()-1))


def test_daily_dates_fields_and_shapes_are_verified_not_inferred():
    raw=dict(Codes=['510300.SH'],Fields=['F_MF_NETSUBAMT'],Times=['2026-11-13'],Data=[[0]])
    rows=WindPyContextClient.daily_rows(raw,'f_mf_netsubamt','2026-11-13')
    assert rows[0]['value']==0 and rows[0]['data_date']=='2026-11-13'
    with pytest.raises(ValueError):WindPyContextClient.daily_rows(raw,'f_mf_netsubamt','2026-11-16')
    with pytest.raises(ValueError):WindPyContextClient.daily_rows(raw,'mfd_inflow_m','2026-11-13')
    with pytest.raises(ValueError):WindPyContextClient.daily_rows(dict(raw,Data=[]),'f_mf_netsubamt','2026-11-13')


def test_windpy_flow_never_invents_realtime_source_timestamp():
    rows=[dict(code='510300.SH',data_date='2026-11-16',value=0,field='mfd_inflow_m',source='WindPy WSD')]
    values=flow_records(rows,{'mfd_inflow_m':'元'},'2026-11-16T16:00:00+08:00',float)
    flow=values['510300.SH']
    assert flow['time'] is None and flow['value']==0 and flow['frequency']=='daily_snapshot'
    assert flow['observed_at'].endswith('16:00:00+08:00')


def test_live_context_does_not_load_old_mcp_cache(tmp_path):
    (tmp_path/'market-context.json').write_text('{"etf_flow":{"old":{"value":1}}}')
    desk=SimpleNamespace(offline=False,watch={},calendar=set())
    context=MarketContext(desk,tmp_path)
    assert isinstance(context.client,WindPyContextClient)
    assert not context.book.etf_flow
    assert context.path.name=='market-context-windpy.json'


def test_background_errors_are_isolated_from_primary_feed(tmp_path):
    from app.service import Desk
    async def check():
        desk=Desk(tmp_path,offline=True)
        try:
            future=asyncio.get_running_loop().create_future()
            desk.context_waiters['c']=future
            desk.pending['c']=dict(cmd=dict(op='context'),generation=desk.generation)
            original=desk.last_error
            desk.handle(dict(type='response',id='c',error='field denied',native_seconds=.1))
            with pytest.raises(ValueError,match='field denied'):await future
            assert desk.last_error==original and not desk.retry and desk.metrics['restarts']==0
        finally:
            desk.stop()
    asyncio.run(check())


def test_disconnect_completes_background_waiter(tmp_path):
    from app.service import Desk
    async def check():
        desk=Desk(tmp_path,offline=True)
        try:
            future=asyncio.get_running_loop().create_future()
            desk.context_waiters['c']=future
            desk.fail('disconnected')
            with pytest.raises(ValueError,match='中断'):await future
            assert not desk.context_waiters
        finally:desk.stop()
    asyncio.run(check())


@pytest.mark.parametrize('data', [[[10,20,30]],[[10],[20],[30]]])
def test_wsd_single_date_supports_both_official_sdk_matrix_orientations(data):
    raw=dict(Codes=['512100.SH','510300.SH','510050.SH'],Fields=['MFD_INFLOW_M'],Times=['2026-11-13'],Data=data)
    rows=WindPyContextClient.daily_rows(raw,'mfd_inflow_m','2026-11-13')
    assert [(r['code'],r['value']) for r in rows]==[('512100.SH',10),('510300.SH',20),('510050.SH',30)]


def test_wsq_fraction_is_converted_to_percentage_points():
    async def query(method,*args):
        return dict(Codes=['SC2701.INE'],Fields=['RT_DATE','RT_TIME','RT_PCT_CHG','RT_AMT'],Times=[],
                    Data=[[20261116],[150000],[.0226],[100]])
    desk=SimpleNamespace(context_query=query,calendar_for=lambda code:set())
    r=asyncio.run(WindPyContextClient(desk).call('securities_get_price',{'windCodes':['SC2701.INE']}))
    assert r['rows'][0]['涨跌幅']==pytest.approx(2.26)


def test_subscription_estimate_is_labelled_derived_and_rejects_forward_fill():
    raw=dict(Codes=['510300.SH'],Fields=['UNIT_TOTAL','NAV'],Times=['2026-11-12','2026-11-13'],Data=[[1000,900],[4,4.1]])
    row=WindPyContextClient.subscription_estimate(raw,'510300.SH','2026-11-12','2026-11-13')
    assert row['value']==pytest.approx(-410) and row['field']=='share_change_times_nav'
    with pytest.raises(ValueError):WindPyContextClient.subscription_estimate(raw,'510300.SH','2026-11-11','2026-11-13')
    from app.etf_context import daily_records
    r=daily_records([row],{'share_change_times_nav':'元'},'2026-11-16T16:00:00+08:00',float)['510300.SH']
    assert r['estimated'] is True and r['published_at'] is None


@pytest.mark.parametrize('upper', [True,False])
def test_breadth_uses_one_existing_index_batch_and_total_fields(upper):
    from app.market_context import ContextBook
    calls=[]
    codes='000852.SH,000300.SH,000016.SH'
    fields=['rt_same_total','rt_time','rt_down_total','rt_date','rt_up_total']
    async def query(method,*args):
        calls.append((method,args))
        assert args==(codes,'rt_date,rt_time,rt_up_total,rt_down_total,rt_same_total')
        return dict(Codes=codes.split(','), Fields=[f.upper() if upper else f for f in fields],Times=[],
                    Data=[[10,5,0],[150000]*3,[860,195,30],[20261116]*3,[130,100,20]])
    r=asyncio.run(WindPyContextClient(SimpleNamespace(context_query=query)).call(
        'get_index_price_indicators',{'windcode':codes}))
    assert len(calls)==1 and calls[0][0]=='wsq'
    b=ContextBook();b.add_snapshots(r['rows'],r['units'])
    view=b.view('2026-11-16T16:00:00+08:00')['breadth']
    assert view['value']==pytest.approx(130/990*100)
    assert view['time']=='2026-11-16T15:00:00+08:00' and view['reason']==''
    # The change of field names must not relax scope/invalid-count gates.
    r['rows'][0]['平盘家数']=None
    b.add_snapshots(r['rows'],r['units'])
    assert b.view('2026-11-16T16:00:00+08:00')['breadth']['value'] is None
