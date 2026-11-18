import {clock, date, equityPhase, finite, marketPhase, opportunityLabels, sourceQuality, sourceUsable, viewTime} from '../shared/market-model.js';
export * from '../shared/market-model.js';

export function recentEvents(snapshot, minutes=30) {
  const times=snapshot.state.instruments.map(i=>Date.parse(i.market_time)).filter(finite);
  const serverEnd=Date.parse(snapshot.state.server_time||snapshot.captured_at);
  const end=snapshot.mode==='live'&&marketPhase(snapshot.state.instruments)==='盘中'?serverEnd:Math.min(serverEnd,times.length?Math.max(...times):serverEnd),start=end-minutes*60000;
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
    const label=e.kind==='preview'?'接近候选区域 · 待收盘确认':opportunityLabels[e.opportunity.status]||e.message;
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
  return {start,end,items:[...map.values()].sort((a,b)=>Date.parse(b.known_at)-Date.parse(a.known_at))};
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
  if(snapshot.context.error||snapshot.context.etf_errors?.flow)return '资金流更新异常';
  return sourceQuality(flow.time,viewTime(snapshot),equityPhase(snapshot));
}

export function etfFlowReading(flow, snapshot) {
  if (flow?.frequency === 'daily_snapshot') {
    const day = flow.data_date;
    const dated = typeof day === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(day)
      && date(day + 'T12:00:00+08:00') === day && day <= date(viewTime(snapshot));
    const error = snapshot.context.error || snapshot.context.etf_errors?.flow || flow.reason;
    const valid = dated && flow.unit === '元' && finite(flow.value) && !error;
    return {
      value: valid ? flow.value : null,
      caption: dated ? `日频 · ${day}` : '日频日期缺失或异常',
      detail: error || 'WindPy 日频截面，源更新时间未提供，金额单位缩放待核验。',
    };
  }
  const quality = etfQuality(flow, snapshot);
  const valid = sourceUsable(quality);
  return {
    value: valid ? flow.value : null,
    caption: valid ? `日内 · ${clock(flow.time)}` : quality,
    detail: [flow?.time, quality, flow?.reason].filter(Boolean).join(' · '),
  };
}
