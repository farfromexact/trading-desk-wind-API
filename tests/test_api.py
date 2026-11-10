from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.service import Desk


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr('app.main.Desk',lambda **kwargs:Desk(tmp_path,offline=True))
    with TestClient(app) as c:
        yield c


def test_state_and_contract_guard(client):
    s=client.get('/api/state')
    assert s.status_code==200
    assert s.json()['offline'] is True
    assert [x['root'] for x in s.json()['instruments']]==['IM','IF','IH','AG','AU','SC','T','TL']
    assert all(x['contract'] is None and not x['ready'] for x in s.json()['instruments'])
    assert client.get('/api/bars?contract=IM2612.CFE&timeframe=7').status_code==400
    assert client.post('/api/watchlist',json={'root':'IM','contract':'IF2612.CFE'}).status_code==400
    assert client.post('/api/watchlist',json={'root':'IM','contract':'IM.CFE'}).status_code==400


def test_remote_origin_cannot_change_watchlist(client):
    r=client.post('/api/watchlist',json={'root':'IM','contract':'IM2611.CFE'},headers={'Origin':'https://external.example'})
    assert r.status_code==403
    r=client.get('/api/state',headers={'Host':'external.example'})
    assert r.status_code==400


def test_replay_validation_and_malformed_requests(client):
    assert client.get('/api/replay?contract=../../other&day=2026-11-09').status_code==400
    assert client.get('/api/replay?contract=IM2612.CFE&day=invalid').status_code==400
    assert client.post('/api/watchlist',json={'contract':'IM2612.CFE'}).status_code==422


def test_latency_measurements_distinguish_transport_from_source(client):
    assert client.post('/api/telemetry',json={'delay_ms':20,'source_delay_ms':950}).status_code==200
    m=client.get('/api/state').json()['metrics']
    assert m['ui_max_ms']==20
    assert m['source_ui_max_ms']==950
    assert m['source_ui_samples']==1
    assert m['source_ui_over_2s']==0


def test_options_endpoints_and_fee_authorization(client):
    assert client.get('/api/options/context?root=T').json()['status']=='无已验证覆盖'
    assert client.get('/api/options/chain?root=IM').json()['rows']==[]
    assert client.get('/api/options/candidates?root=IM&holding_days=3').json()['candidates']==[]
    assert client.get('/api/options/candidates?root=IM&holding_days=2').status_code==400
    assert client.get('/api/options/chain?root=BAD').status_code==400
    assert client.get('/api/options/audit?kind=BAD').status_code==400
    assert client.get('/api/options/audit?kind=signal').json()==[]
    assert client.post('/api/options/fees',json={'root':'IM','fee':2.5},headers={'Origin':'https://external.example'}).status_code==403
    assert client.post('/api/options/fees',json={'root':'IM','fee':-1}).status_code==400
    assert client.post('/api/options/fees',json={'root':'T','fee':1}).status_code==400
    before=client.get('/api/options/settings').json()['hash']
    r=client.post('/api/options/fees',json={'root':'IM','fee':2.5})
    assert r.status_code==200 and r.json()['hash']!=before
    assert client.get('/api/options/settings').json()['fee_per_contract_each_side']['IM']==2.5
    assert client.post('/api/options/fees',json={'root':'IM','fee':None}).status_code==200
    assert client.get('/api/options/settings').json()['fee_per_contract_each_side']['IM'] is None
    assert client.get('/api/options/research').json()['signals']==0
    client.post('/api/telemetry',json={'delay_ms':50,'option_delay_ms':2500})
    metrics=client.get('/api/options/research').json()['metrics']
    assert metrics['ui_over_2s']==1 and metrics['ui_samples']==1
