// Presentation rules only. No signal engine changes, weights or composite score.
import {CANDLE_WINDOW} from './candle-layout.js';

export const finite = x => typeof x === 'number' && Number.isFinite(x);
export const signed = (x, digits=2) => finite(x) ? `${x>0?'+':''}${x.toFixed(digits)}` : '—';
export const clock = x => x ? new Date(x).toLocaleTimeString('en-GB',{timeZone:'Asia/Shanghai',hour12:false}).slice(0,5) : '—';
export const clockSeconds = x => x ? new Date(x).toLocaleTimeString('en-GB',{timeZone:'Asia/Shanghai',hour12:false}) : '—';
export const date = x => x ? new Date(x).toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'}) : '';
export const datedClock = x => x ? `${date(x).slice(5)} ${clock(x)}` : '—';
export const tone = x => !finite(x)||x===0?'neutral':x>0?'up':'down';
export const CFG = Object.freeze({breadthGap:20, turnoverBias:1.15, contextAge:180, minHistory:20});

export function normalizedMove(pct, sigma) {
  return finite(pct)&&finite(sigma)&&sigma>0 ? pct/sigma : null;
}
export function percentile(value, history=[]) {
  const valid=history.filter(finite);
  return finite(value)&&valid.length>=CFG.minHistory ? valid.filter(x=>x<=value).length/valid.length*100 : null;
}
export const viewTime = snapshot => snapshot.view_time||snapshot.state.server_time;
export const equityPhase = snapshot => snapshot.state.instruments.find(i=>i.root==='IM')?.phase||'交易中';
export function marketPhase(instruments) {
  if(instruments.some(i=>i.phase==='交易中'))return '盘中';
  if(instruments.some(i=>i.phase==='午休'))return '午休';
  if(instruments.some(i=>i.phase==='小节休息'))return '小节休息';
  if(instruments.some(i=>i.phase==='盘前'))return '盘前';
  return '休市';
}
export function sourceQuality(time, asof, phase='', maxAge=CFG.contextAge, closeTime='15:00') {
  const age=(Date.parse(asof)-Date.parse(time))/1000;
  if(!finite(age))return '缺少源时间';
  if(age < -10)return '源时间异常';
  if(date(time)!==date(asof))return '历史数据';
  // A paused market is a summary, never a claim of live freshness.
  if(phase==='午休' && clock(time)>='11:27' && clock(time)<='11:31')return '午间快照';
  if(phase==='小节休息' && clock(time)>='10:12' && clock(time)<='10:16')return '小节休息快照';
  if(phase==='已收盘'){
    const minutes=s=>Number(s.slice(0,2))*60+Number(s.slice(3,5));
    const offset=minutes(clock(time))-minutes(closeTime);
    if(offset>=-3&&offset<=1)return '收盘快照';
  }
  if(age>maxAge)return '数据滞后';
  return '快照内有效';
}
export const sourceUsable = q => ['快照内有效','午间快照','小节休息快照','收盘快照'].includes(q);
const reviewPhase = phase => ['盘前','午休','小节休息','已收盘','非交易日'].includes(phase);
export const displayUsable = q => sourceUsable(q)||q==='最近快照';

export function reviewQuality(quality, phase) {
  if(reviewPhase(phase)&&['历史数据','数据滞后','快照内有效'].includes(quality))return '最近快照';
  return quality;
}

export function metricQuality(row, asof, phase, minutes=0) {
  // Native 5m/15m labels include that minute: final at label+65s,
  // next expected label at +window, plus the existing 30s context cadence.
  const quality=sourceQuality(row?.time,asof,phase,minutes?minutes*60+65+30:CFG.contextAge);
  return reviewQuality(quality,phase);
}

export function contextMetricQuality(context, row, asof, phase, minutes=0) {
  return context.error&&!reviewPhase(phase)?'背景更新异常':metricQuality(row,asof,phase,minutes);
}

const positive = value => finite(value)&&value>0;
const dayOf = stamp => stamp.slice(0,10);
const hmOf = stamp => stamp.slice(11,16);

// The backend has validated trading-day ownership and completed bars. Group
// chart points by their observed day or overnight session for display.
function chartSession(stamp, market) {
  if(!stamp)return null;
  const day=dayOf(stamp);
  const hm=hmOf(stamp);
  const night=market?.night;
  if(night&&hm>=night[0])return {name:'夜盘',start:`${day}T${night[0]}`};
  if(night&&hm<=night[1]){
    const previous=new Date(Date.parse(day)-86400000).toISOString().slice(0,10);
    return {name:'夜盘',start:`${previous}T${night[0]}`};
  }
  return {name:'日盘',start:`${day}T${market?.sessions?.[0]?.[0]||'09:00'}`};
}

function lastChart(history, market) {
  const last=history.at(-1);
  if(!last)return {bars:[],session:null,time:null,contextBars:[],contextSession:null};
  const session=chartSession(last.end,market);
  const bars=history.filter(b=>b.end>=session.start);
  // Only borrow the tail of the immediately preceding session when the
  // current one has fewer bars than the chart can show.
  const preceding=history.slice(-CANDLE_WINDOW).filter(b=>b.end<session.start);
  const contextSession=chartSession(preceding.at(-1)?.end,market);
  const contextBars=contextSession?preceding.filter(b=>b.end>=contextSession.start):[];
  return {bars,session,time:last.end,contextBars,contextSession};
}

function priceObservation(instrument, lastBar, asof, mode) {
  const {phase,market_time:time,market,quote,status,ready}=instrument;
  const price=quote?.rt_last;
  const quality=sourceQuality(time,asof,phase,15);
  const usableTime=finite(Date.parse(time))&&quality!=='源时间异常';
  const quoteUsable=phase==='交易中'&&sourceUsable(quality)&&positive(price)&&(mode!=='live'||ready===true);
  if(quoteUsable)return {price,time,quality,quoteUsable,source:'quote'};

  // During a pause the vendor may issue a new timestamp with a non-trading
  // placeholder. Only use dated positive quotes within a session window.
  const hm=usableTime?hmOf(time):'';
  const inDay=market?.sessions?.some(([open,close])=>hm>=open&&hm<=close);
  const night=market?.night;
  const inNight=night&&hm&&(hm>=night[0]||hm<=night[1]);
  const pastQuote=positive(price)&&usableTime&&Date.parse(time)<=Date.parse(asof)&&(inDay||inNight);
  const fallbackQuality=reviewPhase(phase)?'最近快照':sourceUsable(quality)?status||'行情未就绪':quality;
  if(pastQuote&&(!lastBar||time>=lastBar.end)){
    return {price,time,quality:fallbackQuality,quoteUsable,source:'quote'};
  }
  if(lastBar)return {price:lastBar.close,time:lastBar.end,quality:fallbackQuality,quoteUsable,source:'bar'};
  return {price:null,time:null,quality:'暂无有效报价',quoteUsable,source:null};
}

export function contracts(snapshot) {
  const {state,context={},bars={}}=snapshot;
  return state.instruments.map(i=>{
    const history=(bars[i.contract]||[]).filter(b=>typeof b.end==='string'&&b.end<=state.server_time&&positive(b.close));
    const chart=lastChart(history,i.market);
    const observation=priceObservation(i,history.at(-1),viewTime(snapshot),snapshot.mode);
    const {price,time:changeTime,quoteUsable}=observation;
    const sourceSession=chartSession(changeTime,i.market);
    const closeTime=i.market?.sessions?.at(-1)?.[1]||(['T','TL'].includes(i.root)?'15:15':'15:00');
    // For a historical day close, compare against the close before that day.
    // For a night observation, the latest preceding day close is its baseline.
    const prior=changeTime?history.findLast(b=>hmOf(b.end)===closeTime&&(
      quoteUsable?dayOf(b.end)<i.trading_day:
      sourceSession.name==='夜盘'?b.end<sourceSession.start:dayOf(b.end)<dayOf(changeTime)
    )):undefined;
    const pct=prior?(price/prior.close-1)*100:null;
    const quality=price!==null&&!finite(pct)?'收盘基准待载入':observation.quality;
    const cross=context.cross?.find(r=>r.code===i.contract);
    return {...i,mode:snapshot.mode,price,pct,changeTime,quality,quoteUsable,chart,sourceSession,
      usable:finite(pct)&&(quoteUsable||reviewPhase(i.phase)),
      prevClose:prior?.close,prevCloseTime:prior?.end,
      basis:'较前收盘',
      basisDetail:`相对 ${prior?.end?.slice(0,10)||'缺失'} ${clock(prior?.end)} 的完整5分钟收盘价。${cross?` Wind 原涨跌幅 ${signed(cross.change_pct)}%（${datedClock(cross.time)}），基准可能不同。`:''}`,
      priceSource:observation.source};
  });
}

export function styleEvidence(context, asof, phase) {
  const rotation=context.rotation||{};
  const rows=[['breadth_gap','广度',CFG.breadthGap,0],['strength','价格',0,0],['turnover','成交',CFG.turnoverBias-1,1]].map(([key,label,threshold,center])=>{
    const r=rotation[key]||{};
    const quality=contextMetricQuality(context,r,asof,phase,key==='breadth_gap'?0:5);
    const usable=finite(r.value)&&displayUsable(quality);
    let side=0;
    if(usable){
      if(key==='turnover')side=r.value>=CFG.turnoverBias?1:r.value<=1/CFG.turnoverBias?-1:0;
      else side=r.value>center+threshold?1:r.value<center-threshold?-1:0;
    }
    return {...r,key,label,quality,usable,side,
      // Raw 5m price direction is descriptive; absent historical sigma means no calibrated vote.
      calibrated:key!=='strength',
      display:usable?(key==='turnover'?r.value.toFixed(2)+'×':signed(r.value,key==='breadth_gap'?1:2)+'pp'):'—',
      verdict:!usable?(displayUsable(quality)?'数据暂缺':quality):side>0?'偏小盘':side<0?'偏大盘':'差异不明显'};
  });
  const [breadth,price,volume]=rows;
  const available=rows.filter(r=>r.usable);
  const latest=available.map(r=>r.time).sort().at(-1);
  const comparable=reviewPhase(phase)?available.filter(r=>
    dayOf(r.time)===dayOf(latest)&&sourceUsable(sourceQuality(r.time,latest,'交易中',r.key==='breadth_gap'?CFG.contextAge:395))
  ):available;
  const directional=comparable.filter(r=>r.side!==0);
  const mixed=directional.some(r=>r.side===1)&&directional.some(r=>r.side===-1);
  const confirmed=comparable.includes(breadth)&&comparable.includes(volume)&&breadth.side!==0&&breadth.side===volume.side&&!mixed;
  const label=mixed?'风格证据分歧':confirmed?(breadth.side>0?'广度、成交偏小盘':'广度、成交偏大盘'):
    breadth.usable&&breadth.side!==0?(breadth.side>0?'小盘广度占优':'大盘广度占优'):'风格证据不足';
  return {rows,label,mixed,confirmed,
    description:comparable.includes(price)?`近5分钟价格${price.side>0?'偏小盘':price.side<0?'偏大盘':'接近'}。历史标准差未接入。`:'缺少可比的5分钟价格。',
    // One measured quantity, explicitly not a weighted style score.
    dot:breadth.usable?Math.max(4,Math.min(96,50+breadth.value)):null,
  };
}

const statuses={watching:'关注中',confirmed:'收盘确认',triggered:'已触价',invalidated:'已失效',expired:'已过期',no_room:'空间不足'};
export function radarItems(instruments) {
  return instruments.flatMap(i=>(i.opportunities||[])
    .filter(o=>o.contract===i.contract&&o.trading_day===i.trading_day&&['watching','confirmed','triggered'].includes(o.status))
    .map(o=>{
      const risk=finite(o.entry)&&finite(o.stop)?Math.abs(o.entry-o.stop):null;
      const canMeasure=i.mode!=='live'||(i.quoteUsable&&i.phase==='交易中');
      const distance=canMeasure&&finite(o.entry)&&finite(i.price)?Math.abs(i.price-o.entry):null;
      const distR=risk>0&&finite(distance)?distance/risk:null;
      return {...o,root:i.root,price:canMeasure?i.price:null,priceLabel:i.mode==='live'?'现价':'快照价',quoteUsable:i.quoteUsable,market:i.market,phase:i.phase,market_time:i.market_time,
        label:o.historical&&o.status==='triggered'?'历史K线覆盖触发价':statuses[o.status],
        setupLabel:o.setup==='pullback'?'顺势突破回踩':'区间假突破反转',
        distance,distR,near:canMeasure&&!o.historical&&['confirmed','triggered'].includes(o.status)&&distR!==null&&distR<=.25,
        distanceTicks:finite(distance)&&i.market.tick_size>0?Math.round(distance/i.market.tick_size):null};
    })).sort((a,b)=>(a.distR??Infinity)-(b.distR??Infinity)||a.root.localeCompare(b.root));
}

function groupReturns(items, roots) {
  const group=items.filter(i=>roots.includes(i.root));
  const active=group.some(i=>i.phase==='交易中');
  const visible=group.filter(i=>i.usable&&(!active||i.quoteUsable));
  const sameSession=new Set(visible.map(i=>i.sourceSession.start)).size===1;
  const times=visible.map(i=>Date.parse(i.changeTime));
  const closeInTime=Math.max(...times)-Math.min(...times)<=CFG.contextAge*1000;
  const complete=visible.length===roots.length&&(active||sameSession&&closeInTime);
  return {visible,complete,review:!active&&visible.length>0};
}

export function headline(snapshot, disconnected=false, items=contracts(snapshot)) {
  const phase=marketPhase(items);
  if(disconnected||snapshot.state.offline||snapshot.state.last_error||!snapshot.state.feed_state?.includes('已连接'))
    return {title:'数据连接异常，结论暂停。',items:[],paused:true,phase};
  const cashPhase=equityPhase(snapshot);
  const asof=viewTime(snapshot);
  const style=styleEvidence(snapshot.context,asof,cashPhase);
  const small=snapshot.context.breadths?.find(b=>b.code==='000852.SH');
  const large=snapshot.context.breadths?.find(b=>b.code==='000300.SH');
  const equityReview=reviewPhase(cashPhase);
  const breadthText=b=>{
    if(!finite(b?.value)||!displayUsable(contextMetricQuality(snapshot.context,b,asof,cashPhase)))return '—';
    const value=b.value.toFixed(1)+'%';
    return equityReview?`${value}（${datedClock(b.time)}）`:value;
  };
  const equity={slot:'风格',title:style.label,tone:'neutral',target:'style',
    evidence:`1000 / 300 上涨广度 ${breadthText(small)} / ${breadthText(large)}`};
  const commodity=groupReturns(items,['SC','AG','AU']);
  const lead=commodity.visible.sort((a,b)=>a.pct-b.pct)[0];
  const leadingDown=commodity.complete&&lead?.pct<0;
  const commodityText=lead?`${lead.market.name} ${signed(lead.pct)}%${leadingDown?'，样本内领跌':''}`:'商品数据不足';
  const bonds=groupReturns(items,['T','TL']);
  const bondsTitle=!bonds.complete?'国债数据不足':bonds.visible.every(i=>i.pct>0)?'国债期货走高':bonds.visible.every(i=>i.pct<0)?'国债期货走低':'国债期货分化';
  const radar=radarItems(items).find(r=>finite(r.entry)&&(snapshot.mode!=='live'||(r.quoteUsable&&r.phase==='交易中')));
  const commodityBasis=lead&&commodity.review?`回顾 ${datedClock(lead.changeTime)} · 较前收盘`:'AG / AU / SC 较前收盘';
  const bondEvidence=bonds.visible.map(i=>`${i.root} ${signed(i.pct)}%${bonds.review?`（${datedClock(i.changeTime)}）`:''}`).join(' · ');
  const slots=[equity,{slot:'商品',title:commodityText,tone:tone(lead?.pct),target:'markets',evidence:commodityBasis},
    {slot:'国债',title:bondsTitle,tone:bonds.complete&&bonds.visible.every(i=>i.pct>0)?'up':'neutral',target:'markets',evidence:bondEvidence||'等待可用行情'}];
  if(radar)slots.push({slot:'关注',title:`${radar.root} ${radar.historical?'历史记录待复核':radar.label}`,tone:'neutral',target:'radar',evidence:`${radar.priceLabel}距触发 ${radar.distanceTicks} 跳 · 不代表成交`});
  const allUp=commodity.complete&&commodity.visible.every(i=>i.pct>0);
  const equityLead=phase==='盘中'&&equityReview&&style.rows.some(r=>r.usable)?'股指回顾 · '+style.label:style.label;
  const commodityLead=leadingDown?lead.market.name+'领跌':allUp?'商品样本普涨':'商品等待确认';
  const title=`${equityLead}，${phase==='盘中'&&commodity.review&&commodity.complete?'商品回顾 · ':''}${commodityLead}。`;
  const subtitle=`${phase==='盘中'&&bonds.review&&bonds.complete?'国债回顾 · ':''}${bondsTitle}。`;
  return {title,subtitle:subtitle+(radar?`${radar.root} ${radar.label}，需与实时确认区分。`:'暂无已确认的触发价。'),items:slots,paused:false,phase};
}

export function recentEvents(snapshot, minutes=30, items=contracts(snapshot)) {
  const times=items.map(i=>Date.parse(i.changeTime)).filter(finite);
  const serverEnd=Date.parse(snapshot.state.server_time||snapshot.captured_at);
  const review=snapshot.mode!=='live'||marketPhase(items)!=='盘中';
  const end=review&&times.length?Math.min(serverEnd,Math.max(...times)):serverEnd;
  const start=end-minutes*60000;
  const map=new Map();
  const add=e=>{
    const time=Date.parse(e.known_at);
    if(finite(time)&&time>=start&&time<=end&&!map.has(e.id))map.set(e.id,e);
  };
  // known_at is when the event became knowable; never move it back to the pivot bar.
  for(const i of snapshot.state.instruments){
    for(const e of i.structures||[])if(e.kind==='structure')add({...e,root:i.root,text:`${i.root} ${e.scale==='internal'?'内部':'波段'} ${e.label}`,kindLabel:e.historical?'历史重建':'结构事件'});
  }
  for(const e of snapshot.events||[]){
    if(['system','pivot','structure'].includes(e.kind)||!e.opportunity)continue;
    const label=e.kind==='preview'?'接近候选区域 · 待收盘确认':statuses[e.opportunity.status]||e.message;
    add({...e,text:`${e.opportunity.root||e.contract?.split('.')[0]} ${label}`,kindLabel:e.historical?'历史重建':e.kind==='preview'?'盘中预览':'机会事件'});
  }
  const small=snapshot.context.breadths?.find(b=>b.code==='000852.SH');
  const trend=[...(small?.trend||[])].sort((a,b)=>Date.parse(a.time)-Date.parse(b.time));
  let lastCross=-Infinity;
  for(let n=1;n<trend.length;n++){
    const prev=trend[n-1],curr=trend[n],time=Date.parse(curr.time);
    if(date(prev.time)!==date(curr.time)||time-Date.parse(prev.time)>90000)continue;
    if(finite(prev.value)&&finite(curr.value)&&prev.value<60&&curr.value>=60&&time-lastCross>=300000){
      add({id:`breadth-${curr.time}`,known_at:curr.time,text:'中证1000 上涨广度升破 60%',kindLabel:'快照变化',historical:false});
      lastCross=time;
    }
  }
  return {start,end,review,items:[...map.values()].sort((a,b)=>Date.parse(b.known_at)-Date.parse(a.known_at))};
}

export function optionSummary(context) {
  const expiry=context?.expiries?.[0],iv=expiry?.atm_iv,rv=context?.rv20;
  const gap=finite(iv)&&finite(rv)?(iv-rv)*100:null;
  return {iv,rv,gap,skew:finite(expiry?.put_minus_call_25d)?expiry.put_minus_call_25d*100:null,
    label:gap===null?'波动数据不足':Math.abs(gap)<3?'IV 接近已实现波动':gap<0?'IV 低于已实现波动':'IV 高于已实现波动',
    expiry:expiry?.expiry,time:context?.valuation_at||context?.market_time,status:context?.status,
    ivPercentile:null,skewPercentile:null};
}

export function etfQuality(flow, snapshot) {
  if(!flow||flow.frequency!=='intraday_cumulative'||flow.unit!=='元'||!finite(flow.value))return '资金流缺失';
  const phase=equityPhase(snapshot);
  if(!reviewPhase(phase)&&(snapshot.context.error||snapshot.context.etf_errors?.flow))return '资金流更新异常';
  return reviewQuality(sourceQuality(flow.time,viewTime(snapshot),phase),phase);
}
