import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from math import exp, sqrt

import pytest

from app.domain import ROOT, dt, iso, expected_end
from app.options_core import analyse, candidates, futures_reference, horizon_dates, quote_issue, realized_vol, select_pool, trading_days_left
from app.options_pricing import price, black_price, implied_vol
from app.options_service import OptionsDesk
from app.options_wind import normalize_chain
from app.service import Desk
from app.wind_worker import serialize_quotes

AT=dt('2026-11-10T14:00:00+08:00')
CAL={(AT+timedelta(days=i)).date().isoformat() for i in range(-100,401) if (AT+timedelta(days=i)).weekday()<5}
CONFIG=json.loads((ROOT/'options-config.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('root,contract',[('SC','SC2612.INE'),('AU','AU2612.SHF'),('AG','AG2612.SHF')])
def test_commodity_valuation_uses_live_future_not_stale_option_snapshot(tmp_path,root,contract):
    d=Desk(tmp_path,offline=True); o=d.options
    try:
        _,rows,_=market_fixture('AG')
        for row in rows:
            row.update(root=root,underlying=contract)
        o.entries[root]=dict(contract=contract,underlying=contract,pool=rows,daily=[])
        o.quotes={r['code']:r['quote'] for r in rows}
        o.quotes[contract]=quote(90,at=AT-timedelta(minutes=10))
        d.quotes[contract]=quote(100)
        d.calendars={'INE':CAL,'SHFE':CAL}
        source=o.input_for(root,AT)
        assert source['underlying_quote']==source['futures_quote']
        context,_=analyse(**source)
        assert context['qualified']>0 and context['basis']==0
        # Inputs used in frozen research must not change with the live feed.
        d.quotes[contract]['rt_last']=101
        assert source['underlying_quote']['rt_last']==100
        d.quotes.clear()
        assert o.underlying_quote(root)=={}  # never revive an old cached price
    finally:
        d.stop()


def test_index_option_keeps_index_as_underlying(tmp_path):
    d=Desk(tmp_path,offline=True); o=d.options
    try:
        o.entries['IM']=dict(contract='IM2612.CFE',underlying='000852.SH')
        d.quotes['IM2612.CFE']=quote(102)
        o.quotes['000852.SH']=quote(100)
        assert o.underlying_quote('IM')['rt_last']==100
    finally:
        d.stop()


@pytest.mark.parametrize('root,underlying,contract',[
    ('SC','SC2612.INE','SC2612.INE'),('IM','000852.SH','IM2612.CFE')])
def test_option_snapshot_batches_do_not_mix_underlying_with_options(tmp_path,root,underlying,contract):
    d=Desk(tmp_path,offline=True); o=d.options
    try:
        o.entries[root]=dict(contract=contract,underlying=underlying,epoch=1,
                             pool=[{'code':'OPTION1'},{'code':'OPTION2'}])
        o.queue_snapshots(root)
        batches=[j['codes'] for j in o.jobs]
        assert ['OPTION1','OPTION2'] in batches
        assert all(underlying not in b or b==[underlying] for b in batches)
        assert ([underlying] in batches)==(root=='IM')
    finally:
        d.stop()


@pytest.mark.parametrize('system,expected',[('Darwin',[8,8,5]),('Windows',[21])])
def test_snapshot_batch_limits_are_platform_specific(tmp_path,monkeypatch,system,expected):
    d=Desk(tmp_path,offline=True);o=d.options
    monkeypatch.setattr('app.options_service.platform.system',lambda:system)
    try:
        o.entries['SC']=dict(contract='SC2612.INE',underlying='SC2612.INE',epoch=1,
                            pool=[{'code':f'OPTION{i}'} for i in range(21)])
        o.queue_snapshots('SC')
        assert [len(j['codes']) for j in o.jobs]==expected
    finally:d.stop()


def test_pending_subscription_changes_coalesce_without_dropping_snapshots(tmp_path):
    d=Desk(tmp_path,offline=True);o=d.options
    try:
        o.entries['IM']=dict(contract='IM2612.CFE',epoch=1)
        o.queue('option_subscribe','IM',codes=['old'])
        o.queue('option_snapshot','IM',codes=['keep'])
        o.queue('option_subscribe','IM',codes=['new'])
        assert [(x['op'],x['codes']) for x in o.jobs]==[
            ('option_subscribe',['new']),('option_snapshot',['keep'])]
    finally:d.stop()


def test_option_dispatch_during_incremental_refresh_remains_single_and_blocks_bootstrap(tmp_path):
    import queue
    d=Desk(tmp_path,offline=True); o=d.options
    d.commands=queue.Queue()
    try:
        o.entries['SC']=dict(contract='SC2612.INE',epoch=1)
        o.queue('option_snapshot','SC',codes=['SC2612C590.INE'])
        o.queue('option_snapshot','SC',codes=['SC2612P590.INE'])
        d.history_pending={'initial'}
        o.dispatch_next()
        assert d.commands.empty()
        d.history_pending.clear()
        d.refresh_pending={'ongoing-minute-refresh'}
        o.dispatch_next()
        assert d.commands.get_nowait()['op']=='option_snapshot'
        o.dispatch_next()
        assert d.commands.empty() and len(o.jobs)==1
        d.pending.clear()  # previous low-priority request completed
        o.dispatch_next()
        assert d.commands.get_nowait()['codes']==['SC2612P590.INE']
    finally:
        d.stop()


def quote(last,bid=None,ask=None,at=AT):
    return dict(rt_last=last,rt_bid1=last-.01 if bid is None else bid,rt_ask1=last+.01 if ask is None else ask,
                rt_bsize1=10,rt_asize1=10,rt_vol=0,rt_oi=25,rt_date=int(at.strftime('%Y%m%d')),rt_time=int(at.strftime('%H%M%S')))


def market_fixture(root='IM', side=1):
    expiry='2026-12-18'; under='000852.SH' if root=='IM' else 'AG2612.SHF'; future='IM2612.CFE' if root=='IM' else under
    style='european' if root=='IM' else 'american'
    t=(dt(expiry+'T15:00:00+08:00')-AT).total_seconds()/(365*86400)
    contracts=[]; quotes={}
    for k in range(70,131,5):
        for cp in (1,-1):
            code=f'{root}{k}{cp}'
            contracts.append(dict(code=code,root=root,underlying=under,style=style,side=cp,strike=float(k),expiry=expiry,multiplier=100.,tick=.01))
            p=price(100.,float(k),t,.35,.02,cp,style)
            quotes[code]=quote(p,bid=max(.00001,p-.01),ask=p+.01)
    fq=quote(102 if root=='IM' else 100)
    context,rows=analyse(root,future,contracts,quotes,quote(100),fq,[],CAL,AT,'交易中',CONFIG)
    op=dict(id='fixture',root=root,contract=future,status='confirmed',direction=side,entry=fq['rt_last'],stop=fq['rt_last']-side*5,
            risk=5,targets=[dict(price=fq['rt_last']+side*10),dict(price=fq['rt_last']+side*15)],trading_day='2026-11-10',confirmed_at=iso(AT),setup='pullback')
    return context,rows,op


def crr(f,k,t,vol,rate,side,steps=801):
    step=t/steps; up=exp(vol*sqrt(step)); down=1/up; p=(1-down)/(up-down); discount=exp(-rate*step)
    values=[max(side*(f*up**j*down**(steps-j)-k),0) for j in range(steps+1)]
    for n in range(steps-1,-1,-1):
        values=[max(discount*((1-p)*values[j]+p*values[j+1]),side*(f*up**j*down**(n-j)-k),0) for j in range(n+1)]
    return values[0]


def test_european_known_value_and_put_call_parity():
    call=price(100.,100.,1.,.2,.05,1)
    assert call==pytest.approx(7.5770821464,abs=1e-8)
    for strike in (80.,100.,120.):
        c=price(100.,strike,.4,.3,.02,1)
        p=price(100.,strike,.4,.3,.02,-1)
        assert c-p==pytest.approx(exp(-.02*.4)*(100-strike))


@pytest.mark.parametrize('side',[1,-1])
@pytest.mark.parametrize('strike',[80.,100.,120.])
def test_american_against_independent_tree_and_iv(side,strike):
    value=price(100.,strike,.13,.3,.04,side,'american')
    assert value>=max(black_price(100.,strike,.13,.3,.04,side),side*(100-strike),0)
    assert value==pytest.approx(crr(100,strike,.13,.3,.04,side),abs=.025)
    if value-max(side*(100-strike),0)>.001:
        assert implied_vol(value,100,strike,.13,.04,side,'american')==pytest.approx(.3,abs=1e-4)


def test_intraday_time_fraction_and_invalid_inputs():
    assert price(100.,100.,1.5/365,.4,.02,1,'american')>price(100.,100.,1/365,.4,.02,1,'american')
    with pytest.raises(ValueError): implied_vol(-1,100,100,.1,.02,1)
    with pytest.raises(ValueError): implied_vol(200,100,100,.1,.02,1)
    with pytest.raises(ValueError): price(-100,100,.1,.2,.02,1)


def test_chain_metadata_uses_last_tradedate_not_expiredate():
    raw=dict(us_code='AG2612.SHF',option_code='AG2612C13000.SHF',exe_type='美式',call_put='认购',last_tradedate='2026-11-24T00:00:00',expiredate=14,strike_price=13000,multiplier=15)
    result=normalize_chain([raw],'AG','AG2612.SHF')[0]
    assert result['expiry']=='2026-11-24' and result['tick']==.5
    assert normalize_chain([raw],'AG','AG2702.SHF')==[]
    assert normalize_chain([dict(raw,exe_type='未知')],'AG','AG2612.SHF')==[]


def test_zero_volume_does_not_reject_valid_book_and_stale_fields_do():
    q=quote(3)
    assert quote_issue(q,AT,'IM',CAL,CONFIG) is None
    assert quote_issue(dict(q,rt_bid1=4,rt_ask1=3),AT,'IM',CAL,CONFIG)
    assert quote_issue(dict(q,rt_bsize1=0),AT,'IM',CAL,CONFIG)
    assert quote_issue(quote(3,at=AT-timedelta(seconds=16)),AT,'IM',CAL,CONFIG)
    q['_field_seen']={'rt_ask1':iso(AT-timedelta(seconds=20))}
    assert '字段' in quote_issue(q,AT,'IM',CAL,CONFIG)


def test_option_explicit_invalid_callback_does_not_retain_old_book():
    class Result:
        ErrorCode=0; Codes=['MO2611-C-6500.CFE']; Fields=['RT_BID1']; Data=[[float('nan')]]
    assert serialize_quotes(Result(),'options')['records'][Result.Codes[0]]['rt_bid1'] is None
    assert serialize_quotes(Result())['records'][Result.Codes[0]]=={}


def test_iv_surface_forward_basis_and_no_fabricated_skew():
    context,rows,_=market_fixture()
    assert context['ready'] and context['basis']==2
    assert context['expiries'][0]['forward']==pytest.approx(100,abs=.01)
    assert context['expiries'][0]['atm_iv']==pytest.approx(.35,abs=.001)
    assert context['expiries'][0]['put_minus_call_25d']==pytest.approx(0,abs=.001)
    assert all(x['iv'] is None or .3<x['iv']<.4 for x in rows)


@pytest.mark.parametrize('root',['IM','AG'])
@pytest.mark.parametrize('direction',[1,-1])
def test_all_strategy_directions_basis_and_protective_legs(root,direction):
    context,rows,op=market_fixture(root,direction)
    result,reasons=candidates(op,context,rows,AT,CAL,CONFIG)
    assert {x['kind'] for x in result}=={'long','debit_vertical','credit_vertical'},reasons
    for c in result:
        assert c['max_loss']>0 and c['fee_known'] is False and c['roundtrip_fee'] is None
        assert len(c['scenarios'])==12
        assert {x['days'] for x in c['scenarios']}=={0,1,3,5}
        assert len({(x['underlying'],x['expiry']) for x in c['legs']})==1
        if c['kind']=='credit_vertical':
            sell=next(x for x in c['legs'] if x['quantity']==-1)
            protect=next(x for x in c['legs'] if x['quantity']==1)
            assert direction*(op['stop']-context['basis']-sell['strike'])>0
            assert direction*(sell['strike']-protect['strike'])>0
            assert c['entry_cost']<0
        if c['kind']=='debit_vertical':
            sell=next(x for x in c['legs'] if x['quantity']==-1)
            assert direction*(sell['strike']-(op['targets'][0]['price']-context['basis']))>=0


def test_costs_and_iv_crush_scenarios_are_not_expiry_payoff():
    context,rows,op=market_fixture()
    config=deepcopy(CONFIG);config['fee_per_contract_each_side']['IM']=2
    result,_=candidates(op,context,rows,AT,CAL,config)
    c=next(x for x in result if x['kind']=='long')
    assert c['roundtrip_fee']==4 and c['roundtrip_slippage']==2
    down=next(x for x in c['scenarios'] if x['days']==1 and x['iv_change']==-.2)['pnl']['横盘']
    up=next(x for x in c['scenarios'] if x['days']==1 and x['iv_change']==.2)['pnl']['横盘']
    assert down<up
    assert c['max_profit'] is None


def test_expiry_holding_period_and_closed_session_gates():
    context,rows,op=market_fixture()
    for x in rows:x['trading_days']=6
    assert candidates(op,context,rows,AT,CAL,CONFIG,5)[0]==[]
    assert candidates(op,context,rows,AT,CAL,CONFIG,0)[0]
    assert candidates(op,dict(context,ready=False),rows,AT,CAL,CONFIG,0)[0]==[]
    assert candidates(dict(op,status='expired'),context,rows,AT,CAL,CONFIG,0)[0]==[]


def test_remaining_days_and_horizons_cross_weekend_night():
    at=dt('2026-11-13T21:30:00+08:00')
    assert trading_days_left('2026-11-17',at,'AG',CAL)==1
    assert horizon_dates(at,'AG',CAL)[0]==dt('2026-11-16T15:00:00+08:00')
    assert horizon_dates(at,'AG',CAL)[1]==dt('2026-11-17T15:00:00+08:00')


def test_rv_excludes_unfinished_daily_close_and_requires_consecutive_days():
    days=sorted(d for d in CAL if d<AT.date().isoformat())[-21:]
    daily=[dict(day=d,close=100+i%3) for i,d in enumerate(days)]
    rv=realized_vol(daily,20,AT,'IM',CAL)
    assert rv is not None
    assert realized_vol(daily+[dict(day='2026-11-10',close=1e9)],20,AT,'IM',CAL)==rv
    assert realized_vol(daily[1:],20,AT,'IM',CAL) is None


def test_extended_calendar_does_not_change_completed_bar():
    short={x for x in CAL if x<='2026-11-20'}
    assert expected_end(AT,5,short)==expected_end(AT,5,CAL)
    evening=dt('2026-11-10T21:06:05+08:00')
    assert expected_end(evening,5,short,code='AG')==expected_end(evening,5,CAL,code='AG')=='2026-11-10T21:05:00+08:00'


def test_option_error_and_out_of_order_are_isolated(tmp_path,monkeypatch):
    d=Desk(tmp_path,offline=True);o=d.options
    d.connected=True;d.watch={'IM':'IM2612.CFE'};d.calendars={'CFFEX':CAL};d.calendar=CAL
    o.entries['IM']=dict(contract='IM2612.CFE',epoch=0,underlying='000852.SH',pool=[{'code':'MO2611-C-6500.CFE'}])
    monkeypatch.setattr('app.options_service.now',lambda:AT)
    code='MO2611-C-6500.CFE'
    def send(q):d.handle(dict(type='option_quotes',records={code:q},received_at=iso(AT)))
    send(quote(3));send({'rt_bid1':2.9});send(quote(1,at=AT-timedelta(seconds=1)))
    assert o.quotes[code]['rt_last']==3 and o.quotes[code]['rt_bid1']==2.9
    send({'rt_ask1':None});assert o.quotes[code]['rt_ask1'] is None
    d.handle(dict(type='option_feed_error',message='测试期权权限不足'))
    assert d.connected and d.metrics['restarts']==0
    d.stop()


def test_freeze_is_append_only_and_history_does_not_create_study(tmp_path,monkeypatch):
    d=Desk(tmp_path,offline=True)
    _,_,op=market_fixture()
    event=dict(id='e',kind='opportunity',opportunity=op,known_at=iso(AT),historical=False)
    d.options.on_event(dict(event,historical=True))
    assert d.store.option_history('signal')==[]
    d.options.on_event(event);d.options.on_event(event)
    assert len(d.store.option_history('signal'))==1
    before=d.store.option_history('signal')[0]
    op['entry']=9999
    assert d.store.option_history('signal')[0]==before
    d.stop()


def test_study_uses_observed_touch_not_ideal_trigger(tmp_path):
    d=Desk(tmp_path,offline=True);_,_,op=market_fixture()
    event=dict(id='e',kind='opportunity',opportunity=op,known_at=iso(AT),historical=False)
    d.options.on_event(event)
    touched=dict(op,status='triggered',touch_price=104)
    d.options.on_event(dict(event,id='t',opportunity=touched))
    assert d.options.studies[op['id']]['trigger_future']==104
    assert d.options.studies[op['id']]['future_high']==104
    d.stop()


def test_futures_comparison_uses_book_and_actual_cost_config():
    _,_,op=market_fixture()
    config=deepcopy(CONFIG);config['futures_fee_per_contract_each_side']['IM']=3
    result=futures_reference(op,quote(102,101.8,102.2),config)
    assert result['fee_known'] and result['entry']==102.2
    assert result['pnl']['T1']==pytest.approx((112-.2-102.2)*200-80-6)
    assert not futures_reference(op,quote(102,103,102),config)['ready']


def test_study_recovery_uses_frozen_quotes_and_fees(tmp_path,monkeypatch):
    context,rows,op=market_fixture()
    d=Desk(tmp_path,offline=True);o=d.options
    d.watch={'IM':op['contract']};d.calendars={'CFFEX':CAL};d.calendar=CAL
    d.quotes[op['contract']]=quote(102)
    o.entries['IM']=dict(contract=op['contract'],underlying='000852.SH',pool=rows,daily=[])
    o.quotes={r['code']:r['quote'] for r in rows};o.quotes['000852.SH']=quote(100)
    event=dict(id='e',kind='opportunity',opportunity=op,known_at=iso(AT),historical=False)
    o.on_event(event)
    o.set_fee('IM',999)
    o.quotes[rows[0]['code']]['rt_ask1']=999
    d.stop()
    d=Desk(tmp_path,offline=True);o=d.options
    assert o.config['fee_per_contract_each_side']['IM']==999
    assert len(o.confirmations)==1
    ident,source=o.confirmations[0]
    assert source['config']['fee_per_contract_each_side']['IM'] is None
    assert source['quotes'][rows[0]['code']]['rt_ask1']!=999
    context,computed=analyse(**source)
    plans,_=candidates(op,context,computed,source['at'],source['calendar'],source['config'])
    assert plans and all(not c['fee_known'] for c in plans)
    assert not d.store.option_history('comparison')  # No fabricated completion on recovery.
    d.stop()


def test_excursions_include_intermediate_quotes_and_no_history_fill(tmp_path,monkeypatch):
    d=Desk(tmp_path,offline=True);_,_,op=market_fixture();o=d.options
    d.calendar=CAL;d.calendars={'CFFEX':CAL}
    event=dict(id='e',kind='opportunity',opportunity=op,known_at=iso(AT),historical=False)
    o.on_event(event);o.on_event(dict(event,id='t',opportunity=dict(op,status='triggered',touch_price=102)))
    o.track_future(op['contract'],quote(110),AT)
    o.track_future(op['contract'],quote(99),AT)
    o.track_future(op['contract'],quote(105),AT)
    assert o.studies[op['id']]['future_high']==110 and o.studies[op['id']]['future_low']==99
    later=AT+timedelta(days=1)
    monkeypatch.setattr('app.options_service.now',lambda:later)
    d.quotes[op['contract']]=quote(105,at=later)
    o.observe_studies()
    records=d.store.option_history('evaluation')
    assert len(records)==1 and records[0]['payload']['horizon']==0
    assert 'baseline' not in records[0]['payload'] and '缺少' in records[0]['payload']['status']
    o.observe_studies()
    assert len(d.store.option_history('evaluation'))==1
    d.stop()


def test_contract_switch_rejects_old_chain_response_and_clears_context(tmp_path,monkeypatch):
    d=Desk(tmp_path,offline=True);o=d.options
    monkeypatch.setattr('app.options_service.now',lambda:AT)
    monkeypatch.setattr('app.service.now',lambda:AT)
    d.calendars={'SHFE':CAL};d.watch={'AG':'AG2612.SHF'};d.code_epochs['AG2612.SHF']=1
    o.sync_watches();previous=deepcopy(o.entries['AG'])
    o.views['AG']={'ready':True};o.context_history['AG']=['old']
    d.watch['AG']='AG2702.SHF';d.code_epochs['AG2702.SHF']=2
    o.sync_watches()
    o.handle(dict(op='option_chain',root='AG',future_contract='AG2612.SHF',watch_epoch=1),dict(payload={'contracts':[{'wrong':True}]},received_at=iso(AT)))
    assert not o.entries['AG']['contracts'] and o.entries['AG']['underlying']=='AG2702.SHF'
    assert 'AG' not in o.views and 'AG' not in o.context_history
    assert all(j['future_contract']=='AG2702.SHF' for j in o.jobs)
    d.stop()


@pytest.mark.parametrize('change',['contract','invalidated','data'])
def test_candidate_discarded_if_state_changes_during_calculation(tmp_path,monkeypatch,change):
    from types import SimpleNamespace
    d=Desk(tmp_path,offline=True);context,rows,op=market_fixture();o=d.options
    d.watch={'IM':op['contract']};d.calendar=CAL
    d.engines[op['contract']]=SimpleNamespace(opportunities={op['id']:op})
    o.entries['IM']={'contract':op['contract']};o.rows['IM']=rows
    monkeypatch.setattr(o,'context',lambda root:deepcopy(context))
    monkeypatch.setattr('app.options_service.now',lambda:AT)
    monkeypatch.setattr(d,'current_day',lambda root:'2026-11-10')
    async def mutate(fn,*args):
        result=fn(*args)
        if change=='contract':d.watch['IM']='IM2703.CFE'
        elif change=='invalidated':op['status']='invalidated'
        else:context['ready']=False
        return result
    monkeypatch.setattr('app.options_service.asyncio.to_thread',mutate)
    result=asyncio.run(o.candidate_view('IM',holding_days=5))
    assert result['candidates']==[] and '发生变化' in result['reasons'][0]
    d.stop()


def test_seeded_delta_book_keeps_unchanged_fields_but_not_stale_or_invalid_quotes(tmp_path,monkeypatch):
    d=Desk(tmp_path,offline=True);o=d.options;code='MO2611-C-6500.CFE'
    d.calendar=CAL;d.calendars={'CFFEX':CAL}
    o.entries['IM']=dict(underlying='000852.SH',pool=[dict(code=code)])
    monkeypatch.setattr('app.options_service.now',lambda:AT)
    def send(fields,when,snapshot=False):
        o.handle_quotes(dict(records={code:fields},received_at=iso(when),snapshot=snapshot))
    try:
        send(quote(3,at=AT-timedelta(seconds=30)),AT-timedelta(seconds=30),True)
        send(dict(rt_time=int(AT.strftime('%H%M%S'))),AT)
        assert quote_issue(o.quotes[code],AT,'IM',CAL,CONFIG) is None
        assert quote_issue(o.quotes[code],AT+timedelta(seconds=16),'IM',CAL,CONFIG)
        invalid=dict(o.quotes[code],_book_seed_day='2026-11-09')
        assert '字段' in quote_issue(invalid,AT,'IM',CAL,CONFIG)
        send(dict(rt_ask1=None),AT)
        assert quote_issue(o.quotes[code],AT,'IM',CAL,CONFIG)
        o.reset()
        assert not o.quotes
    finally:d.stop()


def test_subscription_changes_preserve_unaffected_groups_and_cancel_void_return():
    from types import SimpleNamespace
    from app.wind_worker import update_option_subscriptions
    class Wind:
        def __init__(self):self.added=[];self.cancelled=[]
        def wsq(self,codes,fields,func):
            self.added.append(codes);return SimpleNamespace(RequestID=len(self.added))
        def cancelRequest(self,ident):self.cancelled.append(ident)
    w=Wind();groups={};check=lambda r,label:r
    update_option_subscriptions(w,['a','b'],groups,None,check)
    update_option_subscriptions(w,['a','b','c'],groups,None,check)
    update_option_subscriptions(w,['a','b','c'],groups,None,check)
    assert w.added==['a,b','c'] and w.cancelled==[]
    update_option_subscriptions(w,['b','c'],groups,None,check)
    assert w.cancelled==[1] and w.added==['a,b','c','b']
    assert set().union(*groups)=={'b','c'}


def test_control_responses_are_processed_before_quote_backlog(tmp_path):
    import queue
    d=Desk(tmp_path,offline=True);seen=[]
    d.results=queue.Queue();d.quote_results=queue.Queue()
    d.results.put('completed');d.quote_results.put('tick1');d.quote_results.put('tick2')
    d.handle=seen.append
    try:
        d.drain_packets()
        assert seen==['completed','tick1','tick2']
    finally:d.stop()


def test_serialization_retains_callback_receipt_time():
    from types import SimpleNamespace
    packet=serialize_quotes(SimpleNamespace(ErrorCode=0,Codes=[],Fields=[],Data=[]),received_ms=AT.timestamp()*1000)
    assert packet['received_at']==iso(AT)


def test_expected_end_cache_keeps_grace_boundary_and_calendar_changes():
    before=dt('2026-11-10T09:36:04.999999+08:00')
    boundary=dt('2026-11-10T09:36:05+08:00')
    assert expected_end(before,5,CAL)=='2026-11-09T15:00:00+08:00'
    assert expected_end(boundary,5,CAL)=='2026-11-10T09:35:00+08:00'
    shortened=CAL-{'2026-11-10'}
    assert expected_end(boundary,5,shortened)=='2026-11-09T15:00:00+08:00'
