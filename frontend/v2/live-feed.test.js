import test from 'node:test';
import assert from 'node:assert/strict';
import {startLiveFeed,feedHealth} from './live-feed.js';
const sample={mode:'live',transport:{upstream_ok:true},state:{server_time:'2026-11-17T14:30:00+08:00',feed_state:'Wind 已连接',instruments:[]}};
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function timers(){
  const queue=new Map();let next=0;
  return {queue,schedule:(fn,delay)=>{queue.set(++next,{fn,delay});return next;},cancel:id=>queue.delete(id),run(delay){
    const [id,{fn}]=[...queue].find(([,item])=>item.delay===delay);queue.delete(id);fn();
  }};
}
test('poll is read-only, single in flight and stops on disposal',async()=>{
  const t=timers(),calls=[],received=[];let resolve;
  const stop=startLiveFeed({...t,onSnapshot:s=>received.push(s),onConnection:()=>{},fetcher:(path,options)=>{
    calls.push({path,options});return new Promise(r=>{resolve=r;});
  }});
  assert.equal(calls.length,1);assert.equal([...t.queue.values()].filter(v=>v.delay===3000).length,0);
  resolve({ok:true,json:async()=>sample});await settle();
  assert.equal(received.length,1);assert.equal(calls[0].path,'./v2-api/live');assert.equal(calls[0].options.method,undefined);
  stop();assert.equal(t.queue.size,0);
});
test('outage retains no fake success and next poll recovers',async()=>{
  const t=timers(),states=[],received=[];let attempt=0;
  const stop=startLiveFeed({...t,onSnapshot:s=>received.push(s),onConnection:s=>states.push(s),fetcher:async()=>({ok:++attempt>1,json:async()=>sample})});
  await settle();assert.equal(states.at(-1).connected,false);assert.equal(received.length,0);
  t.run(3000);await settle();assert.equal(states.at(-1).connected,true);assert.equal(received.length,1);
  stop();
});
test('unmount aborts an in-flight poll and ignores its later result',async()=>{
  const t=timers();let resolve,signal;const received=[];
  const stop=startLiveFeed({...t,onSnapshot:s=>received.push(s),onConnection:()=>{},fetcher:(path,o)=>{signal=o.signal;return new Promise(r=>{resolve=r;});}});
  stop();assert.equal(signal.aborted,true);resolve({ok:true,json:async()=>sample});await settle();
  assert.equal(received.length,0);assert.equal(t.queue.size,0);
});
test('client checks heartbeat age separately from the upstream connected string',()=>{
  const now=Date.parse(sample.state.server_time);
  assert.equal(feedHealth(sample,{connected:true},now+1000).connected,true);
  assert.equal(feedHealth(sample,{connected:false,error:'timeout'},now).connected,false);
  assert.equal(feedHealth(sample,{connected:true},now+16000).connected,false);
  assert.equal(feedHealth({...sample,state:{...sample.state,last_error:'failed'}},{connected:true},now).connected,false);
});
