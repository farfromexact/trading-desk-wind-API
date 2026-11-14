"""Strict ETF context parsing: keep secondary-market flow and daily subscriptions separate."""
from __future__ import annotations

import re
from datetime import date

from .domain import dt, iso

ETF_SAMPLES = {
    'IM': {'code': '512100.SH', 'name': '中证1000ETF', 'manager': '南方'},
    'IF': {'code': '510300.SH', 'name': '沪深300ETF', 'manager': '华泰柏瑞'},
    'IH': {'code': '510050.SH', 'name': '上证50ETF', 'manager': '华夏'},
}
ETF_CODES = {x['code'] for x in ETF_SAMPLES.values()}
ETF_FLOW_FIELDS = '最新交易日,交易时间,中文简称,当日主力净流入额'
DAILY_FIELD = 'f_mf_netsubamt'
DAILY_HEADER = '申赎净流入额（元）'


def data_day(value):
    text = str(value or '')
    if re.fullmatch(r'\d{8}', text):
        text = f'{text[:4]}-{text[4:6]}-{text[6:]}'
    return date.fromisoformat(text).isoformat()


def daily_etf_rows(envelope, requested_day):
    """Parse the official single-day tool's declared date and exact table, never prose guesses."""
    if envelope.get('ok') is False or envelope.get('isError'):
        raise ValueError('ETF日频查询失败，请检查权限或额度')
    requested_day = data_day(requested_day)
    for block in envelope.get('content', []):
        if block.get('type') != 'text':
            continue
        text = block.get('text', '')
        # This tool declares one actual trading date for the whole returned table.
        dates = set(re.findall(r'已返回\s*\d+\s*只基金\s*(\d{4}-\d{2}-\d{2})\s*的行情数据', text))
        if len(dates) != 1:
            continue
        actual_day = data_day(dates.pop())
        if actual_day > requested_day:
            raise ValueError('ETF日频返回日期晚于查询截止日')
        lines = [line.strip() for line in text.splitlines() if line.strip().startswith('|')]
        if not lines:
            continue
        split = lambda line: [x.strip() for x in line.strip('|').split('|')]
        headers = split(lines[0])
        if not {'基金代码', '基金简称', DAILY_HEADER}.issubset(headers) or len(set(headers)) != len(headers):
            raise ValueError('ETF日频字段或金额单位未获确认')
        rows, seen = [], set()
        for line in lines[1:]:
            cells = split(line)
            if all(re.fullmatch(r'[:\- ]+', c) for c in cells):
                continue
            if len(cells) != len(headers):
                raise ValueError('ETF日频表格列数变化')
            row = dict(zip(headers, cells))
            code = row['基金代码']
            if code not in ETF_CODES or code in seen:
                raise ValueError('ETF日频代码不匹配或重复')
            seen.add(code)
            rows.append(dict(code=code, name=row['基金简称'], data_date=actual_day,
                             value=row[DAILY_HEADER], field=DAILY_FIELD))
        if rows:
            return rows, {DAILY_FIELD: '元'}
    raise ValueError('ETF日频缺少可验证的实际数据日或申赎金额表格')


def flow_records(rows, units, received_at, number):
    result = {}
    received_at = dt(received_at)
    unit_ok = units.get('当日主力净流入额') == '元'
    for row in rows:
        code = row.get('Wind代码') or row.get('windcode')
        if code not in ETF_CODES:
            continue
        reason = ''
        try:
            stamp = dt(row['交易时间'])
            trading_day = data_day(row['最新交易日'])
            if stamp > received_at or stamp.date().isoformat() != trading_day:
                raise ValueError()
            stamp = iso(stamp)
        except (KeyError, ValueError, TypeError):
            stamp, trading_day = None, None
            reason = '源时间缺失或与交易日不一致'
        value = number(row.get('当日主力净流入额')) if unit_ok and not reason else None
        if not unit_ok:
            reason = '金额单位未确认'
        elif value is None and not reason:
            reason = '资金流暂缺'
        result[code] = dict(code=code, value=value, time=stamp, trading_day=trading_day,
                            observed_at=iso(received_at), field='当日主力净流入额', unit='元',
                            frequency='intraday_cumulative', source='Wind MCP基金行情', reason=reason)
    return result


def daily_records(rows, units, received_at, number):
    result = {}
    for row in rows:
        code = row.get('code')
        if code not in ETF_CODES:
            continue
        day = data_day(row['data_date'])
        if day >= dt(received_at).date().isoformat():
            raise ValueError('日频申赎只能采用当前日期之前的数据日')
        value = number(row.get('value')) if units.get(DAILY_FIELD) == '元' else None
        result[code] = dict(code=code, value=value, data_date=day, observed_at=iso(received_at),
                            published_at=None, field=DAILY_FIELD, unit='元', frequency='daily',
                            source='Wind MCP日频申赎净流入额', reason='' if value is not None else '日频申赎暂缺')
    return result
