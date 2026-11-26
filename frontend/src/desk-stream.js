// The desk generates a snapshot every second, even when the exchange is closed.
// Its send time measures the local stream independently of Wind quote times.
const SILENCE_MS = 8000;
const reusableFields = ['structures', 'levels', 'opportunities', 'live_bars'];

function restoreInstruments(items, previous) {
  for (const item of items) {
    const old = previous?.get(item.root);
    for (const field of reusableFields) {
      if (Object.hasOwn(item, field)) continue;
      if (!old || old.contract !== item.contract || old.trading_day !== item.trading_day) return false;
      item[field] = old[field];
    }
  }
  return true;
}

export function listenToDesk({onPacket, onDisconnected, onClock}) {
  let source, deadline, receivedAt = 0, first = true;

  function check() {
    const now = Date.now();
    if (now - receivedAt >= 1500) onClock(now);
    if (now < deadline) return;
    first = true;
    onDisconnected();
    if (!document.hidden) connect();
  }

  function connect() {
    source?.close();
    const current = new EventSource('/api/stream?compact=true');
    let previous, lastFrame = 0;
    source = current;
    deadline = Date.now() + SILENCE_MS;
    current.addEventListener('update', event => {
      if (source !== current) return;
      const packet = JSON.parse(event.data);
      const now = Date.now();
      const sent = packet.state.sent_ms ?? Date.parse(packet.state.server_time);
      if (packet.frame !== undefined) {
        const full = packet.frame === 1;
        if ((!full && (!previous || packet.frame !== lastFrame + 1)) ||
            !restoreInstruments(packet.state.instruments, full ? null : previous)) {
          // A missing base requires a new full frame and event-log catch-up.
          first = true;
          onDisconnected();
          connect();
          return;
        }
        previous = new Map(packet.state.instruments.map(item => [item.root, item]));
        lastFrame = packet.frame;
      }
      // An old buffered frame may be the base of the next fresh one. Do not
      // display it, sound its alerts, or count it as a fresh heartbeat.
      if (!Number.isFinite(sent) || Math.abs(now - sent) >= SILENCE_MS) {
        first = true;
        check();
        return;
      }
      const resuming = first || packet.frame === 1 || now >= deadline;
      first = false;
      receivedAt = now;
      deadline = Math.min(now, sent) + SILENCE_MS;
      onPacket(packet, resuming, now);
    });
    current.onerror = () => {
      if (source !== current) return;
      previous = null;
      lastFrame = 0;
      first = true;
      onDisconnected();
      // EventSource retries ordinary errors itself. Replace it only after silence.
      deadline = Date.now() + SILENCE_MS;
    };
  }

  connect();
  const timer = setInterval(check, 1000);
  document.addEventListener('visibilitychange', check);
  window.addEventListener('pageshow', check);
  return () => {
    clearInterval(timer);
    document.removeEventListener('visibilitychange', check);
    window.removeEventListener('pageshow', check);
    source.close();
    source = null;
  };
}

export function mergeDeskEvents(existing, incoming) {
  const byId = new Map(existing.map(event => [event.id, event]));
  for (const event of incoming) byId.set(event.id, event);
  return [...byId.values()].sort((a, b) => b.seq - a.seq).slice(0, 500);
}
