import {test} from 'node:test';
import assert from 'node:assert/strict';
import {contextMetrics, positionMetric, sourceStatus, crossRows, etfRows} from './market-context.js';

test('missing values stay missing; zero remains a valid measurement',()=>{
  assert.equal(contextMetrics(null)[0].value,'—');
  assert.equal(contextMetrics({breadth:{value:0},style:{value:0},activity:{IM:{value:0,baseline_days:10}}})[0].value,'0.0%');
  assert.equal(contextMetrics({activity:{IM:{value:0,baseline_days:10}}})[2].value,'0.00×');
});
test('timestamps do not pass old or future observations as current',()=>{
  assert.equal(sourceStatus('2026-11-13T15:00:00+08:00','2026-11-14T10:00:00+08:00'),'历史');
  assert.equal(sourceStatus('2026-11-13T10:10:00+08:00','2026-11-13T10:00:00+08:00'),'时间异常');
  assert.equal(sourceStatus('2026-11-13T09:59:00+08:00','2026-11-13T10:00:00+08:00'),'最近');
});
test('position requires healthy live state and already-known levels',()=>{
  const item={root:'IM',contract:'IM2612.CFE',ready:true,phase:'交易中',market_time:'2026-11-13T14:00:00+08:00',quote:{rt_last:100},market:{decimals:1},levels:[
    {name:'已知支撑',price:98,known_at:'2026-11-13T13:50:00+08:00'},
    {name:'未来结构',price:100,known_at:'2026-11-13T14:05:00+08:00'}]};
  assert.equal(positionMetric(item).value,'上方 2.0');
  assert.equal(positionMetric(item,false).value,'等待行情');
  assert.equal(positionMetric({...item,ready:false}).value,'等待行情');
});
test('cross-market rows retain their own dates, no total flow is synthesized',()=>{
  const rows=crossRows({cross:[{code:'AU2612.SHF',time:'2026-11-14T02:30:00+08:00',trading_day:'20261116',change_pct:-.21,amount:1e8},
    {code:'T2612.CFE',time:'2026-11-13T15:15:00+08:00',trading_day:'20261113',change_pct:0}]});
  assert.equal(rows[0].trading_day,'20261116');assert.equal(rows[0].group,'商品样本');
  assert.equal(rows[1].group,'国债');assert.equal(rows[1].value,'0.00%');assert.equal(rows[1].amountLabel,'未取得');
});

test('ETF daily dates and intraday clocks stay separate; missing and zero are distinct',()=>{
  const rows=etfRows({etfs:[{code:'512100.SH',flow:{value:-779190837,time:'2026-11-13T15:30:29+08:00',unit:'元',frequency:'intraday_cumulative'},
    daily:{value:0,data_date:'2026-11-12',unit:'元',frequency:'daily'}}]},'2026-11-14T14:00:00+08:00');
  assert.equal(rows[0].flow.display,'-7.79亿');
  assert.equal(rows[0].flow.stage,'盘后快照');
  assert.equal(rows[0].flow.status,'历史');
  assert.equal(rows[0].daily.display,'0.00亿');
  assert.equal(rows[0].daily.dateLabel,'数据日 2026-11-12');
  assert.equal(rows[1].flow.display,'—');
  assert.equal(rows[1].daily.display,'—');
});

test('ETF unknown units or wrong frequency never become apparently valid money flows',()=>{
  const row=etfRows({etfs:[{code:'510300.SH',flow:{value:100,time:'2026-11-13T10:00:00+08:00',unit:'万元',frequency:'intraday_cumulative'},
    daily:{value:100,data_date:'2026-11-13',unit:'元',frequency:'intraday_cumulative'}}]})[1];
  assert.equal(row.flow.display,'—');assert.equal(row.daily.display,'—');
});
