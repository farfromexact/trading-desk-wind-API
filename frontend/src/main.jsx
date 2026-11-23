import { OpportunityEvidence } from './opportunity-evidence.jsx';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { CandlestickSeries, createChart, createSeriesMarkers, LineStyle } from 'lightweight-charts';
import './style.css';
import { opportunityAtmosphere } from './opportunity-atmosphere';
import { mergeChartBars, updateChartCandles, followLatestRange, structureMarkers, spaceMarkerLabels } from './chart-data';

import { contractRoot, shortContract, formatPrice, formatNumber, currentOpportunity } from './market-format';
import {OptionsEnvironment,OptionsWorkbench,OptionsDetails} from './options';
import {MarketContextPanel, useMarketContext} from './market-context.jsx';
import {EtfFlowMonitor} from './etf-flow-monitor.jsx';
import {MarketBrief} from './market-brief.jsx';
import {MarketCardGrid} from '../shared/market-cards.jsx';
import {CARD_TREND_BARS, contracts as marketContracts} from '../shared/market-model.js';
import {useCardBars} from './use-card-bars.js';
import {listenToDesk, mergeDeskEvents} from './desk-stream.js';
import './desk-layout.css';
const time = x => x ? new Date(x).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}) : '—';
const fullTime = x => x ? new Date(x).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}) : '—';
const epoch = x => Math.floor(new Date(x).getTime()/1000);
const EMPTY_BARS = [];
const names={watching:'关注中',confirmed:'收盘确认',triggered:'已触价',invalidated:'已失效',expired:'已过期',filtered:'空间不足',uncertain:'先后不确定'};
const setups={pullback:'顺势突破回踩',failed_break:'假突破反转'};

async function api(path,options){
  const response=await fetch('/api/'+path,options);
  if(!response.ok){let body=await response.json().catch(()=>({}));throw new Error(body.detail||'服务暂时不可用');}
  return response.json();
}

function useFeed(onAlert,preview=false){
  const [state,setState]=useState(null),[events,setEvents]=useState([]),[connected,setConnected]=useState(false);
  const [clock,setClock]=useState(Date.now);
  const callback=useRef(onAlert); callback.current=onAlert;
  useEffect(()=>{
    let active=true,hasStreamState=false,lastMetric=0,metricFrame,retryHistoryAt=Infinity;
    const measuredQuotes=new Map(),measuredOptions=new Map();
    api('state').then(s=>active&&!hasStreamState&&setState(s)).catch(()=>{});
    function readHistory(limit=300){
      retryHistoryAt=Infinity;
      api('events?limit='+limit).then(rows=>{
        if(active)setEvents(old=>mergeDeskEvents(old,rows));
      }).catch(()=>{retryHistoryAt=Date.now()+5000;});
    }
    if(preview){readHistory();return()=>{active=false;};}
    // An ordinary first packet loads history. Keep the log available when only
    // the streaming endpoint is slow on the initial visit.
    const historyFallback=setTimeout(()=>{if(!hasStreamState)readHistory();},2000);
    function onPacket(packet,firstPacket,now){
      if(firstPacket||now>=retryHistoryAt)readHistory(hasStreamState?500:300);
      hasStreamState=true;setClock(now);setConnected(true);setState(packet.state);
      if(packet.events.length){
        setEvents(old=>mergeDeskEvents(old,packet.events));
        packet.events.filter(x=>x.audible).forEach(x=>callback.current(x));
      }
      const arrivals=packet.state.instruments.filter(x=>x.quote.received_ms&&x.quote.received_ms!==measuredQuotes.get(x.contract)).map(x=>{measuredQuotes.set(x.contract,x.quote.received_ms);return x.quote.received_ms;});
      const optionArrivals=Object.entries(packet.state.options?.quotes||{}).filter(([code,q])=>q.received_ms&&q.received_ms!==measuredOptions.get(code)).map(([code,q])=>{measuredOptions.set(code,q.received_ms);return q.received_ms;});
      if(!firstPacket && Date.now()-lastMetric>900 && !document.hidden){
        lastMetric=Date.now();
        metricFrame=requestAnimationFrame(()=>api('telemetry',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({delay_ms:Math.max(0,Date.now()-packet.state.sent_ms),source_delay_ms:arrivals.length?Math.max(0,Date.now()-Math.min(...arrivals)):null,option_delay_ms:optionArrivals.length?Math.max(0,Date.now()-Math.min(...optionArrivals)):null})}).catch(()=>{}));
      }
    }
    const stop=listenToDesk({onPacket,onDisconnected:()=>setConnected(false),onClock:setClock});
    const onVisibility=()=>{if(document.hidden)cancelAnimationFrame(metricFrame);};
    document.addEventListener('visibilitychange',onVisibility);
    return()=>{active=false;clearTimeout(historyFallback);stop();document.removeEventListener('visibilitychange',onVisibility);cancelAnimationFrame(metricFrame);};
  },[preview]);
  function applyOptionsMonitor(monitor){
    setState(current=>{
      if(!current)return current;
      const options={...current.options,monitor};
      if(current.options.monitor?.enabled!==monitor.enabled){
        options.contexts={};
        options.quotes={};
      }
      return {...current,options};
    });
  }
  return {state,events,connected,clock,applyOptionsMonitor};
}

function Chart({contract,market,tf,bars,levels,structures,quote,op,focus,focusTime,provisional,onLatest,preview=false}){
  const element=useRef(), chart=useRef(), series=useRef(),markers=useRef(), lines=useRef([]),initial=useRef(true),focusApplied=useRef(null);
  const previousBars=useRef(EMPTY_BARS);
  const fullMarkers=useRef([]),markerTimes=useRef([]),refreshMarkerLabels=useRef(null);
  const [hoverStructure,setHoverStructure]=useState('');
  const displayBars=useMemo(()=>mergeChartBars(bars,bars.length?provisional:[]),[bars,provisional]);
  useEffect(()=>{
    initial.current=true;
    previousBars.current=EMPTY_BARS;
    focusApplied.current=null;
    const c=createChart(element.current,{
      autoSize:true,height:465,
      layout:{background:{color:'#141a21'},textColor:'#7f90a2',fontFamily:'Segoe UI, Microsoft YaHei, sans-serif',fontSize:11,attributionLogo:true},
      grid:{vertLines:{visible:false},horzLines:{visible:false}},
      rightPriceScale:{borderColor:'#29333f',scaleMargins:{top:.12,bottom:.09}},
      timeScale:{borderColor:'#29333f',timeVisible:true,secondsVisible:false,rightOffset:8,
        tickMarkFormatter:(ts,type)=>new Date(ts*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',...(type<=2?{month:'2-digit',day:'2-digit'}:{hour:'2-digit',minute:'2-digit',hour12:false})})},
      crosshair:{mode:0,vertLine:{color:'#4e647a',labelBackgroundColor:'#34475b'},horzLine:{color:'#4e647a',labelBackgroundColor:'#34475b'}},
      localization:{priceFormatter:x=>formatNumber(x,market),timeFormatter:ts=>fullTime(ts*1000)},
    });
    const s=c.addSeries(CandlestickSeries,{upColor:'#ec746d',downColor:'#56bca7',borderUpColor:'#ec746d',borderDownColor:'#56bca7',wickUpColor:'#ec746d',wickDownColor:'#56bca7',priceFormat:{type:'price',precision:market?.decimals??1,minMove:market?.tick_size??.2},lastValueVisible:false,priceLineVisible:false});
    chart.current=c;series.current=s;markers.current=createSeriesMarkers(s,[]);lines.current=[];
    fullMarkers.current=[];markerTimes.current=[];setHoverStructure('');
    let lastLabels;
    refreshMarkerLabels.current=()=>{
      const visible=spaceMarkerLabels(fullMarkers.current,markerTimes.current,c.timeScale().getVisibleLogicalRange(),Math.max(1,element.current.clientWidth-75));
      const key=JSON.stringify(visible);
      if(key!==lastLabels){lastLabels=key;markers.current.setMarkers(visible);}
    };
    c.timeScale().subscribeVisibleLogicalRangeChange(()=>refreshMarkerLabels.current?.());
    c.subscribeCrosshairMove(param=>setHoverStructure(fullMarkers.current.filter(p=>p.time===param.time).map(p=>p.text).join(' · ')));
    return()=>{refreshMarkerLabels.current=null;c.remove();chart.current=null;series.current=null;};
  },[contract,tf,market?.decimals,market?.tick_size]);
  useEffect(()=>{
    if(!series.current)return;
    const previous=previousBars.current;
    const count=displayBars.length;
    const scale=chart.current.timeScale();
    const follow=count!==previous.length && followLatestRange(scale.getVisibleLogicalRange(),previous.length,count);
    updateChartCandles(series.current,previous,displayBars);
    previousBars.current=displayBars;
    if(!count){initial.current=true;return;}
    if(initial.current){scale.setVisibleLogicalRange({from:Math.max(0,count-85),to:count+6});initial.current=false;}
    else if(follow)scale.setVisibleLogicalRange(follow);
  },[displayBars,contract,tf,market?.decimals,market?.tick_size]);
  useEffect(()=>{
    if(!series.current)return;
    const list=[];
    const current=quote?.rt_last||bars.at(-1)?.close;
    const levelStyles={
      '前日高点':{color:'#88b9ed',lineStyle:LineStyle.Dashed},
      '前日低点':{color:'#88b9ed',lineStyle:LineStyle.Dashed},
      '波段高点':{color:'#b49ade',lineStyle:LineStyle.LargeDashed},
      '波段低点':{color:'#b49ade',lineStyle:LineStyle.LargeDashed},
      EQH:{color:'#d1aa71',lineStyle:LineStyle.Dotted},
      EQL:{color:'#d1aa71',lineStyle:LineStyle.Dotted},
      '区间上沿':{color:'#71b9b5',lineStyle:LineStyle.SparseDotted},
      '区间下沿':{color:'#71b9b5',lineStyle:LineStyle.SparseDotted},
    };
    const closest=[...(levels||[])].sort((a,b)=>Math.abs(a.price-current)-Math.abs(b.price-current)).slice(0,5);
    closest.forEach(p=>list.push({price:p.price,
      ...(levelStyles[p.name]||{color:'#88b9ed',lineStyle:LineStyle.Dashed}),
      title:p.name==='EQH'?'EQH 等高':p.name==='EQL'?'EQL 等低':p.name,
      lineWidth:1,axisLabelVisible:true}));
    if(op?.entry){
      list.push({price:op.entry,color:'#d8bd7a',title:'触发',lineWidth:1,lineStyle:LineStyle.Solid,axisLabelVisible:true},
        {price:op.stop,color:'#b57780',title:'失效',lineWidth:1,lineStyle:LineStyle.Dashed,axisLabelVisible:true});
      op.targets.forEach((t,i)=>list.push({price:t.price,color:'#6aa99e',title:'T'+(i+1),lineWidth:1,lineStyle:LineStyle.Dashed,axisLabelVisible:true}));
    }
    if(current>0)list.push({price:current,color:'#b6c4d4',title:quote?.rt_last>0?'现价':'收盘',lineWidth:1,lineStyle:LineStyle.Dotted,axisLabelVisible:true});
    list.forEach((options,i)=>{
      const old=lines.current[i];
      if(!old)lines.current.push({line:series.current.createPriceLine(options),options});
      else if(old.options.price!==options.price || old.options.title!==options.title){
        old.line.applyOptions(options);
        old.options=options;
      }
    });
    for(const old of lines.current.splice(list.length))series.current.removePriceLine(old.line);
  },[contract,tf,market?.decimals,market?.tick_size,levels,quote?.rt_last,op,bars]);
  useEffect(()=>{
    if(!series.current)return;
    const points=structureMarkers(structures,displayBars,tf);
    for(const b of displayBars.filter(b=>b.provisional))points.push({time:epoch(b.end),position:'aboveBar',color:'#e0bd79',shape:'circle',size:.5,text:b.forming?'未收盘':'待核对'});
    fullMarkers.current=points.sort((a,b)=>a.time-b.time);
    markerTimes.current=displayBars.map(b=>epoch(b.end));
    refreshMarkerLabels.current?.();
  },[contract,tf,market?.decimals,market?.tick_size,structures,displayBars]);
  useEffect(()=>{
    const focusKey=focusTime||focus;
    if(!focusKey||!bars.length||!chart.current||focusApplied.current===focusKey)return;
    const target=focusTime||op?.confirmation_bar||op?.signal_bar;
    const i=bars.findIndex(b=>b.end>=target);
    if(i>=0){chart.current.timeScale().setVisibleLogicalRange({from:Math.max(0,i-50),to:Math.min(bars.length+8,i+12)});focusApplied.current=focusKey;}
  },[focus,focusTime,bars]);
  return <div className="chart-wrap"><div ref={element} className="chart-canvas"/>{hoverStructure&&<div className="chart-structure-readout">{hoverStructure}</div>}{quote&&<button className="chart-latest" onClick={()=>{onLatest?.();chart.current?.timeScale().scrollToRealTime();}}>{preview?'回到样本末端 ↗':'回到最新 ↗'}</button>}{!bars.length&&<div className="chart-empty">{preview?'历史样本未载入该合约或周期的K线':<><span className="loader"/>正在等待真实K线数据</>}</div>}</div>;
}

function Opportunity({op,market,selected,onClick,last,tone}){
  const spec=market||{decimals:op.price_decimals??1};
  const price=x=>formatPrice(x,spec);
  const number=x=>formatNumber(x,spec);
  const unit=market?.unit||op.price_unit||'点';
  const direction=op.direction===1?'多':'空';
  return <button className={'op-card '+(selected?'selected ':'')+op.status+(tone?' signal-'+tone:'')} onClick={onClick}>
    <div className="op-head"><span className={'direction '+(op.direction===1?'up':'down')}>{direction}</span><b>{shortContract(op.contract)}</b><span className={'badge '+op.status}>{names[op.status]}</span></div>
    <div className="op-name">{setups[op.setup]}{op.historical&&<span className="tiny">历史 · 静默</span>}</div>
    <p>{op.reason}</p>
    {op.entry?<><div className="op-prices"><div><small>触发价</small><b>{price(op.entry)}</b></div><div><small>失效价</small><b>{price(op.stop)}</b></div><div><small>T1 {op.target_source==='synthetic'?'测算':'空间'}</small><b className={op.rr>=1.5&&op.target_source!=='synthetic'?'accent':'amber'}>{op.rr?.toFixed(2)}R</b></div></div><div className="op-targets">{op.targets.map((t,i)=><span key={i}>T{i+1} <b>{price(t.price)}</b><small>{t.label}</small></span>)}</div><div className="op-foot"><span>止损距 {number(op.risk)} {unit}</span><span>距触发 {last>0?number(Math.abs(last-op.entry)):'—'} {unit}</span></div><div className="op-foot"><span>确认 {time(op.confirmed_at)}</span><span>{op.remaining_bars??0} 根内有效</span></div></>:<div className="op-foot"><span>关键位 {price(op.level)}</span><span>{op.near?'已接近区域':'等待价格接近'}</span></div>}
  </button>;
}

function ReplayDialog({contract,market,onClose}){
  const price=x=>formatPrice(x,market);
  const [day,setDay]=useState(new Date(Date.now()-86400000).toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'}));
  const [data,setData]=useState(null),[index,setIndex]=useState(0),[playing,setPlaying]=useState(false),[error,setError]=useState(''),[loading,setLoading]=useState(false);
  async function load(){setLoading(true);setError('');setPlaying(false);try{const result=await api('replay?contract='+encodeURIComponent(contract)+'&day='+day);setData(result);setIndex(0);if(!result.frames.length)setError('该日期没有已缓存的5分钟数据。请使用已采集的交易日。');}catch(e){setError(e.message);}finally{setLoading(false);}}
  useEffect(()=>{load();},[contract]);
  useEffect(()=>{if(!playing)return;const timer=setInterval(()=>setIndex(i=>{if(i>=data.frames.length-1){setPlaying(false);return i;}return i+1;}),1000);return()=>clearInterval(timer);},[playing,data]);
  useEffect(()=>{const onKey=e=>{if(e.key==='Escape')onClose();};document.addEventListener('keydown',onKey);return()=>document.removeEventListener('keydown',onKey);},[onClose]);
  const frame=data?.frames[index],view=frame?.snapshot;
  const visibleBars=useMemo(()=>data?.bars.filter(b=>b.end<=(frame?.label||''))||[],[data,frame?.label]);
  const op=view?.opportunities.at(-1);
  return <div className="modal-backdrop"><section className="replay-modal panel" role="dialog" aria-modal="true" aria-label="历史K线回放"><div className="panel-head"><h2>历史回放 · {shortContract(contract)}</h2><button onClick={onClose} aria-label="关闭回放">关闭 ×</button></div><div className="replay-controls"><input aria-label="回放日期" type="date" value={day} onChange={e=>setDay(e.target.value)}/><button disabled={loading} onClick={load}>{loading?'加载中…':'加载日期'}</button><span>终态K线 · 确认声音暂停</span></div><div className="replay-caption">{data?.description||'仅使用当前电脑已经采集的数据。回放不会调用 Wind 下单或修改真实事件。'}</div>{error&&<p className="replay-error">{error}</p>}{frame&&<><div className="replay-timeline"><button onClick={()=>{setPlaying(false);setIndex(i=>Math.max(0,i-1));}} disabled={index===0}>上一根</button><button onClick={()=>setPlaying(!playing)}>{playing?'暂停':'播放'}</button><button onClick={()=>{setPlaying(false);setIndex(i=>Math.min(data.frames.length-1,i+1));}} disabled={index===data.frames.length-1}>下一根</button><input aria-label="回放进度" type="range" min="0" max={data.frames.length-1} value={index} onChange={e=>{setPlaying(false);setIndex(Number(e.target.value));}}/><span>{index+1} / {data.frames.length}</span></div><div className="replay-summary"><b>标签 {time(frame.label)} · 最早确认 {time(frame.available_after)}</b><span>{view.regime.label} · {view.note}</span></div><Chart contract={contract} market={market} tf={5} bars={visibleBars} levels={view.levels} structures={view.structures} quote={null} op={op} focus={0} focusTime={frame.label}/><div className="replay-op">{op?<><b>{setups[op.setup]} · {names[op.status]}</b><p>{op.reason}</p><span>触发 {price(op.entry)}　失效 {price(op.stop)}　T1 {price(op.targets[0]?.price)}　T2 {price(op.targets[1]?.price)}</span></>:<span>截至该时刻尚无符合条件的候选。</span>}</div></>}</section></div>;
}

export function App({preview=false}){
  const [sound,setSound]=useState(false),soundRef=useRef(false),audio=useRef(null),[notice,setNotice]=useState('');
  const [root,setRoot]=useState('IM'),[tf,setTf]=useState(5),[chartHistory,setChartHistory]=useState(null),[selectedOp,setSelectedOp]=useState(null),[focus,setFocus]=useState(0);
  const [edit,setEdit]=useState(false),[choice,setChoice]=useState(''),[busy,setBusy]=useState(false),[logFilter,setLogFilter]=useState('today'),[history,setHistory]=useState(preview);
  const [replayOpen,setReplayOpen]=useState(false),replayRef=useRef(false);
  const [optionsOpen,setOptionsOpen]=useState(false);
  const [optionsBusy,setOptionsBusy]=useState(false);
  replayRef.current=replayOpen;
  function beep(){
    if(!audio.current)return;
    const ctx=audio.current;
    [0,.16].forEach((delay,i)=>{const oscillator=ctx.createOscillator(),gain=ctx.createGain();oscillator.connect(gain);gain.connect(ctx.destination);oscillator.frequency.value=i?880:660;gain.gain.setValueAtTime(.001,ctx.currentTime+delay);gain.gain.exponentialRampToValueAtTime(.12,ctx.currentTime+delay+.01);gain.gain.exponentialRampToValueAtTime(.001,ctx.currentTime+delay+.13);oscillator.start(ctx.currentTime+delay);oscillator.stop(ctx.currentTime+delay+.15);});
  }
  async function enableSound(){
    if(sound){soundRef.current=false;setSound(false);return;}
    try{audio.current=audio.current||new AudioContext();await audio.current.resume();soundRef.current=true;setSound(true);beep();}catch{setNotice('浏览器未允许声音，请再次点击启用声音。');}
  }
  async function alert(event){
    const play=()=>{
      const seen=JSON.parse(localStorage.getItem('wind-heard')||'[]');
      if(seen.includes(event.id))return;
      localStorage.setItem('wind-heard',JSON.stringify([...seen,event.id].slice(-300)));
      if(soundRef.current&&!replayRef.current)beep();
    };
    if(navigator.locks)await navigator.locks.request('wind-desk-sound',play);else play();
  }
  const {state,events,connected,clock,applyOptionsMonitor}=useFeed(alert,preview);
  const optionMonitor=state?.options?.monitor;
  const optionsVisible=!!state?.options && (!optionMonitor || (optionMonitor.enabled && !optionMonitor.stopping));
  useEffect(()=>{if(!optionsVisible)setOptionsOpen(false);},[optionsVisible]);
  async function toggleOptions(){
    if(!optionMonitor || optionsBusy)return;
    setOptionsBusy(true);
    try{
      const monitor=await api('options/monitor',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({enabled:!optionMonitor.enabled})});
      applyOptionsMonitor(monitor);
    }catch(error){setNotice('期权采集切换失败：'+error.message);}
    finally{setOptionsBusy(false);}
  }
  const marketContext=useMarketContext();
  const [atmospherePreview,setAtmospherePreview]=useState(null);
  useEffect(()=>{
    if(!atmospherePreview)return;
    const timer=setTimeout(()=>setAtmospherePreview(null),30000);
    return()=>clearTimeout(timer);
  },[atmospherePreview]);
  const atmosphere=opportunityAtmosphere(state,connected,replayOpen);
  const atmosphereTone=atmospherePreview||atmosphere.tone;
  const atmosphereSignals=atmosphereTone==='action'?atmosphere.actionable:atmosphere.watching;
  const atmosphereRoots=[...new Set(atmosphereSignals.map(x=>x.root))].join(' · ');

  const item=state?.instruments.find(x=>x.root===root);
  const contract=item?.contract||root;
  const bars=chartHistory?.contract===contract&&chartHistory.tf===tf?chartHistory.rows:EMPTY_BARS;
  const {bars: cardBars, remember: rememberCardBars} = useCardBars(state?.instruments, tf === 5 ? item?.contract : null, preview, api);
  const context = marketContext.error ? {...marketContext.context, error: marketContext.error} : marketContext.context || {};
  const snapshot = state && {state, context, bars: cardBars, mode: preview ? 'preview' : 'live',
    view_time: preview ? state.server_time : new Date(clock).toISOString()};
  const cardItems = snapshot ? marketContracts(snapshot) : [];
  const price=x=>formatPrice(x,item?.market);
  const number=x=>formatNumber(x,item?.market);
  const currentOp=item?.opportunities.find(x=>x.id===selectedOp)||null;
  const today=preview?state?.preview_day:state?.server_time?.slice(0,10);
  useEffect(()=>{
    if(preview||tf!==1||!item?.contract||replayOpen)return;
    const touch=()=>{if(!document.hidden)api('chart-interest',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({contract})}).catch(()=>{});};
    touch();const timer=setInterval(touch,30000);
    document.addEventListener('visibilitychange',touch);
    return()=>{clearInterval(timer);document.removeEventListener('visibilitychange',touch);};
  },[contract,tf,replayOpen,item?.contract,preview]);
  const liveBars=(item?.live_bars?.[tf]||[]).filter(b=>b.end>(bars.at(-1)?.end||''));
  const liveBar=liveBars.at(-1);
  const chartVersion=item?.bar_versions?.[tf]??state?.metrics.bar_batches;
  const lastFive=tf===5?item?.last_bar:null;
  useEffect(()=>{
    if(!item?.contract||(!preview&&!connected))return;
    const controller=new AbortController();
    let retry;
    async function read(){
      try{
        const rows=await api('bars?contract='+encodeURIComponent(contract)+'&timeframe='+tf,{signal:controller.signal});
        if(controller.signal.aborted)return;
        setChartHistory({contract,tf,rows});
        if(tf===5)rememberCardBars(item,rows);
      }catch(e){
        if(controller.signal.aborted)return;
        setNotice(e.message);
        if(!preview)retry=setTimeout(read,5000);
      }
    }
    read();
    return()=>{controller.abort();clearTimeout(retry);};
  },[contract,tf,item?.trading_day,chartVersion,lastFive,state?.metrics.started_at,connected,preview,rememberCardBars]);
  const rank={confirmed:0,triggered:1,watching:2,filtered:3,uncertain:4,invalidated:5,expired:6};
  const ops=(state?.instruments.flatMap(x=>x.opportunities)||[]).filter(o=>(!preview||o.trading_day===state.preview_day)&&(history||currentOpportunity(o,state?.instruments.find(x=>x.contract===o.contract))&&['watching','confirmed','triggered','filtered'].includes(o.status))).sort((a,b)=>(rank[a.status]-rank[b.status])||b.signal_bar.localeCompare(a.signal_bar)).slice(0,history?30:8);
  const logs=events.filter(e=>e.kind!=='pivot'&&(logFilter==='all'||(e.known_at||'').slice(0,10)===today)).slice(0,80);
  function selectOp(op){setRoot(contractRoot(op.contract));setTf(5);setSelectedOp(op.id);setFocus(x=>x+1);requestAnimationFrame(()=>document.getElementById('opportunity-detail')?.scrollIntoView({behavior:'smooth',block:'start'}));}
  async function saveChoice(auto=false){
    setBusy(true);setNotice('');
    try{await api('watchlist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({root,contract:auto?null:choice.trim()})});setEdit(false);}catch(e){setNotice(e.message);}finally{setBusy(false);}
  }
  return <div className={'app atmosphere-'+atmosphereTone+(preview?' desk-preview':'')}>
    <div className="atmosphere-light" aria-hidden="true"/><div className="atmosphere-edge" aria-hidden="true"/>
    <div className="topline">
      <div className="feed-status"><span className="live-tag">{state?.phase||'连接中'}</span><span>{state?.feed_state||'等待本机行情服务'}{state?.bootstrap_remaining>0?' · 剩余 '+state.bootstrap_remaining+' 项':''}</span></div>
      <MarketBrief snapshot={snapshot} items={cardItems} connected={connected} preview={preview} replay={replayOpen}/>
      <span className="desk-clock">{fullTime(state?.server_time)} <span className="muted">上海</span></span>
    </div>
    {(!connected||state?.last_error||notice||state?.offline)&&<div className="banner">{notice||state?.last_error||(preview?'21:9 分支布局预览 · 11月13日历史样本，商品含随后夜盘；各自时间保留，未载入盘口保持缺失，全部提醒静默':state?.offline?'离线回放模式 · 所有提醒保持静默':'页面与服务连接中断，等待恢复')} {notice&&<button onClick={()=>setNotice('')}>关闭</button>}</div>}
    {atmosphereTone!=='idle'&&<div className={'atmosphere-status '+(atmospherePreview?'is-preview':'')} role="status" aria-live="polite">
      <span className="atmosphere-beacon" aria-hidden="true"/>
      <div><span className="atmosphere-eyebrow">{atmospherePreview?'灯效预览 · 30秒后恢复实时':'全市场机会雷达'}</span><strong>{atmosphereTone==='action'?'已收盘确认 · 关注触发条件':'关注中 · 等待收盘确认'}</strong></div>
      <span className="atmosphere-detail">{atmospherePreview?'仅预览视觉效果，不代表出现真实机会':atmosphereTone==='action'?`${atmosphereRoots} · ${atmosphere.actionable.filter(x=>x.status==='confirmed').length} 个待触发 / ${atmosphere.actionable.filter(x=>x.status==='triggered').length} 个已触价`:`${atmosphereRoots} · ${atmosphere.watching.length} 个关注机会`}</span>
      {atmospherePreview&&<button onClick={()=>setAtmospherePreview(null)}>恢复实时</button>}
    </div>}
    <section className="desk-markets" aria-labelledby={cardItems.length ? 'desk-market-heading' : 'desk-title'}>
      <header className="desk-market-heading">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">W<span>·</span></span>
          <div><h1 id="desk-title">实时机会工作台</h1><span>PRICE ACTION / 股指 · 贵金属 · 原油 · 国债</span></div>
        </div>
        {cardItems.length > 0 && <div className="desk-market-caption">
          <h2 id="desk-market-heading">先看哪里在动</h2><span>较前收盘 · 最近{CARD_TREND_BARS}根5分钟线</span>
        </div>}
        <div className="header-right">
          <span className={'connection '+(connected?'ok':'')}><i/>{preview?'历史布局预览':connected?'本机服务在线':'正在连接服务'}</span>
          <button type="button" disabled={preview} onClick={enableSound} className={'sound '+(sound?'enabled':'')}>{sound?'◖ 声音已开启':'◖ 启用声音'}</button>
          <button type="button" disabled={preview} onClick={()=>{if(sound)beep();else enableSound();}} className="subtle">测试声音</button>
        </div>
      </header>
      {cardItems.length > 0 && <MarketCardGrid items={cardItems} selected={root} showStatus atmosphere={atmosphere}
        paused={!preview && (!connected || Boolean(state.offline || state.last_error))}
        onSelect={next => { setRoot(next); setSelectedOp(null); setEdit(false); }}/>}
    </section>
    <OptionsEnvironment root={root} context={state?.options?.contexts?.[root]} onOpen={()=>setOptionsOpen(true)}
      monitor={optionMonitor} onToggle={preview?null:toggleOptions} busy={optionsBusy||!connected}/>
    <main className="desk-workspace">
      <EtfFlowMonitor source={marketContext} clock={clock} connected={connected&&!state?.offline&&!replayOpen} preview={preview}/>
      <section className="chart-panel panel"><div className="panel-head"><div className="chart-title"><h2>{shortContract(contract)} <span>价格结构</span></h2><button disabled={preview} className="text-button" onClick={()=>{setChoice(item?.contract||'');setEdit(!edit);}}>切换合约 ↗</button></div><div className="segments">{[1,5,15].map(n=><button key={n} disabled={preview&&n===1} title={preview&&n===1?'样本未载入1分钟线':undefined} className={tf===n?'active':''} onClick={()=>setTf(n)}>{n}分钟</button>)}</div></div>
      {edit&&<form className="contract-form" onSubmit={e=>{e.preventDefault();saveChoice();}}><label>真实月份合约<input value={choice} onChange={e=>setChoice(e.target.value.toUpperCase())} placeholder={root+'2612.'+(item?.market.exchange||'CFE')} aria-label="真实月份合约"/></label><button disabled={busy} type="submit">应用</button><button disabled={busy} type="button" onClick={()=>saveChoice(true)}>恢复每日主力</button><button type="button" onClick={()=>setEdit(false)}>取消</button></form>}
      <div className="chart-stats"><span>{item?.market.name} <b>{item?.market.unit}</b></span><span>背景 <b>15分钟</b></span><span>确认 <b>5分钟收盘</b></span><span>买一 <b>{price(item?.quote.rt_bid1)}</b></span><span>卖一 <b>{price(item?.quote.rt_ask1)}</b></span><span className="right chart-times"><span>已收盘 · {time(bars.at(-1)?.end)}</span>{liveBar&&<span className="live-candle-note" title={liveBar.source==='quotes_only'?'当前仅含已接收报价，等待Wind分钟数据补齐开高低价':'Wind盘中快照及实时报价预览，最终OHLC以收盘数据为准'}>{liveBar.forming?'未收盘':'待核对'} · {time(liveBar.end)}{liveBar.source==='quotes_only'?' · 局部报价':''}</span>}</span></div>
      <Chart contract={contract} market={item?.market} tf={tf} bars={bars} levels={item?.levels} structures={item?.structures} quote={item?.quote} op={currentOp} focus={focus} provisional={liveBars} onLatest={()=>setSelectedOp(null)} preview={preview}/>
      <div className="chart-legend"><span><i className="red"/>上涨</span><span><i className="green"/>下跌</span><span><i className="blue"/>5分钟确认结构</span><span className="live-candle-note">金边K线为盘中预览</span><span className="right">i 内部 / s 波段 · H 高 / L 低 · {tf===5?'确认后回标':'5m标注来自5分钟'}</span></div>
      {currentOp?<div className="thesis" id="opportunity-detail"><div><span className={'direction '+(currentOp.direction===1?'up':'down')}>{currentOp.direction===1?'多':'空'}</span><h3>{setups[currentOp.setup]}</h3><span className="muted">{names[currentOp.status]}</span></div><p>{currentOp.reason}</p><div className="thesis-grid"><div><small>触发价</small><b>{price(currentOp.entry)}</b></div><div><small>结构失效</small><b>{price(currentOp.stop)}</b></div>{[0,1].map(i=><div key={i}><small>T{i+1} · {currentOp.targets[i]?.label||'待确认'}</small><b>{price(currentOp.targets[i]?.price)}</b></div>)}</div><div className="facts"><span>冻结关键位 {price(currentOp.level)} · {currentOp.level_name}</span><span>止损距离 {number(currentOp.risk)} {item?.market.unit}</span><span>距触发 {currentOp.entry&&item?.quote.rt_last>0?number(Math.abs(item.quote.rt_last-currentOp.entry)):'—'} {item?.market.unit}</span></div><OpportunityEvidence op={currentOp} instrument={item} connected={connected && !state?.offline && !replayOpen}/></div>:<div className="context-box"><div><span className="eyebrow">当前观察</span><h3>{item?.note||'等待行情就绪'}</h3><p>交易日 {item?.trading_day} · 先看15分钟环境，再等5分钟确认。</p></div><div className="context-metrics"><span>效率比<b>{item?.regime.efficiency?.toFixed(2)||'—'}</b></span><span>EMA斜率 / ATR<b>{item?.regime.slope?.toFixed(2)||'—'}</b></span></div></div>}
    </section><MarketContextPanel source={marketContext} clock={clock} instruments={state?.instruments} connected={connected&&!state?.offline&&!replayOpen} preview={preview} layout="rail"/><aside className="panel opportunity-panel"><div className="panel-head"><h2>{preview?'历史机会':'机会雷达'} <span className="count">{ops.length}</span></h2><button className="text-button" onClick={()=>setHistory(!history)}>{history?'只看当前':'包含历史'}</button></div><div className="aside-hint">{preview?'11月13日规则回放 · 历史静默':'盘中预警 · 收盘确认 · 条件失效'}</div><div className="op-list">{ops.length?ops.map(op=><Opportunity key={op.id} op={op} tone={atmosphere.actionable.some(o=>o.id===op.id)?'action':atmosphere.watching.some(o=>o.id===op.id)?'watch':null} market={state?.instruments.find(x=>x.contract===op.contract)?.market} selected={op.id===selectedOp} last={state?.instruments.find(x=>x.contract===op.contract)?.quote.rt_last} onClick={()=>selectOp(op)}/>):<div className="empty-state"><div className="empty-radar"><i/><i/><i/><b>＋</b></div><h3>{preview?'收盘快照，无当前入场信号':'等待条件完成'}</h3><p>市场状态、结构位置和后续确认一致时，机会会出现在这里。</p><ul>{state?.instruments.map(x=><li key={x.root}><b>{x.root}</b> {x.ready?x.note:x.status}</li>)}</ul></div>}</div><div className="aside-foot"><span className="small-dot"/>{preview?'历史预览 · 全部提醒静默':sound?'新的收盘确认将播放一次声音':'预警静默 · 点击顶部启用确认声音'}</div></aside></main>
    {optionsVisible&&<OptionsWorkbench root={root} opportunityId={currentOp?.id} context={state.options.contexts?.[root]}/>}
    <section className="panel event-panel"><div className="panel-head"><h2>事件记录 <span className="muted">保留当时依据</span></h2><div className="segments"><button className={logFilter==='today'?'active':''} onClick={()=>setLogFilter('today')}>今日</button><button className={logFilter==='all'?'active':''} onClick={()=>setLogFilter('all')}>全部记录</button></div></div><div className="event-scroll"><table><thead><tr><th>确认 / 生成时间</th><th>合约</th><th>事件</th><th>说明</th><th>来源</th></tr></thead><tbody>{logs.length?logs.map(e=><tr key={e.id} onClick={()=>e.opportunity&&selectOp(e.opportunity)} className={e.opportunity?'clickable':''}><td>{logFilter==='all'?fullTime(e.known_at):time(e.known_at)}</td><td>{shortContract(e.contract)||'系统'}</td><td><span className={'badge '+(e.opportunity?.status||'neutral')}>{e.opportunity?names[e.opportunity.status]:({structure:'结构变化',preview:'接近区域',revision:'数据修订',data_error:'数据异常',option_data_error:'期权数据异常',system:'系统状态'}[e.kind]||e.kind)}</span></td><td>{e.message}</td><td className="muted">{e.historical?'历史回放':'实时'}</td></tr>):<tr><td colSpan="5" className="empty-row">尚无事件，等待数据同步</td></tr>}</tbody></table></div></section>
    <div className="replay-entry"><div><b>复核历史，而非推定成交</b><span>保留 Wind 原生K线标签；完整性确认通常晚于标签1分钟。夜盘归入下一交易日；首根15分钟完成前使用上一交易日背景。</span></div><button disabled={preview||!item?.contract} onClick={()=>setReplayOpen(true)}>打开 {root} 历史回放 ↗</button></div>
    <footer><div className="atmosphere-preview-controls"><span>页面灯效</span><button onClick={()=>setAtmospherePreview('watch')}>预览橙色关注</button><button onClick={()=>setAtmospherePreview('action')}>预览红色确认</button></div><span>{preview?'Wind 历史样本 · 布局预览':'Wind 实时数据 · 本机运行'} · <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">Charts by TradingView</a></span><span>规则 {state?.rules.version||'v1.0'} · 初始规则待验证 · 不展示推定胜率</span></footer>
    {replayOpen&&<ReplayDialog contract={contract} market={item?.market} onClose={()=>setReplayOpen(false)}/>}
    {optionsVisible&&optionsOpen&&<OptionsDetails root={root} liveQuotes={state.options.quotes} contextRevision={state.options.contexts?.[root]?.generated_at} onClose={()=>setOptionsOpen(false)}/>}
  </div>;
}
