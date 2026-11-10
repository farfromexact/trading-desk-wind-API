from __future__ import annotations

import asyncio
import multiprocessing as mp
import queue
import re
import time
from datetime import timedelta

from .domain import ROOT, Bar, bar_finished_at, completed_bar, dt, expected_end, iso, load_config, now, quote_problem, quote_time, session, stable_id
from .engine import Engine
from .live_preview import LivePreview
from .markets import market, valid_contract, root_of, trading_day, trading_windows, day_close, labels
from .storage import Store
from .wind_worker import worker_main
from .options_service import OptionsDesk


class Desk:
    def __init__(self, data_dir=None, offline=False):
        self.config=load_config()
        self.store=Store((data_dir or ROOT/'data')/'desk.sqlite3')
        previous_run=self.store.get('last_observation')
        runs=self.store.get('observation_runs',[])
        if previous_run and not any(x['started_at']==previous_run['started_at'] for x in runs):
            self.store.set('observation_runs',runs+[previous_run])
        self.store.set('rule_'+self.config['hash'],self.config)
        self.offline=offline
        self.mapping={}
        self.watch={}
        self.calendar=set()
        self.calendars={}
        self.loading=set()
        self.code_epochs={}
        self.root_errors={}
        self.initialized=set()
        self.expired_days={}
        self.calendar_refresh_day=None
        self.engines={}
        self.quotes={}
        self.preview=LivePreview()
        self.feed_state='离线回放' if offline else '正在连接 Wind'
        self.last_error=None
        self.pending={}
        self.seq=0
        self.generation=0
        self.bootstrap=True
        self.history_pending=set()
        self.refresh_pending=set()
        self.retry=[]
        self.calendar_ready=self.mapping_ready=False
        self.proc=None
        self.connected=False
        self.reconnect_at=0
        self.last_snapshot=self.last_refresh=self.last_health=time.monotonic()
        self.last_refresh_slot=None
        self.last_quote_saved={}
        self.gap_cache={}
        self.running=True
        self.metrics={'started_at':iso(now()),'rule_version':self.config['version'],'first_live_at':None,'last_live_at':None,'healthy_seconds':0.,'quote_updates':0,'bar_batches':0,'live_confirmations':0,'restarts':0,'ui_samples':0,'ui_max_ms':0,'ui_over_2s':0,'source_ui_samples':0,'source_ui_max_ms':0,'source_ui_over_2s':0,'per_contract':{}}
        self.options=OptionsDesk(self)
        self.request_metrics={}
        self.quote_processing_lag_ms=0.

    def start_worker(self):
        self.options.reset()
        self.close_worker_queues()
        ctx=mp.get_context('spawn')
        self.commands,self.results,self.quote_results=ctx.Queue(),ctx.Queue(),ctx.Queue()
        self.proc=ctx.Process(target=worker_main,args=(self.commands,self.results,self.quote_results),daemon=True)
        self.proc.start()
        self.worker_start=time.monotonic()
        self.connected=False
        self.pending.clear()
        self.retry.clear()
        self.bootstrap=True
        self.calendar_ready=self.mapping_ready=False
        self.history_pending.clear()
        self.refresh_pending.clear()
        self.feed_state='正在连接 Wind'
        self.initialized.clear()
        self.loading=set(self.watch.values())
        self.mapping={}
        self.calendars={}

    def submit(self, op, **kwargs):
        self.seq+=1
        ident=str(self.seq)
        cmd=dict(id=ident,op=op,**kwargs)
        self.pending[ident]={'cmd':cmd,'started':None,'generation':self.generation,'epoch':self.code_epochs.get(kwargs.get('code'),0)}
        self.commands.put(cmd)
        return ident

    def system_event(self, message, kind='system', **kwargs):
        event=dict(id=stable_id(kind,message,iso(now())),kind=kind,contract='',message=message,known_at=iso(now()),occurred_at=iso(now()),historical=False,**kwargs)
        self.store.event(event)

    def drain_engine(self, engine, allow_sound=False):
        for event in engine.events:
            op=event.get('opportunity',{})
            event['audible']=bool(allow_sound and not event['historical'] and event['kind']=='opportunity' and op.get('status')=='confirmed')
            inserted=self.store.event(event)
            if inserted:
                try:
                    self.options.on_event(event)
                except Exception as exc:
                    self.options.error(op.get('root',''),f'期权审计失败：{exc}')
            if inserted and event['audible']:
                self.metrics['live_confirmations']+=1
        engine.events.clear()

    def calendar_for(self, code):
        name=market(code,self.config)['calendar']
        return self.calendars.get(name,self.calendar if name=='CFFEX' else set())

    def current_day(self, code, at=None):
        at=at or now()
        cal=self.calendar_for(code)
        day=trading_day(at,code,cal)
        return day if day in cal else max((d for d in cal if d<=day),default=day)

    def history_dates(self, code='IM'):
        day=self.current_day(code)
        return sorted(d for d in self.calendar_for(code) if d<=day)[-self.config['history_days']:]

    def begin_watch(self, roots=None):
        roots=roots or [r for r in self.config['watchlist'] if r in self.mapping and self.calendar_for(r)]
        choices=self.store.get('choices',{})
        pins=self.store.get('daily_pins',{})
        legacy=self.store.get('daily_pin',{})
        added=[]
        for root in roots:
            day=self.current_day(root)
            pin=pins.get(root,{})
            if not pin and legacy.get('day')==day and root in legacy.get('mapping',{}):
                pin={'day':day,'contract':legacy['mapping'][root]}
            if pin.get('day')!=day:
                pin={'day':day,'contract':self.mapping.get(root)}
            code=choices.get(root) or pin.get('contract')
            if not valid_contract(code,root):
                self.root_errors[root]='等待有效的 Wind 主力合约映射'
                continue
            old=self.watch.get(root)
            if old and old in self.engines:
                self.engines[old].expire_session(now(),'重新加载该品种，原未触发方案取消')
                self.drain_engine(self.engines[old])
                self.engines.pop(old,None)
            stale_codes={old,code}
            self.history_pending={i for i in self.history_pending if self.pending.get(i,{}).get('cmd',{}).get('code') not in stale_codes}
            self.refresh_pending={i for i in self.refresh_pending if self.pending.get(i,{}).get('cmd',{}).get('code') not in stale_codes}
            self.retry=[(cmd,hist) for cmd,hist in self.retry if cmd.get('code') not in stale_codes]
            if old:
                self.loading.discard(old)
                self.quotes.pop(old,None)
            self.preview.rows={k:v for k,v in self.preview.rows.items() if k[0] not in stale_codes}
            self.code_epochs[code]=self.code_epochs.get(code,0)+1
            self.watch[root]=code
            self.initialized.add(root)
            self.root_errors.pop(root,None)
            pins[root]=pin
            self.loading.add(code)
            added.append(code)
        self.store.set('daily_pins',pins)
        if not added:
            return
        self.bootstrap=True
        self.submit('subscribe',codes=list(self.watch.values()))
        for code in added:
            cal=self.calendar_for(code)
            for tf in (1,5,15):
                cached={b.end for b in self.store.bars(code,tf)}
                for day in self.history_dates(code):
                    windows=trading_windows(day,code,cal)
                    if not windows or windows[0][0]>now():
                        continue
                    if day<self.current_day(code) and set(labels(day,code,tf,cal)).issubset(cached):
                        continue
                    start=windows[0][0]-timedelta(minutes=1)
                    end=min(windows[-1][1],now())
                    ident=self.submit('bars',code=code,timeframe=tf,start=start.strftime('%Y-%m-%d %H:%M:%S'),end=end.strftime('%Y-%m-%d %H:%M:%S'))
                    self.history_pending.add(ident)
        self.feed_state='Wind 已连接 · 分品种同步中'
        self.finish_bootstrap()

    def available_bars(self, code, tf):
        if not valid_contract(code):
            return []
        dates=self.history_dates(code)
        cal=self.calendar_for(code)
        allowed=set(dates)
        return [b for b in self.store.bars(code,tf) if trading_day(b.end,code,cal) in allowed and completed_bar(b,now(),self.config['bar_grace_seconds'],cal)]

    def rebuild(self, code):
        self.gap_cache.clear()
        engine=Engine(code,self.config,self.calendar_for(code))
        combined=self.available_bars(code,5)+self.available_bars(code,15)
        for b in sorted(combined,key=lambda b:(b.end,-b.timeframe)):
            engine.on_bar(b,historical=True)
        self.engines[code]=engine
        self.drain_engine(engine)

    def has_jobs(self, code, ids):
        return any(self.pending.get(i,{}).get('cmd',{}).get('code')==code for i in ids)

    def finish_bootstrap(self):
        for code in list(self.loading):
            if self.has_jobs(code,self.history_pending) or any(cmd.get('code')==code for cmd,_ in self.retry):
                continue
            self.rebuild(code)
            self.loading.remove(code)
            self.root_errors.pop(root_of(code),None)
            self.system_event(f'{code} 行情已同步；历史确认保持静默，等待新的实时机会')
        self.bootstrap=bool(self.loading)
        if not self.bootstrap:
            self.feed_state='Wind 已连接'
            self.last_refresh_slot=None

    def gap_problem(self, code, at):
        e=self.engines.get(code)
        if not e or e.regime['state']=='warming':
            return '历史数据预热中'
        cal=self.calendar_for(code)
        ends={tf:expected_end(at,tf,cal,self.config['bar_grace_seconds'],code) for tf in (5,15)}
        cache_key=(code,e.bars[-1].end if e.bars else '',e.context[-1].end if e.context else '',ends[5],ends[15])
        if cache_key in self.gap_cache:
            return self.gap_cache[cache_key]
        problem=None
        for tf,bars in ((5,e.bars),(15,e.context)):
            expected=ends[tf]
            if not bars or expected and bars[-1].end<expected:
                problem=f'等待{tf}分钟收盘数据'
                break
            present={b.end for b in bars}
            for day in self.history_dates(code):
                missing=next((v for v in labels(day,code,tf,cal) if bars[0].end<=v<=(expected or '') and v not in present),None)
                if missing:
                    problem=f'{tf}分钟K线缺失：{dt(missing):%m-%d %H:%M}'
                    break
            if problem:
                break
        self.gap_cache[cache_key]=problem
        return problem

    def health(self, code):
        at=now()
        if self.offline:
            return '离线回放',False
        if not self.connected:
            return self.feed_state,False
        if root_of(code) in self.root_errors:
            return self.root_errors[root_of(code)],False
        if code in self.loading or code not in self.engines:
            return '历史数据同步中',False
        cal=self.calendar_for(code)
        phase=session(at,cal,code)
        if phase!='交易中':
            return phase,False
        reason=quote_problem(self.quotes.get(code,{}),at,self.config['quote_stale_seconds'],code,cal) or self.gap_problem(code,at)
        return reason or '实时',not reason

    def apply_new_bars(self):
        for code,engine in self.engines.items():
            if code in self.loading or self.has_jobs(code,self.refresh_pending) or any(cmd.get('code')==code for cmd,_ in self.retry):
                continue
            latest5=engine.bars[-1].end if engine.bars else ''
            latest15=engine.context[-1].end if engine.context else ''
            pending=[b for b in self.available_bars(code,5) if b.end>latest5]+[b for b in self.available_bars(code,15) if b.end>latest15]
            # Recovery after a missed cycle is replay-only. Ordinary updates must be recent.
            recovery=any((now()-bar_finished_at(b)).total_seconds()>95 for b in pending if b.timeframe==5)
            for b in sorted(pending,key=lambda b:(b.end,-b.timeframe)):
                engine.on_bar(b,known_at=iso(now()),historical=recovery)
            self.drain_engine(engine,allow_sound=self.health(code)[1] and not recovery)

    def handle_quotes(self, packet):
        received=packet.get('received_at',iso(now()))
        for code,fields in packet['records'].items():
            if code not in self.watch.values():
                continue
            previous=self.quotes.get(code,{})
            merged=dict(previous,**fields)
            cal=self.calendar_for(code)
            old_stamp,new_stamp=quote_time(previous,code,cal,now()),quote_time(merged,code,cal,now())
            if old_stamp and new_stamp and new_stamp<old_stamp:
                continue
            merged['received_at']=received
            merged['received_ms']=packet.get('received_ms',time.time()*1000)
            merged['processed_at']=iso(now())
            self.quotes[code]=merged
            try:
                self.options.track_future(code,merged,now())
            except Exception as exc:
                self.options.error(root_of(code),f'报价路径记录暂停：{exc}')
            if quote_problem(merged,now(),self.config['quote_stale_seconds'],code,cal) is None:
                self.preview.on_quote(code,merged['rt_last'],new_stamp,cal)
            self.metrics['quote_updates']+=1
            per=self.metrics['per_contract'].setdefault(code,{'updates':0,'first':received,'last':received,'last_market_time':None})
            per.update(updates=per['updates']+1,last=received,last_market_time=iso(new_stamp) if new_stamp else None)
            if time.monotonic()-self.last_quote_saved.get(code,0)>=1:
                self.store.quote(code,merged)
                self.last_quote_saved[code]=time.monotonic()
            if self.health(code)[1]:
                self.engines[code].on_quote(merged['rt_last'],iso(new_stamp),iso(now()))
                self.drain_engine(self.engines[code])

    def handle(self, packet):
        kind=packet['type']
        if kind in ('quotes','option_quotes') and packet.get('received_ms'):
            self.quote_processing_lag_ms=max(0,time.time()*1000-packet['received_ms'])
        if kind=='option_quotes':
            try:
                self.options.handle_quotes(packet)
            except Exception as exc:
                self.options.error('',str(exc))
            return
        if kind=='option_feed_error':
            self.options.error('',packet['message'])
            return
        if kind=='connected':
            self.connected=True
            self.request_calendars()
        elif kind=='request_started':
            if packet['id'] in self.pending:
                self.pending[packet['id']]['started']=packet.get('native_started',time.monotonic())
        elif kind=='quotes':
            self.handle_quotes(packet)
        elif kind in ('fatal','feed_error'):
            self.fail(packet['message'])
        elif kind=='response':
            job=self.pending.pop(packet['id'],None)
            if not job or job['generation']!=self.generation:
                return
            cmd=job['cmd']
            seconds=packet.get('native_seconds',0.)
            metric=self.request_metrics.setdefault(cmd['op'],dict(count=0,max_seconds=0.,last_seconds=0.))
            metric.update(count=metric['count']+1,max_seconds=max(metric['max_seconds'],seconds),last_seconds=seconds)
            if cmd['op'].startswith('option_'):
                try:
                    self.options.handle(cmd,packet)
                except Exception as exc:
                    self.options.error(cmd.get('root',''),str(exc))
                return
            code=cmd.get('code')
            if code and (code not in self.watch.values() or job.get('epoch',self.code_epochs.get(code,0))!=self.code_epochs.get(code,0)):
                return
            was_history=packet['id'] in self.history_pending
            self.history_pending.discard(packet['id'])
            self.refresh_pending.discard(packet['id'])
            if 'error' in packet:
                self.last_error=packet['error']
                self.system_event(packet['error'],'data_error')
                if code:
                    self.root_errors[root_of(code)]=packet['error']
                if cmd['op'] in ('calendar','mapping','bars'):
                    self.retry.append((cmd,was_history))
            else:
                if cmd['op']=='calendar':
                    name=cmd.get('calendar','CFFEX')
                    self.calendars[name]=set(packet['payload'])
                    self.store.set('calendar_'+name,sorted(self.calendars[name]))
                    if name=='CFFEX':
                        self.calendar=self.calendars[name]
                    self.calendar_ready=True
                    aliases=[market(r)['alias'] for r in self.config['watchlist'] if market(r)['calendar']==name and r not in self.mapping]
                    if aliases and not any(p['cmd']['op']=='mapping' and set(p['cmd'].get('aliases',[])) & set(aliases) for p in self.pending.values()):
                        day=max((d for d in self.calendars[name] if d<=now().date().isoformat()),default=now().date().isoformat())
                        self.submit('mapping',day=day,aliases=aliases)
                    for contract,engine in self.engines.items():
                        if market(contract)['calendar']==name:
                            engine.calendar=self.calendars[name]
                elif cmd['op']=='mapping':
                    valid={root:value for root,value in packet['payload'].items() if valid_contract(value,root)}
                    self.mapping.update(valid)
                    for root in packet['payload'].keys()-valid.keys():
                        self.root_errors[root]='Wind 暂未返回有效主力映射，等待重试'
                        self.retry.append((dict(cmd,aliases=[market(root)['alias']]),False))
                    self.mapping_ready=True
                elif cmd['op']=='bars':
                    bars=[Bar(**x) for x in packet['payload']]
                    at=now()
                    cal=self.calendar_for(cmd['code'])
                    for b in bars:
                        if not completed_bar(b,at,self.config['bar_grace_seconds'],cal) and dt(b.end)-timedelta(minutes=b.timeframe)>=dt(cmd['start']):
                            self.preview.on_native(b,packet['received_at'],at,cal)
                    bars=[b for b in bars if completed_bar(b,at,self.config['bar_grace_seconds'],cal) and dt(b.end)-timedelta(minutes=b.timeframe)>=dt(cmd['start'])]
                    self.preview.finalized(bars)
                    existing=self.engines.get(cmd['code'])
                    engine_bars=(existing.bars if cmd['timeframe']==5 else existing.context) if existing else []
                    known_labels={b.end for b in engine_bars}
                    late=bool(cmd['timeframe'] in (5,15) and engine_bars and any(b.end<engine_bars[-1].end and b.end not in known_labels for b in bars))
                    changed=self.store.save_bars(bars,packet['received_at'])
                    self.metrics['bar_batches']+=1
                    self.gap_cache.clear()
                    if not any(c.get('code')==code for c,_ in self.retry):
                        self.root_errors.pop(root_of(code),None)
                    if (changed or late) and cmd['timeframe'] in (5,15) and cmd['code'] in self.engines:
                        self.system_event(f'{cmd["code"]} {cmd["timeframe"]}分钟数据修订/补回，保留原提醒记录并静默重算','revision')
                        engine=self.engines[cmd['code']]
                        engine.expire_session(now(),'数据修订，原方案暂停并静默重算')
                        self.drain_engine(engine)
                        self.rebuild(cmd['code'])
                if cmd['op'] in ('calendar','mapping') and self.calendar_ready and self.mapping_ready:
                    roots=[r for r in self.config['watchlist'] if r in self.mapping and self.calendar_for(r) and (r not in self.initialized or r in cmd.get('roll_roots',[]))]
                    if roots:
                        self.begin_watch(roots)
                if self.last_error and not self.retry and not self.history_pending and not self.refresh_pending:
                    self.system_event('失败查询已重试完成，行情数据恢复同步')
                    self.last_error=None
            if self.loading:
                self.finish_bootstrap()
            self.apply_new_bars()

    def fail(self, message):
        self.last_error=message
        self.feed_state='Wind 断开，准备重连'
        self.system_event(message,'data_error')
        self.connected=False
        if self.proc and self.proc.is_alive():
            self.proc.terminate()
            self.proc.join(timeout=2)
        self.pending.clear()
        self.reconnect_at=time.monotonic()+5
        self.metrics['restarts']+=1

    def request_calendars(self):
        today=now()
        for name in sorted({market(r)['calendar'] for r in self.config['watchlist']}):
            self.submit('calendar',calendar=name,start=(today-timedelta(days=120)).date().isoformat(),end=(today+timedelta(days=400)).date().isoformat())
        self.calendar_refresh_day=today.date().isoformat()

    def schedule_refresh(self):
        at=now()
        if at.second < self.config['bar_grace_seconds']:
            return
        for code in self.watch.values():
            if code in self.loading or self.has_jobs(code,self.refresh_pending):
                continue
            cal=self.calendar_for(code)
            windows=[w for day in self.history_dates(code)[-2:] for w in trading_windows(day,code,cal)]
            # Continue overlapping requests briefly after each close to capture final revisions.
            if not any(a<=at<=b+timedelta(minutes=3) for a,b in windows):
                continue
            for tf in (15,5,1):
                ident=self.submit('bars',code=code,timeframe=tf,start=(at-timedelta(minutes=45)).strftime('%Y-%m-%d %H:%M:%S'),end=at.strftime('%Y-%m-%d %H:%M:%S'))
                self.refresh_pending.add(ident)
        self.last_refresh=time.monotonic()
        self.last_refresh_slot=at.strftime('%Y-%m-%d %H:%M')

    def roll_sessions(self):
        at=now()
        pins=self.store.get('daily_pins',{})
        for root,code in list(self.watch.items()):
            cal=self.calendar_for(code)
            day=self.current_day(code,at)
            # Midnight doesn't reopen commodity sessions or replace their pinned contract.
            open_now=session(at,cal,code)=='交易中'
            prepare_day=day==at.date().isoformat() and at.hour==9
            busy=any(root in p['cmd'].get('roll_roots',[]) for p in self.pending.values())
            if pins.get(root,{}).get('day')!=day and (open_now or prepare_day) and not busy:
                asof=max((d for d in cal if d<=at.date().isoformat()),default=day)
                self.submit('mapping',day=asof,aliases=[market(root)['alias']],roll_roots=[root])
            engine=self.engines.get(code)
            if engine and engine.day:
                close=day_close(engine.day,code,cal)
                if close and at>=close and self.expired_days.get(code)!=engine.day:
                    engine.expire_session(at)
                    self.drain_engine(engine)
                    self.expired_days[code]=engine.day

    async def run(self):
        if self.offline:
            self.connected=False
            return
        self.start_worker()
        saved=time.monotonic()
        while self.running:
            backlog=False
            try:
                backlog=self.drain_packets()
                clock=time.monotonic()
                if self.reconnect_at:
                    if clock>=self.reconnect_at:
                        self.reconnect_at=0
                        self.start_worker()
                elif not self.proc.is_alive():
                    self.fail(f'Wind进程退出（exitcode={self.proc.exitcode}），重新连接并补齐历史')
                elif not self.connected and clock-self.worker_start>25:
                    self.fail('Wind连接超时，请确认终端已经登录且具有API权限')
                elif any(p['started'] and clock-p['started']>self.config['request_timeout_seconds'] for p in self.pending.values()):
                    timed_out=[p for p in self.pending.values() if p['started'] and clock-p['started']>self.config['request_timeout_seconds']]
                    detail='；'.join(f"{p['cmd']['op']} {p['cmd'].get('root') or p['cmd'].get('code') or ''} "
                                    f"{clock-p['started']:.1f}s" for p in timed_out)
                    self.fail(f'Wind查询超时（{detail}），重新连接后静默补数')
                if self.connected and not self.reconnect_at:
                    if self.watch and clock-self.last_snapshot>=self.config['snapshot_seconds'] and not any(p['cmd']['op']=='snapshot' for p in self.pending.values()):
                        self.submit('snapshot',codes=list(self.watch.values()))
                        self.last_snapshot=clock
                    if self.retry and not self.history_pending and not self.refresh_pending and int(clock)%15==0:
                        cmd,hist=self.retry.pop(0)
                        ident=self.submit(cmd['op'],**{k:v for k,v in cmd.items() if k not in ('op','id')})
                        (self.history_pending if hist else self.refresh_pending).add(ident)
                    if self.last_refresh_slot!=now().strftime('%Y-%m-%d %H:%M'):
                        self.schedule_refresh()
                    self.roll_sessions()
                    if self.calendar_refresh_day!=now().date().isoformat():
                        self.request_calendars()
                    try:
                        await self.options.tick()
                    except Exception as exc:
                        self.options.error('',f'期权模块暂停本轮处理：{type(exc).__name__}: {exc}')
                elapsed=min(1,clock-self.last_health)
                self.last_health=clock
                if self.watch and any(self.health(code)[1] for code in self.watch.values()):
                    self.metrics['healthy_seconds']+=elapsed
                    self.metrics['first_live_at']=self.metrics['first_live_at'] or iso(now())
                    self.metrics['last_live_at']=iso(now())
                if clock-saved>=10:
                    self.store.set('last_observation',self.metrics)
                    saved=clock
            except Exception as exc:
                self.fail(f'服务处理异常: {type(exc).__name__}: {exc}')
            await asyncio.sleep(.005 if backlog else .1)

    def drain_packets(self):
        # Completion / watchdog messages cannot wait behind a flood of ticks.
        # Keep all futures ticks in FIFO order (no coalescing of price touches).
        for _ in range(500):
            try: packet=self.results.get_nowait()
            except queue.Empty: break
            self.handle(packet)
        deadline=time.monotonic()+.04
        for _ in range(2000):
            try: packet=self.quote_results.get_nowait()
            except queue.Empty: break
            self.handle(packet)
            if time.monotonic()>=deadline:return True
        return False

    def close_worker_queues(self):
        for name in ('commands','results','quote_results'):
            q=getattr(self,name,None)
            if q is not None and hasattr(q,'close'):
                q.cancel_join_thread()
                q.close()

    def snapshot(self):
        clock=time.monotonic()
        request_status=[dict(op=p['cmd']['op'],root=p['cmd'].get('root'),code=p['cmd'].get('code'),
                             codes_count=len(p['cmd'].get('codes',[])),
                             running=p['started'] is not None,
                             elapsed_seconds=round(clock-p['started'],1) if p['started'] is not None else None)
                        for p in self.pending.values()]
        instruments=[]
        for root in self.config['watchlist']:
            code=self.watch.get(root)
            spec=market(root,self.config)
            engine=self.engines.get(code)
            q=self.quotes.get(code,{})
            stamp=quote_time(q,root,self.calendar_for(root),now())
            status,ready=self.health(code) if code else (self.root_errors.get(root,'等待主力映射'),False)
            instruments.append(dict(root=root,contract=code,market=spec,trading_day=self.current_day(root),quote=q,market_time=iso(stamp) if stamp else None,
                                    phase=session(now(),self.calendar_for(root),code or root),
                                    live_bars=self.preview.snapshot(code,now()) if code else {},
                                    age_seconds=round((now()-stamp).total_seconds(),1) if stamp else None,
                                    status=status,ready=ready,**(engine.snapshot() if engine else {'regime':{'state':'warming','label':'数据预热'},'note':'正在加载真实行情','levels':[],'structures':[],'opportunities':[]})))
        active=sum(x['phase']=='交易中' for x in instruments)
        phase=f'{active} / {len(instruments)} 品种交易中' if active else '休市 / 等待开盘'
        return {'server_time':iso(now()),'sent_ms':time.time()*1000,'phase':phase,'feed_state':self.feed_state,
                'last_error':self.last_error,'bootstrap_remaining':len(self.history_pending),'instruments':instruments,
                'rules':{'version':self.config['version'],'hash':self.config['hash']},'metrics':self.metrics,'offline':self.offline,
                'acquisition':{'pending':request_status,
                               'native_requests':self.request_metrics,'quote_processing_lag_ms':self.quote_processing_lag_ms,
                               'option_queue':[dict(op=j['op'],root=j.get('root'),codes_count=len(j.get('codes',[]))) for j in self.options.jobs]},
                'options':{'version':self.options.config['version'],'contexts':{r:self.options.context(r) for r in self.config['watchlist']},'metrics':self.options.metrics,
                           'quotes':{code:{k:v for k,v in q.items() if k!='_field_seen'} for code,q in self.options.quotes.items()}}}

    def set_watch(self, root, code):
        if root not in self.config['watchlist'] or code and not valid_contract(code,root):
            raise ValueError('请输入同品种真实月份合约，如 AU2612.SHF、SC2612.INE、T2612.CFE；留空恢复每日主力')
        if not self.connected or not self.calendar_ready or not self.mapping_ready:
            raise ValueError('等待 Wind 连接和主力映射完成后再切换')
        choices=self.store.get('choices',{})
        choices[root]=code
        self.store.set('choices',choices)
        self.begin_watch([root] if self.watch else None)

    def stop(self):
        self.running=False
        if self.proc and self.proc.is_alive():
            self.commands.put({'id':'stop','op':'stop'})
            self.proc.join(timeout=2)
            if self.proc.is_alive():
                self.proc.terminate()
                self.proc.join(timeout=2)
        self.close_worker_queues()
        self.store.set('last_observation',self.metrics)
        self.store.close()
