import asyncio
import json
import os
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .domain import ROOT
from .service import Desk
from .replay import build_replay
from .markets import valid_contract
from .market_context import MarketContext


@asynccontextmanager
async def lifespan(app):
    desk=Desk(offline=os.environ.get('WIND_DESK_OFFLINE')=='1')
    app.state.desk=desk
    context=MarketContext(desk)
    app.state.market_context=context
    context_task=asyncio.create_task(context.run())
    task=asyncio.create_task(desk.run())
    try:
        yield
    finally:
        context_task.cancel()
        await asyncio.gather(context_task,return_exceptions=True)
        await context.close()
        desk.running=False
        try:
            await task
        finally:
            try:
                await desk.options.close()
            finally:
                desk.stop()


app=FastAPI(title='Wind 实时机会工作台',lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])


@app.middleware('http')
async def local_origin(request,call_next):
    origin=request.headers.get('origin')
    if request.method not in ('GET','HEAD') and origin and urlparse(origin).hostname not in ('127.0.0.1','localhost'):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail':'仅允许本机页面操作'},status_code=403)
    return await call_next(request)


@app.get('/api/state')
async def state(request:Request):
    return request.app.state.desk.snapshot()


@app.get('/api/market-context')
async def market_context(request:Request):
    return request.app.state.market_context.snapshot()


@app.post('/api/market-context/refresh')
async def refresh_market_context(request:Request):
    try:
        accepted=request.app.state.market_context.request_refresh()
    except ValueError as exc:
        raise HTTPException(400,str(exc))
    return {'accepted':accepted,**request.app.state.market_context.snapshot()}


class ContextMonitor(BaseModel):
    enabled:bool


@app.post('/api/market-context/monitor')
async def context_monitor(body:ContextMonitor,request:Request):
    context=request.app.state.market_context
    if context.disabled:
        raise HTTPException(400,'离线或已禁用市场背景查询')
    context.monitor=body.enabled
    if body.enabled:
        context.request_refresh()
    return context.snapshot()


@app.get('/api/bars')
async def bars(request:Request,contract:str,timeframe:int=5):
    if timeframe not in (1,5,15):
        raise HTTPException(400,'周期必须是1、5或15分钟')
    desk=request.app.state.desk
    return [b.json() for b in desk.available_bars(contract,timeframe)]


class ChartInterest(BaseModel):
    contract:str


@app.post('/api/chart-interest')
async def chart_interest(body:ChartInterest,request:Request):
    desk=request.app.state.desk
    if body.contract not in desk.watch.values():
        raise HTTPException(400,'仅支持当前监测合约')
    desk.touch_minute_chart(body.contract)
    return {'ok':True}


@app.get('/api/events')
async def events(request:Request,limit:int=150):
    return request.app.state.desk.store.events(min(max(limit,1),1000))


@app.get('/api/opportunities')
async def opportunities(request:Request):
    events=request.app.state.desk.store.events(5000)
    latest={}
    for e in events:
        op=e.get('opportunity')
        if op and op['id'] not in latest:
            latest[op['id']]=op
    return list(latest.values())


@app.get('/api/replay')
async def replay(request:Request,contract:str,day:str):
    if not valid_contract(contract):
        raise HTTPException(400,'仅支持监控品种的真实月份合约')
    try:
        return await asyncio.to_thread(build_replay,ROOT/'data'/'desk.sqlite3',contract,day,request.app.state.desk.config)
    except ValueError:
        raise HTTPException(400,'日期格式必须为 YYYY-MM-DD')


class WatchChoice(BaseModel):
    root:str
    contract:str|None=None


@app.post('/api/watchlist')
async def watch(request:Request,choice:WatchChoice):
    try:
        request.app.state.desk.set_watch(choice.root,(choice.contract or '').upper() or None)
    except ValueError as exc:
        raise HTTPException(400,str(exc))
    return {'ok':True}


class Timing(BaseModel):
    delay_ms:float
    source_delay_ms:float|None=None
    option_delay_ms:float|None=None


@app.post('/api/telemetry')
async def telemetry(request:Request,timing:Timing):
    metrics=request.app.state.desk.metrics
    delay=timing.delay_ms
    if 0<=delay<60000:
        metrics['ui_samples']+=1
        metrics['ui_max_ms']=max(metrics['ui_max_ms'],round(delay,1))
        metrics['ui_over_2s']+=int(delay>2000)
    source_delay=timing.source_delay_ms
    if source_delay is not None and 0<=source_delay<60000:
        metrics['source_ui_samples']+=1
        metrics['source_ui_max_ms']=max(metrics['source_ui_max_ms'],round(source_delay,1))
        metrics['source_ui_over_2s']+=int(source_delay>2000)
    option_delay=timing.option_delay_ms
    if option_delay is not None and 0<=option_delay<60000:
        m=request.app.state.desk.options.metrics
        m['ui_samples']+=1
        m['ui_max_ms']=max(m['ui_max_ms'],round(option_delay,1))
        m['ui_over_2s']+=int(option_delay>2000)
    return {'ok':True}


def option_root(request, root):
    if root not in request.app.state.desk.config['watchlist']:
        raise HTTPException(400,'未知监控品种')
    return request.app.state.desk.options


@app.get('/api/options/context')
async def option_context(request:Request,root:str='IM'):
    return option_root(request,root).context(root)


@app.get('/api/options/chain')
async def option_chain(request:Request,root:str='IM',expiry:str|None=None):
    options=option_root(request,root)
    rows=options.rows.get(root,[])
    return dict(context=options.context(root),rows=[r for r in rows if not expiry or r['expiry']==expiry],
                metadata_count=len(options.entries.get(root,{}).get('contracts',[])))


@app.get('/api/options/candidates')
async def option_candidates(request:Request,root:str='IM',opportunity_id:str|None=None,holding_days:int=5):
    if holding_days not in (0,1,3,5):
        raise HTTPException(400,'持有周期为日内或1/3/5个交易日')
    return await option_root(request,root).candidate_view(root,opportunity_id,holding_days)


@app.get('/api/options/research')
async def option_research(request:Request):
    return request.app.state.desk.options.report()


class OptionFee(BaseModel):
    root:str
    fee:float|None=None


@app.get('/api/options/settings')
async def option_settings(request:Request):
    return request.app.state.desk.options.config


@app.post('/api/options/fees')
async def option_fee(request:Request,choice:OptionFee):
    try:
        return request.app.state.desk.options.set_fee(choice.root,choice.fee)
    except ValueError as exc:
        raise HTTPException(400,str(exc))


@app.get('/api/options/audit')
async def option_audit(request:Request,kind:str='evaluation',root:str|None=None,limit:int=100,after:int=0):
    if kind not in ('signal','comparison','lifecycle','trigger','path','evaluation','context','chain','vendor','quotes'):
        raise HTTPException(400,'未知期权记录类型')
    if root:
        option_root(request,root)
    return request.app.state.desk.store.option_history(kind,root,min(max(limit,1),1000),max(after,0))


@app.get('/api/stream')
async def stream(request:Request):
    desk=request.app.state.desk
    recent=desk.store.events(1)
    cursor=recent[0]['seq'] if recent else 0
    async def generate():
        nonlocal cursor
        while desk.running and not await request.is_disconnected():
            rows=desk.store.events(1000,after=cursor)
            if rows:
                cursor=max(e['seq'] for e in rows)
            payload={'state':desk.snapshot(),'events':list(reversed(rows))}
            yield 'id: '+str(cursor)+'\nevent: update\ndata: '+json.dumps(payload,ensure_ascii=False,allow_nan=False)+'\n\n'
            await asyncio.sleep(1)
    return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})


dist=ROOT/'frontend'/'dist'
app.mount('/assets',StaticFiles(directory=dist/'assets',check_dir=False),name='assets')


@app.get('/')
async def index():
    if not (dist/'index.html').exists():
        raise HTTPException(503,'前端尚未构建，请运行安装脚本')
    return FileResponse(dist/'index.html')
