"""Compare like-for-like Wind ETF flow snapshots at the same exchange clock."""
from bisect import bisect_left, bisect_right
from math import isfinite
from operator import itemgetter
from statistics import fmean

from .domain import dt


BASELINE_DAYS = 20
SPEED_SECONDS = 300
MATCH_SECONDS = 75  # Older WSQ receipts were sampled once per minute.
AM_OPEN, AM_CLOSE = 9*3600 + 30*60, 11*3600 + 30*60
PM_OPEN, PM_CLOSE = 13*3600, 15*3600
_second = itemgetter(0)


def session_clock(stamp):
    t = dt(stamp)
    second = t.hour*3600 + t.minute*60 + t.second
    for opening, closing in ((AM_OPEN, AM_CLOSE), (PM_OPEN, PM_CLOSE)):
        if opening <= second < closing + 60:
            # Wind can stamp the closing snapshot a few seconds into the closing minute.
            return opening, min(second, closing)
    return None


def observation(points, target, opening):
    if target < opening:
        return None
    i = bisect_right(points, target, key=_second)
    if i:
        second, value = points[i-1]
        if second >= opening and target-second <= MATCH_SECONDS:
            return second, value
    return None


def rate(points, second, value, opening):
    before = observation(points, second - SPEED_SECONDS, opening)
    if before is not None:
        return (value-before[1])*60/(second-before[0])
    return None


class EtfFlowHistory:
    def __init__(self):
        self.days = {}

    def record(self, row):
        if (row.get('source') != 'WindPy WSQ' or row.get('frequency') != 'intraday_cumulative'
                or row.get('unit') != '元' or row.get('value') is None or not row.get('time')):
            return
        clock = session_clock(row['time'])
        if clock is None:
            return
        day = row['time'][:10]
        dates = self.days.setdefault(row['code'], {})
        points = dates.setdefault(day, [])
        second = clock[1]
        i = bisect_left(points, second, key=_second)
        point = (second, row['value'])
        if i < len(points) and points[i][0] == second:
            points[i] = point
        else:
            points.insert(i, point)
        if len(dates) > BASELINE_DAYS + 2:
            del dates[min(dates)]

    def compare(self, code, flow, at, prior_days, daily_closes):
        full_days = [daily_closes[d] for d in prior_days if daily_closes.get(d) is not None]
        result = dict(days=0, speed_days=0, mean=None, excess=None, speed=None, speed_mean=None,
                      sample_mean=None, rolling_20d_mean=None, rolling_20d_excess=None, full_days=len(full_days),
                      full_day_mean=fmean(full_days) if len(full_days) == BASELINE_DAYS else None)
        if (not flow or flow.get('source') != 'WindPy WSQ' or flow.get('value') is None
                or not flow.get('time') or flow['time'][:10] != at.date().isoformat()):
            return result
        rolling_total = flow.get('rolling_20d_total')
        if isinstance(rolling_total, (int, float)) and isfinite(rolling_total):
            # Wind also publishes dated totals after the exchange session.
            result['rolling_20d_mean'] = rolling_total / BASELINE_DAYS
            result['rolling_20d_excess'] = flow['value'] - result['rolling_20d_mean']
        clock = session_clock(flow['time'])
        if clock is None:
            return result
        opening, second = clock
        dates = self.days.get(code, {})
        result['speed'] = rate(dates.get(flow['time'][:10], []), second, flow['value'], opening)
        values, speeds = [], []
        for day in prior_days:
            points = dates.get(day, [])
            end = observation(points, second, opening)
            if end is None:
                continue
            values.append(end[1])
            sample_rate = rate(points, *end, opening)
            if sample_rate is not None:
                speeds.append(sample_rate)
        result.update(days=len(values), speed_days=len(speeds),
                      sample_mean=fmean(values) if values else None)
        if len(values) == BASELINE_DAYS:
            result['mean'] = fmean(values)
            result['excess'] = flow['value'] - result['mean']
        if len(speeds) == BASELINE_DAYS:
            result['speed_mean'] = fmean(speeds)
        return result
