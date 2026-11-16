const finite = x => typeof x === 'number' && Number.isFinite(x);
const signed = (x, digits = 2) => `${x > 0 ? '+' : ''}${x.toFixed(digits)}`;
export const contextTime = x => x ? new Date(x).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false}) : '未取得';

export function sourceStatus(stamp, now = new Date(), maxSeconds = 180) {
  if (!stamp || !Number.isFinite(new Date(stamp).getTime())) return '缺失';
  const age = (new Date(now) - new Date(stamp)) / 1000;
  if (age < -10) return '时间异常';
  const date = x => new Date(x).toLocaleDateString('sv-SE', {timeZone:'Asia/Shanghai'});
  if (date(stamp) !== date(now)) return '历史';
  return age > maxSeconds ? '滞后' : '最近';
}

export function positionMetric(item, connected = true, allowHistorical = false) {
  const missing = {label:'期货位置', value:'—', subtitle:'等待期货行情与已确认结构', tone:'muted', time:null,
    detail:'当前价与最近已确认关键位的距离（点）；行情与结构未就绪时保持缺失，不推定交易动作。'};
  if (!item?.contract || !finite(item?.quote?.rt_last)) return missing;
  const historical = allowHistorical && item.snapshot_mode === 'historical';
  if (!historical && (!connected || !item.ready || item.phase !== '交易中')) {
    const closed = connected && item.phase === '已收盘';
    return {...missing, value:closed?'已收盘':'等待行情',
      subtitle:closed?`${item.contract.split('.')[0]} · 实时位置在交易时段展示`:`${item.contract.split('.')[0]} · ${item.status || '非交易时段'}`,
      time:item.market_time};
  }
  const price = item.quote.rt_last;
  const levels = (item.levels || []).filter(x => finite(x.price) && x.price > 0 && x.known_at
    && new Date(x.known_at) <= new Date(item.market_time));
  const nearest = levels.sort((a,b) => Math.abs(a.price-price)-Math.abs(b.price-price))[0];
  if (!nearest) return {...missing, subtitle:'已有行情，等待确认关键位', time:item.market_time};
  const offset = price-nearest.price, decimals = item.market?.decimals ?? 1;
  return {label:historical?'历史期货位置':'期货位置', value:`${offset >= 0 ? '上方' : '下方'} ${Math.abs(offset).toFixed(decimals)}`,
    subtitle:`${item.root} · ${nearest.name} ${nearest.price.toFixed(decimals)}`, time:item.market_time,
    tone:'neutral', detail:'当前价与最近已确认关键位的距离（点）；不是买卖指令或回踩完成判断。'};
}

export function contextMetrics(context, root = 'IM', item, connected = true, preview = false) {
  const breadth = context?.breadth || {}, style = context?.style || {}, activity = context?.activity?.[root] || {};
  const trend = finite(breadth.change_pp) ? ` · 5m ${signed(breadth.change_pp,1)}pp` : ' · 趋势待积累';
  const small = finite(style.value) && style.value >= 0;
  const target = ['IM','IF','IH'].includes(root) ? root : null;
  return [
    {label:'上涨广度 · 5m变化', value:finite(breadth.value) ? `${breadth.value.toFixed(1)}%` : '—',
      subtitle:finite(breadth.value) ? `中证1000${trend}` : breadth.reason || '等待聚合家数', time:breadth.time, tone:'neutral',
      detail:'上涨家数 ÷（上涨家数＋下跌家数），排除平盘；相对前收盘。前三张使用指数样本，后两张为板块聚合；前三指数家数须匹配对应样本总数；两板块保留Wind返回家数，未独立核验全板覆盖。近5分钟变化缺历史时保持待积累，数量匹配不代表已逐只复算涨跌。'},
    {label:'相对强弱 · 5m', value:finite(style.value) ? `${small ? '小票' : '权重'} ${Math.abs(style.value).toFixed(2)}pp` : '—',
      subtitle:finite(style.value) ? '中证1000 − 沪深300' : style.reason || '等待同步分钟线', time:style.time, tone:'neutral',
      detail:'最近完整5分钟两指数收益率之差，单位为百分点；表示价格相对强弱，不推断机构或ETF买入。'},
    {label:'成交活跃度 · 15m', value:target && finite(activity.value) ? `${activity.value.toFixed(2)}×` : '—',
      subtitle:target ? (finite(activity.value) ? `${root}现货 · ${activity.baseline_days}日同刻中位数` : activity.reason || '等待成交额基准') : '选择 IM / IF / IH 查看现货成交',
      time:activity.time, tone:'neutral', detail:'对应现货指数最近完整15分钟成交额 ÷ 过去20个有数据交易日同刻中位数，至少10个有效基准；只用此前日期，不跨午休。'},
    positionMetric(item, connected, preview),
  ];
}

export function contextRows(context, instruments = [], connected = true, preview = false) {
  const items = preview ? context?.preview_instruments || [] : instruments;
  return ['IM','IF','IH'].map(root => {
    const item = items.find(x => x.root === root);
    const metrics = contextMetrics(context, root, item, connected, preview);
    return {root, activity:metrics[2], position:metrics[3]};
  });
}

export function crossRows(context) {
  return (context?.cross || []).filter(x => x.code && x.time).map(x => ({...x,
    label:x.code.split('.')[0], value:finite(x.change_pct) ? `${signed(x.change_pct)}%` : '—',
    amountLabel:finite(x.amount) ? `${(x.amount/1e8).toFixed(1)}亿` : '未取得',
    group:/^(T|TL)\d/.test(x.code) ? '国债' : '商品样本',
  }));
}

export function etfRows(context, clock = new Date()) {
  const expected = [{root:'IM',code:'512100.SH',name:'中证1000ETF',manager:'南方'},
    {root:'IF',code:'510300.SH',name:'沪深300ETF',manager:'华泰柏瑞'},
    {root:'IH',code:'510050.SH',name:'上证50ETF',manager:'华夏'}];
  const money = x => finite(x) ? `${signed(x/1e8)}亿` : '—';
  return expected.map(sample => {
    const row = context?.etfs?.find(x=>x.code===sample.code) || {};
    const flow = row.flow || {}, daily = row.daily || {};
    const datedFlow = flow.frequency==='daily_snapshot' && flow.data_date;
    const flowValid = finite(flow.value) && flow.unit==='元' && (datedFlow || flow.time && flow.frequency==='intraday_cumulative');
    const dailyValid = finite(daily.value) && daily.data_date && daily.unit==='元' && daily.frequency==='daily';
    const sourceClock = flow.time ? new Date(flow.time).toLocaleTimeString('en-GB',{timeZone:'Asia/Shanghai',hour12:false}) : '';
    return {...sample,
      flow:{...flow, value:flowValid?flow.value:null, display:money(flowValid?flow.value:null),
        timeLabel:datedFlow?`数据日 ${flow.data_date}`:flow.time?contextTime(flow.time):'时间未取得',
        stage:datedFlow?'日频截面':sourceClock>='15:00:00'?'盘后快照':'盘中累计', status:datedFlow?'源更新时间未提供':sourceStatus(flow.time,clock)},
      daily:{...daily, value:dailyValid?daily.value:null, display:money(dailyValid?daily.value:null),
        dateLabel:daily.data_date?`数据日 ${daily.data_date}`:'数据日未取得'},
    };
  });
}


export function breadthCards(context) {
  const indices=[['000016.SH','上证50'],['000300.SH','沪深300'],['000852.SH','中证1000'],['000688.SH','科创板'],['399006.SZ','创业板']];
  return indices.map(([code,name])=>{
    const row=context?.breadths?.find(x=>x.code===code)||{};
    const valid=finite(row.value), counts=row.counts;
    return {...row,code,name,scopeLabel:['000688.SH','399006.SZ'].includes(code)?'Wind聚合':'指数样本',display:valid?`${row.value.toFixed(1)}%`:'—',
      tone:!valid?'muted':row.value>50?'up':row.value<50?'down':'neutral',
      change:finite(row.change_pp)?`${signed(row.change_pp,1)}pp`:'待积累',
      changeTone:!finite(row.change_pp)?'muted':row.change_pp>0?'up':row.change_pp<0?'down':'neutral',
      countsLabel:valid&&counts?.every(finite)?`涨 ${counts[0]} · 跌 ${counts[1]} · 平 ${counts[2]}`:row.reason||'等待聚合家数',
      progress:valid?Math.max(0,Math.min(100,row.value)):null};
  });
}

export function breadthTrend(trend=[]) {
  if(trend.length<2)return '';
  const end=new Date(trend.at(-1).time).getTime(), start=end-30*60000;
  let previous=null, path='';
  for(const point of trend){
    const time=new Date(point.time).getTime();
    if(!finite(point.value)||!finite(time)||time<start||time>end){previous=null;continue;}
    const x=2+(time-start)/(30*60000)*116, y=30-Math.max(0,Math.min(100,point.value))*.26;
    path+=`${previous!==null&&time-previous<=90000?'L':'M'}${x.toFixed(2)},${y.toFixed(2)} `;
    previous=time;
  }
  return path.includes('L')?path.trim():'';
}

export function rotationMetrics(context) {
  const rotation=context?.rotation||{}, strength=rotation.strength||{}, turnover=rotation.turnover||{}, gap=rotation.breadth_gap||{};
  const side=(x,center=0)=>!finite(x)?'待数据':x>center?'偏小盘':x<center?'偏大盘':'均衡';
  const tone=(x,center=0)=>!finite(x)?'muted':x>center?'small':x<center?'large':'neutral';
  return [
    {...strength,key:'strength',label:'相对强弱',period:'近5分钟',
      display:finite(strength.value)?`${signed(strength.value)}pp`:'—',side:side(strength.value),tone:tone(strength.value),
      subtitle:finite(strength.value)?`1000 ${finite(strength.small_return)?signed(strength.small_return)+'%':'—'} / 300 ${finite(strength.large_return)?signed(strength.large_return)+'%':'—'}`:strength.reason||'等待完整5分钟线',
      detail:'中证1000减沪深300的最近完整5分钟收益率；正值小盘相对强，负值大盘相对强。两指数使用同一结束时刻，不跨午休，不等于资金净流入。'},
    {...turnover,key:'turnover',label:'成交偏向',period:'近5分钟',
      display:finite(turnover.value)?`${turnover.value.toFixed(2)}×`:'—',side:side(turnover.value,1),tone:tone(turnover.value,1),
      subtitle:finite(turnover.value)?`${turnover.baseline_days}日同刻基准 · 1×为常态`:turnover.reason||'等待同刻成交基准',
      detail:'（1000 / 300的5分钟成交额之比）÷过去20个交易日同刻配对比值的中位数，至少10日。大于1表示相对常态更偏小盘成交；不代表买入方向或净流入。'},
    {...gap,key:'breadth_gap',label:'广度差',period:'当前 / 5m变化',
      display:finite(gap.value)?`${signed(gap.value,1)}pp`:'—',side:side(gap.value),tone:tone(gap.value),
      subtitle:finite(gap.value)?`近5m ${finite(gap.change_pp)?signed(gap.change_pp,1)+'pp':'待积累'}`:gap.reason||'等待同步广度',
      detail:'当前中证1000减沪深300的上涨占比（源时间差不超过10秒），另列近5分钟差值变化。上涨占比均排除平盘，不跨交易时段。'}
  ];
}
