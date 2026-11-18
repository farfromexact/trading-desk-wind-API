// Presentation rules only. No signal engine changes, weights or composite score.
export const finite = x => typeof x === 'number' && Number.isFinite(x);
export const signed = (x, digits=2) => finite(x) ? `${x>0?'+':''}${x.toFixed(digits)}` : '—';
export const clock = x => x ? new Date(x).toLocaleTimeString('en-GB',{timeZone:'Asia/Shanghai',hour12:false}).slice(0,5) : '—';
export const clockSeconds = x => x ? new Date(x).toLocaleTimeString('en-GB',{timeZone:'Asia/Shanghai',hour12:false}) : '—';
export const date = x => x ? new Date(x).toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'}) : '';
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

export function metricQuality(row, asof, phase, minutes=0) {
  // Native 5m/15m labels include that minute: final at label+65s,
  // next expected label at +window, plus the existing 30s context cadence.
  return sourceQuality(row?.time,asof,phase,minutes?minutes*60+65+30:CFG.contextAge);
}

export function contracts(snapshot) {
  const {state,context,bars}=snapshot;
  return state.instruments.map(i=>{
    const history=bars[i.contract]||[], finalTime=['T','TL'].includes(i.root)?'15:15':'15:00';
    const prior=history.filter(b=>b.end.slice(0,10)<i.trading_day && b.end.slice(11,16)===finalTime).at(-1);
    const quoteDate=date(i.market_time);
    const daily=history.filter(b=>b.end.slice(0,10)===quoteDate&&b.end.slice(11,16)>='09:00'&&b.end<=state.server_time);
    const cross=context.cross?.find(r=>r.code===i.contract);
    const price=i.quote?.rt_last;
    const calculated=finite(price)&&price>0&&finite(prior?.close)&&prior.close>0 ? (price/prior.close-1)*100 : null;
    // All eight cards share the same cached close reference. A slower context
    // percentage must not freeze an otherwise live price or mix settlement bases.
    const pct=calculated;
    const changeTime=i.market_time;
    const closeTime=clock(viewTime(snapshot))<'09:00'?'02:30':clock(viewTime(snapshot))<'12:00'?'11:30':finalTime;
    const quoteQuality=sourceQuality(i.market_time,viewTime(snapshot),i.phase,15,closeTime);
    const quoteUsable=sourceUsable(quoteQuality)&&finite(price)&&price>0&&(snapshot.mode!=='live'||i.phase!=='交易中'||i.ready===true);
    const quality=!finite(pct)?'收盘基准待载入':!quoteUsable?(sourceUsable(quoteQuality)?i.status||'行情未就绪':quoteQuality):quoteQuality;
    return {...i,mode:snapshot.mode,price,pct,changeTime,quality,quoteQuality,quoteUsable,usable:quoteUsable&&sourceUsable(quality),daily,
      prevClose:prior?.close,prevCloseTime:prior?.end,
      basis:'较前收盘',sigma20:null,z:null,
      basisDetail:`相对 ${prior?.end?.slice(0,10)||'缺失'} ${clock(prior?.end)} 完整5分钟收盘价。${cross?` 后台 Wind 原涨跌幅 ${signed(cross.change_pct)}%（${clockSeconds(cross.time)}），基准可能不同，仅供核对。`:''}`,
      pctClose:calculated};
  });
}

export function styleEvidence(context, asof, phase) {
  const rotation=context.rotation||{};
  const rows=[['breadth_gap','广度',CFG.breadthGap,0],['strength','价格',0,0],['turnover','成交',CFG.turnoverBias-1,1]].map(([key,label,threshold,center])=>{
    const r=rotation[key]||{}, quality=context.error?'背景更新异常':metricQuality(r,asof,phase,key==='breadth_gap'?0:5);
    const usable=finite(r.value)&&sourceUsable(quality);
    let side=0;
    if(usable){
      if(key==='turnover')side=r.value>=CFG.turnoverBias?1:r.value<=1/CFG.turnoverBias?-1:0;
      else side=r.value>center+threshold?1:r.value<center-threshold?-1:0;
    }
    return {...r,key,label,quality,usable,side,
      // Raw 5m price direction is descriptive; absent historical sigma means no calibrated vote.
      calibrated:key!=='strength',
      display:usable?(key==='turnover'?r.value.toFixed(2)+'×':signed(r.value,key==='breadth_gap'?1:2)+'pp'):'—',
      verdict:!usable?quality:side>0?'偏小盘':side<0?'偏大盘':'差异不明显'};
  });
  const [breadth,price,volume]=rows;
  const directional=rows.filter(r=>r.usable&&r.side!==0);
  const mixed=directional.some(r=>r.side===1)&&directional.some(r=>r.side===-1);
  const confirmed=breadth.usable&&volume.usable&&breadth.side!==0&&breadth.side===volume.side&&!mixed;
  const label=mixed?'风格证据分歧':confirmed?(breadth.side>0?'广度、成交偏小盘':'广度、成交偏大盘'):
    breadth.usable&&breadth.side!==0?(breadth.side>0?'小盘广度占优':'大盘广度占优'):'风格证据不足';
  return {rows,label,mixed,confirmed,
    description:price.usable?`近5分钟价格${price.side>0?'同向偏小盘':price.side<0?'偏大盘':'接近'}；历史标准差未接入，不作显著性确认。`:'价格对照缺失，暂不判断价格是否确认。',
    // One measured quantity, explicitly not a weighted style score.
    dot:breadth.usable?Math.max(4,Math.min(96,50+breadth.value)):null,
  };
}

export const opportunityLabels={watching:'关注中',confirmed:'收盘确认',triggered:'已触价',invalidated:'已失效',expired:'已过期',no_room:'空间不足'};
export function radarItems(instruments) {
  return instruments.flatMap(i=>(i.opportunities||[])
    .filter(o=>o.contract===i.contract&&o.trading_day===i.trading_day&&['watching','confirmed','triggered'].includes(o.status))
    .map(o=>{
      const risk=finite(o.entry)&&finite(o.stop)?Math.abs(o.entry-o.stop):null;
      const canMeasure=i.mode!=='live'||(i.quoteUsable&&i.phase==='交易中');
      const distance=canMeasure&&finite(o.entry)&&finite(i.price)?Math.abs(i.price-o.entry):null;
      const distR=risk>0&&finite(distance)?distance/risk:null;
      return {...o,root:i.root,price:canMeasure?i.price:null,priceLabel:i.mode==='live'?'现价':'快照价',quoteUsable:i.quoteUsable,market:i.market,phase:i.phase,market_time:i.market_time,
        label:o.historical&&o.status==='triggered'?'历史K线覆盖触发价':opportunityLabels[o.status],
        setupLabel:o.setup==='pullback'?'顺势突破回踩':'区间假突破反转',
        distance,distR,near:canMeasure&&!o.historical&&['confirmed','triggered'].includes(o.status)&&distR!==null&&distR<=.25,
        distanceTicks:finite(distance)&&i.market.tick_size>0?Math.round(distance/i.market.tick_size):null};
    })).sort((a,b)=>(a.distR??Infinity)-(b.distR??Infinity)||a.root.localeCompare(b.root));
}

export function headline(snapshot, disconnected=false, items=contracts(snapshot)) {
  const phase=marketPhase(items);
  if(disconnected||snapshot.state.offline||snapshot.state.last_error||!snapshot.state.feed_state?.includes('已连接'))
    return {title:'数据连接异常，结论暂停。',items:[],paused:true,phase};
  const style=styleEvidence(snapshot.context,viewTime(snapshot),equityPhase(snapshot));
  const small=snapshot.context.breadths?.find(b=>b.code==='000852.SH');
  const large=snapshot.context.breadths?.find(b=>b.code==='000300.SH');
  const breadthText=b=>!snapshot.context.error&&finite(b?.value)&&sourceUsable(metricQuality(b,viewTime(snapshot),equityPhase(snapshot)))?b.value.toFixed(1)+'%':'—';
  const equity={slot:'风格',title:style.label,tone:'neutral',target:'style',
    evidence:`1000 / 300 上涨广度 ${breadthText(small)} / ${breadthText(large)}`};
  const commodity=items.filter(i=>['SC','AG','AU'].includes(i.root)&&i.usable&&finite(i.pct)).sort((a,b)=>a.pct-b.pct);
  const lead=commodity[0];
  const commodityText=lead?`${lead.market.name}${signed(lead.pct)}%${commodity.length===3&&lead.pct<0?'，样本内领跌':''}`:'商品数据不足';
  const bonds=items.filter(i=>['T','TL'].includes(i.root)&&i.usable&&finite(i.pct));
  const bondsTitle=bonds.length<2?'国债数据不足':bonds.every(i=>i.pct>0)?'国债期货走高':bonds.every(i=>i.pct<0)?'国债期货走低':'国债期货分化';
  const radar=radarItems(items).find(r=>finite(r.entry)&&(snapshot.mode!=='live'||(r.quoteUsable&&r.phase==='交易中')));
  const slots=[equity,{slot:'商品',title:commodityText,tone:tone(lead?.pct),target:'markets',evidence:'AG / AU / SC 较前收盘；未作波动归一'},
    {slot:'国债',title:bondsTitle,tone:bonds.length===2&&bonds.every(i=>i.pct>0)?'up':'neutral',target:'markets',evidence:bonds.map(i=>`${i.root} ${signed(i.pct)}%`).join(' · ')||'等待完整报价'}];
  if(radar)slots.push({slot:'关注',title:`${radar.root} ${radar.historical?'历史记录待复核':radar.label}`,tone:'neutral',target:'radar',evidence:`${radar.priceLabel}距触发 ${radar.distanceTicks} 跳 · 不代表成交`});
  const allUp=commodity.length===3&&commodity.every(i=>i.pct>0);
  const title=`${style.label}，${lead&&lead.pct<0&&commodity.length===3?lead.market.name+'领跌':allUp?'商品样本普涨':'商品等待确认'}。`;
  return {title,subtitle:bondsTitle+'。'+(radar?`${radar.root} ${radar.label}，需与实时确认区分。`:'暂无已确认的触发价。'),items:slots,paused:false,phase};
}

export function feedHealth(snapshot, connection, now=Date.now()) {
  if(!connection.connected)return {connected:false,label:connection.error||'正在连接后台'};
  const age=(now-Date.parse(snapshot?.state?.server_time))/1000;
  if(!Number.isFinite(age)||age>15||age < -10)return {connected:false,label:'后台状态未及时更新，结论暂停'};
  if(snapshot.state.offline||snapshot.state.last_error||!snapshot.state.feed_state?.includes('已连接'))
    return {connected:false,label:'Wind 数据源异常，结论暂停'};
  return {connected:true,label:'实时缓存已连接'};
}
