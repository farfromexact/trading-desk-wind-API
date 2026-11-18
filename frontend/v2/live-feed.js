// A single, cancellable poll chain. Never call an upstream mutation endpoint.
export function startLiveFeed({onSnapshot, onConnection, fetcher=fetch, intervalMs=3000, timeoutMs=20000,
  schedule=setTimeout, cancel=clearTimeout}) {
  let closed=false, timer=null, controller=null;
  async function poll() {
    if(closed)return;
    controller=new AbortController();
    const timeout=schedule(()=>controller?.abort(),timeoutMs);
    try {
      const response=await fetcher('./v2-api/live',{cache:'no-store',signal:controller.signal});
      if(!response.ok)throw Error('原看板后台暂不可达，正在重连');
      const data=await response.json();
      if(data.mode!=='live'||!data.state?.server_time||!Array.isArray(data.state.instruments)||!data.transport?.upstream_ok)
        throw Error('实时数据格式异常，正在重试');
      if(!closed){onSnapshot(data);onConnection({connected:true,error:''});}
    } catch(error) {
      if(!closed)onConnection({connected:false,error:error.name==='AbortError'?'读取超时，正在重连':error.message});
    } finally {
      cancel(timeout);controller=null;
      if(!closed)timer=schedule(poll,intervalMs);
    }
  }
  poll();
  return ()=>{closed=true;cancel(timer);controller?.abort();};
}

export function feedHealth(snapshot, connection, now=Date.now()) {
  if(!connection.connected)return {connected:false,label:connection.error||'正在连接后台'};
  const age=(now-Date.parse(snapshot?.state?.server_time))/1000;
  if(!Number.isFinite(age)||age>15||age < -10)return {connected:false,label:'后台状态未及时更新，结论暂停'};
  if(snapshot.state.offline||snapshot.state.last_error||!snapshot.state.feed_state?.includes('已连接'))
    return {connected:false,label:'Wind 数据源异常，结论暂停'};
  return {connected:true,label:'实时缓存已连接'};
}
