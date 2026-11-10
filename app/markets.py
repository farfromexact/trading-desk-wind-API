"""Exchange sessions and trading-day ownership, shared by live, rules and replay.

Calendar dates are Wind exchange trading dates. Night sessions belong to the
next trading day, including Friday night / Saturday morning -> Monday.
"""
from datetime import date, datetime, time, timedelta
from functools import lru_cache
import re

from .domain import TZ, dt, iso, load_config


@lru_cache(maxsize=1)
def registry():
    return load_config()['instruments']


def root_of(code):
    match = re.match(r'^([A-Z]+)', str(code).upper())
    return match[1] if match else ''


def market(code='IM', config=None):
    root = root_of(code)
    specs = config['instruments'] if config else registry()
    return dict(specs[root], root=root, alias=root+'.'+specs[root]['exchange'])


def valid_contract(code, root=None):
    root = root or root_of(code)
    return root in registry() and bool(re.fullmatch(root+r'\d{2}(0[1-9]|1[0-2])\.'+registry()[root]['exchange'], str(code)))


def at_time(day, hm):
    return datetime.combine(date.fromisoformat(day), time.fromisoformat(hm), TZ)


def expiry_day(code, calendar):
    """CFFEX Treasury last trading day: second Friday, holiday rolls forward."""
    if not market(code).get('expiry_morning_only') or not valid_contract(code):
        return None
    month = re.search(r'(\d{2})(\d{2})\.', code)
    first = date(2000+int(month[1]), int(month[2]), 1)
    friday = first + timedelta(days=(4-first.weekday()) % 7 + 7)
    return next((d for d in sorted(calendar) if d >= friday.isoformat()), friday.isoformat())


@lru_cache(maxsize=1024)
def _windows(day, code, calendar):
    spec = market(code)
    if day not in calendar:
        return ()
    windows = []
    if spec.get('night'):
        prev = next((d for d in reversed(calendar) if d < day), None)
        if prev:
            gap = (date.fromisoformat(day)-date.fromisoformat(prev)).days
            # Exchange holiday eves have no night session. Ordinary Friday is open.
            normal = gap == 1 or (gap == 3 and date.fromisoformat(prev).weekday() == 4)
            if normal:
                start = at_time(prev, spec['night'][0])
                end = at_time(prev, spec['night'][1]) + timedelta(days=1)
                windows.append((start, end))
    sessions = spec['sessions']
    if spec.get('expiry_morning_only') and day == expiry_day(code, calendar):
        sessions = sessions[:1]
    for start, end in sessions:
        windows.append((at_time(day, start), at_time(day, end)))
    return tuple(windows)


def trading_windows(day, code, calendar):
    return _windows(day, code, tuple(sorted(calendar)))


def active_window(at, code, calendar, include_end=False):
    at = dt(at)
    # Nearest future trading day can own the current Friday/Saturday night.
    days = sorted(calendar)
    nearby = [d for d in days if at.date().isoformat() <= d][:1]
    if at.date().isoformat() in calendar and at.hour >= 20:
        nearby += [d for d in days if d > at.date().isoformat()][:1]
    for day in nearby:
        for start, end in trading_windows(day, code, calendar):
            if start <= at < end or include_end and at == end:
                return day, start, end
    return None


def trading_day(at, code, calendar=None):
    at = dt(at)
    if not market(code).get('night'):
        return at.date().isoformat()
    if calendar:
        window = active_window(at, code, calendar, include_end=True)
        if window:
            return window[0]
        # Keep Saturday's finished night attached to Monday in the resting UI.
        if at.hour < 9:
            prev = at.date()-timedelta(days=1)
            evening = active_window(at_time(prev.isoformat(), '21:00'), code, calendar)
            if evening:
                return evening[0]
    # No holiday calendar is available only in isolated rule fixtures / old replay.
    day = at.date()+timedelta(days=int(at.hour >= 21))
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.isoformat()


def phase(at, code, calendar):
    at = dt(at)
    if not calendar:
        return '等待交易日历'
    if active_window(at, code, calendar):
        return '交易中'
    day = at.date().isoformat()
    if day not in calendar:
        return '非交易日'
    windows = [w for w in trading_windows(day, code, calendar) if w[0].date() == at.date() and w[0].hour >= 9]
    if not windows or at >= windows[-1][1]:
        return '已收盘'
    if at < windows[0][0]:
        return '盘前'
    return '小节休息' if at.hour == 10 else '午休'


def day_close(day, code, calendar):
    windows = trading_windows(day, code, calendar)
    return windows[-1][1] if windows else None


@lru_cache(maxsize=2048)
def _labels(day, code, timeframe, calendar):
    values = []
    for start, end in _windows(day, code, calendar):
        cursor = start if timeframe == 1 else start+timedelta(minutes=timeframe)
        while cursor <= end:
            values.append(iso(cursor))
            cursor += timedelta(minutes=timeframe)
    return tuple(values)


def labels(day, code, timeframe, calendar):
    return _labels(day, code, timeframe, tuple(sorted(calendar)))


def label_window(stamp, code, calendar=None):
    """Validate wall-clock segments even when filtering uncached historical dates."""
    stamp = dt(stamp)
    if calendar:
        window = active_window(stamp, code, calendar, include_end=True)
        return window[1:] if window else None
    spec = market(code)
    day = stamp.date().isoformat()
    windows = [(at_time(day,a),at_time(day,b)) for a,b in spec['sessions']]
    if spec.get('night'):
        start = at_time(day,spec['night'][0])
        end = at_time(day,spec['night'][1])+timedelta(days=1)
        windows += [(start,end),(start-timedelta(days=1),end-timedelta(days=1))]
    return next(((a,b) for a,b in windows if a <= stamp <= b),None)
