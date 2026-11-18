import test from 'node:test';
import assert from 'node:assert/strict';
import {nextCardRead} from './use-card-bars.js';
import {contracts} from '../v2/model.js';

const day = '2026-11-18';
const at = '2026-11-18T10:30:00+08:00';
const im = {root: 'IM', contract: 'IM2612.CFE', phase: '交易中', trading_day: day, last_bar: at};
const ag = {root: 'AG', contract: 'AG2702.SHF', phase: '交易中', trading_day: day, last_bar: at};
const entries = () => new Map([[ag.contract, {...ag, retry: false, nextRead: 300000}]]);

test('card cache reuses the selected 5m chart and ignores ordinary quote ticks', () => {
  assert.equal(nextCardRead([im, ag], im.contract, new Map(), 0, false), ag);
  assert.equal(nextCardRead([im, {...ag, quote: {rt_last: 8860}}], im.contract, entries(), 290000, false), undefined);
  assert.equal(nextCardRead([im, ag], im.contract, entries(), 300000, false), ag);
  assert.equal(nextCardRead([im, {...ag, phase: '已收盘'}], im.contract, entries(), 600000, false), undefined);
  assert.equal(nextCardRead([im, ag], im.contract, entries(), 600000, true), undefined);
});

test('new mapping, trading day or closed candle invalidates the corresponding curve', () => {
  for (const change of [{contract:'AG2703.SHF'}, {trading_day:'2026-11-19'}, {last_bar:'2026-11-18T10:35:00+08:00'}]) {
    const updated = {...ag, ...change};
    assert.equal(nextCardRead([im, updated], im.contract, entries(), 1000, false), updated);
  }
});

test('missing bars never turn invalid quotes into a zero return or a settlement-based return', () => {
  const state = {server_time: at, instruments: [{...im, ready: true, market_time: at, quote: {rt_last: 101}}]};
  const data = {state, mode:'live', context:{}, bars:{}};
  assert.equal(contracts(data)[0].pct, null);
  data.bars[im.contract] = [{end:'2026-11-17T15:00:00+08:00', close:100}];
  assert.ok(Math.abs(contracts(data)[0].pct - 1) < 1e-8);
  state.instruments[0].quote.rt_last = 0;
  assert.equal(contracts(data)[0].pct, null);
});
