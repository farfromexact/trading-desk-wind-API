import test from 'node:test';
import assert from 'node:assert/strict';
import { opportunityEvidence } from './opportunity-evidence.js';
const op={contract:'IM2612.CFE',trading_day:'2026-11-11',direction:1,setup:'pullback',status:'confirmed',entry:100,stop:95,rr:2,targets:[{price:110,source:'structural'}],regime:{state:'bull',label:'多头趋势'},source_bars:[]};
const item={contract:op.contract,trading_day:op.trading_day,ready:true,phase:'交易中',status:'实时',market:{tick_size:0.2,decimals:1},quote:{rt_bid1:100.8,rt_ask1:101}};
test('current book deterioration lowers planned RR and is explicitly an estimate',()=>{
  const result=opportunityEvidence(op,item);
  assert.match(result.execution,/1.34R/);
  assert.match(result.execution,/未扣手续费/);
  assert.match(result.steps.at(-1).text,/结构目标/);
});
test('stale, disconnected, historical and filtered cards cannot show executable estimate',()=>{
  for(const [o,i,c] of [[op,{...item,ready:false},true],[op,item,false],[{...op,historical:true},item,true],[{...op,status:'filtered'},item,true]])
    assert.match(opportunityEvidence(o,i,c).execution,/不可用/);
});
test('preview and old records do not fabricate confirmation evidence',()=>{
  const preview=opportunityEvidence({...op,entry:null,preview_only:true},item);
  assert.match(preview.steps.at(-1).text,/尚未完成/);
  const old=opportunityEvidence(op,item);
  assert.match(old.steps.find(x=>x.title.includes('收盘确认')).text,/尚无/);
});
test('synthetic T1 is explicitly labelled as scenario only',()=>{
  assert.match(opportunityEvidence({...op,target_source:'synthetic'},item).steps.at(-1).text,/不能用测算目标证明/);
});
