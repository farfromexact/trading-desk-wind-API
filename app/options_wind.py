"""Only called by the existing, single Wind worker. No trade APIs."""
from datetime import date, datetime
from math import isfinite

OPTION_FIELDS='rt_last,rt_bid1,rt_ask1,rt_bsize1,rt_asize1,rt_vol,rt_oi,rt_date,rt_time'
INDEX_UNDERLYINGS={'IM':'000852.SH','IF':'000300.SH','IH':'000016.SH'}
OPTION_ROOTS={'IM':'MO','IF':'IO','IH':'HO','AG':'AG','AU':'AU','SC':'SC'}
# Exchange option ticks, explicitly independent of the futures registry.
OPTION_TICKS={'IM':.2,'IF':.2,'IH':.2,'AG':.5,'AU':.02,'SC':.05}


def clean(value):
    if isinstance(value,(datetime,date)):
        return value.isoformat()
    if isinstance(value,float) and not isfinite(value):
        return None
    return value


def rows(result):
    if not result.Data:
        return []
    return [{str(f).lower():clean(col[i]) for f,col in zip(result.Fields,result.Data)} for i in range(len(result.Data[0]))]


def normalize_chain(raw, root, underlying):
    result=[]
    for row in raw:
        code=str(row.get('option_code','')).upper()
        if row.get('us_code','').upper()!=underlying.upper() or not code.startswith(OPTION_ROOTS[root]):
            continue
        style={'欧式':'european','美式':'american'}.get(row.get('exe_type'))
        side={'认购':1,'认沽':-1}.get(row.get('call_put'))
        # Wind 'expiredate' is DAYS TO EXPIRY, not an expiry date.
        expiry=str(row.get('last_tradedate',''))[:10]
        if not style or not side or len(expiry)!=10:
            continue
        try:
            strike=float(row['strike_price']); multiplier=float(row['multiplier'])
            date.fromisoformat(expiry)
            if not isfinite(strike*multiplier) or min(strike,multiplier)<=0:
                continue
        except (ValueError,KeyError,TypeError):
            continue
        result.append(dict(code=code,root=root,underlying=underlying,style=style,side=side,
                           strike=strike,expiry=expiry,multiplier=multiplier,tick=OPTION_TICKS[root],
                           name=row.get('option_name'),settlement=row.get('settle_method'),
                           metadata_source='Wind WSET optionchain',raw=row))
    return result


def dispatch(w, cmd, check):
    op=cmd['op']
    if op=='option_chain':
        r=check(w.wset('optionchain',f"date={cmd['day']};us_code={cmd['underlying']};option_var=all;call_put=all"),'期权链')
        return {'contracts':normalize_chain(rows(r),cmd['root'],cmd['underlying']),'fields':r.Fields}
    if op=='option_daily':
        r=check(w.wsd(cmd['underlying'],'close',cmd['start'],cmd['end'],'PriceAdj=U;Days=Trading;Fill=Blank'),'标的日线')
        return [{'day':str(t)[:10],'close':clean(r.Data[0][i])} for i,t in enumerate(r.Times)]
    if op=='option_vendor':
        r=check(w.wss(','.join(cmd['codes']),'us_impliedvol,delta,gamma',f"tradeDate={cmd['day'].replace('-','')}"),'Wind日终Greeks')
        return dict(zip(r.Codes,rows(r)))
    raise ValueError('未知期权请求')
