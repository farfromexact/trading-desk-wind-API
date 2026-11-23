// The desk generates a snapshot every second, even when the exchange is closed.
// Its send time measures the local stream independently of Wind quote times.
const SILENCE_MS = 8000;

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
    const current = new EventSource('/api/stream');
    source = current;
    deadline = Date.now() + SILENCE_MS;
    current.addEventListener('update', event => {
      if (source !== current) return;
      const packet = JSON.parse(event.data);
      const now = Date.now();
      const sent = packet.state.sent_ms ?? Date.parse(packet.state.server_time);
      if (!Number.isFinite(sent) || Math.abs(now - sent) >= SILENCE_MS) {
        first = true;
        check();
        return;
      }
      const resuming = first || now >= deadline;
      first = false;
      receivedAt = now;
      deadline = Math.min(now, sent) + SILENCE_MS;
      onPacket(packet, resuming, now);
    });
    current.onerror = () => {
      if (source !== current) return;
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
