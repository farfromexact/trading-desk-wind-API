import test from 'node:test';
import assert from 'node:assert/strict';
import {contracts, feedHealth, headline} from '../shared/market-model.js';

test('the brief uses the same completed bars and eligible quotes as the original cards', () => {
  const at = '2026-11-18T21:55:00+08:00';
  const instruments = [['AG', '白银', 98], ['AU', '黄金', 99], ['SC', '原油', 101]].map(([root, name, price]) => ({
    root, contract: root + '2612', trading_day: '2026-11-19', market: {name},
    phase: '交易中', ready: true, market_time: at, quote: {rt_last: price},
  }));
  const bars = Object.fromEntries(instruments.map(i => [i.contract, [{end: '2026-11-18T15:00:00+08:00', close: 100}]]));
  const snapshot = {state: {server_time: at, feed_state: 'Wind 已连接', instruments}, mode: 'live', view_time: at,
    bars, context: {error: '背景查询超时'}};
  const items = contracts(snapshot);
  const cached = {...snapshot, get bars() { throw Error('行情卡已经计算过'); }};
  const summary = headline(cached, false, items);
  assert.equal(summary.title, '风格证据不足，白银领跌。');
  assert.equal(summary.subtitle, '国债数据不足。暂无已确认的触发价。');

  instruments[0].ready = false;
  assert.equal(headline(snapshot).title, '风格证据不足，商品等待确认。');
  const stalled = feedHealth(snapshot, {connected: true}, Date.parse(at) + 16000);
  assert.equal(headline(cached, !stalled.connected, items).paused, true);
});

test('dated breadth can describe the preopen review and expires as live evidence when trading starts', () => {
  const time = '2026-11-18T15:00:00+08:00';
  const equity = {root:'IM', phase:'盘前', market:{name:'中证1000'}};
  const snapshot = {mode:'live', state:{server_time:'2026-11-19T08:30:00+08:00', feed_state:'Wind 已连接', instruments:[equity]},
    context:{rotation:{breadth_gap:{value:-21.1, time}}}, bars:{}};
  const review = headline(snapshot);
  assert.equal(review.phase, '盘前');
  assert.match(review.title, /大盘广度占优/);
  equity.phase = '交易中';
  snapshot.state.server_time = '2026-11-19T09:30:16+08:00';
  assert.match(headline(snapshot).title, /风格证据不足/);
});
