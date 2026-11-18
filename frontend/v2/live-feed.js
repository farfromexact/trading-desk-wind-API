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

export {feedHealth} from '../shared/market-model.js';
