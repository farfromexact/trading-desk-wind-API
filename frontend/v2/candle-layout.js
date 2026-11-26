export const CANDLE_WINDOW = 30;

export function candleLayout(bars, contextCount, livePrice, tickSize) {
  const left = 18, right = 735, top = 28, bottom = 225;
  const gap = contextCount ? 14 : 0;
  const step = (right - left - gap) / CANDLE_WINDOW;
  const x = index => right - step / 2 - (bars.length - 1 - index) * step
    - (index < contextCount ? gap : 0);
  const divider = contextCount ? (x(contextCount - 1) + x(contextCount)) / 2 : null;

  const hasLivePrice = Number.isFinite(livePrice);
  const min = Math.min(...bars.map(bar => bar.low), hasLivePrice ? livePrice : Infinity);
  const max = Math.max(...bars.map(bar => bar.high), hasLivePrice ? livePrice : -Infinity);
  const tick = Number.isFinite(tickSize) && tickSize > 0 ? tickSize : 0.01;
  const padding = Math.max((max - min) * 0.1, tick * 2);
  const low = min - padding, high = max + padding;
  const y = value => bottom - (value - low) / (high - low) * (bottom - top);

  const indices = [...new Set([0, Math.floor((bars.length - 1) / 2), bars.length - 1])];
  const labelIndices = [];
  for (const index of indices.reverse()) {
    if (!labelIndices.length || x(labelIndices.at(-1)) - x(index) >= 42) labelIndices.push(index);
  }

  return {
    left, right, top, bottom, step, bodyWidth: Math.min(10, step * 0.56), divider,
    low, high, x, y, labelIndices: labelIndices.reverse(),
  };
}
