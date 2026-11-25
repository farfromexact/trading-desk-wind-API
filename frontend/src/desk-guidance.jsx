import React, {useEffect, useRef, useState} from 'react';
import {deskGuidance, changesWhileAway} from './desk-guidance.js';
import {formatPrice} from './market-format.js';
import {formatBookR} from './opportunity-evidence.js';
import {MetricHelp} from './metric-help.jsx';
import './desk-guidance.css';

export function useAwaySummary(state, live, muted) {
  const latest = useRef(null), away = useRef(null);
  const [note, setNote] = useState('');
  useEffect(() => {
    latest.current = live && !muted ? state : null;
    if (muted) {
      away.current = null;
      setNote('');
      return;
    }
    const trip = away.current;
    if (!live || !trip?.returned || document.hidden || state.sent_ms < trip.returned) return;
    away.current = null;
    setNote(changesWhileAway(trip.before, state, trip.left, trip.returned));
  }, [state, live, muted]);
  useEffect(() => {
    function visibility() {
      if (document.hidden) {
        away.current = latest.current ? {before:latest.current, left:Date.now()} : null;
        setNote('');
      } else if (away.current) {
        away.current.returned = Date.now();
      }
    }
    document.addEventListener('visibilitychange', visibility);
    return () => document.removeEventListener('visibilitychange', visibility);
  }, []);
  useEffect(() => {
    if (!note) return;
    const timer = setTimeout(() => setNote(''), 15000);
    return () => clearTimeout(timer);
  }, [note]);
  return live && !muted ? note : '';
}

function BookMetrics({op, item, book}) {
  const long = op.direction === 1;
  return <dl className="guide-metrics">
    <div><dt>{long ? '当前卖一' : '当前买一'}</dt><dd>{formatPrice(book.book, item.market)}</dd></div>
    <div><dt>{long ? '最高卖一' : '最低买一'} <MetricHelp label="盘口复核界限">
      沿用这张计划的 {book.minimum}R 门槛，按结构失效位和第一结构目标反算，再向保守方向取整到一跳。
      做多看卖一，做空看买一。进、出各预留一跳，手续费另计。触价后价格回撤不会重新生成信号。
    </MetricHelp></dt><dd>{formatPrice(book.limit, item.market)}</dd></div>
    <div className={book.within === false ? 'guide-shortfall' : ''}><dt>目标空间 <MetricHelp label="当前目标空间">
      每承担 1 份到结构失效位的风险，按当前盘口到第一目标还剩几份空间。估算使用触发价与盘口中较不利的一口价，实际成交和滑点可能有差异。
    </MetricHelp></dt><dd>{formatBookR(book)}<small>R</small><span>门槛 {book.minimum}R</span></dd></div>
  </dl>;
}

export function GuidanceStrip({guide, onSelect, onExample, recap, example = false}) {
  const {op, item, book, stage} = guide;
  const actionable = op && op.status !== 'watching';
  return <section className="desk-guidance" data-tone={op?.status === 'watching' ? 'watch' : guide.atmosphere.tone} aria-label={example ? '示例行动提示' : '当前行动提示'}>
    <div className="guide-main">
      <div className="guide-copy">
        <div className="guide-heading">
          {!example && <div className="guide-entry">
            <h2>盘中行动提示</h2>
            {onExample && <button className="guide-example-link" type="button" aria-haspopup="dialog" onClick={onExample}>看示例</button>}
          </div>}
          <div className="guide-state"><i aria-hidden="true"/><span>{stage}</span>
            {guide.caption && <b>{guide.caption}</b>}
          </div>
        </div>
        <div className="guide-instruction" role="status" aria-live="polite" aria-atomic="true">
          <span className="guide-sr">{guide.caption} {stage} </span><strong>{guide.title}</strong>
        </div>
        {guide.detail && <p>{guide.detail}</p>}
      </div>
      {actionable && (book?.limit != null ? <BookMetrics op={op} item={item} book={book}/> :
        <span className="guide-book-missing">{book ? '原计划的复核界限待核对' : '盘口复核待数据'}</span>)}
      <div className="guide-actions">
        {op && onSelect && <button type="button" onClick={() => onSelect(op)}>查看计划 ↗</button>}
        {guide.others > 0 && <a href="#opportunity-radar">另有 {guide.others} 项</a>}
      </div>
    </div>
    {guide.pause && <p className="guide-paused">暂停判断 · {guide.pause}</p>}
    {recap && <p className="guide-recap" role="status"><b>离开期间</b>{recap}</p>}
  </section>;
}

const exampleSteps = [
  ['idle','等待'], ['watch','关注中'], ['confirmed','待触价'], ['touched','已触价'], ['late','价格走远'], ['paused','行情中断'],
];
const sampleTime = '2026-11-25T09:36:24+08:00';
export function exampleGuide(step) {
  const watch = step === 'watch', confirmed = step === 'confirmed';
  const ask = step === 'late' ? 590.8 : confirmed || watch ? 589.8 : 590.2;
  const op = {id:'example-sc', contract:'SC2701.INE', trading_day:'2026-11-25', direction:1, setup:'pullback',
    status:watch ? 'watching' : confirmed ? 'confirmed' : 'triggered', touched:true,
    level:589, level_name:'回踩位', entry:watch ? null : 590, stop:watch ? null : 588,
    targets:watch ? [] : [{price:594, source:'structural'}], remaining_bars:confirmed ? 2 : null,
    touch_at:watch || confirmed ? null : '2026-11-25T09:36:08+08:00', rule_parameters:{minimum_rr:1.5}};
  const item = {root:'SC', contract:op.contract, trading_day:op.trading_day,
    market:{name:'原油', decimals:1, tick_size:0.1}, phase:'交易中', ready:step !== 'paused',
    market_time:step === 'paused' ? '2026-11-25T09:34:48+08:00' : sampleTime,
    quote:{rt_ask1:ask, rt_bid1:ask - .1, rt_last:ask - .1}, opportunities:step === 'idle' ? [] : [op]};
  return deskGuidance({server_time:sampleTime, feed_state:'Wind 已连接', instruments:[item]}, true, Date.parse(sampleTime));
}

export function GuidanceExamples({step, onChange, onClose}) {
  return <>
    <div className="guide-example-heading"><div><span>SC 原油 · 虚构行情</span><h2 id="guide-example-title">盘中提示示例</h2></div>
      {onClose && <button type="button" onClick={onClose} aria-label="关闭示例">关闭 ×</button>}
    </div>
    <div className="guide-example-steps" role="group" aria-label="选择示例场景">{exampleSteps.map(([key, label]) =>
      <button type="button" key={key} aria-pressed={step === key} onClick={() => onChange(key)}>{label}</button>)}</div>
    <GuidanceStrip guide={exampleGuide(step)} example/>
    {['confirmed','touched','late'].includes(step) && <p className="guide-example-plan">示例多头计划　触发 590.0　·　结构失效 588.0　·　第一目标 594.0</p>}
  </>;
}

export function GuidanceExampleDialog({step, onChange, onClose}) {
  const ref = useRef(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  return <dialog className="guide-dialog" aria-labelledby="guide-example-title" ref={ref}
    onCancel={onClose} onClick={e => {if (e.target === ref.current) onClose();}}>
    <div className="guide-example-body"><GuidanceExamples step={step} onChange={onChange} onClose={onClose}/></div>
  </dialog>;
}
