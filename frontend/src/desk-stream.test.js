import test from 'node:test';
import assert from 'node:assert/strict';
import {listenToDesk, mergeDeskEvents} from './desk-stream.js';

function browser(t) {
  let now = 100000, tick;
  const sources = [], packets = [], document = new EventTarget(), window = new EventTarget();
  document.hidden = false;
  class Source extends EventTarget {
    constructor() { super(); sources.push(this); }
    close() { this.closed = true; }
    emit(sent = now) {
      this.dispatchEvent(new MessageEvent('update', {data:JSON.stringify({state:{sent_ms:sent}, events:[]})}));
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
  const stop = listenToDesk({onPacket:(packet, first) => packets.push({sent:packet.state.sent_ms, first}),
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
