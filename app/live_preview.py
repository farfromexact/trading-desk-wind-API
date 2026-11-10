"""Display-only candles. These must never enter the rule engine or bar cache."""
from datetime import timedelta

from .domain import Bar, bar_finished_at, dt, expected_end, iso, session


def forming_label(at, timeframe, calendar, code='IM'):
    from .markets import active_window
    at=dt(at)
    window=active_window(at,code,calendar)
    if not window:
        return None
    _,start,end=window
    minute=int((at-start).total_seconds()//60)
    offset=minute if timeframe==1 else max(timeframe,((minute+timeframe-1)//timeframe)*timeframe)
    return iso(min(start+timedelta(minutes=offset),end))


class LivePreview:
    def __init__(self):
        self.rows={}

    def bucket(self,code,tf):
        return self.rows.setdefault((code,tf),{})

    def on_quote(self,code,price,stamp,calendar):
        for tf in (1,5,15):
            label=forming_label(stamp,tf,calendar,code)
            if not label:
                continue
            rows=self.bucket(code,tf)
            row=rows.get(label)
            if row is None:
                row=dict(contract=code,timeframe=tf,end=label,open=price,high=price,low=price,close=price,
                         provisional=True,source='quotes_only',as_of=iso(stamp))
                rows[label]=row
            elif dt(stamp)<dt(row['as_of']):
                continue
            row.update(high=max(row['high'],price),low=min(row['low'],price),close=price,as_of=iso(stamp))
            self.trim(code,tf)

    def on_native(self,bar:Bar,as_of,at,calendar):
        cap=forming_label(at,bar.timeframe,calendar,bar.contract) or expected_end(at,bar.timeframe,calendar,grace=0,code=bar.contract)
        if not cap or bar.end>cap or abs((dt(bar.end)-dt(at)).total_seconds())>3600 or not bar.valid():
            return
        rows=self.bucket(bar.contract,bar.timeframe)
        old=rows.get(bar.end)
        row=dict(bar.json(),provisional=True,source='wind_partial',as_of=iso(as_of))
        # A quote can already be newer than the serial WSI request.
        if old and dt(old['as_of'])>dt(as_of):
            row.update(high=max(row['high'],old['high']),low=min(row['low'],old['low']),close=old['close'],as_of=old['as_of'])
        rows[bar.end]=row
        self.trim(bar.contract,bar.timeframe)

    def trim(self,code,tf):
        rows=self.bucket(code,tf)
        for label in sorted(rows)[:-4]:
            del rows[label]

    def finalized(self,bars):
        for bar in bars:
            self.bucket(bar.contract,bar.timeframe).pop(bar.end,None)

    def snapshot(self,code,at):
        return {str(tf):[dict(row,forming=bar_finished_at(Bar(code,tf,label,row['open'],row['high'],row['low'],row['close']))>dt(at))
                        for label,row in sorted(self.bucket(code,tf).items()) if -900 <= (dt(at)-dt(label)).total_seconds() < 8*3600] for tf in (1,5,15)}
