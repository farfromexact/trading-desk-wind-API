import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { CandlestickSeries, createChart, createSeriesMarkers, LineStyle } from 'lightweight-charts';
import './style.css';
import { opportunityAtmosphere } from './opportunity-atmosphere';
import { mergeChartBars, followLatestRange, structureMarkers, spaceMarkerLabels } from './chart-data';

import { contractRoot, shortContract, formatPrice, formatNumber, currentOpportunity } from './market-format';
import {OptionsEnvironment,OptionsWorkbench,OptionsDetails} from './options';
const time = x => x ? new Date(x).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}) : '—';
const fullTime = x => x ? new Date(x).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}) : '—';
const epoch = x => Math.floor(new Date(x).getTime()/1000);
const names={watching:'关注中',confirmed:'收盘确认',triggered:'已触价',invalidated:'已失效',expired:'已过期',filtered:'空间不足',uncertain:'先后不确定'};
const setups={pullback:'顺势突破回踩',failed_break:'假突破反转'};

async function api(path,options){
  const response=await fetch('/api/'+path,options);
  if(!response.ok){let body=await response.json().catch(()=>({}));throw new Error(body.detail||'服务暂时不可用');}
  return response.json();
}

function useFeed(onAlert){
  const [state,setState]=useState(null),[events,setEvents]=useState([]),[connected,setConnected]=useState(false);
  const callback=useRef(onAlert); callback.current=onAlert;
  useEffect(()=>{
    let active=true,lastMetric=0,firstPacket=true; const measuredQuotes=new Map(),measuredOptions=new Map();
    api('state').then(s=>active&&setState(s)).catch(()=>{});
    api('events?limit=300').then(e=>active&&setEvents(e)).catch(()=>{});
    const source=new EventSource('/api/stream');
    source.addEventListener('update',event=>{
      if(!active)return;
      const packet=JSON.parse(event.data);setConnected(true);setState(packet.state);
      if(packet.events.length){
        setEvents(old=>{const map=new Map([...old,...packet.events].map(x=>[x.id,x]));return [...map.values()].sort((a,b)=>b.seq-a.seq).slice(0,500);});
        packet.events.filter(x=>x.audible).forEach(x=>callback.current(x));
      }
      const arrivals=packet.state.instruments.filter(x=>x.quote.received_ms&&x.quote.received_ms!==measuredQuotes.get(x.contract)).map(x=>{measuredQuotes.set(x.contract,x.quote.received_ms);return x.quote.received_ms;});
      const optionArrivals=Object.entries(packet.state.options?.quotes||{}).filter(([code,q])=>q.received_ms&&q.received_ms!==measuredOptions.get(code)).map(([code,q])=>{measuredOptions.set(code,q.received_ms);return q.received_ms;});
      if(!firstPacket && Date.now()-lastMetric>900 && !document.hidden){
        lastMetric=Date.now();
        requestAnimationFrame(()=>api('telemetry',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({delay_ms:Math.max(0,Date.now()-packet.state.sent_ms),source_delay_ms:arrivals.length?Math.max(0,Date.now()-Math.min(...arrivals)):null,option_delay_ms:optionArrivals.length?Math.max(0,Date.now()-Math.min(...optionArrivals)):null})}).catch(()=>{}));
      }
      firstPacket=false;
    });
    source.onerror=()=>{setConnected(false);firstPacket=true;};
    return()=>{active=false;source.close();};
  },[]);
  return {state,events,connected};
}

function Chart({contract,market,tf,bars,levels,structures,quote,op,focus,focusTime,provisional,onLatest}){
  const element=useRef(), chart=useRef(), series=useRef(),markers=useRef(), lines=useRef([]),initial=useRef(true),focusApplied=useRef(null);
  const previousCount=useRef(0);
  const fullMarkers=useRef([]),markerTimes=useRef([]),refreshMarkerLabels=useRef(null);
  const [hoverStructure,setHoverStructure]=useState('');
  const displayBars=useMemo(()=>mergeChartBars(bars,bars.length?provisional:[]),[bars,provisional]);
  useEffect(()=>{
    initial.current=true;
    previousCount.current=0;
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
    refreshMarkerLabels.current=()=>markers.current?.setMarkers(spaceMarkerLabels(fullMarkers.current,markerTimes.current,c.timeScale().getVisibleLogicalRange(),Math.max(1,element.current.clientWidth-75)));
    c.timeScale().subscribeVisibleLogicalRangeChange(()=>refreshMarkerLabels.current?.());
    c.subscribeCrosshairMove(param=>setHoverStructure(fullMarkers.current.filter(p=>p.time===param.time).map(p=>p.text).join(' · ')));
    return()=>{c.remove();chart.current=null;series.current=null;};
  },[contract,tf,market?.decimals,market?.tick_size]);
  useEffect(()=>{
    if(!series.current || !bars.length)return;
    const data=displayBars.map(b=>({time:epoch(b.end),open:b.open,high:b.high,low:b.low,close:b.close,...(b.provisional?{color:b.close>=b.open?'rgba(236,116,109,.35)':'rgba(86,188,167,.35)',borderColor:'#e0bd79',wickColor:'#e0bd79'}:{})}));
    const follow=followLatestRange(chart.current.timeScale().getVisibleLogicalRange(),previousCount.current,data.length);
    series.current.setData(data);
    if(initial.current){chart.current.timeScale().setVisibleLogicalRange({from:Math.max(0,data.length-85),to:data.length+6});initial.current=false;}
    else if(follow&&data.length!==previousCount.current)chart.current.timeScale().setVisibleLogicalRange(follow);
    previousCount.current=data.length;
  },[displayBars,contract,tf]);
  useEffect(()=>{
    if(!series.current)return;
    lines.current.forEach(line=>series.current.removePriceLine(line));lines.current=[];
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
    for(const line of list)lines.current.push(series.current.createPriceLine(line));
    const points=structureMarkers(structures,displayBars,tf);
    for(const b of displayBars.filter(b=>b.provisional))points.push({time:epoch(b.end),position:'aboveBar',color:'#e0bd79',shape:'circle',size:.5,text:b.forming?'未收盘':'待核对'});
    fullMarkers.current=points.sort((a,b)=>a.time-b.time);
    markerTimes.current=displayBars.map(b=>epoch(b.end));
    refreshMarkerLabels.current?.();
  },[levels,structures,quote,op,bars,tf,displayBars]);
  useEffect(()=>{
    const focusKey=focusTime||focus;
    if(!focusKey||!bars.length||!chart.current||focusApplied.current===focusKey)return;
    const target=focusTime||op?.confirmation_bar||op?.signal_bar;
    const i=bars.findIndex(b=>b.end>=target);
    if(i>=0){chart.current.timeScale().setVisibleLogicalRange({from:Math.max(0,i-50),to:Math.min(bars.length+8,i+12)});focusApplied.current=focusKey;}
  },[focus,focusTime,bars]);
  return <div className="chart-wrap"><div ref={element} className="chart-canvas"/>{hoverStructure&&<div className="chart-structure-readout">{hoverStructure}</div>}{quote&&<button className="chart-latest" onClick={()=>{onLatest?.();chart.current?.timeScale().scrollToRealTime();}}>回到最新 ↗</button>}{!bars.length&&<div className="chart-empty"><span className="loader"/>正在等待真实K线数据</div>}</div>;
}

function Opportunity({op,market,selected,onClick,last}){
  const spec=market||{decimals:op.price_decimals??1};
  const price=x=>formatPrice(x,spec);
  const number=x=>formatNumber(x,spec);
  const unit=market?.unit||op.price_unit||'点';
  const direction=op.direction===1?'多':'空';
  return <button className={'op-card '+(selected?'selected ':'')+op.status} onClick={onClick}>
    <div className="op-head"><span className={'direction '+(op.direction===1?'up':'down')}>{direction}</span><b>{shortContract(op.contract)}</b><span className={'badge '+op.status}>{names[op.status]}</span></div>
    <div className="op-name">{setups[op.setup]}{op.historical&&<span className="tiny">历史 · 静默</span>}</div>
    <p>{op.reason}</p>
    {op.entry?<><div className="op-prices"><div><small>触发价</small><b>{price(op.entry)}</b></div><div><small>失效价</small><b>{price(op.stop)}</b></div><div><small>T1 空间</small><b className={op.rr>=1.5?'accent':'amber'}>{op.rr?.toFixed(2)}R</b></div></div><div className="op-targets">{op.targets.map((t,i)=><span key={i}>T{i+1} <b>{price(t.price)}</b><small>{t.label}</small></span>)}</div><div className="op-foot"><span>止损距 {number(op.risk)} {unit}</span><span>距触发 {last>0?number(Math.abs(last-op.entry)):'—'} {unit}</span></div><div className="op-foot"><span>确认 {time(op.confirmed_at)}</span><span>{op.remaining_bars??0} 根内有效</span></div></>:<div className="op-foot"><span>关键位 {price(op.level)}</span><span>{op.near?'已接近区域':'等待价格接近'}</span></div>}
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

function App(){
  const [sound,setSound]=useState(false),soundRef=useRef(false),audio=useRef(null),[notice,setNotice]=useState('');
  const [root,setRoot]=useState('IM'),[tf,setTf]=useState(5),[bars,setBars]=useState([]),[selectedOp,setSelectedOp]=useState(null),[focus,setFocus]=useState(0);
  const [edit,setEdit]=useState(false),[choice,setChoice]=useState(''),[busy,setBusy]=useState(false),[logFilter,setLogFilter]=useState('today'),[history,setHistory]=useState(false);
  const [replayOpen,setReplayOpen]=useState(false),replayRef=useRef(false);
  const [optionsOpen,setOptionsOpen]=useState(false);
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
  const {state,events,connected}=useFeed(alert);
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
  const price=x=>formatPrice(x,item?.market);
  const number=x=>formatNumber(x,item?.market);
  const currentOp=item?.opportunities.find(x=>x.id===selectedOp)||null;
  const today=state?.server_time?.slice(0,10);
  const liveBars=(item?.live_bars?.[tf]||[]).filter(b=>b.end>(bars.at(-1)?.end||''));
  const liveBar=liveBars.at(-1);
  useEffect(()=>{
    let alive=true;
    if(!item?.contract)return;
    api('bars?contract='+encodeURIComponent(contract)+'&timeframe='+tf).then(rows=>alive&&setBars(rows)).catch(e=>alive&&setNotice(e.message));
    return()=>{alive=false;};
  },[contract,tf,item?.last_bar,state?.metrics.bar_batches,state?.bootstrap_remaining]);
  useEffect(()=>{setBars([]);},[contract,tf]);
  const rank={confirmed:0,triggered:1,watching:2,filtered:3,uncertain:4,invalidated:5,expired:6};
  const ops=(state?.instruments.flatMap(x=>x.opportunities)||[]).filter(o=>history||currentOpportunity(o,state?.instruments.find(x=>x.contract===o.contract))&&['watching','confirmed','triggered','filtered'].includes(o.status)).sort((a,b)=>(rank[a.status]-rank[b.status])||b.signal_bar.localeCompare(a.signal_bar)).slice(0,history?30:8);
  const logs=events.filter(e=>e.kind!=='pivot'&&(logFilter==='all'||(e.known_at||'').slice(0,10)===today)).slice(0,80);
  function selectOp(op){setRoot(contractRoot(op.contract));setTf(5);setSelectedOp(op.id);setFocus(x=>x+1);}
  async function saveChoice(auto=false){
    setBusy(true);setNotice('');
    try{await api('watchlist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({root,contract:auto?null:choice.trim()})});setEdit(false);}catch(e){setNotice(e.message);}finally{setBusy(false);}
  }
  return <div className={'app atmosphere-'+atmosphereTone}>
    <div className="atmosphere-light" aria-hidden="true"/><div className="atmosphere-edge" aria-hidden="true"/>
    <header><div className="brand"><span className="brand-mark">W<span>·</span></span><div><h1>实时机会工作台</h1><span>PRICE ACTION / 股指 · 贵金属 · 原油 · 国债</span></div></div><div className="header-right"><span className={'connection '+(connected?'ok':'')}><i/>{connected?'本机服务在线':'正在连接服务'}</span><button onClick={enableSound} className={'sound '+(sound?'enabled':'')}>{sound?'◖ 声音已开启':'◖ 启用声音'}</button><button onClick={()=>{if(sound)beep();else enableSound();}} className="subtle">测试声音</button></div></header>
    <div className="topline"><div><span className="live-tag">{state?.phase||'连接中'}</span><span>{state?.feed_state||'等待本机行情服务'}{state?.bootstrap_remaining>0?' · 剩余 '+state.bootstrap_remaining+' 项':''}</span></div><span>{fullTime(state?.server_time)} <span className="muted">上海</span></span></div>
    {(!connected||state?.last_error||notice||state?.offline)&&<div className="banner">{notice||state?.last_error||(state?.offline?'离线回放模式 · 所有提醒保持静默':'页面与服务连接中断，等待恢复')} {notice&&<button onClick={()=>setNotice('')}>关闭</button>}</div>}
    {atmosphereTone!=='idle'&&<div className={'atmosphere-status '+(atmospherePreview?'is-preview':'')} role="status" aria-live="polite">
      <span className="atmosphere-beacon" aria-hidden="true"/>
      <div><span className="atmosphere-eyebrow">{atmospherePreview?'灯效预览 · 30秒后恢复实时':'全市场机会雷达'}</span><strong>{atmosphereTone==='action'?'已收盘确认 · 关注触发条件':'关注中 · 等待收盘确认'}</strong></div>
      <span className="atmosphere-detail">{atmospherePreview?'仅预览视觉效果，不代表出现真实机会':atmosphereTone==='action'?`${atmosphereRoots} · ${atmosphere.actionable.filter(x=>x.status==='confirmed').length} 个待触发 / ${atmosphere.actionable.filter(x=>x.status==='triggered').length} 个已触价`:`${atmosphereRoots} · ${atmosphere.watching.length} 个关注机会`}</span>
      {atmospherePreview&&<button onClick={()=>setAtmospherePreview(null)}>恢复实时</button>}
    </div>}
    <div className="radar-grid">{(state?.instruments||[]).map(x=><button key={x.root} aria-label={'选择'+x.market.name+' '+x.root} className={'radar '+(root===x.root?'active':'')} onClick={()=>{setRoot(x.root);setSelectedOp(null);setEdit(false);}}><div className="radar-top"><span><b>{x.root}</b><small>{x.market.name}</small></span><span className={'badge '+(x.ready?'live':'neutral')} title={x.status}>{x.status}</span></div><div className="radar-mid"><strong>{formatPrice(x.quote.rt_last,x.market)}</strong><span className={'regime '+x.regime.state}>{x.regime.label}</span></div><div className="radar-bottom"><span>{x.contract||'解析合约中'}</span><span>行情 {time(x.market_time)}</span></div><div className="radar-note" title={x.note}>{x.note}</div></button>)}</div>
    <OptionsEnvironment root={root} context={state?.options?.contexts?.[root]} onOpen={()=>setOptionsOpen(true)}/>
    <main><section className="chart-panel panel"><div className="panel-head"><div className="chart-title"><h2>{shortContract(contract)} <span>价格结构</span></h2><button className="text-button" onClick={()=>{setChoice(item?.contract||'');setEdit(!edit);}}>切换合约 ↗</button></div><div className="segments">{[1,5,15].map(n=><button key={n} className={tf===n?'active':''} onClick={()=>setTf(n)}>{n}分钟</button>)}</div></div>
      {edit&&<form className="contract-form" onSubmit={e=>{e.preventDefault();saveChoice();}}><label>真实月份合约<input value={choice} onChange={e=>setChoice(e.target.value.toUpperCase())} placeholder={root+'2612.'+(item?.market.exchange||'CFE')} aria-label="真实月份合约"/></label><button disabled={busy} type="submit">应用</button><button disabled={busy} type="button" onClick={()=>saveChoice(true)}>恢复每日主力</button><button type="button" onClick={()=>setEdit(false)}>取消</button></form>}
      <div className="chart-stats"><span>{item?.market.name} <b>{item?.market.unit}</b></span><span>背景 <b>15分钟</b></span><span>确认 <b>5分钟收盘</b></span><span>买一 <b>{price(item?.quote.rt_bid1)}</b></span><span>卖一 <b>{price(item?.quote.rt_ask1)}</b></span><span className="right chart-times"><span>已收盘 · {time(bars.at(-1)?.end)}</span>{liveBar&&<span className="live-candle-note" title={liveBar.source==='quotes_only'?'当前仅含已接收报价，等待Wind分钟数据补齐开高低价':'Wind盘中快照及实时报价预览，最终OHLC以收盘数据为准'}>{liveBar.forming?'未收盘':'待核对'} · {time(liveBar.end)}{liveBar.source==='quotes_only'?' · 局部报价':''}</span>}</span></div>
      <Chart contract={contract} market={item?.market} tf={tf} bars={bars} levels={item?.levels} structures={item?.structures} quote={item?.quote} op={currentOp} focus={focus} provisional={liveBars} onLatest={()=>setSelectedOp(null)}/>
      <div className="chart-legend"><span><i className="red"/>上涨</span><span><i className="green"/>下跌</span><span><i className="blue"/>5分钟确认结构</span><span className="live-candle-note">金边K线为盘中预览</span><span className="right">i 内部 / s 波段 · H 高 / L 低 · {tf===5?'确认后回标':'5m标注来自5分钟'}</span></div>
      {currentOp?<div className="thesis"><div><span className={'direction '+(currentOp.direction===1?'up':'down')}>{currentOp.direction===1?'多':'空'}</span><h3>{setups[currentOp.setup]}</h3><span className="muted">{names[currentOp.status]}</span></div><p>{currentOp.reason}</p><div className="thesis-grid"><div><small>触发价</small><b>{price(currentOp.entry)}</b></div><div><small>结构失效</small><b>{price(currentOp.stop)}</b></div>{[0,1].map(i=><div key={i}><small>T{i+1} · {currentOp.targets[i]?.label||'待确认'}</small><b>{price(currentOp.targets[i]?.price)}</b></div>)}</div><div className="facts"><span>冻结关键位 {price(currentOp.level)} · {currentOp.level_name}</span><span>止损距离 {number(currentOp.risk)} {item?.market.unit}</span><span>距触发 {currentOp.entry&&item?.quote.rt_last>0?number(Math.abs(item.quote.rt_last-currentOp.entry)):'—'} {item?.market.unit}</span></div><details><summary>查看判断依据与时间</summary><p>候选环境：{currentOp.regime?.label}；关键位确认：{fullTime(currentOp.level_known_at)}；机会确认：{fullTime(currentOp.confirmed_at)}。</p><p>参考盈亏比未扣费用与滑点。已触价只表示行情到达条件，不代表成交。</p><div className="source-bars">{currentOp.source_bars?.slice(-4).map((b,i)=><span key={i}>{time(b.end)}　开 {price(b.open)}　高 {price(b.high)}　低 {price(b.low)}　收 {price(b.close)}</span>)}</div></details></div>:<div className="context-box"><div><span className="eyebrow">当前观察</span><h3>{item?.note||'等待行情就绪'}</h3><p>交易日 {item?.trading_day} · 先看15分钟环境，再等5分钟确认。</p></div><div className="context-metrics"><span>效率比<b>{item?.regime.efficiency?.toFixed(2)||'—'}</b></span><span>EMA斜率 / ATR<b>{item?.regime.slope?.toFixed(2)||'—'}</b></span></div></div>}
    </section><aside className="panel opportunity-panel"><div className="panel-head"><h2>机会雷达 <span className="count">{ops.length}</span></h2><button className="text-button" onClick={()=>setHistory(!history)}>{history?'只看当前':'包含历史'}</button></div><div className="aside-hint">盘中预警 · 收盘确认 · 条件失效</div><div className="op-list">{ops.length?ops.map(op=><Opportunity key={op.id} op={op} market={state?.instruments.find(x=>x.contract===op.contract)?.market} selected={op.id===selectedOp} last={state?.instruments.find(x=>x.contract===op.contract)?.quote.rt_last} onClick={()=>selectOp(op)}/>):<div className="empty-state"><div className="empty-radar"><i/><i/><i/><b>＋</b></div><h3>等待条件完成</h3><p>市场状态、结构位置和后续确认一致时，机会会出现在这里。</p><ul>{state?.instruments.map(x=><li key={x.root}><b>{x.root}</b> {x.ready?x.note:x.status}</li>)}</ul></div>}</div><div className="aside-foot"><span className="small-dot"/>{sound?'新的收盘确认将播放一次声音':'预警静默 · 点击顶部启用确认声音'}</div></aside></main>
    <OptionsWorkbench root={root} opportunityId={currentOp?.id} context={state?.options?.contexts?.[root]}/>
    <section className="panel event-panel"><div className="panel-head"><h2>事件记录 <span className="muted">保留当时依据</span></h2><div className="segments"><button className={logFilter==='today'?'active':''} onClick={()=>setLogFilter('today')}>今日</button><button className={logFilter==='all'?'active':''} onClick={()=>setLogFilter('all')}>全部记录</button></div></div><div className="event-scroll"><table><thead><tr><th>确认 / 生成时间</th><th>合约</th><th>事件</th><th>说明</th><th>来源</th></tr></thead><tbody>{logs.length?logs.map(e=><tr key={e.id} onClick={()=>e.opportunity&&selectOp(e.opportunity)} className={e.opportunity?'clickable':''}><td>{logFilter==='all'?fullTime(e.known_at):time(e.known_at)}</td><td>{shortContract(e.contract)||'系统'}</td><td><span className={'badge '+(e.opportunity?.status||'neutral')}>{e.opportunity?names[e.opportunity.status]:({structure:'结构变化',preview:'接近区域',revision:'数据修订',data_error:'数据异常',option_data_error:'期权数据异常',system:'系统状态'}[e.kind]||e.kind)}</span></td><td>{e.message}</td><td className="muted">{e.historical?'历史回放':'实时'}</td></tr>):<tr><td colSpan="5" className="empty-row">尚无事件，等待数据同步</td></tr>}</tbody></table></div></section>
    <div className="replay-entry"><div><b>复核历史，而非推定成交</b><span>保留 Wind 原生K线标签；完整性确认通常晚于标签1分钟。夜盘归入下一交易日；首根15分钟完成前使用上一交易日背景。</span></div><button disabled={!item?.contract} onClick={()=>setReplayOpen(true)}>打开 {root} 历史回放 ↗</button></div>
    <footer><div className="atmosphere-preview-controls"><span>页面灯效</span><button onClick={()=>setAtmospherePreview('watch')}>预览橙色关注</button><button onClick={()=>setAtmospherePreview('action')}>预览红色确认</button></div><span>Wind 实时数据 · 本机运行 · <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">Charts by TradingView</a></span><span>规则 {state?.rules.version||'v1.0'} · 初始规则待验证 · 不展示推定胜率</span></footer>
    {replayOpen&&<ReplayDialog contract={contract} market={item?.market} onClose={()=>setReplayOpen(false)}/>}
    {optionsOpen&&<OptionsDetails root={root} liveQuotes={state?.options?.quotes} contextRevision={state?.options?.contexts?.[root]?.generated_at} onClose={()=>setOptionsOpen(false)}/>}
  </div>;
}

createRoot(document.getElementById('root')).render(<App/>);
