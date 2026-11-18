import {useCallback, useEffect, useRef, useState} from 'react';

const EMPTY = [];
const sameSeries = (left, right) => left.contract === right.contract
  && left.trading_day === right.trading_day && left.last_bar === right.last_bar;

export function nextCardRead(instruments, selectedFive, entries, now, preview) {
  return instruments.find(item => {
    if (!item.contract || item.contract === selectedFive) return false;
    const entry = entries.get(item.contract);
    if (!entry || !sameSeries(entry, item)) return true;
    return !preview && (entry.retry || item.phase === '交易中') && now >= entry.nextRead;
  });
}

// The selected 5m chart supplies its own read. Other cards share one serial cache
// reader for finalized 5m bars, independent of the quote stream and 1m interest.
export function useCardBars(instruments = EMPTY, selectedFive, preview, api) {
  const entries = useRef(new Map());
  const latest = useRef();
  latest.current = {instruments, selectedFive};
  const [, invalidate] = useState(0);

  const remember = useCallback((item, rows) => {
    if (!latest.current.instruments.some(current => sameSeries(current, item))) return;
    const old = entries.current.get(item.contract);
    const compact = Array.isArray(rows)
      ? rows.filter(b => typeof b?.end === 'string' && Number.isFinite(b.close)).map(({end, close}) => ({end, close}))
      : old && old.trading_day === item.trading_day ? old.rows : EMPTY;
    const retry = !Array.isArray(rows) || !compact.length;
    const {contract, trading_day, last_bar} = item;
    entries.current.set(contract, {contract, trading_day, last_bar, rows: compact, retry,
      nextRead: Date.now() + (retry ? 30_000 : 300_000)});
    invalidate(version => version + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let timer;
    async function step() {
      const {instruments, selectedFive} = latest.current;
      for (const code of entries.current.keys()) {
        if (!instruments.some(i => i.contract === code)) entries.current.delete(code);
      }
      const item = !document.hidden && nextCardRead(instruments, selectedFive, entries.current, Date.now(), preview);
      if (item) {
        let rows;
        try {
          rows = await api('bars?contract=' + encodeURIComponent(item.contract) + '&timeframe=5', {signal: controller.signal});
        } catch {
          // Retain the last same-day curve and retry later. Quotes keep streaming.
        }
        if (!controller.signal.aborted && latest.current.selectedFive !== item.contract) remember(item, rows);
      }
      if (!controller.signal.aborted) timer = setTimeout(step, 750);
    }
    step();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [api, preview, remember]);

  const bars = {};
  for (const i of instruments) {
    const entry = entries.current.get(i.contract);
    if (entry && entry.trading_day === i.trading_day) bars[i.contract] = entry.rows;
  }
  return {bars, remember};
}
