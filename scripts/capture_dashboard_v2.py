"""Archive the existing HTTP caches for V2 diagnostics; never call Wind."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'data' / 'dashboard-v2'


def get(path):
    with urlopen('http://127.0.0.1:8765' + path, timeout=20) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--saved', action='store_true', help='Use the previously saved state/context pair')
    args = parser.parse_args()
    DEST.mkdir(parents=True, exist_ok=True)
    if args.saved:
        state, context = [json.loads((DEST / name).read_text(encoding='utf-8'))
                          for name in ('state-source.json', 'context-source.json')]
    else:
        state = get('/api/state')
        context = get('/api/market-context')
    cutoff = state['server_time']
    bars = {}
    for instrument in state['instruments']:
        code = instrument['contract']
        # available_bars is a cache read, not chart-interest or a native query.
        bars[code] = [b for b in get('/api/bars?' + urlencode(dict(contract=code, timeframe=5)))
                      if b['end'] <= (instrument.get('market_time') or cutoff)]
        instrument.pop('live_bars', None)
        instrument['structures'] = instrument.get('structures', [])[-100:]
        instrument['opportunities'] = [o for o in instrument.get('opportunities', [])
                                       if o.get('trading_day') == instrument['trading_day']]
    events = [e for e in get('/api/events?limit=1000') if e.get('known_at', '') <= cutoff]
    snapshot = dict(captured_at=cutoff, mode='frozen', source='现有 WindPy 看板只读缓存',
                    state={k:state[k] for k in ('server_time', 'feed_state', 'last_error', 'offline', 'instruments')},
                    context=context, options=state.get('options', {}).get('contexts', {}),
                    bars=bars, events=events)
    (DEST / 'snapshot.json').write_text(json.dumps(snapshot, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    print(json.dumps(dict(captured_at=cutoff, instruments=len(bars), bars={k:len(v) for k,v in bars.items()},
                         events=len(events), output=str(DEST / 'snapshot.json')), ensure_ascii=False))


if __name__ == '__main__':
    main()
