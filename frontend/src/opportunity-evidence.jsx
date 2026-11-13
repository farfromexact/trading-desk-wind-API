import React from 'react';
import { opportunityEvidence } from './opportunity-evidence';

export function OpportunityEvidence({op,instrument,connected}) {
  const evidence=opportunityEvidence(op,instrument,connected);
  return <section className="op-evidence" aria-label="机会触发原因">
    <h3>为什么出现这个机会</h3>
    <div className="evidence-steps">{evidence.steps.map(step=><div key={step.title}><h4>{step.title}</h4><p>{step.text}</p></div>)}</div>
    <div className="evidence-now"><h4>现在还能怎么用</h4>{evidence.warnings.map((text,i)=><p key={i}>{text}</p>)}</div>
    <div className="evidence-now"><h4>计划与当前盘口的差别</h4><p>{evidence.execution}</p></div>
    <p className="muted">这是规则证据，不是胜率预测。请核对消息事件、更大周期、已有敞口和真实成交成本，再决定参与或放弃。</p>
  </section>;
}
