import test from 'node:test';
import assert from 'node:assert/strict';
import {CANDLE_WINDOW, candleLayout} from './candle-layout.js';

test('three opening candles keep normal spacing with or without older data', () => {
  const bar = {low: 818.92, high: 823.44};
  const opening = [bar, bar, bar];
  const sparse = candleLayout(opening, 0, 821.64, 0.02);
  assert.ok(sparse.x(2) - sparse.x(0) < (sparse.right - sparse.left) * 0.1);
  assert.ok(sparse.x(2) < sparse.right);
  assert.equal(sparse.labelIndices.at(-1), 2);

  const context = CANDLE_WINDOW - opening.length;
  const full = candleLayout([...Array(context).fill(bar), ...opening], context, 821.64, 0.02);
  assert.ok(full.x(0) > full.left);
  assert.ok(full.x(context - 1) < full.divider && full.divider < full.x(context));
  assert.ok(full.x(context + 2) - full.x(context) < (full.right - full.left) * 0.1);
});

test('vertical range fits candle highs and lows and the live quote with room at the edges', () => {
  const layout = candleLayout([{low: 818.92, high: 823.44}], 0, 824, 0.02);
  assert.ok(layout.y(824) > layout.top);
  assert.ok(layout.y(818.92) < layout.bottom);
  assert.ok(layout.high < 836.42);
});
