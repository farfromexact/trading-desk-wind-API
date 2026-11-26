import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.desk_stream import REUSABLE_FIELDS, StreamHistory
from app.main import stream
from app.service import Desk


def test_independent_subscribers_observe_in_place_changes_and_explicit_clears():
    history = StreamHistory()
    item = dict(root='SC', contract='SC2701.INE', trading_day='2026-11-26',
                opportunities=[{'status': 'confirmed', 'targets': [{'price': 601}]}],
                structures=[{'kind': 'pivot'}], levels=[], live_bars={'5': []})
    state = {'instruments': [item], 'sent_ms': 10}
    first, a = history.compact(state, {})
    assert first == state
    delta, _ = history.compact(state, a)
    assert set(delta['instruments'][0]).isdisjoint(REUSABLE_FIELDS)
    assert delta['sent_ms'] == 10

    full, b = history.compact(state, {})
    assert full == state
    item['opportunities'][0].update(status='triggered', touch_price=599)
    item['opportunities'][0]['targets'][0]['price'] = 603
    change, a = history.compact(state, a)
    assert change['instruments'][0]['opportunities'] == item['opportunities']
    item['live_bars']['5'].append({'close': 600})
    later, b = history.compact(state, b)
    assert later['instruments'][0]['opportunities'] == item['opportunities']
    assert later['instruments'][0]['live_bars'] == item['live_bars']
    assert 'structures' not in later['instruments'][0]
    item['opportunities'].clear()
    assert history.compact(state, a)[0]['instruments'][0]['opportunities'] == []
    assert set(item).issuperset(REUSABLE_FIELDS)


def test_new_day_new_contract_and_returning_instrument_send_full_history():
    history = StreamHistory()
    item = dict(root='IM', contract='IM2612.CFE', trading_day='2026-11-25',
                opportunities=[], structures=[], levels=[], live_bars={})
    state = {'instruments': [item]}
    _, previous = history.compact(state, {})
    for changes in ({'trading_day': '2026-11-26'}, {'contract': 'IM2701.CFE'}):
        item.update(changes)
        packet, previous = history.compact(state, previous)
        assert packet == state
    history.compact({'instruments': []}, previous)
    assert history.compact(state, previous)[0] == state


def test_stream_endpoint_preserves_legacy_frames_heartbeat_and_event_order(tmp_path, monkeypatch):
    desk = Desk(tmp_path, offline=True)
    state = SimpleNamespace(desk=desk, stream_history=StreamHistory())
    request = SimpleNamespace(app=SimpleNamespace(state=state), is_disconnected=AsyncMock(return_value=False))
    monkeypatch.setattr('app.main.asyncio.sleep', AsyncMock())

    async def read(iterator):
        block = await anext(iterator)
        header, data = block.split('\ndata: ')
        return header, json.loads(data)

    async def check():
        legacy = (await stream(request)).body_iterator
        compact = (await stream(request, compact=True)).body_iterator
        try:
            _, full = await read(compact)
            _, original = await read(legacy)
            assert full['frame'] == 1
            assert 'frame' not in original
            assert full['state']['instruments'] == original['state']['instruments']
            for i in (1, 2):
                desk.store.event({'id': f'event-{i}', 'kind': 'opportunity', 'audible': i == 1})
            header, update = await read(compact)
            old_header, old_update = await read(legacy)
            assert update['frame'] == 2 and update['state']['sent_ms'] >= full['state']['sent_ms']
            assert header == old_header and header.startswith('id: 2\n')
            assert update['events'] == old_update['events']
            assert [e['id'] for e in update['events']] == ['event-1', 'event-2']
            assert all(set(item).isdisjoint(REUSABLE_FIELDS) for item in update['state']['instruments'])
            assert all(set(item).issuperset(REUSABLE_FIELDS) for item in old_update['state']['instruments'])
        finally:
            await compact.aclose()
            await legacy.aclose()
    try:
        asyncio.run(check())
    finally:
        desk.stop()
