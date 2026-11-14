import json

from fastapi.testclient import TestClient

from scripts.preview_market_context import create_preview


def preview(tmp_path):
    data=tmp_path/'data';ui=tmp_path/'ui'
    data.mkdir();(ui/'assets').mkdir(parents=True)
    (ui/'context-preview.html').write_text('<html>preview</html>',encoding='utf-8')
    (data/'market-context.json').write_text('{}',encoding='utf-8')
    (data/'preview-instruments.json').write_text(json.dumps([{'root':'IM','contract':'IM2612.CFE'}]),encoding='utf-8')
    bar={'contract':'IM2612.CFE','timeframe':5,'end':'2026-11-13T15:00:00+08:00','open':100,'high':101,'low':99,'close':100,'volume':1}
    pack={'state':{'offline':True,'mode':'historical_preview','preview_day':'2026-11-13',
                   'options':{'contexts':{'IM':{'ready':False,'readonly_preview':True,'expiries':[]}}}},
          'bars':{'IM2612.CFE':{'5':[bar]}},'events':[]}
    (data/'desk-preview.json').write_text(json.dumps(pack),encoding='utf-8')
    return TestClient(create_preview(data,ui)),bar


def test_integrated_preview_only_returns_supplied_history_and_keeps_unknown_data_empty(tmp_path):
    client,bar=preview(tmp_path)
    with client:
        assert client.get('/api/state').json()['offline'] is True
        assert client.get('/api/state').headers['Cache-Control']=='no-store'
        assert client.get('/api/bars?contract=IM2612.CFE&timeframe=5').json()==[bar]
        assert client.get('/api/bars?contract=IM2612.CFE&timeframe=1').json()==[]
        assert client.get('/api/bars?contract=../private&timeframe=5').status_code==400
        assert client.get('/api/options/candidates').json()['candidates']==[]
        assert client.get('/api/market-context').json()['disabled'] is True


def test_integrated_preview_cannot_change_watchlist_start_collectors_or_write_fees(tmp_path):
    client,_=preview(tmp_path)
    with client:
        for path in ('watchlist','chart-interest','market-context/refresh','market-context/monitor','options/fees'):
            assert client.post('/api/'+path,json={}).status_code==403
        assert client.get('/api/state',headers={'Host':'external.example'}).status_code==400
