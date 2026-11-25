import test from 'node:test';
import assert from 'node:assert/strict';
import {deskGuidance, changesWhileAway} from './desk-guidance.js';
import {opportunityBook, opportunityEvidence, formatBookR} from './opportunity-evidence.js';

const stamp = '2026-11-25T09:36:24+08:00', now = Date.parse(stamp);
const oil = {root:'SC', contract:'SC2701.INE', trading_day:'2026-11-25', ready:true, phase:'交易中',
  market_time:stamp, market:{name:'原油', tick_size:.1, decimals:1}, quote:{rt_bid1:590.1, rt_ask1:590.2, rt_last:590.1}};
const op = {id:'sc', contract:oil.contract, trading_day:oil.trading_day, direction:1, setup:'pullback',
  status:'triggered', touch_at:stamp, updated_at:stamp, entry:590, stop:588, targets:[{price:594, source:'structural'}],
  rule_parameters:{minimum_rr:1.5}};
const state = (...items) => ({instruments:items, feed_state:'Wind 已连接', server_time:stamp, sent_ms:now, metrics:{started_at:1}});
const guide = (items, overrides = {}) => deskGuidance({...state(...items), ...overrides}, true, now);
const instrument = (ops, props = {}) => ({...oil, opportunities:ops, ...props});

test('long and short boundaries include equality and use the adverse side and two ticks of slippage', () => {
  const pairs = [
    [op, 590.2, 590.3, 590.8],
    [{...op, direction:-1, stop:592, targets:[{price:586, source:'structural'}]}, 589.8, 589.7, 589.2],
  ];
  for (const [plan, edge, outside, late] of pairs) {
    for (const [value, within] of [[edge,true], [outside,false], [late,false]]) {
      const quote = plan.direction === 1 ? {rt_ask1:value, rt_bid1:value-.1} : {rt_bid1:value, rt_ask1:value+.1};
      const item = instrument([plan], {quote:{...quote, rt_last:value}});
      const book = opportunityBook(plan, item, true);
      assert.equal(book.limit, edge);
      assert.equal(book.within, within);
      if (value === late) {
        assert.equal(book.rr.toFixed(2), '1.00');
        assert.match(opportunityEvidence(plan, item).execution, /1.00R/);
        assert.match(guide([item]).title, /空仓跳过/);
      }
    }
  }
});

test('a three-decimal tick rounds down for a long and up for a short, and the trigger floor still applies', () => {
  const item = instrument([], {market:{tick_size:.005}, quote:{rt_bid1:99.98, rt_ask1:99.985}});
  const long = {...op, entry:100, stop:99.9, targets:[{price:100.21, source:'structural'}]};
  assert.equal(opportunityBook(long, item, true).limit, 100.01);
  const short = {...long, direction:-1, stop:100.1, targets:[{price:99.79, source:'structural'}]};
  assert.equal(opportunityBook(short, item, true).limit, 99.99);
  const thin = {...long, targets:[{price:100.15, source:'structural'}], status:'confirmed'};
  assert.equal(opportunityBook(thin, item, true).within, false);
  assert.match(guide([{...item, quote:{...item.quote, rt_last:99.98}, opportunities:[thin]}]).title, /尚未触价.*空间不足/);
});

test('display does not round a missed threshold up to an apparently qualifying R', () => {
  const plan = {...op, entry:100, stop:90, targets:[{price:115.015, source:'structural'}]};
  const item = instrument([plan], {market:{tick_size:.005}, quote:{rt_bid1:99.995, rt_ask1:100}});
  const book = opportunityBook(plan,item,true);
  assert.equal(book.rr.toFixed(2), '1.50');
  assert.equal(formatBookR(book), '<1.5');
});

test('live guidance stays separate from projections, missing quotes and unverified old limits', () => {
  for (const change of [{rule_parameters:null}, {target_source:'synthetic'}, {targets:[{price:594}]}]) {
    const plan = {...op, ...change};
    assert.equal(guide([instrument([plan])]).book.within, null);
    assert.match(guide([instrument([plan])]).title, /缺少/);
  }
  for (const quote of [{rt_bid1:0,rt_ask1:590.2}, {rt_bid1:591,rt_ask1:590.2}]) {
    assert.match(guide([instrument([op], {quote:{...quote,rt_last:590.1}})]).title, /等有效双边盘口/);
  }
  const old = {...op, historical:true};
  for (const plan of [old, {...op, contract:'SC2612.INE'}, {...op, trading_day:'2026-11-24'}, {...op, status:'expired'}]) {
    assert.equal(guide([instrument([plan])]).atmosphere.tone, 'idle');
  }
});

test('the next missing watch step, completion window and actual touch have distinct instructions', () => {
  const watch = {...op, status:'watching'};
  for (const [props, expected] of [[{},/回踩突破位/], [{touched:true},/回踩已出现/], [{setup:'failed_break'},/收回关键位/],
    [{setup:'failed_break',returned_index:0},/下一根/], [{preview_only:true},/扫过/]]) {
    assert.match(guide([instrument([{...watch, ...props}])]).title, expected);
  }
  const waiting = guide([instrument([{...op,status:'confirmed',remaining_bars:2}])]);
  assert.match(waiting.title, /向上触及 590.0/);
  assert.match(waiting.detail, /2 根完整/);
  assert.match(guide([instrument([op])]).title, /已触价，核对/);
  assert.match(guide([instrument([op], {quote:{...oil.quote,rt_last:589.9}})]).title, /退回/);
});

test('focus picks a fresh qualified touch, then a valid waiting plan, before an old touch or a poor book', () => {
  const waiting = {...op, id:'waiting', status:'confirmed'};
  const old = {...op, touch_at:'2026-11-25T09:25:00+08:00'};
  assert.equal(guide([instrument([waiting,op])]).op.id, op.id);
  assert.equal(guide([instrument([waiting,old])]).op.id, waiting.id);
  const other = instrument([{...waiting,contract:'AG2702.SHF'}], {contract:'AG2702.SHF',root:'AG'});
  const pick = guide([other, instrument([op], {quote:{rt_bid1:590.7,rt_ask1:590.8,rt_last:590.7}})]);
  assert.equal(pick.op.contract, other.contract);
  assert.equal(pick.others, 1);
  const watch = {...op, id:'watch', status:'watching'};
  assert.equal(guide([instrument([watch,old])]).op.id, 'watch');
});

test('pause the affected contract, distinguish unavailable data and closure from waiting, and mute replay', () => {
  const bad = instrument([op], {ready:false, status:'分钟线缺失'});
  const other = instrument([], {root:'AG',contract:'AG2702.SHF'});
  const partial = guide([bad, other], {last_error:bad.status});
  assert.equal(partial.live, true);
  assert.match(partial.pause,/SC.*09:36/);
  assert.match(partial.title,/继续等/);
  assert.equal(guide([bad,{...other,opportunities:[{...op,contract:other.contract}]}], {last_error:bad.status}).op.root, 'AG');
  for (const item of [bad, instrument([op],{market_time:'2026-11-25T09:36:08+08:00'}), instrument([op],{market_time:null})]) {
    assert.match(guide([item]).title,/暂停判断 SC/);
  }
  assert.match(guide([instrument([op],{phase:'已收盘'})]).title, /下个交易时段/);
  assert.match(guide([instrument([op],{phase:'等待交易日历'})]).title, /等交易日历/);
  assert.match(guide([]).title, /等交易日历/);
  assert.equal(guide([instrument([op])], {last_error:'全局连接错误'}).live, false);
  assert.equal(guide([instrument([op])], {offline:true}).live, false);
  assert.equal(deskGuidance(state(instrument([op])),true,now,true).live, false);
  assert.equal(deskGuidance(state(instrument([op])),true,now+16000).live, false);
  assert.equal(deskGuidance(state(instrument([op])),false,now).live, false);
});

test('return recap uses actual stages only within the absence on the same stream session, contract and trading day', () => {
  const before = state(instrument([{...op,status:'confirmed'}]));
  const after = state(instrument([op]));
  const compare = (b,a) => changesWhileAway(b,a,now-3000,now+1000);
  assert.match(compare(before,after), /SC 待触价 → 已触价/);
  assert.equal(compare(after,after), '');
  for (const changed of [instrument([{...op,historical:true}]), instrument([{...op,updated_at:'2026-11-25T09:37:00+08:00'}]),
    instrument([{...op,updated_at:'2026-11-25T09:35:00+08:00'}]), instrument([op],{contract:'SC2612.INE'}), instrument([op],{trading_day:'2026-11-26'})]) {
    assert.equal(compare(before,state(changed)), '');
  }
  assert.equal(compare(before,{...after,metrics:{started_at:2}}), '');
});
