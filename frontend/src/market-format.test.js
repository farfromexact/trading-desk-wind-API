import test from 'node:test';
import assert from 'node:assert/strict';
import { contractRoot, shortContract, formatPrice, formatNumber, currentOpportunity } from './market-format.js';

test('treasury and gold prices retain every tradable decimal',()=>{
  assert.equal(formatPrice(110.205,{decimals:3}),'110.205');
  assert.equal(formatPrice(118.55,{decimals:2}),'118.55');
  assert.equal(formatPrice(883.9200000000001,{decimals:2}),'883.92');
  assert.equal(formatPrice(12734,{decimals:0}),'12734');
  assert.equal(formatNumber(.005,{decimals:3}),'0.005');
  assert.equal(formatNumber(0,{decimals:3}),'0.000');
  assert.equal(formatPrice(0,{decimals:3}),'—');
});
test('selecting a TL or commodity opportunity opens its own instrument',()=>{
  assert.equal(contractRoot('TL2612.CFE'),'TL');
  assert.equal(contractRoot('SC2612.INE'),'SC');
  assert.equal(shortContract('AU2612.SHF'),'AU2612');
});
test('Friday night and Monday use one opportunity trading day',()=>{
  assert.equal(currentOpportunity({trading_day:'2026-11-09',signal_bar:'2026-11-06T21:05:00+08:00'},{trading_day:'2026-11-09'}),true);
  assert.equal(currentOpportunity({trading_day:'2026-11-06'},{trading_day:'2026-11-09'}),false);
});
