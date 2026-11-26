import test from 'node:test';
import assert from 'node:assert/strict';
import {listenToDesk, mergeDeskEvents} from './desk-stream.js';

function browser(t) {
  let now = 100000, tick;
  const sources = [], packets = [], document = new EventTarget(), window = new EventTarget();
  document.hidden = false;
  class Source extends EventTarget {
    constructor(url) { super(); this.url = url; sources.push(this); }
    close() { this.closed = true; }
    emit(sent = now) {
      this.send({state:{sent_ms:sent}, events:[]});
    }
    send(packet) {
      this.dispatchEvent(new MessageEvent('update', {data:JSON.stringify(packet)}));
    }
  }
  for (const [name, value] of Object.entries({document, window, EventSource:Source})) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, {configurable:true, value});
    t.after(() => { if (previous) Object.defineProperty(globalThis, name, previous); else delete globalThis[name]; });
  }
  t.mock.method(Date, 'now', () => now);
  t.mock.method(globalThis, 'setInterval', fn => { tick = fn; return 1; });
  t.mock.method(globalThis, 'clearInterval', () => { tick = null; });
  let disconnected = 0;
  const stop = listenToDesk({onPacket:(packet, first) => packets.push({...packet, first}),
    onDisconnected:() => disconnected++, onClock:() => {}});
  return {sources, packets, document, window, stop, get disconnected(){ return disconnected; },
    advance:ms => { now += ms; }, tick:() => tick?.()};
}

test('a silent foreground stream is replaced once, queued packets from the old source are ignored', t => {
  const b = browser(t);
  const [old] = b.sources;
  old.emit();
  b.advance(1000); old.emit(); b.tick();
  b.advance(7000); b.tick();
  assert.equal(b.sources.length, 1);
  b.advance(1000); old.emit(100000); b.tick();
  assert.equal(b.disconnected, 1);
  assert.equal(b.sources.length, 2);
  assert.equal(old.closed, true);
  old.emit(); old.onerror();
  b.sources[1].emit();
  assert.deepEqual(b.packets.map(p => p.first), [true,false,true]);
  b.stop();
  b.advance(20000); b.tick(); b.window.dispatchEvent(new Event('pageshow'));
  b.sources[1].emit();
  assert.equal(b.sources.length, 2);
  assert.equal(b.packets.length, 3);
});

test('hidden tabs keep receiving live updates and a stale tab heals on wake before the next timer', t => {
  const b = browser(t);
  const [source] = b.sources;
  source.emit();
  b.document.hidden = true;
  for (let i=0; i<12; i++) { b.advance(1000); source.emit(); }
  assert.equal(b.packets.length, 13);
  b.advance(15000);
  b.tick();
  assert.equal(b.sources.length, 1);
  b.document.hidden = false;
  b.document.dispatchEvent(new Event('visibilitychange'));
  b.window.dispatchEvent(new Event('pageshow'));
  assert.equal(b.sources.length, 2);
  b.sources[1].emit();
  assert.equal(b.packets.at(-1).first, true);
  b.stop();
});

test('an ordinary EventSource retry reuses the stream and starts a new event-log catch-up', t => {
  const b = browser(t);
  const [source] = b.sources;
  source.emit(); source.onerror();
  b.advance(3000); b.tick(); source.emit();
  assert.equal(b.sources.length, 1);
  assert.deepEqual(b.packets.map(p=>p.first),[true,true]);
  b.stop();
});

test('a late history response retains newer streamed events and deduplicates repeated confirmations', () => {
  const e = n => ({id:'event-'+n,seq:n});
  assert.deepEqual(mergeDeskEvents([e(4),e(3)], [e(3),e(2)]), [e(4),e(3),e(2)]);
});

const sc = {root:'SC', contract:'SC2701.INE', trading_day:'2026-11-26'};
const history = {structures:[{kind:'pivot'}], levels:[], live_bars:{}, opportunities:[{id:'p',status:'confirmed'}]};
function frame(n, item, events = []) {
  return {frame:n, state:{sent_ms:100000, instruments:[item]}, events};
}

test('compact updates reuse unchanged references and deliver new stages, clears and events immediately', t => {
  const b = browser(t);
  const source = b.sources[0];
  assert.equal(source.url, '/api/stream?compact=true');
  source.send(frame(1, {...sc,...history, quote:{rt_last:599}}));
  const touch = {id:'p', status:'triggered', touch_price:600};
  const event = {id:'touch', seq:9};
  source.send(frame(2, {...sc, quote:{rt_last:600}, opportunities:[touch]}, [event]));
  const [before, after] = b.packets.map(p => p.state.instruments[0]);
  assert.equal(before.structures, after.structures);
  assert.deepEqual(before.opportunities, history.opportunities);
  assert.deepEqual(after, {...sc,...history,quote:{rt_last:600}, opportunities:[touch]});
  assert.deepEqual(b.packets[1].events, [event]);
  source.send(frame(3, {...sc, opportunities:[], live_bars:{}}));
  assert.deepEqual(b.packets[2].state.instruments[0].opportunities, []);
  assert.equal(b.packets[2].state.instruments[0].quote, undefined);
  assert.deepEqual(b.packets.map(p=>p.first), [true,false,false]);
  b.stop();
});

test('a missing frame, native reconnect or different contract cannot reuse an older history', t => {
  const b = browser(t);
  const old = b.sources[0];
  old.send(frame(1, {...sc,...history}));
  old.send(frame(3, sc));
  assert.equal(b.packets.length, 1);
  assert.equal(old.closed, true);
  const next = b.sources[1];
  next.send(frame(1, {...sc,...history}));
  next.send(frame(2, {...sc, contract:'SC2702.INE'}));
  assert.equal(next.closed, true);
  const last = b.sources[2];
  last.send(frame(1, {...sc,...history}));
  last.onerror();
  last.send(frame(2, sc));
  assert.equal(last.closed, true);
  b.sources[3].send(frame(1, {...sc,...history, opportunities:[]}));
  assert.deepEqual(b.packets.map(p=>p.first), [true,true,true,true]);
  b.stop();
});

test('a buffered heartbeat is silent while its successor still delivers fresh evidence and alerts', t => {
  const b = browser(t);
  const source = b.sources[0];
  const buffered = frame(1, {...sc,...history}, [{id:'old', audible:true}]);
  buffered.state.sent_ms -= 9000;
  source.send(buffered);
  assert.equal(b.packets.length, 0);
  const live = {id:'live', audible:true};
  source.send(frame(2, {...sc,quote:{rt_last:598}}, [live]));
  assert.equal(b.sources.length, 1);
  assert.deepEqual(b.packets[0].events, [live]);
  assert.deepEqual(b.packets[0].state.instruments[0].opportunities, history.opportunities);
  assert.ok(b.packets[0].first);
  b.stop();
});
