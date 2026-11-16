import React, {useEffect, useState} from 'react';
import {contextMetrics, contextRows, contextTime, crossRows, etfRows, sourceStatus, breadthCards, breadthTrend, rotationMetrics} from './market-context.js';
import './market-context.css';
import {MetricHelp} from './metric-help.jsx';

export function MarketContextPanel({instruments=[], connected=true, preview=false, layout='wide'}) {
  const [context,setContext]=useState(null), [error,setError]=useState(''), [pending,setPending]=useState(false);
  const [clock,setClock]=useState(Date.now());
  useEffect(()=>{
    let active=true;
    async function load(){
      try {
        const result=await fetch('/api/market-context');
        if(!result.ok)throw new Error('市场背景服务尚未就绪');
        const data=await result.json();
        if(active){setContext(data);setError('');setClock(Date.now());}
      } catch(e) {if(active)setError(e.message);}
    }
    load();const timer=setInterval(load,5000);
    return()=>{active=false;clearInterval(timer);};
  },[]);
  async function action(path,body){
    setPending(true);setError('');
    try{
      const result=await fetch('/api/market-context/'+path,{method:'POST',headers:{'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});
      const data=await result.json();
      if(!result.ok)throw new Error(data.detail||'查询失败');
      setContext(data);
    }catch(e){setError(e.message);}finally{setPending(false);}
  }
  const metrics=contextMetrics(context), rows=contextRows(context,instruments,connected,preview), cross=crossRows(context);
  const etfs=etfRows(context,clock), breadth=breadthCards(context), rotation=rotationMetrics(context);
  const allMetrics=[...breadth,...rotation,...rows.flatMap(row=>[row.activity,row.position])];
  const archived=allMetrics.some(x=>x.time&&sourceStatus(x.time,clock)==='历史');
  const stamp=(metric,maxSeconds=420)=>metric.time?<span className="mc-time">{contextTime(metric.time)}<em title="源行情时间的状态：最近表示在时效窗口内；滞后或历史表示旧时点，不能当作当前实时数据。" className={'mc-status '+sourceStatus(metric.time,clock,maxSeconds)}>{sourceStatus(metric.time,clock,maxSeconds)}</em></span>:null;
  return <section className={'market-context market-context--'+layout} aria-label="股指日内市场背景">
    <div className="mc-heading"><div><span className="mc-eyebrow">MARKET CONTEXT</span><h2>盘面底色 <span>{preview?'分支预览':archived?'历史快照':'观察中'}</span></h2></div>
      <div className="mc-controls"><span>{context?.history_loading?`补齐${context.history_loading}历史基准…`:context?.busy?'正在查询…':context?.monitoring?'盘中每60秒更新':'按需更新'}</span>
        <button disabled={pending||context?.busy||context?.disabled} onClick={()=>action('refresh')}>刷新</button>
        {!preview&&<button disabled={pending||context?.disabled} aria-pressed={!!context?.monitoring} onClick={()=>action('monitor',{enabled:!context?.monitoring})}>{context?.monitoring?'暂停更新':'盘中更新'}</button>}
      </div>
    </div>
    <div className="mc-overview">
      <div className="mc-breadth-section">
        <div className="mc-section-title"><h3>上涨广度 <MetricHelp label="上涨广度">{metrics[0].detail}</MetricHelp></h3><span>上涨 / 涨跌家数 · 近5m变化</span></div>
        <div className="mc-breadth-grid">{breadth.map(card=><article className={'mc-breadth-card mc-tone-'+card.tone} key={card.code} aria-label={card.name+'上涨广度'}>
          <div className="mc-card-name"><span>{card.name} <MetricHelp label={card.name+'广度'}>{`${card.name}上涨家数 ÷（上涨＋下跌家数），平盘不进分母。${card.scopeLabel==='Wind聚合'?'这里是Wind返回的板块聚合，不是50/100只指数样本。':'这里是对应指数样本。'}红色表示超过半数上涨，绿色表示不足半数；横线中点为50%。`}</MetricHelp></span><small>{card.scopeLabel}{(card.expected??card.count_total)?' · '+(card.expected??card.count_total)+'家':''}</small></div>
          <strong className={card.progress===null?'mc-empty':''}>{card.display}</strong>
          <div className="mc-breadth-track" aria-hidden="true"><i style={{width:(card.progress??0)+'%'}}/><span/></div>
          <div className="mc-breadth-delta"><span>近5m <MetricHelp label={card.name+'近5分钟变化'}>当前上涨占比减约5分钟前的上涨占比，单位pp（百分点），不是指数涨跌幅。例：30%升到35%为+5pp。只比较同一交易时段；缺历史时显示待积累。下方细线是近30分钟轨迹，缺数据会断开。</MetricHelp></span><b className={'mc-'+card.changeTone}>{card.change}</b></div>
          <div className="mc-breadth-counts">{card.countsLabel}</div>
          <div className="mc-sparkline">{breadthTrend(card.trend)?<svg viewBox="0 0 120 34" preserveAspectRatio="none" role="img" aria-label={card.name+'近30分钟广度轨迹'}><path d="M2,17 L118,17" className="mc-spark-mid"/><path d={breadthTrend(card.trend)}/></svg>:<span>轨迹待积累</span>}</div>
          {stamp(card,180)}
        </article>)}</div>
      </div>
      <div className="mc-rotation-section">
        <div className="mc-section-title"><h3>大小盘观察</h3><span>中证1000 − 沪深300</span></div>
        <div className="mc-rotation-list">{rotation.map(metric=><article className={'mc-rotation-card mc-style-'+metric.tone} key={metric.key}>
          <div className="mc-rotation-label"><b>{metric.label} <MetricHelp label={metric.label}>{metric.detail}</MetricHelp></b><span>{metric.period}</span></div>
          <div className="mc-rotation-value"><strong className={metric.display==='—'?'mc-empty':''}>{metric.display}</strong><span>{metric.side}</span></div>
          <p>{metric.subtitle}</p>{stamp(metric,metric.key==='breadth_gap'?180:420)}
        </article>)}</div>
        <div className="mc-rotation-note">紫色偏小盘 · 蓝色偏大盘 · 成交偏向不等于净流入</div>
      </div>
    </div>
    <div className="mc-grid">
      <div className="mc-comparison"><table aria-label="IM IF IH 成交与结构并列"><thead><tr><th scope="col">股指</th><th scope="col">成交活跃度 · 15m <MetricHelp label="成交活跃度">{metrics[2].detail} 大于1表示比常态活跃，小于1表示较清淡。</MetricHelp></th><th scope="col">{preview?'历史期货位置':'期货位置'} <MetricHelp label="期货位置">{metrics[3].detail} 已收盘或行情未就绪时不展示实时距离。</MetricHelp></th></tr></thead><tbody>
        {rows.map(row=><tr key={row.root}><th scope="row">{row.root}</th>
          <td><strong className={row.activity.value==='—'?'mc-empty':''}>{row.activity.value}</strong><div className="mc-row-meta"><span className="mc-row-note" title={row.activity.subtitle}>{row.activity.value==='—'?row.activity.subtitle:`现货 · ${context?.activity?.[row.root]?.baseline_days}日同刻基准`}</span>{stamp(row.activity)}</div></td>
          <td><strong className={row.position.value==='—'?'mc-empty':''}>{row.position.value}</strong><div className="mc-row-meta"><span className="mc-row-note">{row.position.subtitle.replace(new RegExp('^'+row.root+' · '),'')}</span>{stamp(row.position,180)}</div></td>
        </tr>)}
      </tbody></table></div>
    </div>
    <div className="mc-cross"><span className="mc-cross-label">跨市场背景 <MetricHelp label="跨市场背景">已监测商品与国债合约的涨跌幅，各自保留源时间。只作背景对照，不能把涨跌幅或成交额解释成资金净流入，也不能直接推断资金轮动。</MetricHelp></span>
      {cross.length?cross.map(row=><span className="mc-cross-item" key={row.code} title={`${row.group} · ${contextTime(row.time)} · 交易日 ${row.trading_day} · 当日累计成交额 ${row.amountLabel}（不是净流入）`}>
        <span>{row.label}</span><b className={row.change_pct>0?'mc-up':row.change_pct<0?'mc-down':''}>{row.value}</b><small>{row.group} · {sourceStatus(row.time,clock,420)}</small>
      </span>):<span className="mc-cross-empty">商品样本 / T、TL · 等待对应监测合约快照</span>}
      <span className="mc-cross-note">各自时点 · 不推断资金轮动</span>
    </div>
    <div className="mc-etfs"><table aria-label="ETF盘中资金流与日频净申赎"><thead><tr><th scope="col">ETF样本 <MetricHelp label="ETF样本">每个指数只选一只ETF作观察样本，不代表跟踪该指数的所有ETF，也不代表整个指数的资金流。</MetricHelp></th>{etfs.map(row=><th scope="col" key={row.code}>{row.name}<small>{row.code.split('.')[0]} · {row.manager}</small></th>)}</tr></thead><tbody>
      <tr><th scope="row">主力净流入 <MetricHelp label="主力净流入">Wind按交易大小分类的二级市场流向，金额显示亿元；“主力”不等于已识别机构。当前为日频截面，没有盘中源更新时间。单位缩放对照尚未通过，不能与ETF申赎相加。</MetricHelp><small>二级市场 · 日频截面</small></th>{etfs.map(row=><td key={row.code} title={`${row.flow.time||'源时间未取得'} · ${row.flow.reason||row.flow.source||'WindPy资金流口径'} · 本机收到 ${row.flow.observed_at||'未取得'}`}>
        <strong className={row.flow.value>0?'mc-up':row.flow.value<0?'mc-down':'mc-empty'}>{row.flow.display}</strong>
        <span className="mc-etf-time">{row.flow.timeLabel} · {row.flow.time||row.flow.data_date?row.flow.stage:'暂缺'}</span>
        {(row.flow.time||row.flow.data_date)&&<span className="mc-etf-status">{row.flow.status}</span>}
      </td>)}</tr>
      <tr><th scope="row">{etfs.some(r=>r.daily.estimated)?'估算净申赎':'日频净申赎'} <MetricHelp label="净申赎">上一完整交易日的ETF份额净变化 × 当日单位净值，金额显示亿元。这是估算，不是逐笔现金到账；数据日和披露时刻不同，不代表该数据日盘中已知。与二级市场资金流分开看。</MetricHelp><small>{etfs.some(r=>r.daily.estimated)?'份额变化 × 单位净值':'Wind申赎净流入额'}</small></th>{etfs.map(row=><td key={row.code} title={`本机收到 ${row.daily.observed_at||'未取得'}；实际披露时刻未核实，不代表此数据日盘中已知。${row.daily.reason||''}`}>
        <strong className={row.daily.value>0?'mc-up':row.daily.value<0?'mc-down':'mc-empty'}>{row.daily.display}</strong>
        <span className="mc-etf-time">{row.daily.dateLabel}</span>
      </td>)}</tr>
    </tbody></table><div className="mc-etf-foot"><span>二级市场流向与申赎分开看 · 不相加 · 单只ETF样本</span><span>{Object.keys(context?.etf_errors||{}).length?'部分ETF未更新，保留源时间':'日频披露时刻未核实'}</span></div></div>
    {(error||context?.error)&&<div className="mc-error" role="status">{error||context.error} · 原数据保留时间标记</div>}
    {Object.keys(context?.history_errors||{}).length>0&&<div className="mc-error" role="status">{Object.entries(context.history_errors).map(([root,message])=>`${root==='calendar'?'基准':root}：${message}`).join('；')}</div>}
    <details className="mc-details"><summary>口径与时间 <span>背景观察 · 不计分</span></summary>
      <div className="mc-definitions">{[...metrics.slice(0,1),...rotation,...metrics.slice(2)].map(metric=><p key={metric.label}><b>{metric.label}</b>{metric.detail||'等待已确认结构；行情不就绪时不推定位置。'}</p>)}</div>
      <p>商品仅含已监测的 AG、AU、SC 样本；国债仅含 T、TL。各合约保留自己的交易日与时间，夜盘与日盘不直接加总。涨跌幅为行情源口径；成交额为名义成交额，不能当作资金净流入。</p>
      {cross.map(row=><p key={row.code}>{row.label} · {contextTime(row.time)} · 交易日 {row.trading_day} · 累计成交额 {row.amountLabel}</p>)}
      <p>ETF二级市场流向采用 WindPy 的 MFD_INFLOW_M，按日频截面展示，显式使用元单位。接口没有提供源更新时间，不能把本机查询时间当作实时行情时间，也不称为盘中逐笔资金流。主力是数据商统计分类，不据此识别机构身份；与旧MCP来源的数据分开保存、不拼接。</p>
      <p>净申赎采用 WindPy UNIT_TOTAL 与 NAV：上一完整交易日的份额净变化×当日单位净值，单位元，明确标记“估算净申赎”。这是资产规模流入估算，不是接口直接返回的申赎金额，也不是逐笔现金到账；与原MCP字段不等同。只使用当前日期之前两个实际交易日的数据，缺任一输入则缺失。数据日、本机收到时间与披露时刻不同，披露时刻未核实，不能回填为历史盘中已知。</p>
      {etfs.map(row=><p key={row.code}>{row.name} {row.code}：资金流源时间 {row.flow.time||'未取得'}；日频数据日 {row.daily.data_date||'未取得'}；日频本机收到 {row.daily.observed_at||'未取得'}；{context?.etf_errors?.flow||context?.etf_errors?.daily||'无本轮ETF查询错误'}</p>)}
      <p>前三张是指数样本；科创板、创业板采用Wind板块聚合口径，通过000688.SH / 399006.SZ取聚合家数，不是50 / 100只指数样本。板块卡按Wind实际返回家数计算，未独立核验全板覆盖和停牌处理，不宣称已逐只核验；它们不参与右侧1000/300比较。近5分钟变化只比较相同交易时段、目标时刻之前90秒内的源快照，缺历史显示待积累。期货资金流未接入。所有空值保持缺失；这组背景不改变原有机会确认或声音规则。</p>
      <p>完整分钟线至少在标签后65秒采用。背景数据通过现有WindPy采集进程串行查询，主行情优先；繁忙时本轮让行并保留原时间。五组广度合并一次WSQ，两个板块不新增分钟线；不查询任何个股行情或板块名单；原三只指数分钟线按新完整标签更新；历史基准分批补齐并缓存，日频申赎成功后每天只查一次。不开启第二个Wind登录，不调用MCP或API Key，不改变原有信号规则。</p>
      <p className="mc-source">数据来源：{context?.source||'WindPy'}。最近成功刷新 {contextTime(context?.updated_at)}</p>
    </details>
  </section>;
}
