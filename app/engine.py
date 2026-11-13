"""Deterministic, causal rules. No order entry, profit estimates or inferred fills."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from math import ceil, floor

from .domain import Bar, bar_finished_at, dt, iso, stable_id
from .markets import day_close, market, trading_day

REGIMES = {'bull': '多头趋势', 'bear': '空头趋势', 'range': '震荡', 'transition': '过渡', 'warming': '数据预热'}
ACTIVE = {'watching', 'confirmed', 'filtered'}
OCCUPYING = {'watching', 'confirmed'}


def ema(values, period):
    if len(values) < period:
        return []
    out = [sum(values[:period]) / period]
    alpha = 2 / (period + 1)
    for value in values[period:]:
        out.append(alpha * value + (1 - alpha) * out[-1])
    return out


def atr(bars, period=14):
    if len(bars) < period:
        return None
    tr = [b.high - b.low if i == 0 else max(b.high-b.low, abs(b.high-bars[i-1].close), abs(b.low-bars[i-1].close)) for i,b in enumerate(bars)]
    result = sum(tr[:period])/period
    for value in tr[period:]:
        result = ((period-1)*result+value)/period
    return result


def classify(bars, c):
    values = [b.close for b in bars]
    e = ema(values, c['ema_period'])
    a = atr(bars, c['atr_period'])
    n = c['efficiency_period']
    lag = c['slope_bars']
    if len(e) <= lag or len(values) <= n or not a:
        return {'state': 'warming', 'label': REGIMES['warming']}
    path = sum(abs(values[i]-values[i-1]) for i in range(len(values)-n,len(values)))
    efficiency = abs(values[-1]-values[-1-n])/path if path else 0
    slope = (e[-1]-e[-1-lag])/a
    state = 'transition'
    if efficiency >= c['trend_efficiency'] and slope >= c['slope_atr'] and values[-1] > e[-1]:
        state = 'bull'
    elif efficiency >= c['trend_efficiency'] and slope <= -c['slope_atr'] and values[-1] < e[-1]:
        state = 'bear'
    elif efficiency <= c['range_efficiency'] and abs(slope) <= c['slope_atr']:
        state = 'range'
    return {'state': state, 'label': REGIMES[state], 'ema': e[-1], 'atr': a, 'efficiency': efficiency, 'slope': slope, 'bar_end': bars[-1].end}


def strong(b, direction, body, quarter):
    width = b.high-b.low
    if width <= 0 or direction*(b.close-b.open) <= 0:
        return False
    return abs(b.close-b.open)/width >= body and (b.high-b.close if direction==1 else b.close-b.low)/width <= quarter


class Engine:
    def __init__(self, contract, config, calendar=None):
        self.market = market(contract,config)
        self.contract, self.c = contract, dict(config,tick_size=self.market['tick_size'])
        self.calendar = calendar
        self.bars = []
        self.context = []
        self.regime = {'state': 'warming', 'label': REGIMES['warming']}
        self.pivots = {}
        self.bias = {'internal': 0, 'swing': 0}
        self.equals = []
        self.structures = []
        self.opportunities = {}
        self.previews = {}
        self.events = []
        self.day = None
        self.day_high = self.day_low = None
        self.previous = []
        self.known_at = None
        self.historical = True
        self.note = '等待已收盘K线'

    def trading_day(self, stamp):
        return trading_day(stamp,self.contract,self.calendar)

    def opportunity_metadata(self, stamp):
        return dict(root=self.market['root'],trading_day=self.trading_day(stamp),
                    price_decimals=self.market['decimals'],price_unit=self.market['unit'])

    def emit(self, kind, anchor, message, **extra):
        event = dict(id=stable_id(self.c['hash'], self.contract, kind, anchor), contract=self.contract,
                     kind=kind, message=message, occurred_at=extra.pop('occurred_at', self.bars[-1].end if self.bars else self.known_at),
                     known_at=self.known_at, historical=self.historical, rule_version=self.c['version'], rule_hash=self.c['hash'], **extra)
        self.events.append(event)
        return event

    def transition(self, op, status, reason):
        if op['status'] == status and op.get('reason') == reason:
            return
        op.update(status=status, reason=reason, updated_at=self.known_at)
        self.emit('opportunity', (op['id'],status,self.bars[-1].end if self.bars else self.known_at), reason, opportunity=deepcopy(op))

    def rounded(self, value, direction):
        tick = self.c['tick_size']
        return round((ceil(value/tick-1e-8) if direction>0 else floor(value/tick+1e-8))*tick, 8)

    def review_regime(self):
        for op in self.opportunities.values():
            required = ('bull' if op['direction']==1 else 'bear') if op['setup']=='pullback' else 'range'
            if op['status']=='confirmed' and self.regime['state']!=required:
                op['regime_at_review']=deepcopy(self.regime)
                self.transition(op,'invalidated','确认后15分钟环境已改变，取消待触发机会；不代表账户平仓')

    def start_op(self, setup, direction, level, a, bounds, b):
        key = stable_id(self.c['hash'], self.contract, setup, direction, b.end, level['id'])
        if key in self.opportunities:
            return self.opportunities[key]
        for preview in self.previews.values():
            if preview['status']=='watching' and preview['direction']==direction:
                self.transition(preview,'expired','预警已进入收盘候选流程，请查看新的候选卡片')
        op = dict(id=key, contract=self.contract, setup=setup, direction=direction, status='new',
                  created_at=self.known_at, signal_bar=b.end, start_index=len(self.bars)-1,
                  level=level['price'], level_name=level['name'], level_id=level['id'], level_known_at=level.get('known_at'),
                  atr=a, tolerance=self.c['zone_atr']*a, bounds=bounds,
                  extreme=b.low if direction==1 else b.high, touched=False, near=False,
                  entry=None, stop=None, targets=[], rr=None, source_bars=[b.json()],
                  rule_version=self.c['version'], rule_hash=self.c['hash'], regime=deepcopy(self.regime),
                  regime_at_creation=deepcopy(self.regime), rule_parameters=deepcopy({k:self.c[k] for k in ('breakout_body','confirmation_body','close_quarter','minimum_rr','pullback_bars','return_bars','entry_expiry_bars')}),
                  historical=self.historical, **self.opportunity_metadata(b.end))
        self.opportunities[key] = op
        self.transition(op, 'watching', '等待突破位回踩及强K线确认' if setup=='pullback' else '等待价格收回关键位，再观察下一根跟随')
        return op

    def levels(self):
        levels = list(self.previous)
        for (scale,side), p in self.pivots.items():
            if scale == 'swing':
                levels.append(dict(p, name='波段高点' if side=='high' else '波段低点', side=side))
        levels.extend(self.equals[-8:])
        if len(self.bars) >= self.c['range_bars']:
            recent = self.bars[-self.c['range_bars']:]
            for side, value in [('high',max(b.high for b in recent)),('low',min(b.low for b in recent))]:
                levels.append({'id': stable_id('range',side,recent[-1].end,value), 'name':'区间上沿' if side=='high' else '区间下沿', 'side':side, 'price':value, 'known_at':recent[-1].end})
        return levels

    def confirm(self, op, b, available_levels):
        d, tick = op['direction'], self.c['tick_size']
        entry = self.rounded(b.high+tick if d==1 else b.low-tick, d)
        stop = self.rounded(op['extreme']-tick if d==1 else op['extreme']+tick, -d)
        risk = d*(entry-stop)
        if risk <= 0:
            self.transition(op, 'invalidated', '结构止损距离无效')
            return
        if op['setup']=='failed_break':
            low, high = op['bounds']
            targets = [{'price':(low+high)/2, 'label':'冻结区间中点','source':'structural'}, {'price':high if d==1 else low, 'label':'冻结区间另一侧','source':'structural'}]
        else:
            ordered = sorted((p for p in available_levels if d*(p['price']-entry)>tick/2), key=lambda p:d*(p['price']-entry))
            unique=[]
            for p in ordered:
                if not unique or abs(p['price']-unique[-1]['price'])>=tick:
                    unique.append(p)
            targets=[]
            for i,multiple in enumerate((2,3)):
                if i<len(unique):
                    targets.append({'price':unique[i]['price'], 'label':unique[i]['name'],'source':'structural'})
                else:
                    price=entry+d*multiple*risk
                    if targets and d*(price-targets[-1]['price'])<=0:
                        price=targets[-1]['price']+d*risk
                    targets.append({'price':self.rounded(price,d), 'label':f'{round(abs(price-entry)/risk,2)}R 测算目标','source':'synthetic'})
        for t in targets:
            t['price'] = self.rounded(t['price'], -d)
        rr=d*(targets[0]['price']-entry)/risk
        op.update(entry=entry,stop=stop,risk=risk,targets=targets,rr=rr,planned_rr=rr,target_source=targets[0]['source'],regime_at_confirmation=deepcopy(self.regime),historical=self.historical,confirmed_index=len(self.bars)-1,
                  confirmed_at=self.known_at,confirmation_bar=b.end,
                  expiry_bars=self.c['entry_expiry_bars'],remaining_bars=self.c['entry_expiry_bars'])
        op['source_bars'].append(b.json())
        if targets[0]['source']=='synthetic':
            self.transition(op,'filtered','缺少前方结构目标；R倍数仅作情景测算，不据此通过空间筛选')
        elif rr < self.c['minimum_rr']:
            self.transition(op,'filtered',f'目标空间不足：T1仅 {rr:.2f}R，低于 {self.c["minimum_rr"]}R')
        else:
            self.transition(op,'confirmed','5分钟收盘确认；等待触发价，盈亏比未扣费用与滑点')

    def lifecycle_bar(self, b):
        index = len(self.bars)-1
        for op in list(self.opportunities.values()):
            if op['status'] not in ('confirmed','filtered','triggered') or b.end <= op.get('confirmation_bar',b.end):
                continue
            d=op['direction']
            if op.get('trading_day',self.trading_day(op['signal_bar'])) != self.trading_day(b.end):
                continue
            if self.historical and op['status']=='confirmed':
                hit = b.high >= op['entry'] if d==1 else b.low <= op['entry']
                stopped = b.low <= op['stop'] if d==1 else b.high >= op['stop']
                if hit and stopped:
                    self.transition(op,'uncertain','同根历史K线覆盖触发价和失效价，先后不确定；不推定成交')
                    continue
                if stopped:
                    self.transition(op,'invalidated','历史K线覆盖失效价；不推定成交')
                    continue
                if hit:
                    op['regime_at_trigger']=deepcopy(self.regime)
                    self.transition(op,'triggered','历史K线覆盖触发价，具体触价时刻未知；不代表成交')
                    continue
            if op['status'] in ('confirmed','filtered'):
                op['remaining_bars']=max(0,self.c['entry_expiry_bars']-(index-op['confirmed_index']))
                if index-op['confirmed_index'] >= self.c['entry_expiry_bars']:
                    self.transition(op,'expired','3根5分钟K线内未实时触价，机会过期')

    def process_candidates(self, b, available_levels):
        index = len(self.bars)-1
        for op in list(self.opportunities.values()):
            if op['status']!='watching' or op['start_index']>=index:
                continue
            d, level, tol = op['direction'], op['level'], op['tolerance']
            age=index-op['start_index']
            if op['setup']=='pullback':
                required='bull' if d==1 else 'bear'
                if self.regime['state']!=required:
                    self.transition(op,'invalidated','15分钟趋势条件已不满足')
                    continue
                if d*(b.close-level)<-tol:
                    self.transition(op,'invalidated','收盘越过回踩区域不利边界')
                    continue
                if b.low<=level+tol and b.high>=level-tol:
                    if not op['touched']:
                        op['pullback_bar']=b.json()
                        op['extreme']=b.low if d==1 else b.high
                    op['touched']=True
                if op['touched']:
                    op['extreme']=min(op['extreme'],b.low) if d==1 else max(op['extreme'],b.high)
                    op['source_bars'].append(b.json())
                    if d*(b.close-level)>0 and strong(b,d,self.c['confirmation_body'],self.c['close_quarter']):
                        self.confirm(op,b,available_levels)
                        continue
                if age>=self.c['pullback_bars']:
                    self.transition(op,'expired','6根5分钟K线内未完成回踩确认')
            else:
                if self.regime['state']!='range':
                    self.transition(op,'invalidated','15分钟环境已离开震荡状态')
                    continue
                op['extreme']=min(op['extreme'],b.low) if d==1 else max(op['extreme'],b.high)
                op['source_bars'].append(b.json())
                if 'returned_index' in op:
                    if index==op['returned_index']+1 and d*(b.close-op['return_close'])>0 and d*(b.close-level)>0 and strong(b,d,self.c['confirmation_body'],self.c['close_quarter']):
                        self.confirm(op,b,available_levels)
                    else:
                        self.transition(op,'expired','收回关键位后下一根未出现有效跟随')
                elif d*(b.close-level)>=0 and age<self.c['return_bars']:
                    op.update(returned_index=index,return_close=b.close,return_bar=b.json())
                    op['reason']='已收回关键位，等待下一根同向强K线'
                elif age>=self.c['return_bars']-1:
                    self.transition(op,'expired','2根5分钟K线内未收回关键位')

    def update_pivots(self, b, a):
        for scale,n in [('internal',self.c['internal_pivot']),('swing',self.c['swing_pivot'])]:
            if len(self.bars)<2*n+1:
                continue
            window=self.bars[-(2*n+1):]
            pivot=window[n]
            for side in ('high','low'):
                value=getattr(pivot,side)
                others=[getattr(x,side) for i,x in enumerate(window) if i!=n]
                valid = value>max(others) if side=='high' else value<min(others)
                if not valid:
                    continue
                key=(scale,side)
                previous=self.pivots.get(key)
                p={'id':stable_id(scale,side,pivot.end),'price':value,'occurred_at':pivot.end,'known_at':iso(bar_finished_at(b)),'crossed':False}
                self.pivots[key]=p
                if scale=='internal' and previous and a and abs(previous['price']-value)<=max(self.c['tick_size'],self.c['equal_atr']*a):
                    self.equals.append({'id':stable_id('equal',previous['id'],p['id']),'name':'EQH' if side=='high' else 'EQL','side':side,'price':(previous['price']+value)/2,'known_at':iso(bar_finished_at(b)),'occurred_at':pivot.end})
                event=self.emit('pivot',p['id'],f'{scale} {side} 已确认',occurred_at=pivot.end,price=value,scale=scale,side=side,confirmed_bar=b.end)
                self.structures.append(event)

    def on_bar(self, b, known_at=None, historical=True):
        if b.contract!=self.contract or not b.valid():
            return
        self.known_at=known_at or iso(bar_finished_at(b))
        self.historical=historical
        if b.timeframe==15:
            if not self.context or b.end>self.context[-1].end:
                self.context.append(b)
                self.regime=classify(self.context,self.c)
                self.review_regime()
            return
        if b.timeframe!=5 or self.bars and b.end<=self.bars[-1].end:
            return
        if self.context and self.context[-1].end>b.end:
            raise ValueError('15分钟上下文超前于当前5分钟事件')
        available_levels=self.levels()  # snapshot before this bar, never freshly back-labelled pivots
        if self.day != self.trading_day(b.end):
            if self.day:
                for op in self.opportunities.values():
                    if op['status'] in ACTIVE:
                        self.transition(op,'expired','交易日结束，未触发机会不跨日')
                self.previous=[{'id':stable_id(self.day,side),'name':name,'side':side,'price':price,'known_at':self.bars[-1].end} for side,name,price in [('high','前日高点',self.day_high),('low','前日低点',self.day_low)]]
            self.day=self.trading_day(b.end)
            self.day_high,self.day_low=b.high,b.low
            available_levels=self.levels()
        self.bars.append(b)
        self.day_high=max(self.day_high,b.high)
        self.day_low=min(self.day_low,b.low)
        a=atr(self.bars,self.c['atr_period'])
        self.lifecycle_bar(b)
        self.process_candidates(b,available_levels)
        breaks=[]
        for scale in ('internal','swing'):
            for side,d in [('high',1),('low',-1)]:
                p=self.pivots.get((scale,side))
                if p and not p['crossed'] and d*(b.close-p['price'])>=self.c['tick_size']-1e-8:
                    old=self.bias[scale]
                    label='初始突破' if old==0 else 'BOS' if old==d else 'CHoCH'
                    self.bias[scale]=d
                    p['crossed']=True
                    event=self.emit('structure',(p['id'],label,b.end),f'{scale} {label}',direction=d,price=p['price'],label=label,scale=scale,level_known_at=p['known_at'])
                    self.structures.append(event)
                    if scale=='internal':
                        breaks.append((d,dict(p,name=label)))
        if a and len(self.bars)>self.c['range_bars']:
            previous_bars=self.bars[-self.c['range_bars']-1:-1]
            bounds=(min(x.low for x in previous_bars),max(x.high for x in previous_bars))
            for d,p in breaks:
                if self.regime['state']==('bull' if d==1 else 'bear') and strong(b,d,self.c['breakout_body'],self.c['close_quarter']):
                    if not any(o['status'] in OCCUPYING and o['setup']=='pullback' and o['direction']==d for o in self.opportunities.values()):
                        self.start_op('pullback',d,p,a,bounds,b)
            if self.regime['state']=='range':
                low,high=bounds
                for d,side in [(-1,'high'),(1,'low')]:
                    if any(o['status'] in OCCUPYING and o['setup']=='failed_break' and o['direction']==d for o in self.opportunities.values()):
                        continue
                    eligible=[]
                    for p in available_levels:
                        if p.get('side')!=side or p['name'] not in ('前日高点','前日低点','EQH','EQL','区间上沿','区间下沿'):
                            continue
                        edge = p['price']>=high-self.c['range_edge']*(high-low) if d==-1 else p['price']<=low+self.c['range_edge']*(high-low)
                        swept = b.high>=p['price']+self.c['tick_size'] if d==-1 else b.low<=p['price']-self.c['tick_size']
                        approached_from_inside = d*(self.bars[-2].close-p['price'])>=0
                        if edge and swept and approached_from_inside:
                            eligible.append(p)
                    if eligible:
                        p=min(eligible,key=lambda p:abs(b.close-p['price']))
                        op=self.start_op('failed_break',d,p,a,bounds,b)
                        if d*(b.close-p['price'])>=0:
                            op.update(returned_index=len(self.bars)-1,return_close=b.close,return_bar=b.json(),reason='已收回关键位，等待下一根同向强K线')
        self.update_pivots(b,a)
        state=self.regime['state']
        self.note={'warming':'等待足够的15分钟历史数据','transition':'处于过渡状态，暂不确认机会','range':'观察区间边缘的假突破与跟随','bull':'多头环境，等待向上突破及回踩','bear':'空头环境，等待向下突破及回踩'}[state]
        close=day_close(self.day,self.contract,self.calendar or {self.day})
        if close and dt(b.end)==close:
            self.expire_session(dt(self.known_at),historical=historical)

    def on_quote(self, price, stamp, known_at):
        self.known_at=known_at
        self.historical=False
        self.review_regime()
        a=atr(self.bars,self.c['atr_period'])
        for preview in self.previews.values():
            if preview['status']=='watching' and (self.regime['state']!='range' or abs(price-preview['level'])>2*preview['tolerance']):
                self.transition(preview,'expired','价格离开预警区域或环境改变，尚未形成收盘机会')
        if self.regime['state']=='range' and a and len(self.bars)>=self.c['range_bars']:
            recent=self.bars[-self.c['range_bars']:]
            low,high=min(b.low for b in recent),max(b.high for b in recent)
            for d,side in [(-1,'high'),(1,'low')]:
                if any(o['status'] in OCCUPYING and o['direction']==d for o in self.opportunities.values()):
                    continue
                choices=[]
                for level in self.levels():
                    if level.get('side')!=side or level['name'] not in ('前日高点','前日低点','EQH','EQL','区间上沿','区间下沿'):
                        continue
                    edge=level['price']>=high-self.c['range_edge']*(high-low) if d==-1 else level['price']<=low+self.c['range_edge']*(high-low)
                    if edge and abs(price-level['price'])<=self.c['zone_atr']*a:
                        choices.append(level)
                if choices:
                    level=min(choices,key=lambda x:abs(price-x['price']))
                    ident=stable_id(self.c['hash'],self.contract,'proximity',self.bars[-1].end,d,level['price'])
                    if ident not in self.previews:
                        # A new bar/nearby level is an updated proximity alert,
                        # not another independent directional opportunity.
                        for old in self.previews.values():
                            if old['status']=='watching' and old['direction']==d:
                                self.transition(old,'expired','同方向接近区域预警已更新，请查看最新预警；并非新增独立机会')
                        preview=dict(id=ident,contract=self.contract,setup='failed_break',direction=d,status='watching',preview_only=True,
                                     signal_bar=self.bars[-1].end,created_at=known_at,updated_at=known_at,level=level['price'],level_name=level['name'],
                                     level_known_at=level.get('known_at'),tolerance=self.c['zone_atr']*a,atr=a,bounds=(low,high),near=True,
                                     entry=None,stop=None,targets=[],rr=None,historical=False,regime=deepcopy(self.regime),source_bars=[self.bars[-1].json()],
                                     rule_version=self.c['version'],rule_hash=self.c['hash'],reason='接近区间边缘；等待越过关键位、收回及下一根跟随，尚未确认',
                                     **self.opportunity_metadata(stamp))
                        self.previews[ident]=preview
                        self.emit('preview',ident,preview['reason'],opportunity=deepcopy(preview),occurred_at=stamp)
        for op in list(self.opportunities.values()):
            if op.get('trading_day',self.trading_day(op['signal_bar']))!=self.trading_day(stamp):
                continue
            d=op['direction']
            if op['status']=='watching' and abs(price-op['level'])<=op['tolerance'] and not op['near']:
                op['near']=True
                self.emit('preview',op['id'],'价格接近候选区域，等待5分钟收盘确认',opportunity=deepcopy(op),occurred_at=stamp)
            elif op['status'] in ('confirmed','triggered'):
                if op.get('historical'):
                    continue
                if dt(stamp)<dt(op['confirmed_at']):
                    continue
                if d*(price-op['stop'])<=0:
                    self.transition(op,'invalidated','实时价格触及结构失效价；不代表账户成交或止损执行')
                elif op['status']=='confirmed' and d*(price-op['entry'])>=0:
                    op['touch_at']=stamp
                    op['touch_price']=price
                    op['regime_at_trigger']=deepcopy(self.regime)
                    self.transition(op,'triggered','实时价格已触价；不代表实际成交')

    def expire_session(self, at, reason='已收盘，未触发机会过期',historical=False):
        self.known_at=iso(at)
        self.historical=historical
        for op in list(self.opportunities.values())+list(self.previews.values()):
            if op['status'] in ACTIVE:
                self.transition(op,'expired',reason)

    def snapshot(self):
        return {'regime':self.regime,'note':self.note,'levels':self.levels(),
                'structures':self.structures[-200:], 'last_bar':self.bars[-1].end if self.bars else None,
                'opportunities':(list(self.opportunities.values())[-80:]+list(self.previews.values())[-20:])}
