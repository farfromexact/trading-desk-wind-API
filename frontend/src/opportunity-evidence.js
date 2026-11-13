import { formatPrice } from './market-format.js';

const finite = x => typeof x === 'number' && Number.isFinite(x);
const num = x => finite(x) ? x.toFixed(2) : '未记录';
const stamp = x => x ? x.replace('T', ' ').replace('+08:00', '') : '未记录';
export function opportunityEvidence(op, instrument, connected = true) {
  const p = x => formatPrice(x, instrument?.market || {decimals: op.price_decimals});
  const d = op.direction, long = d === 1, pullback = op.setup === 'pullback';
  const r = op.regime_at_creation || op.regime || {};
  const params = op.rule_parameters;
  const bars = op.source_bars || [];
  const first = bars[0];
  const confirmation = bars.find(b => b.end === op.confirmation_bar);
  const candle = b => {
    if (!b) return '未记录，不能补造当时证据';
    const width = b.high-b.low;
    return `${stamp(b.end)}（标签） · 开${p(b.open)} 高${p(b.high)} 低${p(b.low)} 收${p(b.close)}；实体占比${width>0?num(100*Math.abs(b.close-b.open)/width)+'%':'不可计算'}，距${long?'最高':'最低'}价${width>0?num(100*(long?b.high-b.close:b.close-b.low)/width)+'%':'不可计算'}`;
  };
  const steps = [
    {title:'1 · 为什么关注这个方向', text:`建立时15分钟为${r.label || r.state || '未记录'}，效率比 ${num(r.efficiency)}，EMA斜率/ATR ${num(r.slope)}。${pullback?'选择顺势突破后的回踩恢复。':'选择区间边缘扫过、收回后的反向跟随。'}`},
    {title:'2 · 为什么是这个位置', text:`${op.level_name || '关键位'} ${p(op.level)}，可知时间 ${stamp(op.level_known_at)}；冻结5分钟ATR ${num(op.atr)}，区域 ${p(op.level-op.tolerance)} ～ ${p(op.level+op.tolerance)}。${op.bounds?`冻结区间 ${p(op.bounds[0])} ～ ${p(op.bounds[1])}。`:''}`},
  ];
  if (op.preview_only) {
    steps.push({title:'3 · 这里只是接近区域',text:'实时价格接近区间关键位，尚未完成扫过、收回与下一根跟随；没有正式触发价。'});
  } else {
    steps.push({title:pullback?'3 · 突破线证据':'3 · 扫过关键位的证据',text:candle(first)+(pullback?`。规则要求同向实体≥${params?params.breakout_body*100+'%':'未保存阈值'}，并收于方向侧四分之一区域。`:'。扫过关键位不等于已经反转。')});
    steps.push({title:pullback?'4 · 回踩是否完成':'4 · 是否收回并跟随',text:pullback?(op.pullback_bar?`第一次触及冻结区域：${candle(op.pullback_bar)}`:op.touched?'已记录触及，旧记录没有独立回踩线。':'尚未记录回踩触及。'):(op.return_bar?`收回线：${candle(op.return_bar)}；必须由紧接下一根强线跟随。`:op.returned_index!=null?'已收回，旧记录未保存独立收回线。':'尚未收回关键位。')});
    steps.push({title:'5 · 收盘确认依据',text:confirmation?`${candle(confirmation)}。获知确认时间 ${stamp(op.confirmed_at)}；确认实体门槛 ${params?params.confirmation_body*100+'%':'旧记录未保存'}。`:'尚无完成的确认线，不能按已确认机会执行。'});
  }
  if (op.entry) {
    const source = op.target_source || op.targets?.[0]?.source;
    steps.push({title:'6 · 触发、失效与目标空间',text:`参考触发 ${p(op.entry)}（确认线${long?'高点上':'低点下'}1跳），结构失效 ${p(op.stop)}；T1 ${p(op.targets?.[0]?.price)}，来源：${source==='structural'?'结构目标':source==='synthetic'?'R倍数测算，仅情景':'旧记录未分类'}；计划 ${num(op.planned_rr??op.rr)}R，未扣成本。${source==='synthetic'?'不能用测算目标证明存在结构获利空间。':''}`});
  }
  const healthy = connected && instrument?.ready && instrument?.phase==='交易中' && instrument?.contract===op.contract && instrument?.trading_day===op.trading_day && !op.historical;
  const warnings = [];
  if (!healthy) warnings.push(`当前不可作为实时入场依据：${!connected?'页面离线、回放或连接不可用':op.historical?'历史信号':instrument?.status || '合约、交易日或数据未就绪'}。`);
  warnings.push(`当前阶段：${op.reason || op.status}。`);
  if(op.status==='triggered') warnings.push(`触价 ${stamp(op.touch_at)}，观察价格 ${p(op.touch_price)}；不是成交确认，也不是允许追价。`);
  if (op.regime_at_trigger) warnings.push(`触价时背景：${op.regime_at_trigger.label || op.regime_at_trigger.state}。`);
  if (r.state && instrument?.regime?.state && r.state!==instrument.regime.state) warnings.push(`当前15分钟已变为${instrument.regime.label || instrument.regime.state}，与建立时不同；已有持仓由人管理。`);
  warnings.push(pullback?'失效检查：候选期趋势改变或收盘越过回踩区域不利边界；确认后环境改变取消待触发，触及结构失效价则失效。':'失效检查：候选期离开震荡、未及时收回或下一根无强跟随；确认后环境改变取消待触发，触及结构失效价则失效。');
  warnings.push(op.status==='confirmed'?`最多再等 ${op.remaining_bars ?? '未记录'} 根完整5分钟线；未触发不跨交易日。`:'结构目标触及不代表自动止盈；下单、仓位、退出均需人工处理。');
  let execution = '当前盘口估计不可用：需要实时健康数据、有效双边盘口及确认/触价状态。';
  const q=instrument?.quote || {}, tick=instrument?.market?.tick_size;
  if(healthy && ['confirmed','triggered'].includes(op.status) && !op.preview_only && finite(tick) && tick>0 && finite(q.rt_bid1) && finite(q.rt_ask1) && q.rt_bid1>0 && q.rt_ask1>=q.rt_bid1 && finite(op.stop) && finite(op.targets?.[0]?.price)) {
    const book=long?q.rt_ask1:q.rt_bid1;
    // Stop-entry opportunity: do not use a favorable pre-trigger book as a fictitious fill.
    const entry=(long?Math.max(book,op.entry):Math.min(book,op.entry))+d*tick;
    const risk=d*(entry-op.stop)+tick, reward=d*(op.targets[0].price-entry)-tick;
    execution=`当前盘口压力估计：参考入场 ${p(entry)}，计入入场和退出各1跳压力滑点，T1空间 ${risk>0?num(reward/risk)+'R':'不可计算'}。基于买入卖一/卖出买一，未触发时不优于计划触发价；未扣手续费、未模拟未来盘口变化。不是可保证成交的RR。`;
  }
  return {steps,warnings,execution};
}
