import React from 'react';
import {feedHealth, headline} from '../shared/market-model.js';

const phaseLabels = {盘前: '盘前回顾', 盘中: '盘中解说', 午休: '上午小结', 小节休息: '小节回顾', 休市: '休市回顾'};

export function MarketBrief({snapshot, items, connected, preview, replay}) {
  let label = '盘中解说', title = '等待本机行情。', subtitle, paused = true;
  if (preview || replay) {
    label = preview ? '历史预览' : '历史回放';
    title = '实时解说已暂停。';
  } else if (snapshot) {
    const health = feedHealth(snapshot, {connected}, Date.parse(snapshot.view_time));
    if (health.connected) {
      const summary = headline(snapshot, false, items);
      label = phaseLabels[summary.phase];
      title = summary.title;
      subtitle = summary.subtitle;
      paused = false;
    } else {
      label = '解说暂停';
      title = health.label;
    }
  }
  return <section className="desk-brief" aria-label="行情解说" data-paused={paused || undefined}>
    <span className="brief-label">{label}</span>
    <p><strong>{title}</strong>{subtitle && <span className="brief-detail">{subtitle}</span>}</p>
  </section>;
}
