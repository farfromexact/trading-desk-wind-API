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
