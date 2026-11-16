"""Normalize the existing collector's WindPy results for the read-only context book."""
import json
from pathlib import Path

from .domain import iso, now, quote_time
from .etf_context import DAILY_FIELD, DERIVED_DAILY_FIELD, ETF_SAMPLES


def columns(raw):
    return {f.lower(): col for f, col in zip(raw['Fields'], raw['Data'])}


def cross_section(raw):
    cols = columns(raw)
    return {code: {f: col[i] if i < len(col) else None for f, col in cols.items()}
            for i, code in enumerate(raw['Codes'])}


class WindPyContextClient:
    source = 'WindPy'

    def __init__(self, desk, directory=None):
        self.desk = desk
        self.directory = Path(directory) if directory is not None else None

    async def request(self, method, *args):
        raw=await self.desk.context_query(method,*args)
        if self.directory is not None:
            self.directory.mkdir(parents=True,exist_ok=True)
            path=self.directory/('windpy-context-raw-'+now().strftime('%Y%m%d')+'.jsonl')
            with path.open('a',encoding='utf-8') as f:
                f.write(json.dumps(dict(observed_at=iso(now()),method=method,args=args,result=raw),
                                   ensure_ascii=False,allow_nan=False)+'\n')
        return raw

    async def call(self, tool, params):
        raw = None
        rows, units = [], {}
        if tool == 'get_index_price_indicators':
            raw = await self.request('wsq', params['windcode'],
                'rt_date,rt_time,rt_up_total,rt_down_total,rt_same_total')
            for code, r in cross_section(raw).items():
                stamp = quote_time(r)
                rows.append({'Wind代码': code, '最新交易日': str(int(r['rt_date'])) if r.get('rt_date') else '',
                             '交易时间': iso(stamp) if stamp else None,
                             '上涨家数': r.get('rt_up_total'), '下跌家数': r.get('rt_down_total'),
                             '平盘家数': r.get('rt_same_total')})
        elif tool == 'get_index_kline':
            raw = await self.request('wsi', params['windcode'], 'close,amt',
                params['begin_date']+' 09:00:00', params['end_date']+' 15:01:00', 'BarSize=5')
            cols = columns(raw)
            # Wind WSI returns the requested amt indicator under AMOUNT on the official SDK.
            amounts = cols.get('amt', cols.get('amount', []))
            closes = cols.get('close', [])
            rows = [dict(TIME=iso(stamp), MATCH=closes[i] if i<len(closes) else None,
                         TURNOVER=amounts[i] if i<len(amounts) else None)
                    for i, stamp in enumerate(raw['Times'])]
            units = {'TURNOVER': '元', 'source': 'WindPy WSI close,amt; BarSize=5'}
        elif tool == 'securities_get_price':
            # Same batch, only monitored contracts; this never feeds the signal engine.
            raw = await self.request('wsq', ','.join(params['windCodes']),
                                               'rt_date,rt_time,rt_pct_chg,rt_amt')
            for code, r in cross_section(raw).items():
                stamp = quote_time(r, code, self.desk.calendar_for(code), now())
                rows.append(dict(windcode=code, **{'交易时间': iso(stamp) if stamp else None,
                    '最新交易日': str(int(r['rt_date'])) if r.get('rt_date') else None,
                    '涨跌幅': r['rt_pct_chg']*100 if r.get('rt_pct_chg') is not None else None, '成交额': r.get('rt_amt'), 'indicator_units': {'成交额': '元'}}))
        elif tool == 'get_fund_price_indicators':
            # WSD supplies actual dates; never manufacture an intraday source clock for WSS/WSD data.
            day = max((d for d in self.desk.calendar if d <= now().date().isoformat()), default=None)
            if not day:
                raise ValueError('ETF资金流等待交易日历')
            raw = await self.request('wsd', params['windcode'], 'mfd_inflow_m', day, day, 'unit=1;Fill=Blank')
            rows = self.daily_rows(raw, 'mfd_inflow_m', day)
            units = {'mfd_inflow_m': '元'}
        elif tool == 'fund_get_listed_historical_price':
            day = max((d for d in self.desk.calendar if d <= params['tradeDate']), default=None)
            if not day:
                raise ValueError('ETF净申赎等待交易日历')
            before=max((d for d in self.desk.calendar if d<day),default=None)
            if not before:
                raise ValueError('净申赎估算等待前一交易日')
            raw=[]
            for code in params['windCodes']:
                sample=await self.request('wsd',code,'unit_total,nav',before,day,'unit=1;Fill=Blank')
                raw.append(sample)
                rows.append(self.subscription_estimate(sample,code,before,day))
            units = {DERIVED_DAILY_FIELD: '元'}
        else:
            raise ValueError('不支持的 WindPy 背景查询')
        return dict(provider='WindPy', rows=rows, units=units, raw=raw)

    @staticmethod
    def daily_rows(raw, field, requested_day):
        if len(raw['Times']) != 1 or not raw['Codes'] or len(set(raw['Codes']))!=len(raw['Codes']):
            raise ValueError('WindPy 日频日期或代码维度变化')
        actual = str(raw['Times'][0])[:10]
        if actual != requested_day or [f.lower() for f in raw['Fields']] != [field]:
            raise ValueError('WindPy 日频字段或实际数据日不匹配')
        # Official Mac WindData.set(bywhich=1) uses fields x codes when timeL==1.
        # Other SDK releases use codes x times for multi-code/single-field WSD.
        data=raw['Data'];n=len(raw['Codes'])
        if len(data)==1 and len(data[0])==n:
            values=data[0]
        elif len(data)==n and all(len(col)==1 for col in data):
            values=[col[0] for col in data]
        else:
            raise ValueError('WindPy 日频矩阵不完整')
        return [dict(code=code, data_date=actual, value=value, field=field, source='WindPy WSD',
                     frequency='daily_snapshot' if field=='mfd_inflow_m' else 'daily')
                for code, value in zip(raw['Codes'], values)]

    @staticmethod
    def subscription_estimate(raw,code,before,day):
        if raw['Codes']!=[code] or [str(t)[:10] for t in raw['Times']]!=[before,day]:
            raise ValueError('ETF份额净值实际代码或日期不匹配')
        cols=columns(raw)
        if set(cols)!={'unit_total','nav'} or any(len(c)!=2 for c in cols.values()):
            raise ValueError('ETF份额净值字段或矩阵不完整')
        previous,current=cols['unit_total'];nav=cols['nav'][1]
        valid=(previous is not None and current is not None and nav is not None
               and previous>0 and current>0 and nav>0)
        value=(current-previous)*nav if valid else None
        return dict(code=code,data_date=day,value=value,field=DERIVED_DAILY_FIELD,
                    source='WindPy WSD 份额变化×单位净值（估算）',frequency='daily',
                    previous_date=before,previous_shares=previous,shares=current,nav=nav)

    async def audit_etfs(self):
        """Explicit, bounded read-only audit through the same queue, never a second login."""
        day=max((d for d in self.desk.calendar if d<now().date().isoformat()),default=None)
        before=max((d for d in self.desk.calendar if day and d<day),default=None)
        if not before:
            raise ValueError('核验等待两个完整交易日')
        codes=','.join(s['code'] for s in ETF_SAMPLES.values())
        audit=dict(data_date=day,observed_at=iso(now()),checks=[],receipts=[])
        field='mfd_inflow_m'
        raw=await self.request('wsd',codes,field,day,day,'unit=1;Fill=Blank')
        scaled=await self.request('wsd',codes,field,day,day,'unit=10000;Fill=Blank')
        a={r['code']:r['value'] for r in self.daily_rows(raw,field,day)}
        b={r['code']:r['value'] for r in self.daily_rows(scaled,field,day)}
        audit['receipts'].append(dict(field=field,unit1=raw,unit10000=scaled))
        for code in a:
            x,y=a[code],b.get(code)
            ok=x is not None and y is not None and abs(x-y*10000)<=max(.01,abs(x)*1e-8)
            audit['checks'].append(dict(code=code,field=field,check='unit_scale_10000',passed=ok,value=x))
        previous=await self.request('wss',codes,'unit_total,nav','tradeDate='+before.replace('-','')+';unit=1')
        current=await self.request('wss',codes,'unit_total,nav','tradeDate='+day.replace('-','')+';unit=1')
        audit['receipts'].append(dict(previous_wss=previous,current_wss=current))
        prior,current=cross_section(previous),cross_section(current)
        for code in codes.split(','):
            try:
                raw=await self.request('wsd',code,'unit_total,nav',before,day,'unit=1;Fill=Blank')
                estimate=self.subscription_estimate(raw,code,before,day)
                audit['receipts'].append(dict(code=code,share_nav=raw,estimate=estimate))
                from math import isclose
                ok=all(isclose(x,y,rel_tol=1e-9,abs_tol=.01) for x,y in (
                    (estimate['previous_shares'],prior[code]['unit_total']),
                    (estimate['shares'],current[code]['unit_total']),
                    (estimate['nav'],current[code]['nav'])))
                audit['checks'].append(dict(code=code,check='WSD_vs_WSS_share_nav',passed=ok,estimate=estimate['value']))
            except (ValueError,KeyError,TypeError,IndexError) as exc:
                audit['checks'].append(dict(code=code,check='WSD_vs_WSS_share_nav',passed=False,error=str(exc)))
        return audit
