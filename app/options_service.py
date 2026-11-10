"""Isolated option state, prioritized acquisition, causal audit and shadow study."""
import asyncio
from collections import deque
from copy import deepcopy
from datetime import timedelta
import json
import os
import platform
import time

from .domain import ROOT, dt, iso, now, quote_time, stable_id
from .markets import phase, trading_day
from .markets import market
from .options_core import analyse, candidates, finite, futures_reference, horizon_dates, quote_issue, select_pool, trading_days_left
from .options_wind import INDEX_UNDERLYINGS, OPTION_ROOTS


def compute_view(args):
    return analyse(**args)


class OptionsDesk:
    def __init__(self, desk):
        self.desk=desk
        self.config=json.loads((ROOT/'options-config.json').read_text(encoding='utf-8'))
        if os.environ.get('WIND_DESK_OPTIONS_DISABLED')=='1':
            self.config['enabled']=False
        self.config['fee_per_contract_each_side'].update(desk.store.get('option_fee_overrides',{}))
        self.config['hash']=stable_id(self.config)
        self.entries={}; self.quotes={}; self.views={}; self.rows={}
        self.jobs=deque(); self.task=None; self.task_meta=None; self.confirmations=deque()
        self.last_dispatch=0.; self.last_subscription=(); self.last_study=0.; self.last_save=0.
        self.last_calc={}; self.last_quote_record={}; self.context_history={}
        self.studies=desk.store.get('option_studies',{})
        self.metrics=desk.store.get('option_metrics',dict(day_seconds=0.,night_seconds=0.,quote_packets=0,calculations=0,errors=0,ui_samples=0,ui_max_ms=0,ui_over_2s=0))
        self.last_tick=time.monotonic(); self.last_live_roots={}
        self.continuous={'day':0.,'night':0.}
        self.metrics.setdefault('day_longest_seconds',0.)
        self.metrics.setdefault('night_longest_seconds',0.)
        self.desk.store.set('option_rule_'+self.config['hash'],self.config)
        pending={i for i,s in self.studies.items() if s.get('options_status')=='计算中'}
        if pending:
            for record in desk.store.option_history('signal',limit=10000):
                signal=record['payload']; ident=signal['op']['id']
                if ident in pending and signal.get('option_input'):
                    source=signal['option_input'];source['calendar']=set(source['calendar'])
                    self.confirmations.append((ident,source))

    def reset(self):
        self.entries.clear(); self.jobs.clear(); self.last_subscription=()
        self.quotes.clear()
        self.continuous={'day':0.,'night':0.}
        for v in self.views.values():
            v.update(ready=False,status='连接恢复后等待重新同步')

    def error(self, root, message):
        self.metrics['errors']+=1
        if root in self.entries:
            self.entries[root]['error']=message
        if root in self.views:
            self.views[root].update(ready=False,status=message)
        self.desk.system_event(f'期权 {root or "数据"}：{message}','option_data_error')

    def queue(self, op, root, **kw):
        e=self.entries.get(root,{})
        cmd=dict(op=op,root=root,future_contract=e.get('contract'),watch_epoch=e.get('epoch'),**kw)
        key=stable_id(cmd)
        if op=='option_subscribe':
            # The subscription covers all roots. Only its newest desired set
            # matters; replaying superseded sets cancels useful live feeds.
            self.jobs=deque(x for x in self.jobs if x['op']!='option_subscribe')
            self.jobs.appendleft(dict(cmd,_key=key))
            return
        if not any(x.get('_key')==key for x in self.jobs):
            self.jobs.append(dict(cmd,_key=key))

    def underlying_quote(self, root):
        e=self.entries[root]
        # Commodity options reference this exact future, already subscribed by
        # Desk. A slower options snapshot must not shadow its live quote.
        if e['underlying']==e['contract']:
            return self.desk.quotes.get(e['contract'],{})
        return self.quotes.get(e['underlying'],{})

    def sync_watches(self):
        for root,contract in self.desk.watch.items():
            if root not in OPTION_ROOTS:
                continue
            old=self.entries.get(root)
            epoch=self.desk.code_epochs.get(contract,0)
            day=self.desk.current_day(root)
            query_day=max((d for d in self.desk.calendar_for(root) if d<=now().date().isoformat()),default=day)
            if old and (old['contract'],old['epoch'],old['day'],old.get('query_day'))==(contract,epoch,day,query_day):
                continue
            underlying=INDEX_UNDERLYINGS.get(root,contract)
            self.jobs=deque(x for x in self.jobs if x['root']!=root)
            self.entries[root]=dict(contract=contract,underlying=underlying,epoch=epoch,day=day,query_day=query_day,
                                    contracts=[],pool=[],daily=[],last_snapshot=0.,chain_loaded=False,error=None,updated=0)
            self.views.pop(root,None); self.rows.pop(root,None)
            self.context_history.pop(root,None)
            if underlying!=contract:
                self.queue('option_snapshot',root,codes=[underlying])
            self.queue('option_chain',root,underlying=underlying,day=query_day)
            self.queue('option_daily',root,underlying=underlying,start=(now()-timedelta(days=100)).date().isoformat(),end=now().date().isoformat())

    def handle(self, cmd, packet):
        root=cmd.get('root'); e=self.entries.get(root)
        if not e or (e['contract'],e['epoch'])!=(cmd.get('future_contract'),cmd.get('watch_epoch')):
            return
        if packet.get('error'):
            self.error(root,packet['error'])
            e['retry_at']=time.monotonic()+60
            e['retry_cmd']=cmd
            return
        op=cmd['op']; payload=packet.get('payload')
        if op=='option_chain':
            e['contracts']=payload['contracts']; e['chain_loaded']=True
            q=self.underlying_quote(root)
            spot=finite(q.get('rt_last'),0)
            if spot>0:
                e['pool']=self.pool_for(root,spot)
            self.desk.store.option_record(stable_id('chain',root,packet['received_at'],payload),root,'chain',dict(contract=e['contract'],**payload),packet['received_at'])
            self.queue_snapshots(root)
        elif op=='option_daily':
            e['daily']=payload
            self.desk.store.option_record(stable_id('daily',e['underlying'],payload),root,'daily',dict(underlying=e['underlying'],rows=payload),packet['received_at'])
        elif op=='option_vendor':
            e['vendor']=dict(day=cmd['day'],price_basis='Wind日终字段，不能替代盘中估值',values=payload,
                             status='有返回值' if any(any(v is not None for v in row.values()) for row in payload.values()) else '字段可请求但无有效数值')
            self.desk.store.option_record(stable_id('vendor',root,cmd['day'],payload),root,'vendor',e['vendor'],packet['received_at'])
        e['error']=None

    def queue_snapshots(self, root):
        e=self.entries[root]
        if any(j['op']=='option_snapshot' and j.get('root')==root for j in self.jobs):
            return
        # Futures already have their own feed. Keep index and option snapshots
        # separate so an active underlying cannot stand in for an option batch.
        if e['underlying']!=e['contract']:
            self.queue('option_snapshot',root,codes=[e['underlying']])
        spot=finite(self.underlying_quote(root).get('rt_last'),0)
        codes=[x['code'] for x in sorted(e['pool'],key=lambda x:abs(x.get('strike',spot)-spot))]
        n=self.config['quote_batch_size']
        if platform.system()=='Darwin':
            n=min(n,self.config.get('mac_quote_batch_size',8))
        for i in range(0,len(codes),n):
            self.queue('option_snapshot',root,codes=codes[i:i+n])
        e['last_snapshot']=time.monotonic()

    def pool_for(self, root, spot):
        contracts=self.entries[root]['contracts']; cal=self.desk.calendar_for(root)
        expiries=sorted({x['expiry'] for x in contracts})
        selected=set()
        # Monitor the union needed by intraday and 5-day views (usually two,
        # at most three maturities near roll), without inventing a new underlying.
        for holding in (0,self.config['holding_days']):
            minimum=max(self.config['minimum_trading_days'],holding+self.config['expiry_buffer_days'])
            qualified=[e for e in expiries if (trading_days_left(e,now(),root,cal) or 0)>=minimum]
            selected.update(qualified[:self.config['expiry_count']])
        if not selected:
            selected.update(expiries[:1])
        return select_pool([x for x in contracts if x['expiry'] in selected],spot,len(selected),self.config['strikes_per_expiry'])

    def handle_quotes(self, packet):
        at=now(); received=packet.get('received_at',iso(at))
        allowed={e['underlying']:root for root,e in self.entries.items()}
        allowed.update({x['code']:root for root,e in self.entries.items() for x in e['pool']})
        for study in self.studies.values():
            if study.get('status')=='observing':
                allowed[study['op']['contract']]=study['root']
                for c in study.get('candidates',[]):
                    for x in c['legs']:
                        allowed[x['code']]=study['root']
        touched=set()
        for code,fields in packet['records'].items():
            root=allowed.get(code)
            if not root:
                continue
            previous=self.quotes.get(code,{})
            merged=dict(previous,**fields)
            cal=self.desk.calendar_for(root)
            old,new=quote_time(previous,root,cal,at),quote_time(merged,root,cal,at)
            if old and new and new<old:
                continue
            seen=dict(previous.get('_field_seen',{}))
            seen.update({k:received for k in fields})
            merged.update(_field_seen=seen,received_at=received,received_ms=packet.get('received_ms'),market_time=iso(new) if new else None)
            book_fields=('rt_bid1','rt_ask1','rt_bsize1','rt_asize1','rt_date','rt_time')
            if packet.get('snapshot') and new and all(f in fields and finite(fields[f]) is not None for f in book_fields):
                merged['_book_seed_day']=trading_day(new,root,cal)
            if not packet.get('snapshot') and new and merged.get('_book_seed_day')==trading_day(new,root,cal):
                # An ordered delta feed keeps unchanged seeded fields valid;
                # their last-change timestamps are not last-confirmed times.
                merged['_book_stream_seen']=received
            self.quotes[code]=merged
            self.track_future(code,merged,at)
            touched.add(root)
        self.metrics['quote_packets']+=1
        for root in touched:
            if root in self.entries:
                self.entries[root]['updated']=time.monotonic()
            if packet.get('snapshot'):
                raw={c:q for c,q in packet['records'].items() if allowed.get(c)==root}
                self.desk.store.option_record(stable_id('quotes',root,received,raw),root,'quotes',dict(records=raw,received_at=received),received)

    def input_for(self, root, at=None):
        e=self.entries[root]
        uq=self.underlying_quote(root)
        return deepcopy(dict(root=root,contract=e['contract'],contracts=e['pool'],quotes={x['code']:self.quotes.get(x['code'],{}) for x in e['pool']},
                             underlying_quote=uq,futures_quote=self.desk.quotes.get(e['contract'],{}),daily=e['daily'],
                             calendar=self.desk.calendar_for(root),at=iso(at or now()),
                             phase=phase(at or now(),root,self.desk.calendar_for(root)),config=self.config))

    def track_future(self, code, quote, at):
        """Update excursions on every received quote, including a switched month."""
        for study in self.studies.values():
            if study['status']!='observing' or study['op']['contract']!=code:
                continue
            root=study['root']; cal=self.desk.calendar_for(root)
            if phase(at,root,cal)=='交易中' and not quote_issue(quote,at,root,cal,self.config,False):
                study['future_high']=max(study['future_high'],quote['rt_last'])
                study['future_low']=min(study['future_low'],quote['rt_last'])

    def on_event(self, event):
        op=event.get('opportunity')
        if not op or event.get('historical') or op.get('root') not in OPTION_ROOTS:
            return
        root=op['root']; ident=op['id']; stamp=event['known_at']
        if op['status']=='confirmed' and ident not in self.studies:
            # Freeze every baseline signal, including missing option coverage.
            inputs=self.input_for(root,stamp) if root in self.entries else None
            prior=self.context(root)
            signal=dict(op=deepcopy(op),option_input=inputs,available_at=stamp,rule_hash=self.config['hash'],prior_context=prior)
            serial=deepcopy(signal)
            if serial['option_input']:
                serial['option_input']['calendar']=sorted(serial['option_input']['calendar'])
            self.desk.store.option_record(stable_id('option-signal',ident),root,'signal',serial,stamp)
            self.studies[ident]=dict(root=root,op=deepcopy(op),status='waiting',candidates=[],confirmation=stamp,
                                     options_status='计算中' if inputs else '无期权覆盖',completed={},paths=0,
                                     futures_fee=self.config['futures_fee_per_contract_each_side'].get(root),futures_multiplier=self.config['futures_multiplier'][root],
                                     futures_tick=market(root)['tick_size'],slippage_ticks=self.config['slippage_ticks_each_side'],
                                     prior_context=prior)
            if inputs:
                self.confirmations.append((ident,inputs))
        study=self.studies.get(ident)
        if not study:
            return
        self.desk.store.option_record(stable_id('option-lifecycle',event['id']),root,'lifecycle',event,stamp)
        if op['status']=='triggered' and study['status']=='waiting':
            touch=op.get('touch_price',op['entry'])
            study.update(status='observing',triggered_at=stamp,trigger_future=touch,
                         trigger_quotes=deepcopy(self.quotes),trigger_future_quote=deepcopy(self.desk.quotes.get(op['contract'],{})),future_high=touch,future_low=touch)
            self.desk.store.option_record(stable_id('option-trigger',ident),root,'trigger',dict(op=op,quotes=study['trigger_quotes']),stamp)
        elif op['status'] in ('expired','invalidated','uncertain'):
            if study['status']=='waiting':
                study['status']=op['status']
            else:
                study['structure_status']=op['status']
                study.setdefault('structure_event_at',stamp)
        self.desk.store.set('option_studies',self.studies)

    async def tick(self):
        if not self.config['enabled'] or self.desk.offline:
            return
        clock=time.monotonic(); elapsed=min(1,clock-self.last_tick); self.last_tick=clock
        if not self.desk.connected:
            return
        self.sync_watches()
        if self.task and self.task.done():
            root,epoch,signal_id,source=self.task_meta
            try:
                context,rows=self.task.result()
                if signal_id:
                    study=self.studies[signal_id]
                    prior=study.get('prior_context',{})
                    if prior.get('ready') and prior.get('generated_at') and 0<=(dt(source['at'])-dt(prior['generated_at'])).total_seconds()<=source['config']['fresh_seconds']:
                        context['iv_change_15m']=prior.get('iv_change_15m')
                        context['iv_change_asof']=prior['generated_at']
                        change=prior.get('iv_change_15m')
                        context['iv_expansion']=change>=source['config']['iv_change_observation'] if change is not None else None
                    built=[]; reasons=[]
                    for holding in (0,1,3,5):
                        plans,why=candidates(study['op'],context,rows,source['at'],source['calendar'],source['config'],holding)
                        built.extend(plans); reasons.extend(why)
                    study.update(candidates=built,context=context,options_status='已有对照方案' if built else '；'.join(reasons))
                    self.desk.store.option_record(stable_id('option-comparison',signal_id),root,'comparison',dict(signal_id=signal_id,context=context,candidates=built,reasons=reasons),source['at'])
                    self.desk.store.set('option_studies',self.studies)
                elif self.entries.get(root,{}).get('epoch')==epoch and self.entries[root]['contract']==source['contract']:
                    history=self.context_history.setdefault(root,deque(maxlen=240))
                    first=context['expiries'][0] if context['expiries'] else None
                    old=next((v for v in reversed(history) if timedelta(minutes=15)<=dt(context['generated_at'])-dt(v['generated_at'])<=timedelta(minutes=16) and v.get('expiry')==(first or {}).get('expiry')),None) if context['ready'] else None
                    change=first['atm_iv']-old['iv'] if first and first['atm_iv'] and old and old['iv'] else None
                    context['iv_change_15m']=change
                    if change is not None and abs(change)>=self.config['iv_change_observation']:
                        context['notes'].append('15分钟IV明显变化，作为独立观察；不直接产生交易提醒')
                    context['vendor']=self.entries[root].get('vendor',{'status':'等待日终交叉核对'})
                    self.views[root],self.rows[root]=context,rows
                    if clock-self.last_quote_record.get(root,0)>=60:
                        self.last_quote_record[root]=clock
                        if context['ready']:
                            history.append(dict(generated_at=context['generated_at'],expiry=(first or {}).get('expiry'),iv=(first or {}).get('atm_iv')))
                        self.desk.store.option_record(stable_id('option-context',root,context['generated_at']),root,'context',context,context['market_time'])
                self.metrics['calculations']+=1
            except Exception as exc:
                self.error(root,f'估值暂停：{type(exc).__name__}: {exc}')
            self.task=None
        if not self.task:
            if self.confirmations:
                ident,source=self.confirmations.popleft(); root=source['root']
                self.task_meta=(root,None,ident,source)
                self.task=asyncio.create_task(asyncio.to_thread(compute_view,source))
            else:
                ready=[r for r,e in self.entries.items() if e['pool'] and clock-self.last_calc.get(r,0)>=1]
                if ready:
                    root=min(ready,key=lambda r:self.last_calc.get(r,0))
                    source=self.input_for(root); self.last_calc[root]=clock
                    self.task_meta=(root,self.entries[root]['epoch'],None,source)
                    self.task=asyncio.create_task(asyncio.to_thread(compute_view,source))
        for root,e in self.entries.items():
            if e.get('retry_at',float('inf'))<=clock:
                cmd=e.pop('retry_cmd'); e.pop('retry_at')
                self.queue(cmd['op'],root,**{k:v for k,v in cmd.items() if k not in ('id','op','root','future_contract','watch_epoch')})
            q=self.underlying_quote(root)
            spot=finite(q.get('rt_last'),0)
            if not e['pool'] and e['contracts'] and spot>0:
                e['pool']=self.pool_for(root,spot)
            if e['chain_loaded'] and clock-e['last_snapshot']>=self.config['snapshot_seconds'] and (phase(now(),root,self.desk.calendar_for(root))=='交易中' or not e['last_snapshot']):
                if spot>0:
                    e['pool']=self.pool_for(root,spot)
                self.queue_snapshots(root)
            if e['pool'] and 'vendor_requested' not in e:
                day=max((d for d in self.desk.calendar_for(root) if d<e['day']),default=e['day'])
                self.queue('option_vendor',root,codes=[e['pool'][len(e['pool'])//2]['code']],day=day)
                e['vendor_requested']=True
        focus=set(INDEX_UNDERLYINGS[r] for r in self.entries if r in INDEX_UNDERLYINGS and phase(now(),r,self.desk.calendar_for(r))=='交易中')
        for root,e in self.entries.items():
            if phase(now(),root,self.desk.calendar_for(root))=='交易中':
                focus.update(x['code'] for x in e['pool'])
        for study in self.studies.values():
            if study['status'] in ('waiting','observing'):
                if study['status']=='observing':
                    focus.add(study['op']['contract'])
                focus.update(x['code'] for c in study.get('candidates',[]) for x in c['legs'])
        subscription=tuple(sorted(focus))
        if self.entries and subscription!=self.last_subscription and not any(p['cmd']['op']=='option_subscribe' for p in self.desk.pending.values()):
            root=next(iter(self.entries))
            self.queue('option_subscribe',root,codes=list(subscription))
            self.last_subscription=subscription
        self.dispatch_next()
        if clock-self.last_study>=5:
            self.observe_studies()
            self.last_study=clock
        day_ok=9<=now().hour<16 and all(self.context(r).get('ready') and self.desk.health(self.desk.watch.get(r,''))[1] for r in OPTION_ROOTS)
        night_ok=all(self.context(r).get('ready') and self.desk.health(self.desk.watch.get(r,''))[1] for r in ('AG','AU','SC')) and (now().hour>=21 or now().hour<3)
        for label,ok in (('day',day_ok),('night',night_ok)):
            self.continuous[label]=self.continuous[label]+elapsed if ok else 0.
            self.metrics[label+'_seconds']+=elapsed if ok else 0.
            self.metrics[label+'_longest_seconds']=max(self.metrics[label+'_longest_seconds'],self.continuous[label])
        if clock-self.last_save>=10:
            self.desk.store.set('option_metrics',self.metrics)
            self.desk.store.set('option_studies',self.studies)
            self.last_save=clock

    def dispatch_next(self):
        # Admit one low-priority request during incremental refresh. The native
        # worker still prioritizes queued futures work. Waiting for every bars
        # response here can starve snapshots across consecutive refresh cycles.
        # Initial historical bootstrap remains protected.
        if self.jobs and not any(p['cmd']['op'].startswith('option_') for p in self.desk.pending.values()) and not self.desk.history_pending:
            job=self.jobs.popleft(); job.pop('_key',None)
            self.desk.submit(job.pop('op'),**job)

    def context(self, root):
        if root not in OPTION_ROOTS:
            return dict(root=root,ready=False,status='无已验证覆盖',notes=['首版未核实T/TL直接场内期权链'],expiries=[])
        if not self.config['enabled']:
            return dict(root=root,ready=False,status='期权模块已关闭',notes=[],expiries=[])
        v=deepcopy(self.views.get(root,dict(root=root,ready=False,status=self.entries.get(root,{}).get('error') or '等待期权链与盘口',notes=[],expiries=[])))
        if self.desk.offline:
            v.update(ready=False,status='离线回放，期权保持静默')
        elif not self.desk.connected:
            v.update(ready=False,status='Wind断开，暂停期权候选')
        elif v.get('generated_at') and (now()-dt(v['generated_at'])).total_seconds()>self.config['fresh_seconds']:
            v.update(ready=False,status='估值快照已过期，等待更新')
        elif v.get('ready') and root in self.desk.watch:
            message,healthy=self.desk.health(self.desk.watch[root])
            if not healthy:
                v.update(ready=False,status='期货数据未就绪：'+message)
        v['coverage']='监测池内近价行权价；不是全市场持仓'
        return v

    async def candidate_view(self, root, op_id=None, holding_days=None):
        e=self.entries.get(root)
        ops=list(self.desk.engines[e['contract']].opportunities.values()) if e and e['contract'] in self.desk.engines else []
        ops=[x for x in ops if x.get('entry') and x['status'] in ('confirmed','triggered') and x.get('trading_day')==self.desk.current_day(root) and (not op_id or x['id']==op_id)]
        op=max(ops,key=lambda x:x.get('confirmed_at',''),default=None)
        if not op:
            return dict(candidates=[],reasons=['等待已确认的期货方向信号'],context=self.context(root))
        ctx=self.context(root); rows=deepcopy(self.rows.get(root,[])); at=now()
        # Recheck freshness at request time, not just at the last calculation.
        for row in rows:
            row['reason']=row['reason'] or quote_issue(row['quote'],at,root,self.desk.calendar_for(root),self.config)
        result,reasons=await asyncio.to_thread(candidates,deepcopy(op),ctx,rows,at,self.desk.calendar_for(root),deepcopy(self.config),holding_days)
        current=self.desk.engines.get(e['contract'])
        latest=current.opportunities.get(op['id']) if current else None
        if self.entries.get(root) is not e or self.desk.watch.get(root)!=e['contract'] or not latest or latest['status'] not in ('confirmed','triggered') or not self.context(root).get('ready'):
            return dict(candidates=[],reasons=['计算期间合约、机会或数据状态发生变化，等待重新评估'],context=self.context(root))
        result=[c for c in result if all(not quote_issue(x['quote'],now(),root,self.desk.calendar_for(root),self.config) for x in c['legs'])]
        if not result:
            reasons.append('当前没有通过盘口新鲜度复核的组合')
        reference=futures_reference(op,self.desk.quotes.get(op['contract'],{}),self.config) if ctx.get('ready') else None
        return dict(candidates=result,reasons=reasons,context=ctx,opportunity=op,futures_reference=reference)

    def observe_studies(self):
        at=now()
        for ident,study in list(self.studies.items()):
            if study['status']!='observing':
                continue
            root=study['root']; cal=self.desk.calendar_for(root); op=study['op']
            fq=self.desk.quotes.get(op['contract'],self.quotes.get(op['contract'],{}))
            if quote_issue(fq,at,root,cal,self.config,False) or phase(at,root,cal)!='交易中':
                continue
            f=fq['rt_last']; study['future_high']=max(study['future_high'],f); study['future_low']=min(study['future_low'],f)
            anchor=study['trigger_future']; d=op['direction']; risk=op['risk']
            baseline=dict(price=f,reference_r=d*(f-anchor)/risk,mfe_r=(study['future_high']-anchor)/risk if d==1 else (anchor-study['future_low'])/risk,
                          mae_r=(anchor-study['future_low'])/risk if d==1 else (study['future_high']-anchor)/risk,
                          basis='标的触价后的已接收报价路径；未扣期货手续费，不是成交收益；断线期间极值未知')
            entryq=study.get('trigger_future_quote',{})
            entry_price=finite(entryq.get('rt_ask1' if d==1 else 'rt_bid1'))
            exit_price=finite(fq.get('rt_bid1' if d==1 else 'rt_ask1'))
            fee=study.get('futures_fee'); mult=study.get('futures_multiplier')
            baseline['net_r']=None
            books_valid=all(0<finite(q.get('rt_bid1'),0)<=finite(q.get('rt_ask1'),0) for q in (entryq,fq))
            if entry_price and exit_price and books_valid and fee is not None and mult and not quote_issue(entryq,study['triggered_at'],root,cal,self.config,False):
                costs=2*study['slippage_ticks']*study['futures_tick']*mult+2*fee
                baseline['net_r']=(d*(exit_price-entry_price)*mult-costs)/(risk*mult)
                baseline['net_basis']='买入看卖价/卖出看买价的每手路径，含配置费用及压力滑点；非成交'
            paths=[]
            for c in study.get('candidates',[]):
                entry=exit_value=0.; problem=None; snapshots=[]
                for leg in c['legs']:
                    q0=study.get('trigger_quotes',{}).get(leg['code'],{})
                    q1=self.quotes.get(leg['code'],{})
                    problem=quote_issue(q0,study['triggered_at'],root,cal,self.config) or quote_issue(q1,at,root,cal,self.config)
                    if problem:
                        break
                    sign=leg['quantity']; mult=leg['multiplier']
                    entry+=sign*(q0['rt_ask1'] if sign>0 else q0['rt_bid1'])*mult
                    exit_value+=sign*(q1['rt_bid1'] if sign>0 else q1['rt_ask1'])*mult
                    snapshots.append(dict(code=leg['code'],quote=q1))
                fee=c['roundtrip_fee']
                paths.append(dict(candidate_id=c['id'],kind=c['kind'],expiry=c['expiry'],planned_holding_days=c['holding_days'],status=problem or '盘口路径参考',quotes=snapshots,
                                  pnl_before_fees=None if problem else exit_value-entry-c['roundtrip_slippage'],
                                  net_pnl=None if problem or fee is None else exit_value-entry-c['roundtrip_slippage']-fee))
            minute=iso(at.replace(second=0,microsecond=0))
            if self.desk.store.option_record(stable_id('path',ident,minute),root,'path',dict(signal_id=ident,at=iso(at),baseline=baseline,options=paths),iso(at)):
                study['paths']+=1
            for n,close in horizon_dates(study['triggered_at'],root,cal).items():
                key=str(n)
                if key in study['completed']:
                    continue
                if close-timedelta(seconds=10)<=at<=close:
                    report=dict(signal_id=ident,root=root,setup=op['setup'],horizon=n,scheduled_close=iso(close),observed_at=iso(at),baseline=baseline,options=paths,
                                context=study.get('context'),status='收盘前10秒内盘口观察；无成交假设')
                    self.desk.store.option_record(stable_id('evaluation',ident,n),root,'evaluation',report,iso(at))
                    study['completed'][key]='observed'
                elif at>close:
                    report=dict(signal_id=ident,root=root,setup=op['setup'],horizon=n,scheduled_close=iso(close),status='缺少收盘时同步盘口，不补造收益')
                    self.desk.store.option_record(stable_id('evaluation',ident,n),root,'evaluation',report,iso(at))
                    study['completed'][key]='missing'
            if len(study['completed'])==4:
                study['status']='complete'

    def report(self):
        signals=self.desk.store.option_history('signal',limit=10000)
        evaluations=self.desk.store.option_history('evaluation',limit=10000)
        days=sorted({x['payload']['op']['trading_day'] for x in signals if x['payload'].get('op',{}).get('trading_day')})
        groups={}; outcomes={}
        for row in evaluations:
            e=row['payload']; key=f"{e['root']} / {e['setup']} / {e['horizon']}日"
            g=groups.setdefault(key,dict(samples=0,missing=0,net_option_samples=0))
            g['samples']+=1
            g['missing']+=int('baseline' not in e)
            g['net_option_samples']+=sum(x.get('net_pnl') is not None for x in e.get('options',[]))
            baseline=e.get('baseline')
            if baseline:
                context=e.get('context') or {}
                ratio=context.get('iv_rv_ratio')
                bucket=context.get('iv_rv_state','IV/RV样本不足')
                change=context.get('iv_expansion')
                expansion='IV变化样本不足' if change is None else ('伴随IV扩张' if change else '未伴随IV扩张')
                for group in ('全部基准',bucket,expansion):
                    b=outcomes.setdefault(key+' / '+group,dict(r=[],mae=[],mfe=[],net_r=[],option_net={}))
                    b['r'].append(baseline['reference_r']); b['mae'].append(baseline['mae_r']); b['mfe'].append(baseline['mfe_r'])
                    if baseline.get('net_r') is not None:
                        b['net_r'].append(baseline['net_r'])
                    for path in e.get('options',[]):
                        if path.get('net_pnl') is not None and path.get('planned_holding_days',5)==e['horizon']:
                            b['option_net'].setdefault(path['kind']+' / '+path.get('expiry',''),[]).append(path['net_pnl'])
        descriptive={k:dict(samples=len(v['r']),mean_reference_r=sum(v['r'])/len(v['r']),mean_mae_r=sum(v['mae'])/len(v['mae']),mean_mfe_r=sum(v['mfe'])/len(v['mfe']),
                            net_r_samples=len(v['net_r']),mean_net_r=sum(v['net_r'])/len(v['net_r']) if v['net_r'] else None,
                            option_mean_net_cny={kind:dict(samples=len(xs),mean=sum(xs)/len(xs)) for kind,xs in v['option_net'].items()}) for k,v in outcomes.items()}
        studied=list(self.studies.values())
        return dict(status='尚未证明交易优势',observation_days=len(days),minimum_days=self.config['minimum_observation_days'],signals=len(signals),
                    coverage=dict(with_candidates=sum(bool(s.get('candidates')) for s in studied),without_candidates=sum(not s.get('candidates') and s.get('options_status')!='计算中' for s in studied),
                                  not_triggered=sum(not s.get('triggered_at') for s in studied),reasons=sorted({s.get('options_status','') for s in studied if not s.get('candidates')})),
                    evaluation_groups=groups,descriptive_comparison=descriptive,metrics=self.metrics,acceptance=dict(day_continuous_complete=self.metrics['day_longest_seconds']>=1800,night_continuous_complete=self.metrics['night_longest_seconds']>=1800,
                        latency_samples=self.metrics['ui_samples'],latency_over_2s=self.metrics['ui_over_2s']),
                    note='每个实时确认信号保留基准；候选不改变期货规则。20个交易日只是首轮评估起点。缺少历史盘口不回填虚构收益。')

    def set_fee(self, root, fee):
        if root not in OPTION_ROOTS or fee is not None and (finite(fee) is None or not 0<=fee<=100000):
            raise ValueError('手续费必须为非负有限数值，或留空')
        self.config['fee_per_contract_each_side'][root]=fee
        self.config['hash']=stable_id({k:v for k,v in self.config.items() if k!='hash'})
        self.desk.store.set('option_fee_overrides',self.config['fee_per_contract_each_side'])
        self.desk.store.set('option_rule_'+self.config['hash'],self.config)
        return dict(root=root,fee=fee,hash=self.config['hash'])

    async def close(self):
        if self.task:
            try:
                await self.task
            except Exception:
                pass
        self.desk.store.set('option_studies',self.studies)
        self.desk.store.set('option_metrics',self.metrics)
