import {chartTimeLookup} from './chart-data.js';

function matchesPivot(pivot, event) {
  if (!pivot || pivot.price !== event.price || Date.parse(pivot.occurred_at) >= Date.parse(event.occurred_at)
      || Date.parse(pivot.known_at) > Date.parse(event.known_at)) return false;
  // The engine freezes level_known_at at the native end of the confirmation
  // candle. The pivot event itself can arrive later, especially during bootstrap.
  if (pivot.confirmed_bar && event.level_known_at) {
    const delay = Date.parse(event.level_known_at) - Date.parse(pivot.confirmed_bar);
    return delay >= 0 && delay <= 60_000;
  }
  return true;
}

function originZone(pivot, event, bars, indices) {
  const start = indices.get(pivot.occurred_at), end = indices.get(event.occurred_at);
  if (start === undefined || end === undefined || end <= start) return null;
  const up = event.direction === 1;
  for (let i = end - 1; i >= start; i--) {
    const {open, high, low, close} = bars[i];
    if (![open, high, low, close].every(Number.isFinite) || low > Math.min(open, close)
        || high < Math.max(open, close)) return null;
    if (up ? close >= open : close <= open) continue;
    // Only later finalized 5m closes can invalidate this observation zone.
    for (let j = end + 1; j < bars.length; j++) {
      const later = bars[j].close;
      if (!Number.isFinite(later) || (up ? later < low : later > high)) return null;
    }
    return {high, low, direction: event.direction, source: bars[i].end};
  }
  return null;
}

export function structureOverlay({contract, timeframe, bars, fiveMinuteBars, lastFiveBar, structures, asOf, detail, showZones}) {
  const result = {segments: [], zones: [], markers: [], breaksByTime: new Map()};
  const cutoff = Date.parse(asOf);
  if (!Number.isFinite(cutoff) || detail === 'off' || !bars.length) return result;
  const finalized = (rows, tf) => rows.filter(bar => !bar.provisional && (!bar.contract || bar.contract === contract)
    && (!bar.timeframe || bar.timeframe === tf) && Date.parse(bar.end) <= cutoff);
  const candles = finalized(bars, timeframe);
  if (!candles.length) return result;
  const at = chartTimeLookup(candles, timeframe);
  const first = candles[0].end;
  const clippedStart = stamp => at(Date.parse(stamp) < Date.parse(first) ? first : stamp);
  const source = showZones ? (timeframe === 5 ? candles : finalized(fiveMinuteBars || [], 5)) : [];
  const zonesReady = source.length > 0 && (!lastFiveBar || Date.parse(source.at(-1).end) >= Date.parse(lastFiveBar));
  const indices = zonesReady ? new Map(source.map((bar, i) => [bar.end, i])) : null;
  const pivots = new Map(), segments = new Map();
  for (const e of structures || []) {
    const known = Date.parse(e.known_at), occurred = Date.parse(e.occurred_at);
    if ((e.contract && e.contract !== contract) || !Number.isFinite(e.price)
        || !(occurred <= known && known <= cutoff)
        || (e.scale !== 'swing' && !(e.scale === 'internal' && detail === 'all'))) continue;
    if (e.kind === 'pivot') {
      pivots.set(e.scale + e.side, e);
      if (detail === 'all') result.markers.push(e);
      continue;
    }
    if (e.kind !== 'structure' || ![1, -1].includes(e.direction)) continue;
    // Keep the engine's confirmation order. Sorting on occurred_at would move
    // retrospectively marked pivots ahead of the bar that made them knowable.
    const candidate = pivots.get(e.scale + (e.direction === 1 ? 'high' : 'low'));
    const pivot = matchesPivot(candidate, e) ? candidate : null;
    const to = at(e.occurred_at);
    if (to !== null) {
      if (!result.breaksByTime.has(to)) result.breaksByTime.set(to, []);
      result.breaksByTime.get(to).push(e);
      const from = pivot && clippedStart(pivot.occurred_at);
      if (from != null && from < to) {
        const key = [from, to, e.price, e.direction].join('/');
        // The two scales can break the same level on exactly the same bar.
        if (!segments.has(key) || e.scale === 'swing') {
          segments.set(key, {from, to, price: e.price, direction: e.direction, scale: e.scale, label: e.label});
        }
      } else {
        result.markers.push(e);
      }
    }
    if (pivot && e.scale === 'swing' && zonesReady) {
      const from = clippedStart(e.occurred_at);
      const zone = from !== null && originZone(pivot, e, source, indices);
      if (zone) result.zones.push({...zone, from});
    }
  }
  result.segments = [...segments.values()];
  // At most the two most recent distinct active zones per direction.
  const counts = new Map(), seen = new Set();
  result.zones = result.zones.reverse().filter(z => {
    const key = [z.direction, z.low, z.high].join('/');
    const count = counts.get(z.direction) || 0;
    if (seen.has(key) || count >= 2) return false;
    seen.add(key);
    counts.set(z.direction, count + 1);
    return true;
  }).reverse();
  return result;
}
