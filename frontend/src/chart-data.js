// Display-only previews are merged here, never sent to a signal/replay endpoint.
export function mergeChartBars(closed, provisional=[]){
  const rows=new Map(provisional.map(b=>[b.end,b]));
  for(const b of closed)rows.set(b.end,b); // Native finalized OHLC always wins.
  return [...rows.values()].sort((a,b)=>a.end.localeCompare(b.end));
}

export function followLatestRange(range,previousCount,nextCount){
  if(!range||!previousCount||range.to<previousCount-1)return null;
  const shift=nextCount-previousCount;
  return {from:range.from+shift,to:range.to+shift};
}

export function structureMarkers(structures,bars,timeframe){
  if(!bars.length)return [];
  const points=new Map();
  const times=bars.map(b=>Date.parse(b.end));
  for(const e of (structures||[]).slice(-70)){
    if(!['pivot','structure'].includes(e.kind))continue;
    const anchor=Date.parse(e.occurred_at);
    const index=timeframe===15?times.findIndex(t=>t>=anchor):times.indexOf(anchor);
    if(index<0||anchor<times[index]-timeframe*60000)continue;
    const isPivot=e.kind==='pivot';
    const above=isPivot?e.side==='high':e.direction!==1;
    const position=above?'aboveBar':'belowBar';
    const text=(e.scale==='internal'?'i':'s')+(isPivot?(e.side==='high'?'H':'L'):' '+e.label);
    const key=times[index]+position;
    let point=points.get(key);
    if(!point){
      point={time:times[index]/1000,position,color:'#7c93ad',shape:'circle',size:.5,labels:[]};
      points.set(key,point);
    }
    if(!point.labels.includes(text))point.labels.push(text);
    if(!isPivot)Object.assign(point,{color:e.direction===1?'#ec746d':'#56bca7',shape:e.direction===1?'arrowUp':'arrowDown',size:1});
  }
  return [...points.values()].map(({labels,...point})=>({...point,text:(timeframe===5?'':'5m ')+labels.join(' / ')})).sort((a,b)=>a.time-b.time);
}

export function spaceMarkerLabels(points,times,range,width){
  if(!range||range.to<=range.from||!width)return points;
  const indices=new Map(times.map((t,i)=>[t,i]));
  const limits={aboveBar:Infinity,belowBar:Infinity};
  const output=points.map(p=>({...p}));
  const order=points.map((p,i)=>({p,i,index:indices.get(p.time)})).filter(x=>x.index!==undefined)
    .sort((a,b)=>b.index-a.index||b.i-a.i);
  for(const {p,i,index} of order){
    if(index<range.from||index>range.to)continue;
    const x=(index-range.from)/(range.to-range.from)*width;
    const half=(Array.from(p.text||'').reduce((n,ch)=>n+(ch.charCodeAt(0)>255?11:6),0)+12)/2;
    if(x+half>limits[p.position])output[i].text='';
    else limits[p.position]=x-half-8;
  }
  return output;
}
