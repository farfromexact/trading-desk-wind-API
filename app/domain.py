from __future__ import annotations

import hashlib
import json
import math
from functools import lru_cache
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TZ = timezone(timedelta(hours=8))


def now() -> datetime:
    return datetime.now(TZ)


def dt(value) -> datetime:
    value = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return value.replace(tzinfo=TZ) if value.tzinfo is None else value.astimezone(TZ)


def iso(value) -> str:
    return dt(value).isoformat(timespec='seconds')


def stable_id(*values) -> str:
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]


def load_config() -> dict:
    c = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
    c['hash'] = stable_id(c)
    return c


@dataclass(frozen=True)
class Bar:
    contract: str
    timeframe: int
    end: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0

    def valid(self) -> bool:
        values = [self.open, self.high, self.low, self.close, self.volume]
        return (all(math.isfinite(v) for v in values) and min(values[:4]) > 0
                and self.volume >= 0 and self.high >= max(self.open, self.close, self.low)
                and self.low <= min(self.open, self.close))

    def json(self):
        return asdict(self)


def session(at: datetime, calendar: set[str], code='IM') -> str:
    from .markets import phase
    return phase(at, code, calendar)


def expected_end(at: datetime, timeframe: int, calendar: set[str], grace: int = 5, code='IM'):
    # Completion boundaries are whole seconds. Cache the expensive calendar /
    # native-label search within that second, without moving any boundary.
    cutoff=(dt(at)-timedelta(seconds=grace)).replace(microsecond=0)
    return _expected_end(cutoff,timeframe,tuple(sorted(calendar)),code)


@lru_cache(maxsize=4096)
def _expected_end(cutoff, timeframe, calendar, code):
    from .markets import labels, trading_windows
    # Option expiries require a longer calendar. Only the nearest future day
    # can own a current night session; don't materialize a year's future windows.
    today=cutoff.date().isoformat()
    nearest=next((d for d in calendar if d>today),today)
    for day in sorted((d for d in calendar if d<=nearest), reverse=True):
        windows=trading_windows(day,code,calendar)
        if not windows or windows[0][0]>cutoff:
            continue
        # The next trading day's night bars can be earlier than its civil date.
        for label in reversed(labels(day, code, timeframe, calendar)):
            if bar_finished_at(Bar(code,timeframe,label,1,1,1,1), calendar) <= cutoff:
                return label
    return None


def bar_finished_at(bar: Bar, calendar=None) -> datetime:
    """Wind native labels include that minute; cap at each exchange session end."""
    from .markets import label_window
    stamp=dt(bar.end)
    window=label_window(stamp,bar.contract,calendar)
    return min(stamp+timedelta(minutes=1),window[1]) if window else stamp+timedelta(minutes=1)


def completed_bar(bar: Bar, at: datetime, grace=5, calendar=None) -> bool:
    from .markets import label_window
    end = dt(bar.end)
    window = label_window(end,bar.contract,calendar)
    if not window or end.second or end.microsecond:
        return False
    offset = int((end-window[0]).total_seconds()//60)
    aligned = bar.timeframe == 1 or offset >= bar.timeframe and offset % bar.timeframe == 0
    return bar.valid() and aligned and bar_finished_at(bar,calendar) <= dt(at)-timedelta(seconds=grace)


def quote_time(q: dict, code=None, calendar=None, at=None):
    try:
        d = str(int(q['rt_date']))
        t = str(int(q['rt_time'])).zfill(6)
        stamp = datetime.strptime(d+t, '%Y%m%d%H%M%S').replace(tzinfo=TZ)
        if code and calendar and at:
            from .markets import market, trading_windows
            if market(code).get('night'):
                candidates=[stamp]
                # Some Wind feeds use the trading date in RT_DATE during night.
                for start,end in trading_windows(stamp.date().isoformat(),code,calendar):
                    if start.hour != 21:
                        continue
                    for civil in (start.date(),end.date()):
                        candidate=datetime.combine(civil,stamp.time(),TZ)
                        if start<=candidate<=end:
                            candidates.append(candidate)
                stamp=min(candidates,key=lambda x:abs((dt(at)-x).total_seconds()))
        return stamp
    except (KeyError, ValueError, TypeError, OverflowError):
        return None


def quote_problem(q: dict, at: datetime, max_age=15, code=None, calendar=None):
    try:
        price = float(q.get('rt_last', 0))
    except (TypeError, ValueError):
        price = 0
    stamp = quote_time(q,code,calendar,at)
    if not math.isfinite(price) or price <= 0:
        return '等待有效报价'
    if stamp is None or abs((dt(at)-stamp).total_seconds()) > 86400:
        return '旧报价或缺少行情时间'
    age = (dt(at)-stamp).total_seconds()
    if age < -3:
        return '行情时间超前，请检查时钟'
    if age > max_age:
        return f'行情超过{max_age}秒未更新'
    return None
