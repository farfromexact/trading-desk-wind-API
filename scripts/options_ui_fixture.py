"""Synthetic browser validation on 8766; no Wind, no real database or alerts.

Run: .venv/Scripts/python.exe -m scripts.options_ui_fixture
Never use this process as a market service.
"""
import asyncio
from copy import deepcopy
import json
from math import sin
import runpy
import time

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.domain import ROOT, iso
from app.markets import market
from app.options_core import candidates, futures_reference

fixture=runpy.run_path(str(ROOT/'tests'/'test_options.py'))
AT,CAL,CONFIG=fixture['AT'],fixture['CAL'],deepcopy(fixture['CONFIG'])
views={root:fixture['market_fixture'](root) for root in ('IM','AG')}
for context,rows,op in views.values():
    op.update(signal_bar=iso(AT),confirmation_bar=iso(AT),rr=2.,remaining_bars=3,
              reason='合成案例，仅验证工具比较与情景切换；不是真实市场机会',
              regime={'state':'bull','label':'合成趋势'},level=100,level_name='合成结构位',level_known_at=iso(AT))
    for row in rows:
        row['quote']['market_time']=iso(AT)

app=FastAPI(title='合成数据 · 期权功能验证')


def snapshot():
    instruments=[]
    for root,(context,rows,op) in views.items():
        instruments.append(dict(root=root,contract=op['contract'],market=market(root),trading_day=op['trading_day'],
            quote=fixture['quote'](context['futures_price']),market_time=iso(AT),phase='合成数据',status='功能验证',ready=False,
            regime=op['regime'],note=op['reason'],levels=[],structures=[],opportunities=[op],last_bar=iso(AT),live_bars={}))
    return dict(server_time=iso(AT),sent_ms=time.time()*1000,phase='合成数据 · 功能验证，不是真实市场机会',
                feed_state='不连接Wind，不写真实数据库',last_error='合成数据 · 仅用于功能与布局验证',offline=True,
                bootstrap_remaining=0,instruments=instruments,rules={'version':'SYNTHETIC'},metrics={'bar_batches':0},
                options={'contexts':{root:value[0] for root,value in views.items()},'quotes':{},'metrics':{}})


@app.get('/api/state')
async def state():return snapshot()


@app.get('/api/stream')
async def stream(request:Request):
    async def generate():
        while not await request.is_disconnected():
            yield 'event: update\ndata: '+json.dumps({'state':snapshot(),'events':[]},ensure_ascii=False)+'\n\n'
            await asyncio.sleep(1)
    return StreamingResponse(generate(),media_type='text/event-stream')


@app.get('/api/events')
async def events():return []


@app.get('/api/bars')
async def bars(contract:str,timeframe:int=5):
    from datetime import timedelta
    return [dict(contract=contract,timeframe=timeframe,end=iso(AT-timedelta(minutes=(40-i)*timeframe)),
                 open=100+sin(i),high=101+sin(i),low=99+sin(i),close=100.5+sin(i),volume=1000) for i in range(41)]


@app.get('/api/options/candidates')
async def comparison(root:str='IM',holding_days:int=5):
    context,rows,op=views[root]
    plans,reasons=candidates(op,context,rows,AT,CAL,CONFIG,holding_days)
    return dict(candidates=plans,reasons=reasons,context=context,opportunity=op,
                futures_reference=futures_reference(op,fixture['quote'](context['futures_price']),CONFIG))


@app.get('/api/options/chain')
async def chain(root:str='IM'):
    context,rows,op=views[root]
    return dict(context=context,rows=rows,metadata_count=len(rows))


@app.get('/api/options/settings')
async def settings():return CONFIG


@app.post('/api/options/fees')
async def fees(request:Request):
    data=await request.json();CONFIG['fee_per_contract_each_side'][data['root']]=data['fee']
    return {'ok':True,'synthetic':True}


@app.post('/api/telemetry')
async def telemetry():return {'ok':True,'synthetic':True}


@app.get('/api/options/research')
async def research():
    return dict(signals=0,observation_days=0,minimum_days=20,metrics={},evaluation_groups={},descriptive_comparison={},
                status='合成案例不进入效果研究',note='此验证服务与生产采集完全分离')


app.mount('/assets',StaticFiles(directory=ROOT/'frontend'/'dist'/'assets'),name='assets')


@app.get('/')
async def index():return FileResponse(ROOT/'frontend'/'dist'/'index.html')


if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8766)
