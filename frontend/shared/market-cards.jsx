import React from 'react';
import {CARD_TREND_BARS, clockSeconds, datedClock, finite, signed, tone} from './market-model.js';
import {formatPrice, shortContract} from '../src/market-format.js';
import './market-cards.css';

const groupOrder = ['股指', '贵金属', '原油', '国债'];
const groupByRoot = {IM: '股指', IF: '股指', IH: '股指', AG: '贵金属', AU: '贵金属', SC: '原油', T: '国债', TL: '国债'};
const structures = {bull: '↑ 多头', bear: '↓ 空头', range: '↔ 震荡'};

function grouped(items) {
  const groups = new Map(groupOrder.map(name => [name, []]));
  for (const item of items) {
    const group = groupByRoot[item.root] || item.market?.group || '其他';
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(item);
  }
  return [...groups].filter(([, members]) => members.length);
}

const slotX = slot => 4 + slot * 152 / (CARD_TREND_BARS - 1);

function Spark({item, label}) {
  const {bars, segments} = item.trend;
  const values = bars.map(bar => bar.close);
  if (!values.length) return <div className="spark-empty">分钟线待补齐</div>;
  const baseline = item.prevClose;
  const domain = finite(baseline) ? [...values, baseline] : values;
  const low = Math.min(...domain), high = Math.max(...domain);
  const y = value => high === low ? 26 : 45 - (value - low) / (high - low) * 36;
  return <svg className="spark" viewBox="0 0 160 52" role="img" aria-label={item.root + ' ' + label}>
    {finite(baseline) && <line x1="2" x2="158" y1={y(baseline)} y2={y(baseline)} stroke="currentColor" strokeDasharray="3 4" opacity=".38"/>}
    {segments.map(({firstSlot, bars: rows}) => {
      const left = slotX(firstSlot), right = slotX(firstSlot + rows.length - 1);
      if (rows.length === 1) return <circle key={rows[0].end} cx={left} cy={y(rows[0].close)} r="1.3" fill="currentColor"/>;
      const path = rows.map((bar, i) => `${i ? 'L' : 'M'}${slotX(firstSlot + i)},${y(bar.close)}`).join(' ');
      return <g key={rows[0].end}>
        <path d={`${path} L${right},52 L${left},52 Z`} fill="currentColor" opacity=".045"/>
        <path d={path} stroke="currentColor" strokeWidth="1.7" fill="none"/>
      </g>;
    })}
    <circle cx="156" cy={y(values.at(-1))} r="2.5" fill="currentColor"/>
  </svg>;
}

export function MarketCardGrid({items, selected, onSelect, atmosphere, showStatus = false, paused = false}) {
  const groups = grouped(items);
  const columns = groups.map(([, members]) => 'minmax(0,' + members.length + 'fr)').join(' ');
  return <div className="market-groups" style={{'--market-columns': columns}}>
    {groups.map(([name, members]) => <div className={'market-group size-' + members.length} key={name}>
      <div className="group-label">{name}<span>{String(members.length).padStart(2, '0')}</span></div>
      <div className="market-cards">{members.map(item => {
        const signal = atmosphere?.actionable.some(o => o.root === item.root) ? 'action'
          : atmosphere?.watching.some(o => o.root === item.root) ? 'watch' : undefined;
        const fullName = item.market?.name || item.root;
        const name = fullName.replace('年国债', '年债').replace(/^(中证|沪深|上证)/, '');
        const muted = paused || !item.usable;
        const status = showStatus ? item.status : '';
        const quality = paused ? '连接暂停' : !item.usable || !item.quoteUsable ? item.quality : '';
        const caption = [...new Set([item.basis, status, quality].filter(Boolean))].join(' · ');
        const source = item.priceSource === 'bar' ? 'K线收盘' : item.quoteUsable ? '行情' : '快照';
        const bars = item.trend.bars;
        const curveLabel = bars.length
          ? `${bars.length}根5分钟收盘线 · ${datedClock(bars[0].end)} 至 ${datedClock(bars.at(-1).end)}`
          : '5分钟收盘线待补齐';
        const title = [fullName, item.basisDetail, source + ' ' + (item.changeTime || '—'),
          curveLabel, item.status, item.note].filter(Boolean).join('\n');
        return <button key={item.root} type="button" className={'market-card ' + tone(item.pct) + (selected === item.root ? ' selected' : '')}
          aria-pressed={selected === item.root} aria-label={showStatus ? '选择' + fullName + ' ' + item.root : '查看 ' + item.root + ' ' + fullName + ' 走势'}
          data-muted={muted || undefined} data-signal={signal} title={title} onClick={() => onSelect(item.root)}>
          <div className="card-name"><b>{item.root}</b>{signal
            ? <span className="card-signal">{signal === 'action' ? '● 收盘已确认' : '◉ 关注中'}</span>
            : <span>{name}</span>}</div>
          <div className="card-change">{signed(item.pct)}{finite(item.pct) && <small>%</small>}</div>
          <div className="change-basis">{caption}</div>
          <Spark item={item} label={curveLabel}/>
          <div className="card-structure"><span>15m结构</span><b>{structures[item.regime?.state] || item.regime?.label || '待同步'}</b></div>
          <div className="card-price"><span>{shortContract(item.contract) || '待映射'}</span><b>{formatPrice(item.price, item.market)}</b></div>
          {showStatus && <time className="card-time" dateTime={item.changeTime || undefined}>{source} {item.quoteUsable ? clockSeconds(item.changeTime) : datedClock(item.changeTime)}</time>}
        </button>;
      })}</div>
    </div>)}
  </div>;
}
