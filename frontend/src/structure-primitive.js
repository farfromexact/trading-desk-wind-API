const up = '#ef9890', down = '#70c9ae';
const font = '11px "Segoe UI","Microsoft YaHei",sans-serif';

// A native series primitive shares the candlesticks' pan, zoom and price scale.
export class StructurePrimitive {
  constructor() {
    this.data = {segments: [], zones: []};
    const renderer = {
      drawBackground: target => this.drawZones(target),
      draw: target => this.drawBreaks(target),
    };
    this.views = [{renderer: () => renderer}];
  }

  attached(api) { this.api = api; }
  detached() { this.api = null; }
  paneViews() { return this.views; }

  setData(data) {
    this.data = data;
    this.api?.requestUpdate();
  }

  drawZones(target) {
    if (!this.api || !this.data.zones.length) return;
    const {chart, series} = this.api;
    target.useMediaCoordinateSpace(({context: ctx, mediaSize: {width, height}}) => {
      ctx.save();
      ctx.font = font;
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      for (const zone of this.data.zones) {
        const start = chart.timeScale().timeToCoordinate(zone.from);
        const top = series.priceToCoordinate(zone.high), bottom = series.priceToCoordinate(zone.low);
        if (start === null || top === null || bottom === null || start >= width || bottom < 0 || top > height) continue;
        const left = Math.max(0, start), w = width - left;
        const bullish = zone.direction === 1;
        ctx.fillStyle = bullish ? 'rgba(236,116,109,.12)' : 'rgba(86,188,167,.12)';
        ctx.fillRect(left, top, w, Math.max(1, bottom - top));
        ctx.fillStyle = bullish ? 'rgba(236,116,109,.43)' : 'rgba(86,188,167,.43)';
        ctx.fillRect(left, bullish ? bottom : top, w, 1);
        if (w > 72) {
          const right = width - (w > 145 ? 64 : 8);
          const labelY = bottom - top >= 13 ? (top + bottom) / 2 : bullish ? bottom + 8 : top - 8;
          ctx.fillStyle = bullish ? up : down;
          ctx.fillText(bullish ? '上破回踩区' : '下破反抽区', right, Math.max(8, Math.min(height - 8, labelY)));
        }
      }
      ctx.restore();
    });
  }

  drawBreaks(target) {
    if (!this.api || !this.data.segments.length) return;
    const {chart, series} = this.api;
    target.useMediaCoordinateSpace(({context: ctx, mediaSize: {width, height}}) => {
      ctx.save();
      ctx.font = font;
      ctx.textBaseline = 'middle';
      const labels = [];
      for (const s of this.data.segments) {
        const start = chart.timeScale().timeToCoordinate(s.from), end = chart.timeScale().timeToCoordinate(s.to);
        const y = series.priceToCoordinate(s.price);
        if (start === null || end === null || y === null || end < 0 || start > width || y < 0 || y > height) continue;
        const left = Math.max(0, start), right = Math.min(width, end);
        const swing = s.scale === 'swing';
        const color = s.direction === 1 ? up : down;
        ctx.strokeStyle = color;
        ctx.lineWidth = swing ? 1.5 : 1;
        ctx.setLineDash(swing ? [] : [3, 3]);
        ctx.beginPath();
        ctx.moveTo(left, y);
        ctx.lineTo(right, y);
        ctx.stroke();
        if (right - left < 9) continue;
        const text = swing ? s.label : 'i ' + s.label;
        const w = ctx.measureText(text).width + 6;
        const x = Math.max(1, Math.min(width - w - 1, (left + right - w) / 2));
        const top = Math.max(0, Math.min(height - 15, s.direction === 1 ? y - 15 : y + 1));
        labels.push({x, y: top, w, text, color, swing, time: s.to});
      }
      // Keep all strokes. Give overlapping labels to the swing or latest break.
      const placed = [];
      labels.sort((a, b) => Number(b.swing) - Number(a.swing) || b.time - a.time);
      for (const label of labels) {
        const {x, y, w, text, color} = label;
        if (placed.some(p => x < p.x + p.w + 3 && x + w + 3 > p.x && Math.abs(y - p.y) < 15)) continue;
        ctx.fillStyle = 'rgba(20,26,33,.87)';
        ctx.fillRect(x, y, w, 14);
        ctx.fillStyle = color;
        ctx.fillText(text, x + 3, y + 7);
        placed.push(label);
      }
      ctx.restore();
    });
  }
}
