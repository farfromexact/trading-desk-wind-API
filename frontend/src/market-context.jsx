import React, {useEffect, useState} from 'react';
import {contextMetrics, contextRows, contextTime, crossRows, etfRows, sourceStatus} from './market-context.js';
import './market-context.css';

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
  const etfs=etfRows(context,clock);
  const allMetrics=[...metrics.slice(0,2),...rows.flatMap(row=>[row.activity,row.position])];
  const archived=allMetrics.some(x=>x.time&&sourceStatus(x.time,clock)==='历史');
  const stamp=(metric,maxSeconds=420)=>metric.time?<span className="mc-time">{contextTime(metric.time)}<em className={'mc-status '+sourceStatus(metric.time,clock,maxSeconds)}>{sourceStatus(metric.time,clock,maxSeconds)}</em></span>:null;
  return <section className={'market-context market-context--'+layout} aria-label="股指日内市场背景">
    <div className="mc-heading"><div><span className="mc-eyebrow">MARKET CONTEXT</span><h2>盘面底色 <span>{preview?'分支预览':archived?'历史快照':'观察中'}</span></h2></div>
      <div className="mc-controls"><span>{context?.busy?'正在查询…':context?.monitoring?'盘中每60秒更新':'按需更新'}</span>
        <button disabled={pending||context?.busy||context?.disabled} onClick={()=>action('refresh')}>刷新</button>
        {!preview&&<button disabled={pending||context?.disabled} aria-pressed={!!context?.monitoring} onClick={()=>action('monitor',{enabled:!context?.monitoring})}>{context?.monitoring?'暂停更新':'盘中更新'}</button>}
      </div>
    </div>
    <div className="mc-grid">{metrics.slice(0,2).map((metric,i)=><div className="mc-metric" key={metric.label}>
      <div className="mc-label"><span>{metric.label}</span><small>现货</small></div>
      <strong className={metric.value==='—'?'mc-empty':''}>{metric.value}</strong>
      <p title={metric.subtitle}>{metric.subtitle}</p>
      {stamp(metric,i===0?180:420)}
    </div>)}
      <div className="mc-comparison"><table aria-label="IM IF IH 成交与结构并列"><thead><tr><th scope="col">股指</th><th scope="col">成交活跃度 · 15m</th><th scope="col">{preview?'历史期货位置':'期货位置'}</th></tr></thead><tbody>
        {rows.map(row=><tr key={row.root}><th scope="row">{row.root}</th>
          <td><strong className={row.activity.value==='—'?'mc-empty':''}>{row.activity.value}</strong><div className="mc-row-meta"><span className="mc-row-note" title={row.activity.subtitle}>{row.activity.value==='—'?row.activity.subtitle:`现货 · ${context?.activity?.[row.root]?.baseline_days}日同刻基准`}</span>{stamp(row.activity)}</div></td>
          <td><strong className={row.position.value==='—'?'mc-empty':''}>{row.position.value}</strong><div className="mc-row-meta"><span className="mc-row-note">{row.position.subtitle.replace(new RegExp('^'+row.root+' · '),'')}</span>{stamp(row.position,180)}</div></td>
        </tr>)}
      </tbody></table></div>
    </div>
    <div className="mc-cross"><span className="mc-cross-label">跨市场背景</span>
      {cross.length?cross.map(row=><span className="mc-cross-item" key={row.code} title={`${row.group} · ${contextTime(row.time)} · 交易日 ${row.trading_day} · 当日累计成交额 ${row.amountLabel}（不是净流入）`}>
        <span>{row.label}</span><b className={row.change_pct>0?'mc-up':row.change_pct<0?'mc-down':''}>{row.value}</b><small>{row.group} · {sourceStatus(row.time,clock,420)}</small>
      </span>):<span className="mc-cross-empty">商品样本 / T、TL · 等待对应监测合约快照</span>}
      <span className="mc-cross-note">各自时点 · 不推断资金轮动</span>
    </div>
    <div className="mc-etfs"><table aria-label="ETF盘中资金流与日频净申赎"><thead><tr><th scope="col">ETF样本</th>{etfs.map(row=><th scope="col" key={row.code}>{row.name}<small>{row.code.split('.')[0]} · {row.manager}</small></th>)}</tr></thead><tbody>
      <tr><th scope="row">主力净流入<small>二级市场 · 当日累计</small></th>{etfs.map(row=><td key={row.code} title={`${row.flow.time||'源时间未取得'} · ${row.flow.reason||'Wind MCP基金行情口径'} · 本机收到 ${row.flow.observed_at||'未取得'}`}>
        <strong className={row.flow.value>0?'mc-up':row.flow.value<0?'mc-down':'mc-empty'}>{row.flow.display}</strong>
        <span className="mc-etf-time">{row.flow.timeLabel} · {row.flow.time?row.flow.stage:'暂缺'}</span>
        {row.flow.time&&<span className="mc-etf-status">{row.flow.status}</span>}
      </td>)}</tr>
      <tr><th scope="row">日频净申赎<small>Wind申赎净流入额</small></th>{etfs.map(row=><td key={row.code} title={`本机收到 ${row.daily.observed_at||'未取得'}；实际披露时刻未核实，不代表此数据日盘中已知。${row.daily.reason||''}`}>
        <strong className={row.daily.value>0?'mc-up':row.daily.value<0?'mc-down':'mc-empty'}>{row.daily.display}</strong>
        <span className="mc-etf-time">{row.daily.dateLabel}</span>
      </td>)}</tr>
    </tbody></table><div className="mc-etf-foot"><span>盘中累计与日频申赎分开看 · 不相加 · 单只ETF样本</span><span>{Object.keys(context?.etf_errors||{}).length?'部分ETF未更新，保留源时间':'日频披露时刻未核实'}</span></div></div>
    {(error||context?.error)&&<div className="mc-error" role="status">{error||context.error} · 原数据保留时间标记</div>}
    <details className="mc-details"><summary>口径与时间 <span>背景观察 · 不计分</span></summary>
      <div className="mc-definitions">{metrics.map(metric=><p key={metric.label}><b>{metric.label}</b>{metric.detail||'等待已确认结构；行情不就绪时不推定位置。'}</p>)}</div>
      <p>商品仅含已监测的 AG、AU、SC 样本；国债仅含 T、TL。各合约保留自己的交易日与时间，夜盘与日盘不直接加总。涨跌幅为行情源口径；成交额为名义成交额，不能当作资金净流入。</p>
      {cross.map(row=><p key={row.code}>{row.label} · {contextTime(row.time)} · 交易日 {row.trading_day} · 累计成交额 {row.amountLabel}</p>)}
      <p>ETF二级市场流向采用基金MCP的“当日主力净流入额”，是当日累计算法口径，不是全部买卖现金净额，也不据此识别机构身份；与WindPy的MFD_INFLOW_M曾出现来源差异，本页不拼接。源时间在收盘后时明确显示“盘后快照”，历史值不标为实时。对应主力资金流历史分钟序列尚未验证。</p>
      <p>日频净申赎采用 f_mf_netsubamt（申赎净流入额），金额原单位为元；样本审计中等于份额净变动×当日单位净值，不是盘中逐笔现金到账。本模块只查询当前日期之前的交易日，非交易日按服务返回的实际数据日展示。数据日、本机收到时间与实际披露时刻是不同概念，后者未核实，不能将历史日频数回填为当日盘中已知。每种宽基仅展示列明的单只ETF，不代表同指数全部产品。</p>
      {etfs.map(row=><p key={row.code}>{row.name} {row.code}：资金流源时间 {row.flow.time||'未取得'}；日频数据日 {row.daily.data_date||'未取得'}；日频本机收到 {row.daily.observed_at||'未取得'}；{context?.etf_errors?.flow||context?.etf_errors?.daily||'无本轮ETF查询错误'}</p>)}
      <p>科创/创业板广度及期货资金流字段尚未接入这一版。所有空值保持缺失；这组背景不改变原有机会确认或声音规则。</p>
      <p>完整分钟线至少在标签后65秒采用。历史基准冷启动需积累或导入；不回填历史分钟广度。每分钟批量查询指数及ETF快照，每5分钟补三只指数分钟线和一批跨市场快照；日频申赎正常每自然日查询一次，失败或缺失间隔30分钟再尝试。开启后约672次业务查询/完整股指交易日，另加日频批次；额度按服务实际规则计算。</p>
      <p className="mc-source">数据来源于 Wind Alice 万得金融数据服务。最近成功刷新 {contextTime(context?.updated_at)}</p>
    </details>
  </section>;
}
