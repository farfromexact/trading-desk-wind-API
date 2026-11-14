import React,{useEffect,useRef,useState} from 'react';
import './options.css';

const num=(x,n=2)=>Number.isFinite(x)?x.toLocaleString('zh-CN',{maximumFractionDigits:n,minimumFractionDigits:n}):'—';
const pct=x=>Number.isFinite(x)?num(x*100)+'%':'—';
const stamp=x=>x?new Date(x).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—';
const kinds={long:'单买期权',debit_vertical:'支付权利金价差',credit_vertical:'收取权利金价差'};

function useData(path,period=5000,refreshKey=null){
  const [data,setData]=useState(null),[error,setError]=useState('');
  const refresh=useRef(null);
  useEffect(()=>{
    let alive=true,running=false; const controller=new AbortController();setData(null);setError('');
    async function load(){
      if(running)return;running=true;
      try{const r=await fetch('/api/'+path,{signal:controller.signal});const body=await r.json();if(!r.ok)throw Error(body.detail||'读取失败');if(alive){setData(body);setError('');}}
      catch(e){if(alive&&e.name!=='AbortError')setError(e.message);}
      finally{running=false;}
    }
    refresh.current=load;load();const t=setInterval(load,period);return()=>{alive=false;refresh.current=null;clearInterval(t);controller.abort();};
  },[path,period]);
  useEffect(()=>{refresh.current?.();},[refreshKey]);
  return {data,error};
}

export function OptionsEnvironment({root,context,onOpen}){
  const first=context?.expiries?.[0];
  return <section className="option-environment" aria-label="期权环境">
    <div className="option-env-title"><span className="eyebrow">OPTIONS CONTEXT</span><b>{root} 期权环境</b><small className={context?.ready?'accent':'amber'}>{context?.status||'连接中'}</small></div>
    <div><small>平值 IV {first?.expiry?.slice(5)}</small><b>{pct(first?.atm_iv)}</b></div>
    <div><small>实际波动 RV5 / RV20</small><b>{pct(context?.rv5)} / {pct(context?.rv20)}</b></div>
    <div><small>25Δ 认沽 − 认购</small><b>{Number.isFinite(first?.put_minus_call_25d)?num(first.put_minus_call_25d*100)+' 波动点':'—'}</b></div>
    <div><small>15分钟 IV 变化</small><b>{Number.isFinite(context?.iv_change_15m)?num(context.iv_change_15m*100)+' 波动点':context?.readonly_preview?'未载入':'采集中'}</b></div>
    <button onClick={onOpen}>期权链与研究 ↗</button>
    <p>{context?.notes?.[0]||(context?.ready?'波动状态作为方向信号的补充，优势尚待验证':context?.phase&&context.phase!=='交易中'?'休市快照仅供观察，开市同步后再评估候选':'等待同步的标的和期权双边盘口')}<span>行情 {stamp(context?.market_time)}</span></p>
  </section>;
}

function Plot({series,xLabel,yLabel,zero=false,nonnegative=false}){
  const good=series.flatMap(s=>s.data).filter(x=>Number.isFinite(x.x)&&Number.isFinite(x.y));
  if(!good.length)return <div className="option-chart-empty">尚无有效数据，缺失值不会填零。</div>;
  const left=65,right=695,top=25,bottom=220;
  let xmin=Math.min(...good.map(x=>x.x)),xmax=Math.max(...good.map(x=>x.x));
  let ymin=Math.min(...good.map(x=>x.y),...(zero?[0]:[])),ymax=Math.max(...good.map(x=>x.y),...(zero?[0]:[]));
  if(xmax===xmin){xmin-=1;xmax+=1;}if(ymax===ymin){ymin-=1;ymax+=1;}
  const pad=(ymax-ymin)*.1;ymin-=pad;ymax+=pad;
  if(nonnegative)ymin=Math.max(0,ymin);
  const X=x=>left+(x-xmin)/(xmax-xmin)*(right-left),Y=y=>bottom-(y-ymin)/(ymax-ymin)*(bottom-top);
  return <div className="option-plot"><svg viewBox="0 0 730 265" role="img" aria-label={yLabel+'随'+xLabel+'变化'}>
    {[0,1,2,3,4].map(i=>{const v=ymin+(ymax-ymin)*i/4;return <g key={i}><line x1={left} x2={right} y1={Y(v)} y2={Y(v)} stroke="#27333f"/><text x={left-8} y={Y(v)+4} textAnchor="end">{num(v,1)}</text></g>;})}
    {zero&&ymin<0&&ymax>0&&<line x1={left} x2={right} y1={Y(0)} y2={Y(0)} stroke="#a4b3be" strokeDasharray="4 4"/>}
    {[0,1,2,3,4].map(i=>{const x=xmin+(xmax-xmin)*i/4;return <text key={i} x={X(x)} y={bottom+18} textAnchor="middle">{num(x,1)}</text>;})}
    {series.map(s=><g key={s.name}><polyline points={s.data.filter(x=>Number.isFinite(x.x)&&Number.isFinite(x.y)).sort((a,b)=>a.x-b.x).map(p=>`${X(p.x)},${Y(p.y)}`).join(' ')} fill="none" stroke={s.color} strokeWidth="2"/>{s.data.filter(x=>Number.isFinite(x.x)&&Number.isFinite(x.y)).map((p,i)=><circle key={i} cx={X(p.x)} cy={Y(p.y)} r="3" fill={s.color}><title>{s.name} · {p.label||num(p.x)}：{num(p.y)}</title></circle>)}</g>)}
    <text x={left} y="14">{yLabel}</text><text x="380" y="260" textAnchor="middle">{xLabel}</text>
  </svg><div className="option-plot-legend">{series.map(s=><span key={s.name} style={{color:s.color}}>● {s.name}</span>)}</div></div>;
}

function Candidate({candidate,active,onSelect}){
  const c=candidate;const base=c.scenarios.find(s=>s.days===c.holding_days&&s.iv_change===0);
  return <button className={'option-choice '+(active?'active':'')} onClick={onSelect}>
    <div className="option-choice-title"><b>{kinds[c.kind]}</b><span>{c.expiry}</span></div>
    <div className="option-legs">{c.legs.map(x=><div key={x.code}><span className={x.quantity>0?'accent':'amber'}>{x.quantity>0?'买':'卖'} 1手</span><b>{x.code}</b></div>)}</div>
    <div className="option-choice-numbers"><span><small>{c.entry_cost<0?'参考收取权利金':'参考支付权利金'}</small><b>¥{num(Math.abs(c.entry_cost))}</b></span><span><small>到期损失上限 · 成本压力</small><b>¥{num(c.max_loss)}</b></span></div>
    <div className="option-mini-scenarios"><span>T1 情景 <b className={base?.pnl.T1>0?'accent':'amber'}>¥{num(base?.pnl.T1)}</b></span><span>横盘 <b>¥{num(base?.pnl['横盘'])}</b></span></div>
    <div className="option-profit-limit">到期收益上限：{c.max_profit===null?'理论不封顶':'¥'+num(c.max_profit)}</div>
    <small className="option-fee-note">{c.fee_known?'含配置手续费':'手续费未配置 · 未扣费'} · {c.holding_days||'日内'}{c.holding_days?'日':''} / IV不变</small>
  </button>;
}

function Scenarios({candidate}){
  const [days,setDays]=useState(0),[iv,setIV]=useState(0),[mode,setMode]=useState('holding'),[target,setTarget]=useState('T1');
  const c=candidate;
  const s=c.scenarios.find(x=>x.days===days&&x.iv_change===iv);
  const prices={'失效':c.underlying_stop,'横盘':c.futures_price,T1:c.targets[0].price,T2:c.targets[1]?.price??c.targets[0].price};
  const series=mode==='expiry'?[{name:'完整组合到期损益',color:'#d8bd7a',data:c.payoff.map(x=>({x:x.underlying,y:x.pnl}))}]:[{name:'持有期估值情景',color:'#84baa9',data:Object.entries(s?.pnl||{}).map(([label,y])=>({x:prices[label],y,label}))}];
  return <div className="option-scenarios"><div className="option-controls"><b>情景比较 · {kinds[c.kind]}</b><div className="segments"><button className={mode==='holding'?'active':''} onClick={()=>setMode('holding')}>持有期估值</button><button className={mode==='expiry'?'active':''} onClick={()=>setMode('expiry')}>到期收益</button></div></div>
    {mode==='holding'&&<div className="option-controls"><label>时间<select aria-label="期权情景时间" value={days} onChange={e=>setDays(+e.target.value)}>{[0,1,3,5].map(x=><option key={x} value={x}>{x?`第${x}个交易日收盘`:'当日收盘'}</option>)}</select></label><label>IV变化<select aria-label="IV压力情景" value={iv} onChange={e=>setIV(+e.target.value)}><option value={-.2}>相对下降20%</option><option value={0}>保持不变</option><option value={.2}>相对上升20%</option></select></label><label>价格情景<select aria-label="期权价格情景" value={target} onChange={e=>setTarget(e.target.value)}>{Object.keys(prices).map(x=><option key={x}>{x}</option>)}</select></label><strong className="option-result">¥{num(s?.pnl[target])}</strong></div>}
    <Plot series={series} xLabel={mode==='expiry'?'到期标的价格':'期货价格情景（保持基差假设）'} yLabel={'每组一手损益 / 元'+(c.fee_known?'':' · 未扣手续费')} zero/>
    <div className="option-explanations"><p className="option-suitable">{c.suitable}。</p><p>关联期货触发价 {num(c.underlying_entry)} · 结构失效价 {num(c.underlying_stop)} · T1 {num(c.targets[0].price)} · T2 {num(c.targets[1]?.price)}。失效仍按原期货结构判断。</p><p>{c.cost_note}。持有期卖出按模型价减当前半价差、买回按模型价加半价差，不能保证未来可成交。</p><p>{c.note}。{c.basis_note}。</p><p>{c.risk_note}。情景是敏感性分析，不是价格或胜率预测。</p><p>组合 Delta ¥{num(c.greeks.delta)}/标的价格1单位 · Gamma ¥{num(c.greeks.gamma,4)}/价格单位² · Vega ¥{num(c.greeks.vega)}/波动率1个百分点 · Theta ¥{num(c.greeks.theta)}/日历日</p></div>
  </div>;
}

export function OptionsWorkbench({root,opportunityId,context}){
  const [holding,setHolding]=useState(5),[chosen,setChosen]=useState(null),[expiry,setExpiry]=useState('');
  const {data,error}=useData('options/candidates?root='+root+'&holding_days='+holding+(opportunityId?'&opportunity_id='+opportunityId:''),5000,context?.generated_at);
  useEffect(()=>{setChosen(null);setExpiry('');},[root,opportunityId,holding]);
  const expiries=[...new Set((data?.candidates||[]).map(x=>x.expiry))];
  const activeExpiry=expiries.includes(expiry)?expiry:expiries[0];
  const cards=(context?.ready?data?.candidates||[]:[]).filter(c=>c.expiry===activeExpiry);
  const selected=cards.find(x=>x.id===chosen)||cards[0];
  return <section className="panel option-workbench"><div className="panel-head"><h2>交易工具比较 <span className="muted">{root} · 与期货确认信号关联</span></h2><label className="option-holding">计划持有<select aria-label="期权计划持有周期" value={holding} onChange={e=>setHolding(+e.target.value)}>{[0,1,3,5].map(x=><option value={x} key={x}>{x?`${x}个交易日`:'日内'}</option>)}</select></label></div>
    {error&&<p className="option-error">{error} · 不使用旧候选作为当前机会</p>}
    {!error&&cards.length>0?<><div className="option-controls"><div className="segments">{expiries.map(e=><button key={e} className={e===activeExpiry?'active':''} onClick={()=>setExpiry(e)}>{e} 到期</button>)}</div><span>报价参考 {stamp(selected.generated_at)} · 已触价不代表成交</span></div>{data.futures_reference?.ready&&<div className="option-futures-reference"><b>期货对照 · 1手</b>{Object.entries(data.futures_reference.pnl).map(([label,value])=><span key={label}>{label} <strong>¥{num(value)}</strong></span>)}<p>{data.futures_reference.note} 不同工具按各自1手展示，金额不代表相同风险预算。</p></div>}<div className="option-comparison-grid">{cards.map(c=><Candidate key={c.id} candidate={c} active={selected?.id===c.id} onSelect={()=>setChosen(c.id)}/>)}</div>{data?.reasons?.length>0&&<p className="option-error">未提供的方案：{data.reasons.join("；")}</p>}{selected&&<Scenarios candidate={selected}/>}</>:<div className="option-wait"><b>{context?.status==='无已验证覆盖'?'此品种暂无已验证的期权覆盖':'等待可比较的期权方案'}</b><p>{data?.reasons?.join('；')||context?.notes?.join('；')||'正在核对期权数据'}</p><div><span>01　期货收盘确认</span><span>02　盘口与到期日合格</span><span>03　比较单买与价差</span></div></div>}
  </section>;
}

function Research(){
  const {data,error}=useData('options/research',30000);
  return <div className="option-research"><h3>验证记录</h3><p>{error||data?.note||'正在读取记录'}</p><div className="option-research-metrics"><span>真实确认信号<b>{data?.signals??'—'}</b></span><span>有信号的交易日<b>{data?.observation_days??0} / {data?.minimum_days??20}</b></span><span>日盘最长连续同步<b>{num((data?.metrics.day_longest_seconds||0)/60,1)} / 30分</b></span><span>夜盘最长连续同步<b>{num((data?.metrics.night_longest_seconds||0)/60,1)} / 30分</b></span></div><p className="amber">{data?.status} · 长期效果需要实际采集后的样本，不能用历史收盘价伪造盘口。连续同步统计不等于人工验收。</p>
    <p>有期权方案的确认信号 {data?.coverage?.with_candidates??0}；因数据或条件不足未提供方案 {data?.coverage?.without_candidates??0}；未触价 {data?.coverage?.not_triggered??0}。{data?.coverage?.reasons?.join('；')}</p>
    <table><thead><tr><th>品种 / 模型 / 持有期</th><th>到点评估</th><th>缺少盘口</th><th>可计算净值的方案</th></tr></thead><tbody>{Object.entries(data?.evaluation_groups||{}).map(([k,v])=><tr key={k}><td>{k}</td><td>{v.samples}</td><td>{v.missing}</td><td>{v.net_option_samples}</td></tr>)}</tbody></table>
    <details><summary>按波动状态比较基准路径</summary><p>以下是描述性样本，不代表可执行策略收益；期货参考R与期权每组一手金额口径不同。极值基于已接收报价，断线期间未知。</p><table><thead><tr><th>分组</th><th>样本</th><th>参考R均值</th><th>扣费参考R</th><th>最大不利波动R</th><th>最大有利波动R</th></tr></thead><tbody>{Object.entries(data?.descriptive_comparison||{}).map(([k,v])=><tr key={k}><td>{k}</td><td>{v.samples}</td><td>{num(v.mean_reference_r)}</td><td>{num(v.mean_net_r)}<small> n={v.net_r_samples??0}</small></td><td>{num(v.mean_mae_r)}</td><td>{num(v.mean_mfe_r)}</td></tr>)}</tbody></table><p>净值仅在手续费已配置且双边盘口有效时提供。期权金额按计划持有期及到期日分组，不与期货R直接相加。</p>{Object.entries(data?.descriptive_comparison||{}).filter(([,v])=>Object.keys(v.option_mean_net_cny||{}).length>0).map(([group,v])=><p key={group}>{group}：{Object.entries(v.option_mean_net_cny).map(([k,x])=>`${k} · ${x.samples}个样本 · 均值 ¥${num(x.mean)}`).join("；")}</p>)}</details>
    <div className="option-audit-links"><a href="/api/options/audit?kind=signal&limit=100" target="_blank" rel="noreferrer">确认时的冻结数据 ↗</a><a href="/api/options/audit?kind=evaluation&limit=100" target="_blank" rel="noreferrer">持有期评估记录 ↗</a></div>
  </div>;
}

function FeeSettings({root}){
  const {data}=useData('options/settings',30000);
  const [fee,setFee]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
  const savedFee=data?.fee_per_contract_each_side?.[root];
  useEffect(()=>{setFee(savedFee??'');setNotice('');},[root,savedFee]);
  if(!['IM','IF','IH','AG','AU','SC'].includes(root))return null;
  async function save(){setBusy(true);try{const r=await fetch('/api/options/fees',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({root,fee:fee===''?null:Number(fee)})});const body=await r.json();if(!r.ok)throw Error(body.detail||'保存失败');setNotice('已保存；新测算采用该费用，已有冻结记录保持原口径。');}catch(e){setNotice(e.message);}finally{setBusy(false);}}
  return <details className="option-fee-settings"><summary>{root} 手续费设置</summary><p>填写包含交易所及券商费用的每手单边合计，开平使用同一估算值；留空表示尚未配置，0表示明确按零估算。</p><label>元 / 手 / 单边 <input type="number" min="0" max="100000" step="0.01" aria-label="期权每手单边手续费" value={fee} onChange={e=>setFee(e.target.value)}/></label><button onClick={save} disabled={busy}>保存费用</button><p>{notice}</p></details>;
}

export function OptionsDetails({root,liveQuotes,contextRevision,onClose}){
  const {data,error}=useData('options/chain?root='+root,5000,contextRevision);
  const [expiry,setExpiry]=useState(''),[tab,setTab]=useState('chain');
  const context=data?.context;
  const expiries=context?.expiries||[];
  const active=expiries.some(e=>e.expiry===expiry)?expiry:expiries[0]?.expiry;
  const rows=(data?.rows||[]).filter(x=>x.expiry===active).map(x=>({...x,quote:liveQuotes?.[x.code]||x.quote,quote_time:liveQuotes?.[x.code]?.market_time||x.quote_time}));
  useEffect(()=>{const handler=e=>{if(e.key==='Escape')onClose();};window.addEventListener('keydown',handler);return()=>window.removeEventListener('keydown',handler);},[onClose]);
  const ivSeries=[{side:1,name:'认购 IV',color:'#d9ad80'},{side:-1,name:'认沽 IV',color:'#8ebdb4'}].map(x=>({...x,data:rows.filter(r=>r.side===x.side&&r.iv!==null).map(r=>({x:r.strike,y:r.iv*100}))}));
  const oiSeries=[{side:1,name:'认购持仓',color:'#d9ad80'},{side:-1,name:'认沽持仓',color:'#8ebdb4'}].map(x=>({...x,data:rows.filter(r=>r.side===x.side).map(r=>({x:r.strike,y:r.quote.rt_oi}))}));
  return <div className="modal-backdrop"><section className="panel option-modal" role="dialog" aria-modal="true" aria-label={root+'期权详情'}><div className="panel-head"><h2>{root} 期权详情</h2><button onClick={onClose}>关闭 ×</button></div><div className="option-controls"><div className="segments"><button className={tab==='chain'?'active':''} onClick={()=>setTab('chain')}>期权链与定价</button><button className={tab==='research'?'active':''} onClick={()=>setTab('research')}>验证记录</button></div><span>{context?.status}</span></div>
    {error&&<p className="option-error">{error}</p>}
    {tab==='research'?<Research/>:<><div className="option-detail-summary"><p>{context?.basis_note}。{context?.coverage}。</p><p>标的 {context?.underlying||'—'}　价格 {num(context?.spot)}　期现差 {num(context?.basis)}　行情 {stamp(context?.market_time)}</p><p>已发现 {data?.metadata_count??0} 个合约 · 当前监测 {context?.total??0} 个 · 估值合格 {context?.qualified??0} 个</p><p>{context?.notes?.join('；')}</p></div>
    <div className="option-expiries">{expiries.map(e=><button key={e.expiry} onClick={()=>setExpiry(e.expiry)} className={e.expiry===active?'active':''}><b>{e.expiry}</b><span>剩余 {e.trading_days??'—'} 个交易日</span><span>ATM IV {pct(e.atm_iv)}</span><span>有效 {e.valid}/{e.total}</span></button>)}</div>
    {rows.length>0&&<><div className="option-charts"><div><h3>隐含波动率曲线</h3><Plot series={ivSeries} xLabel="行权价（期权标的价格）" yLabel="IV / %" nonnegative/></div><div><h3>行权价持仓分布</h3><Plot series={oiSeries} xLabel="行权价（监测池内）" yLabel="持仓量 / 手" nonnegative/><p>Wind RT_OI · 每行显示行情时间；持仓自身更新频率待验证。无法据此识别持仓方向或做市商净敞口。</p></div></div><div className="option-chain-scroll"><table><thead><tr><th>合约 / 方向</th><th>行权价</th><th>买一 / 量</th><th>卖一 / 量</th><th>成交 / 持仓</th><th>IV / Delta</th><th>行情时间 / 状态</th></tr></thead><tbody>{rows.map(r=><tr key={r.code} className={r.reason?'option-excluded':''}><td>{r.code}<small>{r.side===1?'认购':'认沽'} · {r.style==='american'?'美式':'欧式'}</small></td><td>{num(r.strike)}</td><td>{num(r.quote.rt_bid1)} / {num(r.quote.rt_bsize1,0)}</td><td>{num(r.quote.rt_ask1)} / {num(r.quote.rt_asize1,0)}</td><td>{num(r.quote.rt_vol,0)} / {num(r.quote.rt_oi,0)}</td><td>{pct(r.iv)} / {num(r.delta,3)}</td><td title={r.reason||'同步盘口估值合格'}>{stamp(r.quote_time)}<small>{r.reason||'盘口估值合格'}</small></td></tr>)}</tbody></table></div></>}
    <div className="option-explanations"><p>定价：{context?.model_version||'等待计算'} · 年化利率假设 {pct(context?.rate)}。{context?.rate_basis}。估值快照 {stamp(context?.generated_at)}；盘口通过实时推送单独更新。</p><p>日终交叉核对：{context?.vendor?.status||'等待读取'}。{context?.vendor?.price_basis}</p><p>持仓集中只是观察位置。没有做市商头寸证据，不输出有方向的GEX、必然支撑阻力或胜率。</p><FeeSettings root={root}/></div></>}
  </section></div>;
}
