"""Optional read-only context, using the existing WindPy collector on both platforms."""
from __future__ import annotations

import asyncio
import json
import math
import os
import statistics
import time
from datetime import timedelta
from pathlib import Path

from .wind_context_client import WindPyContextClient
from .domain import dt, iso, now
from .etf_context import ETF_CODES, ETF_SAMPLES, ETF_FLOW_FIELDS, DAILY_FIELD, daily_etf_rows, flow_records, daily_records
from .etf_flow_history import BASELINE_DAYS, EtfFlowHistory

REFRESH_SECONDS = 30
ETF_FLOW_SECONDS = 15

INDICES = {'IM': '000852.SH', 'IF': '000300.SH', 'IH': '000016.SH'}
COUNTS = {'000016.SH': 50, '000300.SH': 300, '000852.SH': 1000, '000688.SH': None, '399006.SZ': None}
NAMES = {'000016.SH': '上证50', '000300.SH': '沪深300', '000852.SH': '中证1000', '000688.SH': '科创板', '399006.SZ': '创业板'}
FIELDS = '最新交易日,交易时间,中文简称,最新成交价,涨跌幅,成交额,上涨家数,下跌家数,平盘家数'


def cash_bar_labels(day):
    start = dt(day+'T00:00:00+08:00')
    return [iso(start+timedelta(minutes=m)) for m in
            [*range(575, 691, 5), *range(785, 901, 5)]]


def completed_cash_bar(day, at):
    """Schedule against eligible labels, not the wall-clock five-minute bucket."""
    return max((t for t in cash_bar_labels(day)
                if dt(t)+timedelta(seconds=65) <= dt(at)), default=None)


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def cash_session(stamp):
    stamp = dt(stamp)
    minute = stamp.hour * 60 + stamp.minute
    if 570 <= minute <= 690:
        return stamp.date().isoformat(), 'am'
    if 780 <= minute <= 900:
        return stamp.date().isoformat(), 'pm'
    return None


def etf_session(at, calendar):
    if not calendar:
        return '等待日历'
    if at.date().isoformat() not in calendar:
        return '非交易日'
    if cash_session(at):
        return '交易中'
    clock = at.strftime('%H:%M')
    if clock < '09:30':
        return '盘前'
    return '午休' if clock < '13:00' else '已收盘'


def mcp_rows(envelope):
    if envelope.get('provider') == 'WindPy':
        return envelope['rows'], envelope['units']
    if envelope.get('ok') is False or envelope.get('isError'):
        raise ValueError('Wind 请求失败，请检查本机登录、权限或额度')
    for block in envelope.get('content', []):
        if block.get('type') != 'text':
            continue
        try:
            body = json.loads(block['text'])
        except (ValueError, TypeError):
            continue
        if body.get('error'):
            raise ValueError('Wind 未返回可用数据')
        data = body.get('data')
        if isinstance(data, list):
            return data, {}
        if isinstance(data, dict) and isinstance(data.get('rows'), list):
            names = [c['name'] for c in data.get('columns', [])]
            if not names:
                raise ValueError('Wind 返回缺少列定义')
            return [dict(zip(names, row)) for row in data['rows']], data.get('unit', {})
    raise ValueError('Wind 未返回受支持的结构化数据')


class ContextBook:
    def __init__(self, data=None):
        data = data or {}
        self.provider=data.get('provider','historical_import')
        self.bars = data.get('bars', {})
        self.snapshots = data.get('snapshots', {})
        self.cross = data.get('cross', [])
        self.etf_flow = data.get('etf_flow', {})
        self.flow_history = EtfFlowHistory()
        self.etf_closes = data.get('etf_closes', {})
        self.etf_daily = data.get('etf_daily', {})
        self.etf_daily_checked_at = data.get('etf_daily_checked_at')
        self.updated_at = data.get('updated_at')

    def json(self):
        return dict(version=5, provider=self.provider, bars=self.bars, snapshots=self.snapshots, cross=self.cross,
                    etf_flow=self.etf_flow, etf_daily=self.etf_daily, etf_daily_checked_at=self.etf_daily_checked_at,
                    etf_closes=self.etf_closes, updated_at=self.updated_at)

    def add_etf_flow(self, rows, units, received_at):
        incoming = flow_records(rows, units, received_at, number)
        for code, row in incoming.items():
            self.flow_history.record(row)
            old = self.etf_flow.get(code, {})
            if row.get('frequency')=='daily_snapshot':
                if (row['value'] is not None or old.get('value') is None) and row['data_date']>=old.get('data_date',''):
                    self.etf_flow[code]=row
                continue
            if old.get('time') and old.get('value') is not None and (not row['time'] or row['value'] is None):
                continue
            if row['time'] and old.get('time') and dt(row['time']) < dt(old['time']):
                continue
            self.etf_flow[code] = row
        return incoming

    def add_etf_daily(self, rows, units, received_at):
        incoming = daily_records(rows, units, received_at, number)
        for code, row in incoming.items():
            if self.etf_daily.get(code, {}).get('value') is not None and row['value'] is None:
                continue
            if row['data_date'] < self.etf_daily.get(code, {}).get('data_date', ''):
                continue
            self.etf_daily[code] = row
        if len(incoming) == len(ETF_SAMPLES) and all(x['value'] is not None for x in incoming.values()):
            self.etf_daily_checked_at = iso(received_at)
        return incoming

    def add_snapshots(self, rows, units):
        for row in rows:
            code = row.get('Wind代码') or row.get('windcode')
            if code not in COUNTS:
                continue
            try:
                stamp = iso(row['交易时间'])
            except (ValueError, KeyError, TypeError):
                continue
            counts = [number(row.get(k)) for k in ('上涨家数', '下跌家数', '平盘家数')]
            expected = COUNTS[code]
            counts_valid = all(v is not None and v >= 0 and v.is_integer() for v in counts)
            # User-selected board aggregates have a vendor-reported universe, not a fixed
            # 50/100-constituent index. Never label them independently coverage-verified.
            valid = counts_valid and (expected is None or sum(counts) == expected)
            value = dict(code=code, time=stamp, trading_day=str(row.get('最新交易日', '')),
                         breadth=counts[0]/sum(counts[:2])*100 if valid and sum(counts[:2]) else None,
                         count_total=sum(counts) if counts_valid else None,
                         scope_ok=valid if expected is not None else None, counts=counts, expected=expected,
                         coverage='index_count_checked' if expected is not None else 'vendor_reported',
                         reason=row.get('reason') or (f'家数合计{sum(counts):g} / 样本{expected}，覆盖不匹配'
                         if expected is not None and counts_valid and not valid else None))
            self.snapshots.setdefault(code, {})[stamp] = value
            self.snapshots[code] = dict(sorted(self.snapshots[code].items())[-500:])

    def add_bars(self, code, rows, units, received_at):
        # This endpoint declares TURNOVER in yuan. Unknown units are never guessed.
        amount_ok = units.get('TURNOVER') == '元' or units.get('TURNOVER 单位：') == '元'
        for row in rows:
            close, amount = number(row.get('MATCH')), number(row.get('TURNOVER'))
            try:
                stamp = dt(row['TIME'])
            except (ValueError, KeyError, TypeError):
                continue
            # Native bars may still be forming; use the desk's conservative label + 65s rule.
            if (stamp + timedelta(seconds=65) > dt(received_at) or not cash_session(stamp)
                    or stamp.minute % 5 or stamp.second or close is None or close <= 0):
                continue
            self.bars.setdefault(code, {})[iso(stamp)] = dict(
                time=iso(stamp), close=close, amount=amount if amount_ok and amount is not None and amount >= 0 else None,
                received_at=iso(received_at), source=units.get('source', '历史导入5分钟线'))
        self.bars[code] = dict(sorted(self.bars.get(code, {}).items())[-2400:])

    def window(self, code, end, minutes=15):
        rows = self.bars.get(code, {})
        stamps = [iso(dt(end)-timedelta(minutes=i*5)) for i in range(minutes//5, -1, -1)]
        values = [rows.get(t) for t in stamps]
        if any(v is None for v in values) or len({cash_session(t) for t in stamps}) != 1:
            return None
        if cash_session(stamps[0]) is None:
            return None
        result = (values[-1]['close']/values[0]['close']-1)*100
        amounts = [v.get('amount') for v in values[1:]]
        return dict(return_pct=result, amount=sum(amounts) if all(v is not None for v in amounts) else None)

    def history_days(self, code, days):
        rows = self.bars.get(code, {})
        return [day for day in days if all(
            rows.get(t, {}).get('amount') is not None for t in cash_bar_labels(day))]

    def breadth_view(self, code, at):
        rows = self.snapshots.get(code, {})
        available = sorted(t for t in rows if dt(t) <= at)
        result = dict(code=code, name=NAMES[code], value=None, change_pp=None,
                      time=None, reason='等待聚合家数', expected=COUNTS[code], scope='index' if COUNTS[code] is not None else 'board',
                      counts=None, trend=[], window_minutes=5)
        if not available:
            return result
        latest = rows[available[-1]]
        stamp = dt(latest['time'])
        result.update(value=latest['breadth'], time=latest['time'], counts=latest['counts'],
                      count_total=latest['count_total'], expected=latest.get('expected',COUNTS[code]), coverage=latest.get('coverage'), reason='' if latest['breadth'] is not None
                      else latest.get('reason') or '聚合家数覆盖不匹配或无涨跌样本')
        target = stamp-timedelta(minutes=5)
        candidates = [t for t in available if target-timedelta(seconds=90) <= dt(t) <= target
                      and cash_session(t) == cash_session(stamp) and cash_session(t) is not None]
        if candidates and latest['breadth'] is not None:
            prior = rows[candidates[-1]]
            if prior['breadth'] is not None:
                result.update(change_pp=latest['breadth']-prior['breadth'], previous_time=prior['time'])
        # Source clocks, not refresh clocks. Re-reading a closing snapshot adds no trend points.
        result['trend'] = [dict(time=t, value=rows[t]['breadth']) for t in available
                           if stamp-timedelta(minutes=30) <= dt(t)
                           and cash_session(t) == cash_session(stamp) and cash_session(t) is not None]
        return result

    def rotation_view(self, at, breadths):
        small_code, large_code = INDICES['IM'], INDICES['IF']
        strength = dict(value=None, time=None, reason='等待同步的完整5分钟线', window_minutes=5)
        turnover = dict(value=None, time=None, baseline_days=0, reason='等待完整5分钟成交额', window_minutes=5)
        common = set(self.bars.get(small_code, {})) & set(self.bars.get(large_code, {}))
        common = sorted(t for t in common if dt(t)+timedelta(seconds=65) <= at)
        if common:
            end = common[-1]
            small, large = self.window(small_code, end, 5), self.window(large_code, end, 5)
            if small and large:
                strength.update(value=small['return_pct']-large['return_pct'], time=end, reason='',
                                small_return=small['return_pct'], large_return=large['return_pct'])
            # A single completed five-minute turnover bar; never sum overlapping universes.
            small_amount = self.bars[small_code][end].get('amount')
            large_amount = self.bars[large_code][end].get('amount')
            if small_amount is not None and small_amount >= 0 and large_amount is not None and large_amount > 0:
                days = sorted({t[:10] for t in common if t[:10] < end[:10]})[-20:]
                ratios = []
                for day in days:
                    slot = day+end[10:]
                    a = self.bars[small_code].get(slot, {}).get('amount')
                    b = self.bars[large_code].get(slot, {}).get('amount')
                    if a is not None and b is not None and a > 0 and b > 0:
                        ratios.append(a/b)
                turnover.update(time=end, baseline_days=len(ratios), small_amount=small_amount,
                                large_amount=large_amount, reason=f'同刻配对基准 {len(ratios)}/10日 · 不足')
                if len(ratios) >= 10:
                    turnover.update(value=(small_amount/large_amount)/statistics.median(ratios), reason='')
        small, large = breadths[small_code], breadths[large_code]
        gap = dict(value=None, change_pp=None, time=None, reason='等待同刻的1000 / 300广度', window_minutes=5)
        if (small['value'] is not None and large['value'] is not None
                and abs((dt(small['time'])-dt(large['time'])).total_seconds()) <= 10
                and cash_session(small['time']) == cash_session(large['time'])
                and cash_session(small['time']) is not None):
            gap.update(value=small['value']-large['value'], time=min(small['time'],large['time']), reason='')
            if (small['change_pp'] is not None and large['change_pp'] is not None
                    and abs((dt(small['previous_time'])-dt(large['previous_time'])).total_seconds()) <= 10):
                gap['change_pp'] = small['change_pp']-large['change_pp']
        return dict(strength=strength, turnover=turnover, breadth_gap=gap)

    def view(self, at=None, calendar=()):
        at = dt(at or now())
        breadths = {code:self.breadth_view(code, at) for code in COUNTS}
        share = breadths[INDICES['IM']]
        rotation = self.rotation_view(at, breadths)
        style = rotation['strength']
        activity = {}
        for root, code in INDICES.items():
            rows = self.bars.get(code, {})
            ends = sorted(t for t in rows if dt(t)+timedelta(seconds=65) <= at)
            value = dict(value=None, time=None, baseline_days=0, reason='等待完整15分钟成交额')
            if ends:
                end = ends[-1]
                window = self.window(code, end)
                if window and window['amount'] is not None:
                    # Calendar dates with observed bars only. Missing slots reduce coverage.
                    days = sorted({t[:10] for t in rows if t[:10] < end[:10]})[-20:]
                    samples = [self.window(code, day+end[10:]) for day in days]
                    amounts = [s['amount'] for s in samples if s and s['amount'] is not None and s['amount'] > 0]
                    value.update(time=end, baseline_days=len(amounts), amount=window['amount'],
                                 reason=f'历史同刻基准 {len(amounts)}/10日 · 不足')
                    if len(amounts) >= 10:
                        value.update(value=window['amount']/statistics.median(amounts), reason='')
            activity[root] = value
        etfs = []
        prior_days = sorted(d for d in calendar if d < at.date().isoformat())[-BASELINE_DAYS:]
        for root, sample in ETF_SAMPLES.items():
            flow, daily = self.etf_flow.get(sample['code']), self.etf_daily.get(sample['code'])
            if flow and (dt(flow['observed_at']) > at or flow.get('time') and dt(flow['time']) > at):
                flow = None
            if daily and (dt(daily['observed_at']) > at or daily['data_date'] >= at.date().isoformat()):
                daily = None
            metrics = self.flow_history.compare(sample['code'], flow, at, prior_days,
                                                self.etf_closes.get(sample['code'], {}))
            etfs.append(dict(root=root, **sample, flow=flow, daily=daily, comparison=metrics))
        return dict(breadth=share, breadths=list(breadths.values()), rotation=rotation, style=style, activity=activity, cross=self.cross, etfs=etfs,
                    updated_at=self.updated_at, generated_at=iso(at))


class MarketContext:
    def __init__(self, desk, data_dir=None, client=None):
        self.desk = desk
        directory = data_dir or Path(desk.store.db.execute('PRAGMA database_list').fetchone()[2]).parent
        self.path = Path(directory)/'market-context-windpy.json'
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = None
        self.book = ContextBook(data)
        self.book.provider='WindPy'
        # The old WSD amount must never appear beneath the new intraday label.
        self.book.etf_flow = {code:row for code,row in self.book.etf_flow.items()
                              if row.get('source') == 'WindPy WSQ'
                              and row.get('frequency') == 'intraday_cumulative'
                              and code in ETF_CODES}
        self.restore_etf_snapshots()
        self.client = client or WindPyContextClient(desk,directory)
        self.disabled = desk.offline or os.environ.get('WIND_DESK_CONTEXT_DISABLED') == '1'
        self.settings_path = Path(directory)/'market-context-settings.json'
        try:
            settings = json.loads(self.settings_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            settings = {}
        self.monitor = not self.disabled and isinstance(settings, dict) and settings.get('monitoring') is True
        self.task = None
        self.last_attempt = float('-inf')
        self.last_bars_slot = {}
        self.last_cross_slot = None
        self.history_attempts = {}
        self.history_errors = {}
        self.history_loading = None
        self.error = None
        self.calls = 0
        self.etf_errors = {}
        self.audit = None
        self.last_flow_attempt = float('-inf')
        self.last_daily_attempt = float('-inf')
        self.last_etf_history_attempt = float('-inf')
        self.pause_flow_slot = None

    def restore_etf_snapshots(self):
        # The existing append-only receipts also retain observations between context writes.
        cutoff = (now().date()-timedelta(days=60)).strftime('%Y%m%d')
        for path in sorted(self.path.parent.glob('market-context-windpy-observed-????????.jsonl')):
            if path.stem[-8:] < cutoff:
                continue
            try:
                with path.open(encoding='utf-8') as file:
                    for line in file:
                        if 'get_fund_price_indicators' not in line:
                            continue
                        try:
                            record = json.loads(line)
                            response = record['response']
                            if record['tool'] == 'get_fund_price_indicators' and response.get('provider') == 'WindPy':
                                rows = [r for r in response['rows'] if r.get('source') == 'WindPy WSQ']
                                self.book.add_etf_flow(rows, response['units'], record['observed_at'])
                        except (TypeError, ValueError, KeyError):
                            continue
            except OSError:
                continue

    def set_monitor(self, enabled):
        if self.disabled:
            raise ValueError('离线或已禁用市场背景查询')
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(dict(monitoring=bool(enabled))), encoding='utf-8')
            temporary.replace(self.settings_path)
        except OSError as exc:
            raise ValueError('盘中更新设置保存失败') from exc
        self.monitor = bool(enabled)

    def snapshot(self):
        at = now()
        return dict(**self.book.view(at, self.desk.calendar), busy=bool(self.task and not self.task.done()),
                    monitoring=self.monitor, disabled=self.disabled, error=self.error,
                    calls=self.calls, etf_errors=self.etf_errors, audit=self.audit,
                    history_loading=self.history_loading, history_errors=self.history_errors,
                    source='WindPy · 本机 Wind 登录', interval_seconds=REFRESH_SECONDS, etf_interval_seconds=ETF_FLOW_SECONDS,
                    etf_session=etf_session(at, self.desk.calendar))

    def request_refresh(self):
        if self.disabled:
            raise ValueError('离线或已禁用市场背景查询')
        if self.task and not self.task.done():
            return False
        if time.monotonic()-self.last_attempt < REFRESH_SECONDS:
            return False
        self.last_attempt = time.monotonic()
        self.task = asyncio.create_task(self.refresh())
        return True

    def request_audit(self):
        if self.disabled:
            raise ValueError('离线或已禁用市场背景查询')
        if self.task and not self.task.done() or time.monotonic()-self.last_attempt<60:
            return False
        self.last_attempt=time.monotonic()
        self.task=asyncio.create_task(self.audit_etfs())
        return True

    async def audit_etfs(self):
        try:
            self.audit=await self.client.audit_etfs()
            self.path.parent.mkdir(parents=True,exist_ok=True)
            (self.path.parent/'market-context-windpy-etf-audit.json').write_text(
                json.dumps(self.audit,ensure_ascii=False,allow_nan=False,indent=2),encoding='utf-8')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.audit=dict(error=str(exc),observed_at=iso(now()))

    async def query(self, tool, params, parser=mcp_rows):
        self.calls += 1
        envelope = await self.client.call(tool, params)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        archive = self.path.parent/('market-context-windpy-observed-'+now().strftime('%Y%m%d')+'.jsonl')
        with archive.open('a', encoding='utf-8') as file:
            file.write(json.dumps(dict(observed_at=iso(now()), tool=tool, params=params,
                                       response=envelope), ensure_ascii=False, allow_nan=False)+'\n')
        return parser(envelope)

    async def refresh(self):
        self.error = None
        try:
            query = self.query
            problems=[]
            day=max((d for d in self.desk.calendar if d<=now().date().isoformat()),default=None)
            updated=False
            try:
                rows, units = await query('get_index_price_indicators', dict(windcode=','.join(COUNTS), indexes=FIELDS))
                if not rows:
                    raise ValueError('Wind 指数快照为空')
                self.book.add_snapshots(rows, units)
                dates = {str(r.get('最新交易日', '')) for r in rows}
                if len(dates)!=1:
                    raise ValueError('指数快照交易日不一致')
                source_day=next(iter(dates))
                if len(source_day)!=8 or not source_day.isdigit():
                    raise ValueError('指数快照缺少交易日')
                day=f'{source_day[:4]}-{source_day[4:6]}-{source_day[6:]}'
                if day>now().date().isoformat():
                    raise ValueError('指数快照日期在未来')
                updated=True
            except ValueError as exc:
                problems.append('指数广度：'+str(exc))
            target=completed_cash_bar(day,now()) if day else None
            if target:
                for code in INDICES.values():
                    if self.last_bars_slot.get(code)==target:
                        continue
                    try:
                        rows, units = await query('get_index_kline', dict(windcode=code, begin_date=day, end_date=day, period='3', count=-24))
                        if not rows:
                            raise ValueError('Wind 分钟线为空')
                        self.book.add_bars(code, rows, units, now())
                        if target in self.book.bars.get(code,{}):
                            self.last_bars_slot[code]=target
                            updated=True
                    except ValueError as exc:
                        problems.append(NAMES[code]+'：'+str(exc))
            slot=now().strftime('%Y-%m-%d-%H:')+str(now().minute//5)
            if slot!=self.last_cross_slot:
                codes=[self.desk.watch[r] for r in ('AG','AU','SC','T','TL') if self.desk.watch.get(r)]
                try:
                    if codes:
                        rows, _ = await query('securities_get_price',dict(windCodes=codes))
                        self.book.cross=[dict(code=r.get('windcode'),name=r.get('中文简称'),
                            time=r.get('交易时间'),trading_day=r.get('最新交易日'),change_pct=number(r.get('涨跌幅')),
                            amount=number(r.get('成交额')) if r.get('indicator_units',{}).get('成交额')=='元' else None)
                            for r in rows if r.get('windcode') in codes]
                        updated=True
                    self.last_cross_slot=slot
                except ValueError as exc:
                    problems.append('跨市场：'+str(exc))
            if updated:
                self.book.updated_at=iso(now())
            await self.refresh_etfs(query)
            if day:
                await self.refresh_history(query,day)
            self.error='；'.join(problems) or None
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self.error = '背景查询超时，保留原时间，等待WindPy恢复'
        except Exception as exc:
            self.error = str(exc) if isinstance(exc, ValueError) else '背景查询失败，保留原时间'
        finally:
            self.save()

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.book.json(), ensure_ascii=False, allow_nan=False), encoding='utf-8')
            temporary.replace(self.path)
        except OSError:
            self.error = '背景缓存写入失败，本轮仅保留内存数据'

    async def refresh_history(self, query, day):
        # Cold start adds at most one bounded request per refresh, after live snapshots.
        # CFFEX cash-equity trading dates are already owned by the running desk.
        days = sorted(d for d in self.desk.calendar if d < day)[-20:]
        if len(days) < 10:
            self.history_errors['calendar'] = '历史基准等待至少10个交易日的日历'
            return
        self.history_errors.pop('calendar', None)
        for root, code in INDICES.items():
            complete_days = set(self.book.history_days(code, days))
            missing = [d for d in days if d not in complete_days]
            if not missing:
                self.history_errors.pop(root, None)
                continue
            if time.monotonic()-self.history_attempts.get(code, float('-inf')) < 1800:
                continue
            self.history_attempts[code] = time.monotonic()
            self.history_loading = root
            try:
                rows, units = await query('get_index_kline', dict(
                    windcode=code, begin_date=missing[0], end_date=missing[-1], period='3', count=0))
                allowed = set(missing)
                rows = [r for r in rows if str(r.get('TIME', ''))[:10] in allowed]
                self.book.add_bars(code, rows, units, now())
                complete = len(self.book.history_days(code, days))
                if complete != len(days):
                    raise ValueError('历史覆盖或金额单位不足')
                self.history_errors.pop(root, None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.history_errors[root] = '历史基准未完整补齐：'+str(exc)
                if any(word in str(exc) for word in ('让行','暂缓','连接尚未就绪')):
                    self.history_attempts[code]=time.monotonic()-1770
            finally:
                self.history_loading = None
            break

    async def refresh_etfs(self, query):
        codes = [sample['code'] for sample in ETF_SAMPLES.values()]
        await self.refresh_etf_monitor(query)
        # At most once per successful local date; failed/partial daily responses retry after 30m.
        today = now().date().isoformat()
        checked = self.book.etf_daily_checked_at
        expected_day = max((d for d in self.desk.calendar if d < today), default=None)
        complete = all(self.book.etf_daily.get(code, {}).get('value') is not None
                       and (expected_day is None or self.book.etf_daily[code]['data_date'] >= expected_day)
                       for code in codes)
        if checked and checked[:10] == today and complete:
            return
        if time.monotonic()-self.last_daily_attempt < 1800:
            return
        self.last_daily_attempt = time.monotonic()
        # Never request unfinished same-day subscriptions. The API declares its actual date.
        day = (now()-timedelta(days=1)).date().isoformat()
        try:
            rows, units = await query('fund_get_listed_historical_price',
                dict(windCodes=codes, includeFields=[DAILY_FIELD], tradeDate=day),
                parser=lambda envelope: daily_etf_rows(envelope, day))
            incoming = self.book.add_etf_daily(rows, units, now())
            if (len(incoming) != len(codes) or any(x['value'] is None for x in incoming.values())
                    or expected_day and any(x['data_date'] < expected_day for x in incoming.values())):
                raise ValueError('部分ETF日频申赎暂缺')
            self.etf_errors.pop('daily', None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.etf_errors['daily'] = 'ETF日频净申赎未完整更新：'+str(exc)
            if any(word in str(exc) for word in ('让行','暂缓','连接尚未就绪')):
                self.last_daily_attempt=time.monotonic()-1770

    async def refresh_etf_monitor(self, query):
        if time.monotonic()-self.last_flow_attempt < ETF_FLOW_SECONDS:
            return
        self.last_flow_attempt = time.monotonic()
        codes = [s['code'] for s in ETF_SAMPLES.values()]
        try:
            rows, units = await query('get_fund_price_indicators', dict(windcode=','.join(codes), indexes=ETF_FLOW_FIELDS))
            incoming = self.book.add_etf_flow(rows, units, now())
            if len(incoming) != len(codes) or any(r['value'] is None for r in incoming.values()):
                raise ValueError('部分ETF资金流缺失或口径未通过校验')
            self.etf_errors.pop('flow', None)
        except Exception as exc:
            self.etf_errors['flow'] = 'ETF资金流未完整更新：'+str(exc)
        prior = sorted(d for d in self.desk.calendar if d < now().date().isoformat())[-BASELINE_DAYS:]
        if len(prior) < BASELINE_DAYS or time.monotonic()-self.last_etf_history_attempt < 1800:
            return
        missing = [d for d in prior if any(self.book.etf_closes.get(c, {}).get(d) is None for c in codes)]
        if not missing:
            return
        self.last_etf_history_attempt = time.monotonic()
        try:
            self.calls += 1
            amounts = await self.client.etf_daily_flows(codes, missing)
            for code in codes:
                previous = self.book.etf_closes.get(code, {})
                values = {d: number(amounts[code].get(d)) for d in missing}
                self.book.etf_closes[code] = {d: values[d] if values.get(d) is not None else previous.get(d)
                                              for d in prior}
            self.save()
            if any(self.book.etf_closes[c][d] is None for c in codes for d in prior):
                raise ValueError('部分交易日缺少日频主力净流入')
            self.etf_errors.pop('full_day', None)
        except Exception as exc:
            self.etf_errors['full_day'] = '20日全日参考待补：'+str(exc)

    async def run(self):
        while True:
            at = now()
            phase = etf_session(at, self.desk.calendar)
            slot = (at.date(), phase)
            if not self.disabled and getattr(self.desk, 'connected', False) and not self.desk.bootstrap:
                if self.monitor and phase == '交易中':
                    self.request_refresh()
                # On a lunch/after-close launch, obtain the last exchange snapshot once.
                closing = phase in ('午休', '已收盘') and self.pause_flow_slot != slot
                if ((phase == '交易中' or closing) and (not self.task or self.task.done())
                        and time.monotonic()-self.last_flow_attempt >= ETF_FLOW_SECONDS):
                    if closing:
                        self.pause_flow_slot = slot
                    self.task = asyncio.create_task(self.refresh_etf_monitor(self.query))
            await asyncio.sleep(1)

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
