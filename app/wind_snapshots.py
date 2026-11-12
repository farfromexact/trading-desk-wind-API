"""Bounded callback snapshots; all SDK calls remain on the worker thread."""
import queue
import time

from .domain import iso, now
from .options_wind import OPTION_FIELDS


class AsyncOptionSnapshots:
    def __init__(self,w,check,serialize,results,quotes,timeout=10):
        self.w=w;self.check=check;self.serialize=serialize
        self.results=results;self.quotes=quotes;self.timeout=timeout
        self.inbox=queue.SimpleQueue();self.pending={}

    def start(self,cmd):
        ident=cmd['id'];started=time.monotonic()
        # The callback only enqueues. In particular it never cancels a request.
        result=self.check(self.w.wsq(','.join(cmd['codes']),OPTION_FIELDS,
                         func=lambda r:self.inbox.put((ident,r,time.time()*1000))),'期权回调快照')
        self.pending[ident]=dict(cmd=cmd,subscription=result.RequestID,started=started,
                                 native_seconds=time.monotonic()-started,missing=set(cmd['codes']))
        self.results.put(dict(type='request_deferred',id=ident))

    def poll(self):
        while True:
            try:ident,result,received_ms=self.inbox.get_nowait()
            except queue.Empty:break
            job=self.pending.get(ident)
            if job is None:continue  # late callback from an already cancelled request
            packet=self.serialize(result,'options',True,received_ms)
            if packet['type']=='option_feed_error':
                job['error']=packet['message']
            else:
                self.quotes.put(packet)
                required=set(OPTION_FIELDS.split(','))
                job['missing'].difference_update(code for code,fields in packet['records'].items() if required<=fields.keys())
        for ident,job in list(self.pending.items()):
            if job['missing'] and not job.get('error') and time.monotonic()-job['started']<self.timeout:continue
            # Registration and cancellation retain the normal native-call watchdog.
            started=time.monotonic()
            self.results.put(dict(type='request_started',id=ident,native_started=started))
            try:
                self.w.cancelRequest(job['subscription'])
            except Exception as exc:
                job['error']=str(exc)
            response=dict(type='response',id=ident,op='option_snapshot',received_at=iso(now()),
                          native_seconds=job['native_seconds']+time.monotonic()-started,
                          payload=dict(missing_codes=sorted(job['missing']),wait_seconds=time.monotonic()-job['started']))
            if job.get('error'):response['error']=job['error']
            self.results.put(response)
            del self.pending[ident]

    def close(self):
        for job in self.pending.values():self.w.cancelRequest(job['subscription'])
        self.pending.clear()
