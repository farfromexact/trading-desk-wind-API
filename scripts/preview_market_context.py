"""Serve an isolated, read-only historical preview. Does not start Desk or WindPy."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
import uvicorn
from app.market_context import ContextBook


def create_preview(data, ui):
    app=FastAPI(title='盘面底色 · 历史预览')
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])
    book=ContextBook(json.loads((data/'market-context.json').read_text(encoding='utf-8')))
    instruments=json.loads((data/'preview-instruments.json').read_text(encoding='utf-8'))
    desk=json.loads((data/'desk-preview.json').read_text(encoding='utf-8'))
    @app.middleware('http')
    async def readonly(request,call_next):
        if request.method not in ('GET','HEAD'):
            return JSONResponse({'detail':'历史布局预览只读'},status_code=403)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        return response
    @app.get('/api/market-context')
    async def context():
        return dict(**book.view(),preview_instruments=instruments,disabled=True,monitoring=False,
                    busy=False,calls=0,error=None,mode='historical_preview')
    @app.get('/api/state')
    async def state():return desk['state']
    @app.get('/api/bars')
    async def bars(contract:str,timeframe:int=5):
        if timeframe not in (1,5,15):raise HTTPException(400,'未知周期')
        if contract not in {i['contract'] for i in instruments if i.get('contract')}:
            raise HTTPException(400,'合约不在历史样本中')
        return desk['bars'].get(contract,{}).get(str(timeframe),[])
    @app.get('/api/events')
    async def events(limit:int=150):return desk['events'][:min(max(limit,1),1000)]
    @app.get('/api/options/candidates')
    async def candidates():return {'candidates':[],'reasons':['历史样本未载入期权双边盘口，不推定交易工具方案']}
    @app.get('/api/options/context')
    async def option_context(root:str='IM'):
        if root not in desk['state']['options']['contexts']:raise HTTPException(400,'未知品种')
        return desk['state']['options']['contexts'][root]
    @app.get('/api/options/chain')
    async def chain(root:str='IM'):return dict(context=await option_context(root),rows=[],metadata_count=0)
    @app.get('/api/options/settings')
    async def settings():return {'fee_per_contract_each_side':{}}
    @app.get('/api/options/research')
    async def research():return dict(metrics={},status='历史布局预览',note='未加载期权观察记录，不推定样本数量或收益')
    @app.get('/')
    async def index():return FileResponse(ui/'context-preview.html')
    app.mount('/assets',StaticFiles(directory=ui/'assets'),name='assets')
    return app


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--ui',type=Path,required=True)
    parser.add_argument('--port',type=int,default=8786)
    args=parser.parse_args()
    uvicorn.run(create_preview(args.data,args.ui),host='127.0.0.1',port=args.port,log_level='warning')
