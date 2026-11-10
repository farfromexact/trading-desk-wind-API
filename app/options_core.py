"""Pure option analysis and candidate construction. No orders or inferred fills."""
from collections import Counter
from copy import deepcopy
from datetime import timedelta
from math import exp, isfinite, log, sqrt
from statistics import median, stdev

from .domain import dt, iso, quote_time, stable_id
from .markets import at_time, day_close, trading_day
from .options_pricing import MODEL_VERSION, greeks, implied_vol, price


def finite(value, default=None):
    try:
        v=float(value)
        return v if isfinite(v) else default
    except (TypeError,ValueError):
        return default


def trading_days_left(expiry, at, root, calendar):
    today=trading_day(at,root,calendar)
    if not calendar or expiry>max(calendar):
        return None
    return sum(today<day<=expiry for day in calendar)


def quote_issue(q, at, root, calendar, config, book=True):
    stamp=quote_time(q,root,calendar,at)
    if stamp is None:
        return '缺少行情时间'
    age=(dt(at)-stamp).total_seconds()
    if age < -3 or age > config['fresh_seconds']:
        return '行情时间过期或超前'
    if book:
        bid,ask=finite(q.get('rt_bid1'),0),finite(q.get('rt_ask1'),0)
        if bid<=0 or ask<=0:
            return '缺少有效双边盘口'
        if bid>ask:
            return '买卖价格交叉'
        if min(finite(q.get('rt_bsize1'),0),finite(q.get('rt_asize1'),0))<1:
            return '买卖挂单量不足1手'
        if (ask-bid)/((ask+bid)/2)>config['maximum_relative_spread']:
            return '买卖价差过宽'
        for field in ('rt_bid1','rt_ask1','rt_bsize1','rt_asize1'):
            seen=q.get('_field_seen',{}).get(field)
            if seen and (dt(at)-dt(seen)).total_seconds()>config['fresh_seconds']:
                return '盘口字段尚未同步更新'
    elif finite(q.get('rt_last'),0)<=0:
        return '标的价格无效'
    return None


def select_pool(contracts, spot, count=2, strikes=21):
    result=[]
    for expiry in sorted({x['expiry'] for x in contracts})[:count]:
        group=[x for x in contracts if x['expiry']==expiry]
        ks=sorted({x['strike'] for x in group},key=lambda k:abs(k-spot))[:strikes]
        result.extend(x for x in group if x['strike'] in ks)
    return sorted(result,key=lambda x:(x['expiry'],x['strike'],-x['side']))


def realized_vol(daily, n, at, root, calendar):
    today=trading_day(at,root,calendar)
    expected=sorted(d for d in calendar if d<today)[-(n+1):]
    data={x['day']:finite(x.get('close')) for x in daily if x['day']<today}
    if len(expected)!=n+1 or any(not data.get(d) or data[d]<=0 for d in expected):
        return None
    returns=[log(data[b]/data[a]) for a,b in zip(expected,expected[1:])]
    return stdev(returns)*sqrt(252)


def delta_iv(rows, side, target=.25):
    values=sorted((abs(x['delta']),x['iv']) for x in rows if x.get('iv') is not None and x['side']==side)
    for (a,av),(b,bv) in zip(values,values[1:]):
        if a<=target<=b and b>a:
            return av+(bv-av)*(target-a)/(b-a)
    return None


def analyse(root, contract, contracts, quotes, underlying_quote, futures_quote, daily, calendar, at, phase, config):
    """Compute a synchronized snapshot; after hours it is descriptive only."""
    at=dt(at)
    uq=underlying_quote
    stamp=quote_time(uq,root,calendar,at)
    # Allow inspection of the last synchronized snapshot outside trading hours.
    valuation_at=stamp if phase!='交易中' and stamp and stamp<=at else at
    spot=finite(uq.get('rt_last'),0)
    underlying_error=quote_issue(uq,valuation_at,root,calendar,config,False)
    f_stamp=quote_time(futures_quote,root,calendar,at)
    futures_error=quote_issue(futures_quote,valuation_at,root,calendar,config,False)
    basis=None
    if not underlying_error and not futures_error and f_stamp and stamp and abs((f_stamp-stamp).total_seconds())<=config['max_skew_seconds']:
        basis=finite(futures_quote.get('rt_last'),0)-spot
    rows=[]
    for meta in contracts:
        q=quotes.get(meta['code'],{})
        qs=quote_time(q,root,calendar,at)
        reason=underlying_error or quote_issue(q,valuation_at,root,calendar,config)
        if not reason and abs((qs-stamp).total_seconds())>config['max_skew_seconds']:
            reason='期权与标的时间不同步'
        expiry=at_time(meta['expiry'],'15:00')
        t=(expiry-valuation_at).total_seconds()/(365*86400)
        if t<=0:
            reason='已经到期'
        bid,ask=finite(q.get('rt_bid1')),finite(q.get('rt_ask1'))
        row=dict(meta,quote=q,quote_time=iso(qs) if qs else None,iv=None,delta=None,gamma=None,vega=None,theta=None,
                 reason=reason,mid=(bid+ask)/2 if bid and ask and bid<=ask else None,
                 trading_days=trading_days_left(meta['expiry'],at,root,calendar),years=max(0,t))
        rows.append(row)
    expiry_context=[]
    for expiry in sorted({x['expiry'] for x in rows}):
        group=[x for x in rows if x['expiry']==expiry]
        eligible=[x for x in group if not x['reason']]
        fwd=spot if root in ('AG','AU','SC') and not underlying_error else None
        if root in ('IM','IF','IH'):
            pairs=[]
            for strike in {x['strike'] for x in eligible}:
                cp={x['side']:x for x in eligible if x['strike']==strike}
                if len(cp)==2 and spot>0 and abs(strike/spot-1)<.15:
                    pairs.append(strike+exp(config['risk_free_rate']*cp[1]['years'])*(cp[1]['mid']-cp[-1]['mid']))
            if len(pairs)>=3:
                fwd=median(pairs)
        for row in eligible:
            if not fwd or fwd<=0:
                row['reason']='不足3对同步认购认沽，无法校准远期'
                continue
            try:
                row['forward']=fwd
                row['iv']=implied_vol(row['mid'],fwd,row['strike'],row['years'],config['risk_free_rate'],row['side'],row['style'],max(1e-7,row['tick']*.001))
                row.update(greeks(fwd,row['strike'],row['years'],row['iv'],config['risk_free_rate'],row['side'],row['style']))
                row['model']=MODEL_VERSION
            except (ValueError,RuntimeError,OverflowError,ZeroDivisionError) as exc:
                row['reason']='定价失败：'+str(exc)[:120]
                row['iv']=None
        good=[x for x in group if x['iv'] is not None and not x['reason']]
        atm=[]
        for side in (1,-1):
            options=[x for x in good if x['side']==side]
            if options:
                atm.append(min(options,key=lambda x:abs(x['strike']-fwd))['iv'])
        call25,put25=delta_iv(good,1),delta_iv(good,-1)
        expiry_context.append(dict(expiry=expiry,forward=fwd,atm_iv=sum(atm)/len(atm) if len(atm)==2 else None,
                                   put_minus_call_25d=put25-call25 if call25 is not None and put25 is not None else None,
                                   valid=len(good),total=len(group),trading_days=group[0]['trading_days']))
    reasons=Counter(x['reason'] for x in rows if x['reason'])
    good=[x for x in rows if x['iv'] is not None and not x['reason']]
    rv5=realized_vol(daily,5,at,root,calendar); rv20=realized_vol(daily,20,at,root,calendar)
    context=dict(root=root,contract=contract,underlying=contracts[0]['underlying'] if contracts else None,
                 generated_at=iso(at),valuation_at=iso(valuation_at),market_time=iso(stamp) if stamp else None,
                 received_ms=uq.get('received_ms'),phase=phase,status='实时' if phase=='交易中' and good and basis is not None else (phase+'快照' if phase!='交易中' else '数据不足'),
                 ready=bool(phase=='交易中' and good and basis is not None),spot=spot,futures_price=finite(futures_quote.get('rt_last')),basis=basis,
                 basis_note='指数行权价≠期货价格；情景保持当前期现及期限基差' if root in ('IM','IF','IH') else '仅匹配当前真实月份期货',
                 expiries=expiry_context,rv5=rv5,rv20=rv20,rv_basis='同一标的已完成日收盘对数收益，样本标准差×√252',
                 qualified=len(good),total=len(rows),excluded=dict(reasons),notes=[],model_version=MODEL_VERSION,
                 rate=config['risk_free_rate'],rate_basis=config['rate_basis'],rule_version=config['version'])
    if not contracts:
        context['notes'].append('等待匹配期权链')
    if basis is None:
        context['notes'].append('期货与期权标的尚无同步有效价格，暂停候选')
    required=max(config['minimum_trading_days'],config['holding_days']+config['expiry_buffer_days'])
    if expiry_context and not any(x['trading_days'] is not None and x['trading_days']>=required for x in expiry_context):
        context['notes'].append(f'当前到期日不足{required}个交易日，不生成{config["holding_days"]}日持有候选')
    if len(expiry_context)==1:
        context['notes'].append('匹配月份只有一个到期日，期限结构不外推')
    if rv5 is None or rv20 is None:
        context['notes'].append('完整日线样本不足，缺失RV不填零')
    if expiry_context and expiry_context[0]['atm_iv'] and rv20:
        ratio=expiry_context[0]['atm_iv']/rv20
        context['iv_rv_ratio']=ratio
        context['iv_rv_state']='IV/RV较高' if ratio>=config['iv_rv_observation_ratio'] else '其他IV/RV状态'
        if ratio>=config['iv_rv_observation_ratio']:
            context['notes'].append('隐含波动高于过去实际波动；观察权利金成本，不据此自动卖出')
    return context,rows


def horizon_dates(at, root, calendar):
    today=trading_day(at,root,calendar)
    days=sorted(d for d in calendar if d>=today)
    return {n:day_close(days[n],root,calendar) for n in (0,1,3,5) if len(days)>n and day_close(days[n],root,calendar)}


def build_candidate(kind, legs, op, context, at, root, calendar, config, holding_days):
    multiplier=legs[0]['multiplier']
    entry=sum(x['quantity']*(x['quote']['rt_ask1'] if x['quantity']>0 else x['quote']['rt_bid1'])*multiplier for x in legs)
    slip=sum(abs(x['quantity'])*x['tick']*multiplier*config['slippage_ticks_each_side'] for x in legs)
    fee=config['fee_per_contract_each_side'].get(root)
    fee_total=None if fee is None else sum(abs(x['quantity']) for x in legs)*fee*2
    stress_entry=entry+slip
    def payoff(s):
        return sum(x['quantity']*max(x['side']*(s-x['strike']),0)*multiplier for x in legs)-stress_entry-slip-(fee_total or 0)
    ks=[x['strike'] for x in legs]
    ps=[payoff(x) for x in [0,*ks,max(ks)*2]]
    max_loss=max(0,-min(ps))
    unlimited=sum(x['quantity'] for x in legs if x['side']==1)>0
    max_profit=None if unlimited else max(ps)
    if max_loss<=0 or max_profit is not None and max_profit<=0:
        return None
    f0=context['futures_price']
    targets=[x['price'] for x in op['targets']]
    prices={'失效':op['stop'],'横盘':f0,'T1':targets[0],'T2':targets[1] if len(targets)>1 else targets[0]}
    scenarios=[]
    for days,stamp in horizon_dates(at,root,calendar).items():
        if stamp<dt(at):
            continue
        for iv_change in (-.2,0.,.2):
            values={}
            for label,future_price in prices.items():
                value=0
                for leg in legs:
                    f=leg['forward']+future_price-f0
                    t=(at_time(leg['expiry'],'15:00')-stamp).total_seconds()/(365*86400)
                    if f<=0 or t<0:
                        value=None; break
                    theoretical=price(f,leg['strike'],t,leg['iv']*(1+iv_change),config['risk_free_rate'],leg['side'],leg['style'])
                    # Exit cost uses half current spread, not a guaranteed future book.
                    half=(leg['quote']['rt_ask1']-leg['quote']['rt_bid1'])/2
                    exit_price=max(0,theoretical-half) if leg['quantity']>0 else theoretical+half
                    value+=leg['quantity']*exit_price*multiplier
                pnl=None if value is None else value-stress_entry-slip-(fee_total or 0)
                values[label]=round(pnl,2) if pnl is not None else None
            scenarios.append(dict(days=days,at=iso(stamp),iv_change=iv_change,pnl=values))
    expiry=legs[0]['expiry']
    candidate=dict(id=stable_id(config['version'],op['id'],kind,expiry,holding_days),root=root,opportunity_id=op['id'],contract=op['contract'],
                   kind=kind,expiry=expiry,holding_days=holding_days,legs=legs,generated_at=iso(at),status=op['status'],
                   entry_cost=round(entry,2),stress_entry_cost=round(stress_entry,2),roundtrip_slippage=round(slip*2,2),roundtrip_fee=fee_total,
                   fee_known=fee is not None,max_loss=round(max_loss,2),max_profit=round(max_profit,2) if max_profit is not None else None,
                   underlying_entry=op['entry'],underlying_stop=op['stop'],targets=op['targets'],scenarios=scenarios,
                   basis=context['basis'],basis_note=context['basis_note'],futures_price=f0,model_version=MODEL_VERSION,
                   note='当前盘口参考成本，触价时重新核对；未记录实际成交',
                   risk_note=('完整组合到期损失上限不保证持有全过程；美式提前行权可能形成期货头寸' if len(legs)>1 else '主动行权后可能形成期货头寸，权利金上限不覆盖期货头寸风险') if legs[0]['style']=='american' else '按完整组合计算到期损失上限',
                   cost_note=f'含买卖价差及每腿每次{config["slippage_ticks_each_side"]:g}跳压力滑点'+('、配置手续费' if fee is not None else '；未配置手续费，尚未扣费'),
                   suitable={'long':'适合较快且幅度足够的方向移动；横盘、时间流逝和IV回落可能抵消方向收益',
                             'debit_vertical':'适合有明确目标的方向观点；降低净权利金和波动敞口，同时限制到期收益上限',
                             'credit_vertical':'适合结构失效位保持有效的温和移动或横盘情景；收益有限，仍须承担跳空及两腿执行风险'}[kind])
    candidate['greeks']={g:sum(x['quantity']*x[g]*multiplier for x in legs) for g in ('delta','gamma','vega','theta')}
    low=max(.001,min(context['spot'],*ks)*.9)
    high=max(context['spot'],*ks)*1.1
    candidate['payoff']=[dict(underlying=s,pnl=round(payoff(s),2)) for s in sorted(set([low+(high-low)*i/60 for i in range(61)]+ks))]
    return candidate


def futures_reference(op, quote, config):
    """Comparable one-contract scenarios, never imply a stop is guaranteed."""
    root=op['root']; direction=op['direction']; mult=config['futures_multiplier'][root]
    entry=finite(quote.get('rt_ask1' if direction==1 else 'rt_bid1'))
    bid,ask=finite(quote.get('rt_bid1')),finite(quote.get('rt_ask1'))
    if not entry or not bid or not ask or bid<=0 or ask<bid:
        return dict(ready=False,note='期货双边盘口无效，暂不计算工具对照')
    from .markets import market
    fee=config['futures_fee_per_contract_each_side'].get(root)
    cost=2*market(root)['tick_size']*config['slippage_ticks_each_side']*mult+(fee or 0)*2
    prices={'失效':op['stop'],'横盘':finite(quote.get('rt_last'),entry),
            'T1':op['targets'][0]['price'],'T2':op['targets'][-1]['price']}
    # Exit bid/ask spread is held constant, just as in the option scenario.
    half=(ask-bid)/2
    return dict(ready=True,entry=entry,multiplier=mult,fee_known=fee is not None,
                pnl={key:round((direction*(value-direction*half-entry))*mult-cost,2) for key,value in prices.items()},
                note='每手期货价格情景，保持当前买卖价差并计压力滑点；'+('含配置手续费' if fee is not None else '手续费未配置，未扣费')+'。结构止损不是保证成交价，保证金不是最大损失。')


def candidates(op, context, rows, at, calendar, config, holding_days=None):
    holding_days=config['holding_days'] if holding_days is None else holding_days
    if op.get('status') not in ('confirmed','triggered') or not op.get('entry'):
        return [],['等待已确认的期货方向信号']
    if not context.get('ready'):
        return [],['期权数据未就绪或当前休市，暂停生成候选']
    root=context['root']; d=op['direction']
    minimum=max(config['minimum_trading_days'],holding_days+config['expiry_buffer_days'])
    good=[x for x in rows if not x['reason'] and x['iv'] is not None and x['trading_days'] is not None and x['trading_days']>=minimum]
    outputs=[]; reasons=[]
    offset=context['spot']-context['futures_price']
    for expiry in sorted({x['expiry'] for x in good})[:config['expiry_count']]:
        group=[x for x in good if x['expiry']==expiry]
        longs=[x for x in group if x['side']==d and .2<=abs(x['delta'])<=.8]
        if not longs:
            reasons.append(expiry+' 缺少合格平值买方盘口'); continue
        buy=min(longs,key=lambda x:(abs(abs(x['delta'])-.5),(x['quote']['rt_ask1']-x['quote']['rt_bid1'])/x['mid']))
        sets=[('long',[dict(buy,quantity=1)])]
        target=op['targets'][0]['price']+offset
        shorts=[x for x in group if x['side']==d and d*(x['strike']-buy['strike'])>0 and d*(x['strike']-target)>=0]
        if shorts:
            sell=min(shorts,key=lambda x:d*(x['strike']-target))
            sets.append(('debit_vertical',[dict(buy,quantity=1),dict(sell,quantity=-1)]))
        else:
            reasons.append(expiry+' 目标之外缺少合格价差卖出腿')
        stop=op['stop']+offset
        sells=[x for x in group if x['side']==-d and d*(stop-x['strike'])>0]
        if sells:
            sell=min(sells,key=lambda x:abs(stop-x['strike']))
            protections=[x for x in group if x['side']==-d and d*(sell['strike']-x['strike'])>0]
            if protections:
                protect=min(protections,key=lambda x:abs(sell['strike']-x['strike']))
                sets.append(('credit_vertical',[dict(sell,quantity=-1),dict(protect,quantity=1)]))
            else:
                reasons.append(expiry+' 卖出腿更外侧缺少合格保护腿')
        else:
            reasons.append(expiry+' 结构失效位外缺少合格价差卖出腿')
        for kind,legs in sets:
            if len({(x['underlying'],x['expiry'],x['multiplier']) for x in legs})!=1:
                continue
            q_times=[dt(x['quote_time']) for x in legs]
            if (max(q_times)-min(q_times)).total_seconds()>config['max_skew_seconds']:
                reasons.append(expiry+' 组合两腿时间不同步'); continue
            built=build_candidate(kind,legs,op,context,at,root,calendar,config,holding_days)
            if built:
                outputs.append(built)
    if not outputs:
        reasons.append(f'没有同时满足期限≥{minimum}个交易日、盘口和结构位置的组合')
    return outputs,list(dict.fromkeys(reasons))
