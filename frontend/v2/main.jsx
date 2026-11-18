import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {contracts, headline, styleEvidence, radarItems, recentEvents, optionSummary, finite, signed, clock, clockSeconds, tone, viewTime, equityPhase, sourceQuality, sourceUsable, etfQuality, metricQuality} from './model.js';
import {startLiveFeed, feedHealth} from './live-feed.js';
import {MarketCardGrid} from '../shared/market-cards.jsx';
import './style.css';

const short = s => s?.split('.')[0]||'—';
const price = (n, i) => finite(n)?n.toFixed(i?.market?.decimals??2):'—';
const money = n => finite(n)?signed(n/1e8)+'亿':'—';
function Arrow({direction='right'}) {return <span aria-hidden="true">{direction==='right'?'↗':'↓'}</span>;}
function SectionTitle({number,title,meta,children}) {return <div className="section-title"><div><span className="section-number">{number}</span><h2>{title}</h2><span className="section-meta">{meta}</span></div>{children}</div>;}

function StylePanel({snapshot}) {
  const [expanded,setExpanded]=useState(false);
  const phase=equityPhase(snapshot),asof=viewTime(snapshot);
  const style=styleEvidence(snapshot.context,asof,phase);
  const small=snapshot.context.breadths?.find(b=>b.code==='000852.SH'),large=snapshot.context.breadths?.find(b=>b.code==='000300.SH');
  const breadthDisplay=b=>finite(b?.value)&&!snapshot.context.error&&sourceUsable(metricQuality(b,asof,phase))?b.value.toFixed(1)+'%':'—';
  return <section className="panel style-panel" id="style">
    <SectionTitle number="02" title="风格，看证据是否同向" meta="1000 vs 300"/>
    <div className="style-intro"><div><span className="eyebrow">市场参与度</span><h3>{style.label}</h3></div><span className="plain-badge">{style.mixed?'存在分歧':style.confirmed?'2 项方向一致':'证据待齐'}</span></div>
    <div className="style-axis"><div className="axis-labels"><span>大盘占优</span><span>广度差 · 非综合评分</span><span>小盘占优</span></div>
      <div className="axis-track"><i className="axis-center"/>{style.dot!==null&&<i className="axis-dot" style={{left:`${style.dot}%`}}/>}</div>
      <div className="axis-values"><span>沪深300 <b>{breadthDisplay(large)}</b></span><span>上涨占比</span><span>中证1000 <b>{breadthDisplay(small)}</b></span></div>
    </div>
    <div className="evidence-grid">{style.rows.map((r,n)=><div key={r.key} className="evidence-cell">
      <div className="evidence-label"><span className="evidence-icon">{['◉','↗','▥'][n]}</span>{r.label}<small>{n===0?'当前':'近5m'}</small></div>
      <strong>{r.display}</strong><span>{r.verdict}{!r.calibrated?' · 未校准':''}</span>
    </div>)}</div>
    <p className="quiet-note">{style.description}</p>
    <div className="etf-summary"><div><b>ETF 日内主力净流入</b><small>各选一只样本 · 不计入风格确认</small></div><div className="etf-values">{(snapshot.context.etfs||[]).map(e=>{const q=etfQuality(e.flow,snapshot),valid=sourceUsable(q);return <span key={e.code} title={`${e.code} · ${e.flow?.time||'缺时间'} · ${q}`}><small>{e.root}</small><b className={valid?tone(e.flow?.value):'data-muted'}>{valid?money(e.flow?.value):'—'}</b>{!valid&&<small className="data-issue">{q}</small>}</span>;})}</div></div>
    {(!snapshot.context.monitoring||snapshot.context.error)&&<p className="quiet-note">{snapshot.context.error||'盘面背景自动更新未开启；此页只读取已有缓存。'}</p>}
    <button className="expand-button" onClick={()=>setExpanded(!expanded)} aria-expanded={expanded}>{expanded?'收起':'展开'}广度、成交与申赎明细 <span>{expanded?'−':'+'}</span></button>
    {expanded&&<div className="style-details"><div className="breadth-list">{(snapshot.context.breadths||[]).map(b=><div key={b.code}>
      <span>{b.name}<small>{b.scope==='board'?'Wind板块聚合':'指数样本'}</small></span><div className="breadth-meter"><i style={{width:`${breadthDisplay(b)==='—'?0:Math.max(0,Math.min(100,b.value??0))}%`}}/></div><b>{breadthDisplay(b)}</b><small>{breadthDisplay(b)==='—'?metricQuality(b,asof,phase):`5m ${signed(b.change_pp,1)}pp`}</small>
    </div>)}</div><div className="detail-facts">{Object.entries(snapshot.context.activity||{}).map(([root,a])=><p key={root}><span>{root} 15m成交 / 20日同刻中位数 · {clock(a.time)}</span><b>{finite(a.value)&&!snapshot.context.error&&sourceUsable(metricQuality(a,asof,phase,15))?a.value.toFixed(2)+'×':'—'}</b></p>)}{snapshot.context.etfs?.map(e=><p key={e.code}><span>{e.root} 估算净申赎 · {e.daily?.data_date}</span><b>{money(e.daily?.value)}</b></p>)}</div>
    <p className="quiet-note">ETF 超大单与大单买入额减卖出额；独立单位对照未完成。估算净申赎为份额变化×净值，与二级市场资金流不相加。</p></div>}
  </section>;
}

function PriceTrack({op}) {
  const points=[{label:'失效',value:op.stop},{label:'触发',value:op.entry},...(op.targets||[]).slice(0,2).map((t,n)=>({label:`T${n+1}${t.source==='synthetic'?' 测算':''}`,value:t.price}))].filter(p=>finite(p.value));
  if(!finite(op.entry)||!finite(op.stop))return <div className="no-trigger"><span>候选仍在形成</span><p>{op.reason}</p><small>触发价、失效价尚未确认</small></div>;
  const values=points.map(p=>p.value).concat(finite(op.price)?[op.price]:[]);
  const lo=Math.min(...values),hi=Math.max(...values),span=hi-lo||1,x=v=>36+(v-lo)/span*450;
  const current=finite(op.price)?{label:op.priceLabel,value:op.price}:null;
  return <svg className="price-track" viewBox="0 0 522 125" role="img" aria-label={points.concat(current?[current]:[]).map(p=>`${p.label} ${price(p.value,op)}`).join('，')}>
    <line x1="24" x2="498" y1="50" y2="50" stroke="var(--line-strong)" strokeWidth="2"/>
    <line x1={x(op.stop)} x2={x(op.entry)} y1="50" y2="50" stroke="var(--muted)" strokeWidth="4"/>
    {points.map((p,n)=>{const lower=n===1,xx=x(p.value);return <g key={p.label}>
      <line x1={xx} x2={xx} y1="44" y2={lower?92:62} stroke="var(--muted)" strokeDasharray={lower?'2 3':undefined}/>
      <text x={xx} y={lower?107:78} textAnchor="middle" className="track-label">{p.label}</text>
      <text x={xx} y={lower?122:93} textAnchor="middle" className="track-value">{price(p.value,op)}</text>
    </g>})}
    {current?<g><line x1={x(current.value)} x2={x(current.value)} y1="29" y2="47" stroke="var(--text)"/>
    <circle cx={x(current.value)} cy="50" r="5" fill="var(--text)" stroke="var(--panel)" strokeWidth="2"/>
    <text x={Math.max(80,Math.min(445,x(current.value)))} y="19" textAnchor="middle" className="track-current">{current.label} {price(current.value,op)}</text></g>:<text x="24" y="20" className="track-label">实时位置暂停 · 保留原计划价位</text>}
  </svg>;
}

function Radar({items,setSelected}) {
  const [all,setAll]=useState(false);const radar=radarItems(items),visible=all?radar:radar.slice(0,2);
  return <section className="panel radar-panel" id="radar">
    <SectionTitle number="03" title="下一步，关注哪里" meta={`${radar.length} 个观察项`}/>
    <p className="radar-sort">按距触发的风险倍数排序 · 仅展示原引擎状态</p>
    {visible.map((op,n)=><article className="radar-item" key={op.id}>
      <div className="radar-head"><button onClick={()=>setSelected(op.root)}><span className="rank">0{n+1}</span><b>{short(op.contract)}</b><small>{op.direction===1?'多头':'空头'} · {op.setupLabel}</small></button><span className="distance">{finite(op.distR)?`${op.distR.toFixed(2)}R`:'待确认'}</span></div>
      <div className="radar-state"><span>{op.label}</span>{finite(op.distR)&&<small>距触发 {op.distanceTicks} 跳</small>}</div>
      <PriceTrack op={op}/>
      {finite(op.entry)&&<p className="quiet-note">{op.historical?'历史重建；具体触价时刻未知，不代表成交。':'价格位置不等于新的入场确认。'}{op.phase!=='交易中'?' 休市期间仅作回顾。':''}</p>}
    </article>)}
    {!radar.length&&<div className="empty-state">当前没有有效观察项；等待原策略完成确认。</div>}
    {radar.length>2&&<button className="expand-button" onClick={()=>setAll(!all)} aria-expanded={all}>{all?'收起其他观察项':`另有 ${radar.length-2} 个观察项`} <span>{all?'−':'+'}</span></button>}
  </section>;
}

function CandleChart({item}) {
  const [structure,setStructure]=useState(false),[hover,setHover]=useState(null);
  const bars=item.daily;
  if(!bars.length)return <div className="empty-state">当前合约暂无可用分钟线。</div>;
  const levels=(item.levels||[]).filter(l=>finite(l.price)&&l.known_at<=item.market_time&&['前日高点','前日低点'].includes(l.name));
  const low=Math.min(...bars.map(b=>b.low),...levels.map(l=>l.price)),high=Math.max(...bars.map(b=>b.high),...levels.map(l=>l.price));
  const range=high-low||1,y=v=>225-(v-low)/range*195,x=n=>28+n/Math.max(1,bars.length-1)*690;
  const selected=hover===null?bars.at(-1):bars[hover]||bars.at(-1);
  const structures=(item.structures||[]).filter(e=>e.kind==='structure'&&e.known_at<=item.market_time&&e.occurred_at>=bars[0].end).slice(-6);
  return <><div className="chart-controls"><span><b>{short(item.contract)}</b> · 5分钟已完成K线</span><label><input type="checkbox" checked={structure} onChange={e=>setStructure(e.target.checked)}/>结构层</label></div>
    <div className="ohlc"><b>{clock(selected.end)}</b><span>开 {price(selected.open,item)}</span><span>高 {price(selected.high,item)}</span><span>低 {price(selected.low,item)}</span><span>收 {price(selected.close,item)}</span></div>
    <svg className="candles" viewBox="0 0 820 270" aria-label={`${item.root} 5分钟K线`} role="img" onMouseLeave={()=>setHover(null)}>
      {[0,.25,.5,.75,1].map(p=><g key={p}><line x1="16" x2="735" y1={30+p*195} y2={30+p*195} stroke="var(--line)"/><text x="746" y={34+p*195} className="axis-text">{price(high-p*range,item)}</text></g>)}
      {levels.map(l=><g key={l.id}><line x1="16" x2="735" y1={y(l.price)} y2={y(l.price)} stroke="var(--muted)" strokeDasharray="4 5" opacity=".65"/><text x="25" y={y(l.price)-6} className="axis-text">{l.name} {price(l.price,item)}</text></g>)}
      {bars.map((b,n)=><g key={b.end} onMouseEnter={()=>setHover(n)}><rect x={x(n)-10} y="20" width="20" height="220" fill="transparent"/>
        <line x1={x(n)} x2={x(n)} y1={y(b.high)} y2={y(b.low)} stroke={b.close>=b.open?'var(--up)':'var(--down)'}/>
        <rect x={x(n)-4} width="8" y={Math.min(y(b.open),y(b.close))} height={Math.max(1,Math.abs(y(b.open)-y(b.close)))} fill={b.close>=b.open?'var(--up)':'var(--down)'}/>
      </g>)}
      {structure&&structures.map(e=>{const n=bars.findIndex(b=>b.end>=e.occurred_at);return n<0?null:<g key={e.id}><circle cx={x(n)} cy={y(e.price)} r="4" fill="var(--text)"/><text x={x(n)} y={y(e.price)-12} textAnchor="middle" className="axis-text">{e.scale==='internal'?'i':'s'} {e.label}</text></g>;})}
      {finite(item.price)&&item.price>=low&&item.price<=high&&<line x1="16" x2="735" y1={y(item.price)} y2={y(item.price)} stroke="var(--text)" opacity=".35" strokeDasharray="2 3"/>}
      {[0,Math.floor(bars.length/2),bars.length-1].map(n=><text key={n} x={x(n)} y="256" textAnchor="middle" className="axis-text">{clock(bars[n].end)}</text>)}
    </svg><p className="quiet-note">默认只显示前日高低与快照价格。{structure?'已展开最近6个已确认结构；标签锚点是发生时间，确认时间见事件流。':'悬停查看单根价格，勾选结构层查看确认标记。'}</p></>;
}

function Options({snapshot,selected}) {
  const context=snapshot.options[selected],o=optionSummary(context),phase=context?.phase;
  const q=sourceQuality(o.time,viewTime(snapshot),phase,15);
  const usable=sourceUsable(q)&&(snapshot.mode!=='live'||phase!=='交易中'||context?.ready);
  const status=sourceUsable(q)?o.status||'估值未就绪':q;
  return <div className="option-strip"><div className="option-reading"><span className="eyebrow">{selected} 期权 · {o.expiry||'无已验证覆盖'}</span><h3>{usable?o.label:'期权估值暂不可用'}</h3><p>历史分位待接入；IV−RV 不能单独判断贵或便宜。</p></div><div className="option-gap"><small>IV − RV20</small><strong>{signed(usable?o.gap:null,1)}<span>波动点</span></strong><small>估值 {clockSeconds(o.time)} · {status}</small></div><details><summary>原始指标与口径</summary><p>{usable?'当前估值':'保留的历史估值'}：IV {finite(o.iv)?(o.iv*100).toFixed(2)+'%':'—'} · RV20 {finite(o.rv)?(o.rv*100).toFixed(2)+'%':'—'} · 25Δ Put−Call {signed(o.skew,1)} 波动点</p><p>期限：{o.expiry||'—'}。RV20来自同一标的已完成日收益。隐含与已实现波动面向不同时间区间；估值和历史数据沿用原后台。</p></details></div>;
}

function EventFeed({snapshot}) {
  const [all,setAll]=useState(false),feed=recentEvents(snapshot);const visible=all?feed.items:feed.items.slice(0,6);
  return <section className="panel events-panel" id="events"><SectionTitle number="05" title="刚才发生了什么" meta="30分钟"/>
    <p className="event-window">{snapshot.state.instruments.every(i=>i.phase==='午休')?'上午最后30分钟':snapshot.mode==='live'?'最近30分钟':'快照前30分钟'} <b>{clock(feed.start)} — {clock(feed.end)}</b></p>
    {snapshot.transport?.partial_errors?.events&&<p className="quiet-note">事件读取失败，显示上次记录。</p>}
    <div className="event-list">{visible.map(e=><article key={e.id}><time>{clock(e.known_at)}</time><div><b>{e.text}</b><span>{e.kindLabel} · {e.kindLabel==='快照变化'?'按相邻源快照识别':'按可知时刻排列'}</span></div></article>)}</div>
    {!feed.items.length&&<div className="empty-state">这一时段没有已记录事件。</div>}
    {feed.items.length>6&&<button className="expand-button" onClick={()=>setAll(!all)} aria-expanded={all}>{all?'收起事件':`查看全部 ${feed.items.length} 条`} <span>{all?'−':'+'}</span></button>}
    <div className="coverage-note"><b>还缺两块证据</b><p>全 A 同刻成交额、涨停 / 跌停家数尚未接入。本版留出位置，不用局部指数代替全市场。</p></div>
  </section>;
}

function App() {
  const [rawSnapshot,setSnapshot]=useState(null),[connection,setConnection]=useState({connected:false,error:''}),[now,setNow]=useState(Date.now()),[selected,setSelected]=useState('IM'),[disconnect,setDisconnect]=useState(false),[rules,setRules]=useState(false);
  useEffect(()=>startLiveFeed({onSnapshot:setSnapshot,onConnection:setConnection}),[]);
  useEffect(()=>{const timer=setInterval(()=>setNow(Date.now()),1000);return ()=>clearInterval(timer);},[]);
  if(!rawSnapshot)return <main className="loading"><span className="brand-mark">W/</span><h1>{connection.error||'正在连接实时后台…'}</h1><p>盘面解说 V2 · 自动重试 · 原看板保持运行</p></main>;
  const snapshot={...rawSnapshot,view_time:new Date(now).toISOString()};
  const health=feedHealth(snapshot,connection,now),paused=disconnect||!health.connected;
  const items=contracts(snapshot).map(i=>paused?{...i,quoteUsable:false}:i),summary=headline(snapshot,paused),active=items.find(i=>i.root===selected)||items[0];
  const exceptions=items.filter(i=>!i.usable);
  const quoteCount=items.filter(i=>i.quoteUsable).length;
  if(!active)return <main className="loading"><h1>等待后台加载监测合约…</h1><p>{health.label}</p></main>;
  return <div className="app-shell">
    <header className="topbar"><a className="brand" href="#"><span className="brand-mark">W/</span><span>盘面解说 <small>WIND DESK</small></span></a><nav><a className="active" href="#">总览</a><a href="#radar">机会</a><a href="#detail">细节</a></nav><div className="top-actions"><span className="version-badge">V2 · 实时</span><a href="http://127.0.0.1:8765/" target="_blank" rel="noreferrer">打开原看板 <Arrow/></a></div></header>
    <div className="snapshot-bar" title="页面约每3秒读取本机缓存；盘面背景沿用原后台30秒、ETF至少60秒的刷新节奏。"><span><i className={`status-dot ${paused?'status-off':'status-live'}`}/>{disconnect?'断线演示':health.label} · {summary.phase==='盘中'?`${quoteCount}/${items.length} 报价有效`:summary.phase+'回顾'} · {snapshot.state.server_time.slice(0,10).replaceAll('-','.')}</span><span>服务 {clockSeconds(snapshot.state.server_time)} · {active.root} 行情 {clockSeconds(active.market_time)} · 自动更新 <button onClick={()=>setDisconnect(!disconnect)} aria-pressed={disconnect}>{disconnect?'退出断线演示':'试看断线状态'}</button></span></div>
    {paused&&<div className="disconnect-banner" role="status">{disconnect?'断线演示：页面与服务连接中断':health.label}，结论暂停。下方仅保留灰色历史数据；恢复后自动更新。</div>}
    <main className={summary.paused?'disconnected':''}>
      <section className="hero"><div className="hero-heading"><span className="eyebrow">{summary.phase==='午休'?'MIDDAY BRIEF / 上午小结':summary.phase==='盘中'?'MARKET BRIEF / 盘中解说':'SESSION BRIEF / 休市回顾'}</span><button className="text-button" onClick={()=>setRules(!rules)} aria-expanded={rules}>结论怎么算 <span>ⓘ</span></button></div>
        <h1>{summary.title}</h1><p className="hero-subtitle">{summary.subtitle||'源状态恢复前，不生成方向判断。'}</p>
        <div className="headline-evidence">{summary.items.map(i=><a href={'#'+i.target} key={i.slot}><span className="headline-slot">{i.slot}</span><div><b className={i.tone}>{i.title}</b><small>{i.evidence}</small></div><Arrow/></a>)}</div>
        {rules&&<div className="rules-box"><b>有证据才下结论；这些是展示规则。</b><p>广度差达到20个百分点、相对同刻成交偏向达到1.15×时描述偏向；价格只报告近5分钟实际价差，不冒充统计显著。三维冲突显示“证据分歧”，不合成评分。阈值为待校准初值。</p><p>八张卡以实时价对比前一完整日盘收盘；期货相对昨结算的涨跌幅可能不同，悬停可核对后台原值。商品“领跌”只比较 AG / AU / SC，不比较跨资产风险；T、TL 同涨才写“国债期货走高”。机会状态完全沿用原策略，历史触价不升级为实时确认。</p><p>历史日收益标准差、IV / skew 同期限分位尚未接入；缺失保持缺失。报价保留15秒门槛及后台健康检查；分钟指标按完整K线节奏检查，午休和收盘标为回顾；断线暂停摘要。</p></div>}
        {exceptions.length>0&&<p className="exception-note">局部数据异常：{exceptions.map(i=>`${i.root} ${i.quality}`).join('；')}，对应项不参与摘要。</p>}
      </section>
      <section id="markets" className="markets">
        <SectionTitle number="01" title="先看哪里在动" meta="日涨跌与15分钟结构分开读"/>
        <MarketCardGrid items={items} selected={selected} onSelect={setSelected} paused={paused}/>
        <div className="section-foot">涨跌幅：现价 / 前一完整日盘收盘 − 1 · 曲线：5分钟收盘价　<span>期货与昨结算口径可能不同；历史波动基准待接入</span></div>
      </section>
      <div className="main-grid"><StylePanel snapshot={snapshot}/><Radar items={items} setSelected={setSelected}/></div>
      <div className="detail-grid" id="detail"><section className="panel chart-panel"><SectionTitle number="04" title="需要时，再看细节" meta="点击上方品种切换"/>{snapshot.transport?.bars_loading?.length>0&&<p className="quiet-note">正在载入 {snapshot.transport.bars_loading.length} 个品种的分钟线缓存，实时报价独立更新。</p>}<CandleChart key={active.contract} item={active}/><Options snapshot={snapshot} selected={active.root}/></section><EventFeed snapshot={snapshot}/></div>
      <footer><span>结论 → 证据 → 细节</span><p>只读 WindPy 缓存 · 原策略、提醒与行情采集保持原样</p><span>WIND DESK / V2</span></footer>
    </main>
  </div>;
}
createRoot(document.getElementById('root')).render(<App/>);
