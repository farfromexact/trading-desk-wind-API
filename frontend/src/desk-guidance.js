import {clockSeconds, date, feedHealth, marketPhase} from '../shared/market-model.js';
import {formatPrice, currentOpportunity} from './market-format.js';
import {opportunityAtmosphere} from './opportunity-atmosphere.js';
import {opportunityBook} from './opportunity-evidence.js';

const stages = {watching:'关注中', confirmed:'待触价', triggered:'已触价', invalidated:'已失效', expired:'已过期', filtered:'空间不足', uncertain:'先后待核实'};
const quiet = {tone:'idle', watching:[], actionable:[]};
const touchPriorityMs = 5 * 60_000;

export function quoteReady(item, now) {
  const age = now - Date.parse(item.market_time);
  return item.ready && item.phase === '交易中' && Number.isFinite(item.quote?.rt_last) && item.quote.rt_last > 0 && age >= -3000 && age <= 15000;
}

function watchInstruction(op) {
  if (op.preview_only) return '等价格扫过区间边缘、收回，再看跟随线。';
  if (op.setup === 'pullback') return op.touched ? '回踩已出现，等同向的完整 5 分钟线确认。' : '等价格回踩突破位，再看 5 分钟收盘。';
  if (op.setup === 'failed_break') return op.returned_index != null || op.return_bar ? '价格已收回，等下一根完整 5 分钟线同向跟随。' : '等 5 分钟线收回关键位，再看下一根跟随。';
  return '等完整 5 分钟线确认。';
}

function describePlan(op, item, now) {
  const book = opportunityBook(op, item, true);
  const price = value => formatPrice(value, item.market);
  const side = op.direction === 1 ? '多头' : '空头';
  const base = {op, item, book, caption:`${item.root} ${item.market?.name || ''} · ${side}`, stage:stages[op.status]};
  if (op.status === 'watching') {
    const level = price(op.level);
    return {...base, priority:op.preview_only ? 5 : 4, title:watchInstruction(op),
      detail:[op.historical ? '初始化关注 · 静默' : '', level === '—' ? '' : `${op.level_name || '关键位'} ${level}`].filter(Boolean).join(' · ')};
  }
  if (op.status === 'confirmed') {
    const remaining = Number.isInteger(op.remaining_bars) ? `还剩 ${op.remaining_bars} 根完整 5 分钟线` : '';
    const waiting = `等成交价${op.direction === 1 ? '向上' : '向下'}触及 ${price(op.entry)}`;
    const shortfall = book?.within === false;
    return {...base, priority:book?.within === true ? 1 : 3, stage:'收盘确认', title:shortfall ? '尚未触价，当前盘口空间不足' : waiting,
      detail:[shortfall ? `原触发 ${price(op.entry)}` : '', remaining, `结构失效 ${price(op.stop)}`].filter(Boolean).join(' · ')};
  }
  const age = now - Date.parse(op.touch_at);
  const detail = [Number.isFinite(age) ? `${clockSeconds(op.touch_at)} 触价` : '触价时间未记录',
    `原触发 ${price(op.entry)}`, `结构失效 ${price(op.stop)}`].join(' · ');
  const recent = age >= -3000 && age <= touchPriorityMs;
  let title = '已触价，查看原计划', priority = recent ? 2 : 6;
  const originalRegime = op.regime_at_confirmation?.state;
  if (originalRegime && item.regime?.state && originalRegime !== item.regime.state) {
    title = '15 分钟环境已变，空仓等新计划';
  } else if (book?.within === false) {
    title = '空间不足，空仓跳过这口价';
  } else if (op.direction * (item.quote.rt_last - op.entry) < 0) {
    title = '价格已退回触发位内，空仓等新计划';
  } else if (book?.within == null) {
    title = book ? '已触价，缺少可核对的价格界限' : '已触价，等有效双边盘口';
  } else if (recent) {
    title = '已触价，核对眼前盘口';
    priority = 0;
  }
  return {...base, priority, title, detail};
}

function updatedAt(op) {
  return Date.parse(op.updated_at || op.touch_at || op.confirmed_at || op.created_at || op.signal_bar) || 0;
}

export function deskGuidance(state, connected, now, replay = false) {
  const idle = (stage, title, detail = '') => ({atmosphere:quiet, stage, title, detail, live:false});
  if (replay || state?.offline) return idle('回看', '实时判断已暂停');
  if (!state || !connected) return idle('连接中', '等本机行情恢复');
  const items = state.instruments || [];
  // A failed request for one contract also sets last_error. Its peers retain their own health.
  const scopedError = state.last_error && items.some(i => i.status === state.last_error);
  const health = feedHealth({state:scopedError ? {...state, last_error:null} : state}, {connected}, now);
  if (!health.connected) return idle('暂停', '等行情恢复后再判断', health.label);
  const open = items.filter(i => i.phase === '交易中');
  const unscheduled = items.filter(i => !i.phase || i.phase === '等待交易日历');
  if (!open.length && (!items.length || unscheduled.length)) return idle('同步中', '等交易日历和品种就绪');
  if (!open.length) return idle(marketPhase(items), '等下个交易时段');
  const ready = open.filter(i => quoteReady(i, now));
  const delayed = [...open.filter(i => !ready.includes(i)), ...unscheduled];
  const pause = delayed.map(i => {
    if (i.phase !== '交易中') return `${i.root} 等待交易日历`;
    const stamp = Date.parse(i.market_time);
    return Number.isFinite(stamp) ? `${i.root} 上次行情 ${date(i.market_time).slice(5)} ${clockSeconds(i.market_time)}` : `${i.root} 等待行情`;
  }).join(' · ');
  if (!ready.length) return idle('数据待齐', delayed.length === 1 ? `暂停判断 ${delayed[0].root}，等有效行情` : '暂停判断，等有效行情', pause);
  const atmosphere = opportunityAtmosphere({instruments:ready}, true);
  const byRoot = new Map(ready.map(i => [i.root, i]));
  const plans = [...atmosphere.actionable, ...atmosphere.watching]
    .filter(op => [1,-1].includes(op.direction))
    .map(op => describePlan(op, byRoot.get(op.root), now))
    .sort((a, b) => a.priority - b.priority || updatedAt(b.op) - updatedAt(a.op) || String(a.op.id).localeCompare(String(b.op.id)));
  return {atmosphere, live:true, pause, others:Math.max(0, plans.length - 1),
    ...(plans[0] || {stage:'观察中', title:'继续等下一根完整 5 分钟线', detail:`${ready.length} 个就绪品种暂无待处理计划`})};
}

// Compare actual snapshots on either side of a tab switch. No extra polling or inferred transitions.
export function changesWhileAway(before, after, left, returned) {
  if (!before || before.metrics?.started_at !== after?.metrics?.started_at) return '';
  const changes = [], still = [];
  for (const item of after.instruments || []) {
    const old = before.instruments.find(i => i.root === item.root);
    if (!old || old.contract !== item.contract || !item.trading_day || old.trading_day !== item.trading_day ||
        !quoteReady(old, before.sent_ms) || !quoteReady(item, after.sent_ms)) continue;
    const previous = new Map((old.opportunities || []).map(o => [o.id, o.status]));
    for (const op of item.opportunities || []) {
      if (op.historical || op.contract !== item.contract || !currentOpportunity(op, item) || !stages[op.status]) continue;
      const from = previous.get(op.id);
      if (from === op.status) {
        if (from === 'watching') still.push(item.root);
        continue;
      }
      const at = updatedAt(op);
      // Backend event timestamps have second precision.
      if (at < Math.floor(left / 1000) * 1000 || at > returned) continue;
      changes.push({root:item.root, at, text:`${item.root} ${from && stages[from] ? stages[from] + ' → ' : ''}${stages[op.status]}`});
    }
  }
  changes.sort((a, b) => b.at - a.at);
  if (!changes.length) return '';
  const notes = changes.slice(0, 2).map(c => c.text);
  if (changes.length > 2) notes.push(`另有 ${changes.length - 2} 项变化`);
  if (changes.length === 1) {
    const other = still.find(root => root !== changes[0].root);
    if (other) notes.push(`${other} 仍在等收盘`);
  }
  return notes.join(' · ');
}
