import React from 'react';
import {etfRows, sourceStatus} from './market-context.js';
import {MetricHelp} from './metric-help.jsx';

const tone = value => value > 0 ? 'mc-up' : value < 0 ? 'mc-down' : '';
const speed = value => Number.isFinite(value)
  ? (value > 0 ? '+' : '') + (value / 1e4).toLocaleString('zh-CN', {maximumFractionDigits: 1}) : '—';
const money = value => {
  if (!Number.isFinite(value)) return '—';
  const large = Math.abs(value) >= 1e8;
  return (value > 0 ? '+' : '') + (value / (large ? 1e8 : 1e4)).toLocaleString('zh-CN',
    {maximumFractionDigits: large ? 2 : 1}) + (large ? '亿' : '万');
};
const clockFormat = new Intl.DateTimeFormat('en-GB', {timeZone:'Asia/Shanghai', hour12:false,
  hour:'2-digit', minute:'2-digit', second:'2-digit'});

export function EtfFlowMonitor({source, clock, connected, preview}) {
  const {context, error} = source;
  const phase = context?.etf_session;
  const paused = !connected || !!error || !!context?.disabled;
  const state = preview ? '历史预览' : context?.disabled ? '采集已停用' : paused ? '等待连接'
    : phase === '交易中' ? `盘中 · ${context.etf_interval_seconds}秒快照` : phase || '等待读取';
  return <section className="panel etf-monitor" aria-label="四只ETF资金流监测">
    <div className="etf-monitor-heading"><h2>ETF 主力资金流 <MetricHelp label="ETF资金流">每个指数观察一只ETF。主力净流入取超大单、大单买入额减卖出额。正数表示净流入，负数表示净流出。时间为每只ETF的Wind行情快照时间。</MetricHelp></h2><span>{state}</span></div>
    <div className="etf-monitor-guide">近5分钟净流速 · 万元/分 <MetricHelp label="净流速与两种均值">净流速用同一交易时段近5分钟的累计净额变化除以实际分钟数。两项差额都用今日累计减去相应均值。滚动日均用 Wind 的20日净流入总额除以20，其中包含今日截至快照时刻的净额。同刻均值取前20个交易日中同一时点的累计净额，有1日样本即可比较。日频全日均值单列作收盘参考。</MetricHelp></div>
    <div className="etf-monitor-rows">{etfRows(context, clock).map(row => {
      const stats = row.comparison;
      const status = sourceStatus(row.flow.time, clock, 45);
      const today = ['最近','滞后'].includes(status) && row.flow.source === 'WindPy WSQ' && Number.isFinite(row.flow.value);
      const stale = phase === '交易中' && status === '滞后';
      const active = today && !paused && !preview && !stale;
      const rate = active ? stats.speed : null;
      const stamp = row.flow.time && Number.isFinite(Date.parse(row.flow.time)) ? clockFormat.format(new Date(row.flow.time)) : '';
      const observedDays = stats.days;
      const mean = stats.mean ?? stats.sample_mean;
      const hasMean = observedDays > 0 && Number.isFinite(mean);
      const noHistory = today && observedDays === 0 && ['交易中', '午休', '已收盘'].includes(phase);
      const excess = active && hasMean ? stats.excess ?? (row.flow.value - mean) : null;
      const rollingExcess = active ? stats.rolling_20d_excess : null;
      return <article key={row.code} className="etf-flow-row" aria-label={row.name+'资金流'}>
        <div className="etf-row-title"><h3>{row.name}</h3><small>{row.code.split('.')[0]} · {row.manager}</small></div>
        <div className="etf-rate-line"><strong className={tone(rate)}>{speed(rate)}</strong><span>{active&&!Number.isFinite(rate)?'缺5分钟前快照':'万/分'}</span>
          <time className={stale?'etf-late':''} dateTime={row.flow.time||undefined}>{today ? (stale?'滞后 ':'')+stamp : '待当日快照'}</time></div>
        <dl><div className="etf-today"><dt>今日累计</dt><dd className={tone(today ? row.flow.value : null)}>{money(today ? row.flow.value : null)}</dd></div>
          <div className="etf-rolling"><dt>较滚动20日均额</dt>
            <dd className={tone(rollingExcess)}>{money(rollingExcess)}</dd></div>
          <div className="etf-relative"><dt>{hasMean ? `较${observedDays}日同刻均值` : '较历史同刻均值'}</dt>
            <dd className={tone(excess)}>{noHistory ? '暂无同刻样本' : money(excess)}</dd></div></dl>
        <div className="etf-reference">
          {today && Number.isFinite(stats.rolling_20d_mean) && <span title="Wind 20日净流入额 ÷ 20">滚动日均 {money(stats.rolling_20d_mean)}（含今日）</span>}
          {hasMean && <span>同刻均值 {money(mean)}（{observedDays}/20日）</span>}
          {noHistory && <span>往日同刻样本 0/20日</span>}
          {Number.isFinite(stats.speed_mean) && <span>同刻均速 {speed(stats.speed_mean)}万/分</span>}
          <span title="Wind WSD 日频主力净流入，单列参考">日频20日全日均值 {money(stats.full_day_mean)}</span>
        </div>
      </article>;
    })}</div>
    {(context?.etf_errors?.flow||context?.etf_errors?.full_day||error)&&<div className="etf-monitor-error" role="status">{error||context.etf_errors.flow||context.etf_errors.full_day}</div>}
  </section>;
}
