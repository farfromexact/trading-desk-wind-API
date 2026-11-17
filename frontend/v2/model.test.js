import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizedMove,percentile,styleEvidence,sourceQuality,headline,radarItems,recentEvents,contracts,etfQuality} from './model.js';

const at='2026-11-17T10:30:00+08:00';
const row=value=>({value,time:at});
test('unknown or invalid normalization inputs remain missing, never calm',()=>{
  for(const v of [null,undefined,0,-1,NaN,Infinity])assert.equal(normalizedMove(2,v),null);
  assert.equal(normalizedMove(0,1),0);
  assert.equal(normalizedMove(-3,1.5),-2);
  assert.equal(percentile(3,[1,2,NaN]),null);
  assert.ok(Math.abs(percentile(10,Array.from({length:20},(_,n)=>n))-55)<1e-9);
});
test('mixed evidence is explicit rather than mislabelled as one supporting vote',()=>{
  const s=styleEvidence({rotation:{breadth_gap:row(25),strength:row(-.1),turnover:row(1.2)}},at,'交易中');
  assert.equal(s.label,'风格证据分歧');assert.equal(s.confirmed,false);
  assert.equal(s.rows[1].calibrated,false);
  const missing=styleEvidence({rotation:{breadth_gap:row(25)}},at,'交易中');
  assert.equal(missing.confirmed,false);assert.equal(missing.rows[2].verdict,'缺少源时间');
});

test('completed-minute metrics use their bar cadence without weakening live quote freshness',()=>{
  const s=styleEvidence({rotation:{strength:row(.1),turnover:row(1.2)}},'2026-11-17T10:36:20+08:00','交易中');
  assert.equal(s.rows[1].usable,true);assert.equal(s.rows[2].usable,true);
  assert.equal(sourceQuality(at,'2026-11-17T10:36:20+08:00','交易中',15),'数据滞后');
  const late=styleEvidence({rotation:{strength:row(.1)}},'2026-11-17T10:36:40+08:00','交易中');
  assert.equal(late.rows[1].usable,false);
});
test('stale or unhealthy real-time quotes cannot supply a radar distance',()=>{
  const snap={mode:'live',state:{server_time:at,instruments:[{root:'TL',contract:'TL2612.CFE',trading_day:'2026-11-17',
    phase:'交易中',ready:false,status:'历史数据同步中',market_time:at,quote:{rt_last:117.36},market:{tick_size:.01},
    opportunities:[{contract:'TL2612.CFE',trading_day:'2026-11-17',status:'confirmed',entry:117.39,stop:117.27}]}]},
    context:{cross:[{code:'TL2612.CFE',time:at,change_pct:1}]},bars:{}};
  const items=contracts(snap);assert.equal(items[0].quoteUsable,false);assert.equal(items[0].usable,false);
  assert.equal(radarItems(items)[0].distR,null);assert.equal(radarItems(items)[0].price,null);
  snap.state.instruments[0].ready=true;snap.view_time='2026-11-17T10:30:16+08:00';
  assert.equal(contracts(snap)[0].quoteUsable,false);
});
test('live card returns use the dated full close, independently of old context percentages',()=>{
  const snap={mode:'live',state:{server_time:at,instruments:[{root:'AG',contract:'AG2702.SHF',trading_day:'2026-11-17',
    phase:'交易中',ready:true,market_time:at,quote:{rt_last:105}}]},
    context:{error:'slow context',cross:[{code:'AG2702.SHF',time:'2026-11-17T09:00:00+08:00',change_pct:-2}]},
    bars:{'AG2702.SHF':[{end:'2026-11-16T15:00:00+08:00',close:100}]}};
  const item=contracts(snap)[0];
  assert.ok(Math.abs(item.pct-5)<1e-9);assert.equal(item.usable,true);assert.equal(item.basis,'较前收盘');
  assert.match(item.basisDetail,/2026-11-16/);assert.match(item.basisDetail,/-2.00%/);
  snap.bars={};assert.equal(contracts(snap)[0].pct,null);assert.equal(contracts(snap)[0].usable,false);
});
test('ETF old-day flows and failed context reads never appear current',()=>{
  const snap={state:{server_time:at,instruments:[{root:'IM',phase:'交易中'}]},context:{}};
  const flow={value:1,unit:'元',frequency:'intraday_cumulative',time:at};
  assert.equal(etfQuality(flow,snap),'快照内有效');
  assert.equal(etfQuality({...flow,time:'2026-11-16T10:30:00+08:00'},snap),'历史数据');
  snap.context.error='timeout';assert.equal(etfQuality(flow,snap),'资金流更新异常');
});
test('headline evidence excludes stale breadth and a failed context read',()=>{
  const snap={mode:'live',state:{server_time:at,feed_state:'Wind 已连接',instruments:[]},bars:{},context:{
    breadths:[{code:'000852.SH',value:66,time:at},{code:'000300.SH',value:23,time:'2026-11-16T10:30:00+08:00'}]}};
  assert.equal(headline(snap).items[0].evidence,'1000 / 300 上涨广度 66.0% / —');
  snap.context.error='timeout';
  assert.equal(headline(snap).items[0].evidence,'1000 / 300 上涨广度 — / —');
});
test('lunch and closing summaries never call a pre-break stalled quote current',()=>{
  assert.equal(sourceQuality('2026-11-17T15:00:00+08:00','2026-11-17T15:30:00+08:00','已收盘'),'收盘快照');
  assert.equal(sourceQuality('2026-11-17T14:50:00+08:00','2026-11-17T15:30:00+08:00','已收盘'),'数据滞后');
  assert.equal(sourceQuality('2026-11-17T15:00:00+08:00','2026-11-17T15:30:00+08:00','已收盘',15,'15:15'),'数据滞后');
});
test('source clocks reject stale, future and prior-date quotes; lunch remains a named snapshot',()=>{
  assert.equal(sourceQuality('2026-11-17T11:30:03+08:00','2026-11-17T12:59:00+08:00','午休'),'午间快照');
  assert.equal(sourceQuality('2026-11-17T10:00:03+08:00','2026-11-17T12:59:00+08:00','午休'),'数据滞后');
  assert.equal(sourceQuality('2026-11-16T11:30:03+08:00','2026-11-17T12:59:00+08:00','午休'),'历史数据');
  assert.equal(sourceQuality('2026-11-17T10:31:00+08:00',at),'源时间异常');
});
test('historical touch is never promoted to a near-trigger live signal',()=>{
  const instrument={root:'TL',contract:'TL2612.CFE',trading_day:'2026-11-17',price:117.36,market:{tick_size:.01},
    opportunities:[{contract:'TL2612.CFE',trading_day:'2026-11-17',entry:117.39,stop:117.27,status:'triggered',historical:true}]};
  const [r]=radarItems([instrument]);assert.equal(r.label,'历史K线覆盖触发价');assert.equal(r.near,false);assert.equal(r.distanceTicks,3);
  instrument.opportunities[0].trading_day='2026-11-16';assert.equal(radarItems([instrument]).length,0);
});
test('disconnection suppresses the headline, including with a locally cached connected state',()=>{
  const snap={state:{instruments:[],feed_state:'Wind 已连接'},context:{},bars:{}};
  assert.equal(headline(snap,true).paused,true);assert.deepEqual(headline(snap,true).items,[]);
  assert.equal(headline({...snap,state:{...snap.state,last_error:'timeout'}}).paused,true);
});
test('events use known time and cannot leak future or old events into 30-minute feed',()=>{
  const snap={captured_at:at,context:{},events:[],state:{instruments:[{root:'IM',market_time:at,structures:[
    {id:'past',kind:'structure',occurred_at:'2026-11-17T09:30:00+08:00',known_at:'2026-11-17T10:15:00+08:00'},
    {id:'future',kind:'structure',occurred_at:'2026-11-17T10:10:00+08:00',known_at:'2026-11-17T10:31:00+08:00'},
    {id:'old',kind:'structure',known_at:'2026-11-17T09:59:00+08:00'},
  ]}]}};
  assert.deepEqual(recentEvents(snap).items.map(e=>e.id),['past']);
});
