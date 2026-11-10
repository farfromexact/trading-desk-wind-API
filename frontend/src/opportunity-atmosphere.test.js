import test from 'node:test';
import assert from 'node:assert/strict';
import { opportunityAtmosphere } from './opportunity-atmosphere.js';
const op=status=>({id:status,status,contract:'SC2612.INE',trading_day:'2026-11-11',entry:590,stop:585});
const state=(ops,overrides={})=>({instruments:[{root:'SC',contract:'SC2612.INE',trading_day:'2026-11-11',phase:'交易中',ready:true,opportunities:ops,...overrides}]});
test('page alert gives confirmed and triggered precedence over watching',()=>{
  assert.equal(opportunityAtmosphere(state([op('watching')]),true).tone,'watch');
  for(const status of ['confirmed','triggered'])assert.equal(opportunityAtmosphere(state([op('watching'),op(status)]),true).tone,'action');
});
test('old, historical, wrong-contract and rejected opportunities cannot light the page',()=>{
  const ops=['filtered','expired','invalidated','uncertain'].map(op);
  ops.push({...op('confirmed'),historical:true},{...op('confirmed'),trading_day:'2026-11-10'},{...op('confirmed'),contract:'SC2701.INE'},{...op('confirmed'),entry:null});
  assert.equal(opportunityAtmosphere(state(ops),true).tone,'idle');
});
test('disconnect, replay, offline, closed session and incomplete data suppress live alerts',()=>{
  const s=state([op('confirmed')]);
  assert.equal(opportunityAtmosphere(s,false).tone,'idle');
  assert.equal(opportunityAtmosphere(s,true,true).tone,'idle');
  assert.equal(opportunityAtmosphere({...s,offline:true},true).tone,'idle');
  for(const overrides of [{ready:false},{phase:'已收盘'}])assert.equal(opportunityAtmosphere(state([op('confirmed')],overrides),true).tone,'idle');
  assert.equal(opportunityAtmosphere(state([]),true).tone,'idle');
});
