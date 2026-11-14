"""Read-only, optional market context. Never submits work to the WindPy worker."""
from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import statistics
import subprocess
import time
import uuid
from datetime import timedelta
from pathlib import Path

from .domain import dt, iso, now
from .etf_context import ETF_SAMPLES, ETF_FLOW_FIELDS, DAILY_FIELD, daily_etf_rows, flow_records, daily_records

INDICES = {'IM': '000852.SH', 'IF': '000300.SH', 'IH': '000016.SH'}
COUNTS = {'000852.SH': 1000, '000300.SH': 300, '000016.SH': 50}
NAMES = {'000852.SH': '中证1000', '000300.SH': '沪深300', '000016.SH': '上证50'}
FIELDS = '最新交易日,交易时间,中文简称,最新成交价,涨跌幅,成交额,上涨家数,下跌家数,平盘家数'


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


def mcp_rows(envelope):
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


class WindContextClient:
    """Official CLI owns authentication. No SDK import, credentials or shell commands."""
    def __init__(self, cli=None, fund_cli=None):
        override = cli or os.environ.get('WIND_CONTEXT_CLI')
        choices = [Path(override).expanduser()] if override else [
            Path.home()/'.codex/skills/wind-one-mcp-skill/scripts/cli.mjs',
            Path.home()/'.agents/skills/wind-one-mcp-skill/scripts/cli.mjs',
        ]
        self.cli = next((p for p in choices if p.is_file()), None)
        fund_override = fund_cli or os.environ.get('WIND_CONTEXT_FUND_CLI')
        fund_choices = [Path(fund_override).expanduser()] if fund_override else [
            Path.home()/'.codex/skills/wind-mcp-skill/scripts/cli.mjs',
            Path.home()/'.agents/skills/wind-mcp-skill/scripts/cli.mjs',
        ]
        self.fund_cli = next((p for p in fund_choices if p.is_file()), None)
        self.node = shutil.which('node')

    async def call(self, tool, params):
        # The verified fund snapshot tool lives on the dedicated official fund endpoint.
        if tool == 'get_fund_price_indicators':
            if not self.fund_cli or not self.node:
                raise ValueError('未找到官方基金MCP CLI；可设置 WIND_CONTEXT_FUND_CLI')
            request = self.fund_cli.parent/('request-context-'+uuid.uuid4().hex+'.json')
            try:
                request.write_text(json.dumps(params, ensure_ascii=False), encoding='utf-8')
                return await self.execute(self.fund_cli, ['call', 'fund_data', tool, '@'+str(request)], None)
            except OSError:
                raise ValueError('基金MCP请求文件不可写或Node不可启动') from None
            finally:
                request.unlink(missing_ok=True)
        if not self.cli or not self.node:
            raise ValueError('未找到官方 Wind MCP CLI；可设置 WIND_CONTEXT_CLI')
        return await self.execute(self.cli, ['call', tool, '-'], json.dumps(params, ensure_ascii=False).encode('utf-8'))

    async def execute(self, cli, arguments, payload):
        env = dict(os.environ, WIND_SKILL_AUTO_UPDATE='0', NODE_USE_SYSTEM_CA='1')
        kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        proc = await asyncio.create_subprocess_exec(
            self.node, str(cli), *arguments, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env, **kwargs)
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(payload), 30)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            if proc.returncode is None:
                proc.kill()
            await proc.communicate()
            raise
        if proc.returncode:
            raise ValueError('Wind MCP 查询失败，请检查本机配置、权限或额度')
        try:
            return json.loads(stdout)
        except ValueError:
            raise ValueError('Wind MCP 返回格式无效') from None


class ContextBook:
    def __init__(self, data=None):
        data = data or {}
        self.bars = data.get('bars', {})
        self.snapshots = data.get('snapshots', {})
        self.cross = data.get('cross', [])
        self.etf_flow = data.get('etf_flow', {})
        self.etf_daily = data.get('etf_daily', {})
        self.etf_daily_checked_at = data.get('etf_daily_checked_at')
        self.updated_at = data.get('updated_at')

    def json(self):
        return dict(version=2, bars=self.bars, snapshots=self.snapshots, cross=self.cross,
                    etf_flow=self.etf_flow, etf_daily=self.etf_daily, etf_daily_checked_at=self.etf_daily_checked_at,
                    updated_at=self.updated_at)

    def add_etf_flow(self, rows, units, received_at):
        incoming = flow_records(rows, units, received_at, number)
        for code, row in incoming.items():
            old = self.etf_flow.get(code, {})
            if row['time'] and old.get('time') and dt(row['time']) < dt(old['time']):
                continue
            self.etf_flow[code] = row
        return incoming

    def add_etf_daily(self, rows, units, received_at):
        incoming = daily_records(rows, units, received_at, number)
        for code, row in incoming.items():
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
            valid = (all(v is not None and v >= 0 and v.is_integer() for v in counts)
                     and sum(counts) == COUNTS[code])
            value = dict(code=code, time=stamp, trading_day=str(row.get('最新交易日', '')),
                         breadth=counts[0]/sum(counts[:2])*100 if valid and sum(counts[:2]) else None,
                         count_total=sum(counts) if all(v is not None for v in counts) else None,
                         scope_ok=valid, counts=counts)
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
                received_at=iso(received_at), source='Wind MCP 5分钟线')
        self.bars[code] = dict(sorted(self.bars.get(code, {}).items())[-2400:])

    def window(self, code, end):
        rows = self.bars.get(code, {})
        stamps = [iso(dt(end)-timedelta(minutes=i*5)) for i in (3, 2, 1, 0)]
        values = [rows.get(t) for t in stamps]
        if any(v is None for v in values) or len({cash_session(t) for t in stamps}) != 1:
            return None
        if cash_session(stamps[0]) is None:
            return None
        result = (values[-1]['close']/values[0]['close']-1)*100
        amounts = [v.get('amount') for v in values[1:]]
        return dict(return_pct=result, amount=sum(amounts) if all(v is not None for v in amounts) else None)

    def view(self, at=None):
        at = dt(at or now())
        breadth = self.snapshots.get(INDICES['IM'], {})
        available = [t for t in breadth if dt(t) <= at]
        latest = breadth[max(available)] if available else None
        share = dict(value=None, change_pp=None, time=None, reason='等待指数聚合快照')
        if latest:
            stamp = dt(latest['time'])
            reason = '' if latest['scope_ok'] and latest['breadth'] is not None else '聚合家数覆盖不匹配或无涨跌样本'
            share.update(value=latest['breadth'], time=latest['time'], reason=reason,
                         count_total=latest['count_total'], expected=1000)
            target = stamp-timedelta(minutes=15)
            candidates = [x for t, x in breadth.items() if target-timedelta(seconds=90) <= dt(t) <= target
                          and cash_session(t) == cash_session(stamp) and cash_session(t) is not None]
            if candidates and latest['breadth'] is not None and candidates[-1]['breadth'] is not None:
                share['change_pp'] = latest['breadth']-candidates[-1]['breadth']
        # Select a common bar end; never splice two index clocks into relative strength.
        common = set(self.bars.get(INDICES['IM'], {})) & set(self.bars.get(INDICES['IH'], {}))
        common = sorted(t for t in common if dt(t)+timedelta(seconds=65) <= at)
        style = dict(value=None, time=None, reason='等待同步的完整15分钟线')
        if common:
            end = common[-1]
            small, large = self.window(INDICES['IM'], end), self.window(INDICES['IH'], end)
            if small and large:
                style.update(value=small['return_pct']-large['return_pct'], time=end, reason='')
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
                                 reason='历史同刻基准不足10天')
                    if len(amounts) >= 10:
                        value.update(value=window['amount']/statistics.median(amounts), reason='')
            activity[root] = value
        etfs = []
        for root, sample in ETF_SAMPLES.items():
            flow, daily = self.etf_flow.get(sample['code']), self.etf_daily.get(sample['code'])
            if flow and (dt(flow['observed_at']) > at or flow.get('time') and dt(flow['time']) > at):
                flow = None
            if daily and (dt(daily['observed_at']) > at or daily['data_date'] >= at.date().isoformat()):
                daily = None
            etfs.append(dict(root=root, **sample, flow=flow, daily=daily))
        return dict(breadth=share, style=style, activity=activity, cross=self.cross, etfs=etfs,
                    updated_at=self.updated_at, generated_at=iso(at))


class MarketContext:
    def __init__(self, desk, data_dir=None, client=None):
        self.desk = desk
        directory = data_dir or Path(desk.store.db.execute('PRAGMA database_list').fetchone()[2]).parent
        self.path = Path(directory)/'market-context.json'
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = None
        self.book = ContextBook(data)
        self.client = client or WindContextClient()
        self.disabled = desk.offline or os.environ.get('WIND_DESK_CONTEXT_DISABLED') == '1'
        self.monitor = False
        self.task = None
        self.last_attempt = float('-inf')
        self.last_bars_slot = None
        self.error = None
        self.calls = 0
        self.etf_errors = {}
        self.last_daily_attempt = float('-inf')

    def snapshot(self):
        return dict(**self.book.view(), busy=bool(self.task and not self.task.done()),
                    monitoring=self.monitor, disabled=self.disabled, error=self.error,
                    calls=self.calls, etf_errors=self.etf_errors,
                    source='Wind Alice 万得金融数据服务', interval_seconds=60)

    def request_refresh(self):
        if self.disabled:
            raise ValueError('离线或已禁用市场背景查询')
        if self.task and not self.task.done():
            return False
        if time.monotonic()-self.last_attempt < 60:
            return False
        self.last_attempt = time.monotonic()
        self.task = asyncio.create_task(self.refresh())
        return True

    async def refresh(self):
        self.error = None
        try:
            async def query(tool, params, parser=mcp_rows):
                self.calls += 1
                envelope = await self.client.call(tool, params)
                self.path.parent.mkdir(parents=True, exist_ok=True)
                archive = self.path.parent/('market-context-observed-'+now().strftime('%Y%m%d')+'.jsonl')
                with archive.open('a', encoding='utf-8') as file:
                    file.write(json.dumps(dict(observed_at=iso(now()), tool=tool, params=params,
                                               response=envelope), ensure_ascii=False, allow_nan=False)+'\n')
                return parser(envelope)
            rows, units = await query('get_index_price_indicators', dict(windcode=','.join(INDICES.values()), indexes=FIELDS))
            if not rows:
                raise ValueError('Wind 指数快照为空')
            self.book.add_snapshots(rows, units)
            dates = {str(r.get('最新交易日', '')) for r in rows}
            # History comes from native 5m bars, not differencing stale cumulative snapshots.
            if len(dates) != 1:
                raise ValueError('指数快照交易日不一致')
            day = next(iter(dates))
            if len(day) != 8 or not day.isdigit():
                raise ValueError('指数快照缺少交易日')
            day = f'{day[:4]}-{day[4:6]}-{day[6:]}'
            slot = day+'-'+now().strftime('%H:')+str(now().minute//5)
            if slot != self.last_bars_slot:
                for code in INDICES.values():
                    rows, units = await query('get_index_kline', dict(windcode=code, begin_date=day, end_date=day, period='3', count=-24))
                    if not rows:
                        raise ValueError('Wind 分钟线为空')
                    self.book.add_bars(code, rows, units, now())
                codes = [self.desk.watch[r] for r in ('AG', 'AU', 'SC', 'T', 'TL') if self.desk.watch.get(r)]
                if codes:
                    rows, _ = await query('securities_get_price', dict(windCodes=codes))
                    # Keep each source clock/trading day. No cross-market sum or flow inference.
                    self.book.cross = [dict(code=r.get('windcode'), name=r.get('中文简称'),
                                            time=r.get('交易时间'), trading_day=r.get('最新交易日'),
                                            change_pct=number(r.get('涨跌幅')),
                                            amount=number(r.get('成交额')) if r.get('indicator_units', {}).get('成交额') == '元' else None)
                                       for r in rows if r.get('windcode') in codes]
                self.last_bars_slot = slot
            self.book.updated_at = iso(now())
            # Independent ETF failures do not discard index/cross-market observations.
            await self.refresh_etfs(query)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self.error = '背景查询超时，保留原时间；独立于WindPy查询'
        except Exception as exc:
            self.error = str(exc) if isinstance(exc, ValueError) else '背景查询失败，保留原时间'
        finally:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix('.tmp')
                temporary.write_text(json.dumps(self.book.json(), ensure_ascii=False, allow_nan=False), encoding='utf-8')
                temporary.replace(self.path)
            except OSError:
                self.error = '背景缓存写入失败，本轮仅保留内存数据'

    async def refresh_etfs(self, query):
        codes = [sample['code'] for sample in ETF_SAMPLES.values()]
        try:
            rows, units = await query('get_fund_price_indicators', dict(windcode=','.join(codes), indexes=ETF_FLOW_FIELDS))
            incoming = self.book.add_etf_flow(rows, units, now())
            if len(incoming) != len(codes) or any(x['value'] is None for x in incoming.values()):
                raise ValueError('部分ETF资金流缺失或口径未通过校验')
            self.etf_errors.pop('flow', None)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.etf_errors['flow'] = 'ETF盘中资金流未完整更新，保留各自源时间'
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
        except Exception:
            self.etf_errors['daily'] = 'ETF日频净申赎未完整更新，保留各自数据日'

    async def run(self):
        while True:
            if (self.monitor and not self.disabled and self.desk.calendar
                    and now().date().isoformat() in self.desk.calendar
                    and cash_session(now()) is not None):
                self.request_refresh()
            await asyncio.sleep(1)

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
