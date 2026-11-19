"""Normalize the existing collector's WindPy results for the read-only context book."""
import json
import time
from pathlib import Path

from .domain import iso, now, quote_time
from .etf_context import (DAILY_FIELD, DERIVED_DAILY_FIELD, ETF_SAMPLES, ETF_FLOW_COMPONENTS,
                          ETF_FLOW_CORE_FIELDS, ETF_FLOW_WSQ_FIELDS, ETF_ROLLING_FIELD)


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
        self.rolling_retry_at = 0.0

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
            raw = await self.etf_snapshot(params['windcode'])
            rows = self.flow_rows(raw, params['windcode'].split(','))
            units = dict.fromkeys((*ETF_FLOW_COMPONENTS, ETF_ROLLING_FIELD), '元')
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

    async def etf_snapshot(self, codes):
        if time.monotonic() >= self.rolling_retry_at:
            try:
                return await self.request('wsq', codes, ETF_FLOW_WSQ_FIELDS, 'unit=1')
            except ValueError as exc:
                # Keep today's flow and speed available if the optional indicator is refused.
                message = str(exc).lower()
                if not any(reason in message for reason in ('invalid indicator', '无效指标', '权限',
                                                            'permission', '未完整返回')):
                    raise
                self.rolling_retry_at = time.monotonic() + 1800
        return await self.request('wsq', codes, ','.join(ETF_FLOW_CORE_FIELDS), 'unit=1')

    @staticmethod
    def flow_rows(raw, expected_codes):
        codes = raw['Codes']
        fields = [field.lower() for field in raw['Fields']]
        core = set(ETF_FLOW_CORE_FIELDS)
        if (len(codes) != len(set(codes)) or set(codes) != set(expected_codes)
                or len(fields) != len(set(fields)) or set(fields) not in (core, core | {ETF_ROLLING_FIELD})
                or len(raw['Data']) != len(fields)):
            raise ValueError('ETF实时资金流代码、字段或矩阵不完整')
        cols = dict(zip(fields, raw['Data']))
        aligned = {f: values for f, values in cols.items()
                   if isinstance(values, (list, tuple)) and len(values) == len(codes)}
        if not core.issubset(aligned):
            raise ValueError('ETF实时资金流代码、字段或矩阵不完整')
        return [dict(code=code, source='WindPy WSQ', **{f: values[i] for f, values in aligned.items()})
                for i, code in enumerate(codes)]

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

    async def etf_daily_flows(self, codes, days):
        """Historical full-session reference only. Never use WSD as an intraday observation."""
        raw = await self.request('wsd', ','.join(codes), 'mfd_inflow_m', days[0], days[-1],
                                 'unit=1;Fill=Blank')
        returned = raw['Codes']
        dates = [str(t)[:10] for t in raw['Times']]
        if (len(returned) != len(codes) or set(returned) != set(codes)
                or [f.lower() for f in raw['Fields']] != ['mfd_inflow_m']
                or len(set(dates)) != len(dates) or not set(days).issubset(dates)
                or any(d < days[0] or d > days[-1] for d in dates)):
            raise ValueError('ETF历史日频资金流的代码、字段或交易日不符')
        if len(dates) == 1:
            rows = self.daily_rows(raw, 'mfd_inflow_m', dates[0])
            return {r['code']: {dates[0]: r['value']} for r in rows}
        if len(raw['Data']) != len(returned) or any(len(col) != len(dates) for col in raw['Data']):
            raise ValueError('ETF历史日频资金流矩阵不完整')
        return {code: dict(zip(dates, col)) for code, col in zip(returned, raw['Data'])}

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
        from .etf_context import flow_records
        from .market_context import number
        flow=await self.call('get_fund_price_indicators',dict(windcode=codes))
        records=flow_records(flow['rows'],flow['units'],now(),number)
        audit['receipts'].append(dict(intraday_flow=flow['raw']))
        for code, record in records.items():
            audit['checks'].append(dict(code=code,check='WSQ_intraday_fields_and_clock',
                passed=record['value'] is not None,record=record))
        audit['flow_unit_note']='WSQ请求unit=1；字段完整性检查不等于独立单位或终端金额对照通过'
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
