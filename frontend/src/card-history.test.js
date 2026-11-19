import test from 'node:test';
import assert from 'node:assert/strict';
import {nextCardRead} from './use-card-bars.js';
import {contracts} from '../shared/market-model.js';

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

test('preopen index card compares the last actual close to its own previous close', () => {
  const asof = '2026-11-19T08:55:05+08:00';
  const closes = [
    {end:'2026-11-17T15:00:00+08:00', close:6023},
    {end:'2026-11-18T14:55:00+08:00', close:5988},
    {end:'2026-11-18T15:00:00+08:00', close:5987.4},
  ];
  const index = {...im, trading_day:'2026-11-19', phase:'盘前', ready:false,
    market:{sessions:[['09:30','11:30'], ['13:00','15:00']]}};
  for (const [market_time, price] of [['2026-11-18T15:38:27+08:00',5987.4], [asof,0]]) {
    const instrument = {...index, market_time, quote:{rt_last:price}};
    const [card] = contracts({state:{instruments:[instrument], server_time:asof},
      mode:'live', bars:{[im.contract]:closes}});
    assert.equal(card.changeTime, closes.at(-1).end);
    assert.equal(card.price, 5987.4);
    assert.equal(card.pct.toFixed(2), '-0.59');
    assert.equal(card.quoteUsable, false);
    assert.deepEqual(card.daily, closes.slice(1));
  }
});

test('original SC card keeps the Friday night curve through midnight until a Monday day bar is available', () => {
  const sc = {root:'SC', contract:'SC2701.INE', trading_day:'2026-11-23', phase:'交易中', ready:true,
    quote:{rt_last:587}, market:{night:['21:00','02:30'], sessions:[['09:00','10:15'],['10:30','11:30'],['13:30','15:00']]}};
  const close = {end:'2026-11-20T15:00:00+08:00', close:581.4};
  const night = [{end:'2026-11-20T21:05:00+08:00', close:585}, {end:'2026-11-21T02:30:00+08:00', close:590.5}];
  const dayBar = {end:'2026-11-23T09:05:00+08:00', close:587.2};
  const bars = {[sc.contract]: [close, ...night]};
  const cardAt = time => contracts({mode:'live', state:{server_time:time, instruments:[{...sc, market_time:time}]}, bars})[0];
  const before = cardAt('2026-11-23T09:03:35+08:00');
  assert.deepEqual(before.daily, night);
  assert.equal(before.curveSession.name, '夜盘');
  assert.equal(before.quoteUsable, true);
  assert.equal(before.prevClose, close.close);
  bars[sc.contract].push(dayBar);
  const after = cardAt('2026-11-23T09:06:08+08:00');
  assert.deepEqual(after.daily, [dayBar]);
  assert.equal(after.curveSession.name, '日盘');
});
