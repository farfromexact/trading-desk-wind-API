import test from 'node:test';
import assert from 'node:assert/strict';
import {structureOverlay} from './structure-overlay.js';

const contract = 'IM2612.CFE';
const end = hm => '2026-11-20T' + hm + ':00+08:00';
const epoch = hm => Date.parse(end(hm)) / 1000;
const bar = (hm, open, high, low, close) => ({end: end(hm), contract, timeframe: 5, open, high, low, close});
const candles = [
  bar('10:00', 97, 100, 96, 99), bar('10:05', 99, 99, 96, 97), bar('10:10', 97, 99, 97, 98),
  bar('10:15', 98, 99, 95, 97), bar('10:20', 97, 100, 96, 99), bar('10:25', 99, 104, 98, 103),
];
const pivot = {kind: 'pivot', contract, side: 'high', price: 100, scale: 'swing', occurred_at: end('10:00'),
  confirmed_bar: end('10:15'), known_at: end('10:17')};
const brk = {kind: 'structure', contract, scale: 'swing', direction: 1, price: 100, label: 'BOS',
  occurred_at: end('10:25'), known_at: end('10:27'), level_known_at: end('10:16')};
const model = (overrides = {}) => structureOverlay({contract, timeframe: 5, bars: candles, structures: [pivot, brk],
  asOf: end('11:00'), detail: 'swing', showZones: true, ...overrides});

test('a break becomes visible at confirmation, at the broken price and from the actual pivot', () => {
  assert.equal(model({asOf: end('10:26')}).segments.length, 0);
  assert.equal(model({asOf: end('10:26')}).zones.length, 0);
  const result = model({asOf: end('10:27')});
  assert.deepEqual(result.segments, [{from: epoch('10:00'), to: epoch('10:25'), price: 100, direction: 1, scale: 'swing', label: 'BOS'}]);
  assert.deepEqual(result.markers, []);
  assert.deepEqual(result.breaksByTime.get(epoch('10:25')), [brk]);
  assert.deepEqual(result.zones, [{from: epoch('10:25'), source: end('10:15'), direction: 1, low: 95, high: 99}]);
});

test('old snapshots and truncated history keep the confirmed event without a guessed pivot or band', () => {
  for (const p of [null, {...pivot, price: 99}, {...pivot, contract: 'IF2612.CFE'},
    {...pivot, confirmed_bar: end('09:45')}, {...pivot, known_at: end('10:28')}]) {
    const result = model({structures: [p, brk].filter(Boolean)});
    assert.equal(result.segments.length, 0);
    assert.equal(result.zones.length, 0);
    assert.deepEqual(result.markers, [brk]);
  }
  assert.equal(model({contract: 'IF2612.CFE'}).breaksByTime.size, 0);
});

test('detail preserves both confirmations at a shared level, drawing a single swing line', () => {
  const internal = {...brk, scale: 'internal', label: 'CHoCH'};
  const events = [pivot, {...pivot, scale: 'internal'}, internal, brk];
  const detailed = model({structures: events, detail: 'all'});
  assert.deepEqual(detailed.breaksByTime.get(epoch('10:25')), [internal, brk]);
  assert.equal(detailed.segments.length, 1);
  assert.equal(detailed.segments[0].label, 'BOS');
  assert.equal(detailed.markers.length, 2);
  assert.deepEqual(model({structures: events}).breaksByTime.get(epoch('10:25')), [brk]);
  assert.equal(model({detail: 'off'}).breaksByTime.size, 0);
  assert.equal(model({showZones: false}).zones.length, 0);
});

test('a zone survives wick tests and closes at the boundary, and expires on an adverse finalized 5m close', () => {
  const onEdge = bar('10:30', 100, 101, 93, 95), through = bar('10:35', 95, 96, 92, 94);
  assert.equal(model({bars: [...candles, onEdge, {...through, provisional: true}]}).zones.length, 1);
  assert.equal(model({bars: [...candles, onEdge, through]}).zones.length, 0);
  const reverse = c => ({...c, high: 200-c.low, low: 200-c.high, open: 200-c.open, close: 200-c.close});
  const structures = [{...pivot, side: 'low'}, {...brk, direction: -1, label: 'CHoCH'}];
  const down = model({bars: candles.map(reverse), structures});
  assert.deepEqual(down.zones, [{from: epoch('10:25'), source: end('10:15'), direction: -1, low: 101, high: 105}]);
  assert.equal(model({bars: [...candles, through].map(reverse), structures}).zones.length, 0);
});

test('1m and 15m project the same confirmed 5m region and never derive it from another timeframe', () => {
  const fifteen = ['10:00', '10:15', '10:30'].map(hm => ({end: end(hm), timeframe: 15, contract}));
  const coarse = model({bars: fifteen, timeframe: 15, fiveMinuteBars: candles});
  assert.equal(coarse.segments[0].to, epoch('10:30'));
  assert.equal(coarse.zones[0].low, 95);
  const minute = ['10:23', '10:24', '10:25'].map(hm => ({...bar(hm, 90, 91, 88, 89), timeframe: 1}));
  const fine = model({bars: minute, timeframe: 1, fiveMinuteBars: candles});
  assert.equal(fine.segments[0].from, epoch('10:23'));
  assert.equal(fine.zones[0].source, end('10:15'));
  assert.equal(model({bars: minute, timeframe: 1, lastFiveBar: end('10:30'), fiveMinuteBars: candles}).zones.length, 0);
  assert.equal(model({bars: minute, timeframe: 1}).zones.length, 0);
  assert.equal(model({bars: fifteen, timeframe: 15, structures: [pivot, {...brk, occurred_at: end('11:30')}]}).segments.length, 0);
});

test('a shared 15m candle or a missing break candle never stretches a segment into a different candle', () => {
  const grouped = model({timeframe: 15, bars: [{end: end('10:15')}, {end: end('10:30')}],
    structures: [{...pivot, occurred_at: end('10:20'), confirmed_bar: end('10:20'), known_at: end('10:22')},
      {...brk, level_known_at: end('10:21')}], fiveMinuteBars: candles});
  assert.equal(grouped.segments.length, 0);
  assert.equal(grouped.markers[0].occurred_at, brk.occurred_at);
  const acrossLunch = model({timeframe: 15, bars: [{end: end('11:30')}, {end: end('13:15')}],
    asOf: end('13:16'), structures: [pivot, {...brk, occurred_at: end('13:00'), known_at: end('13:01')}]});
  assert.equal(acrossLunch.breaksByTime.size, 0);
});

test('a missing source candle or a leg with no opposite body cannot generate a zone', () => {
  const incomplete = candles.map(c => c.end === end('10:20') ? {...c, open: undefined} : c);
  assert.equal(model({bars: incomplete}).zones.length, 0);
  const allUp = candles.map(c => ({...c, open: c.low, close: c.high}));
  assert.equal(model({bars: allUp}).zones.length, 0);
  assert.equal(model({bars: allUp}).segments.length, 1);
});
