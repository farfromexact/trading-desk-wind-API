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
    return {...missing, value:'等待行情', subtitle:`${item.contract.split('.')[0]} · ${item.status || '非交易时段'}`, time:item.market_time};
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
  const trend = finite(breadth.change_pp) ? ` · 15m ${signed(breadth.change_pp,1)}pp` : ' · 趋势待积累';
  const small = finite(style.value) && style.value >= 0;
  const target = ['IM','IF','IH'].includes(root) ? root : null;
  return [
    {label:'小票参与', value:finite(breadth.value) ? `${breadth.value.toFixed(1)}%` : '—',
      subtitle:finite(breadth.value) ? `中证1000${trend}` : breadth.reason || '等待聚合家数', time:breadth.time, tone:'neutral',
      detail:'上涨家数 ÷（上涨家数＋下跌家数），排除平盘；相对前收盘。聚合家数须合计1000，数量匹配不代表已逐一验证成分名单。'},
    {label:'大小盘偏向 · 15m', value:finite(style.value) ? `${small ? '小票' : '权重'} ${Math.abs(style.value).toFixed(2)}pp` : '—',
      subtitle:finite(style.value) ? '中证1000 − 上证50' : style.reason || '等待同步分钟线', time:style.time, tone:'neutral',
      detail:'最近完整15分钟两指数收益率之差，单位为百分点；表示价格相对强弱，不推断机构或ETF买入。'},
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
    const flowValid = finite(flow.value) && flow.time && flow.unit==='元' && flow.frequency==='intraday_cumulative';
    const dailyValid = finite(daily.value) && daily.data_date && daily.unit==='元' && daily.frequency==='daily';
    const sourceClock = flow.time ? new Date(flow.time).toLocaleTimeString('en-GB',{timeZone:'Asia/Shanghai',hour12:false}) : '';
    return {...sample,
      flow:{...flow, value:flowValid?flow.value:null, display:money(flowValid?flow.value:null),
        timeLabel:flow.time?contextTime(flow.time):'时间未取得',
        stage:sourceClock>='15:00:00'?'盘后快照':'盘中累计', status:sourceStatus(flow.time,clock)},
      daily:{...daily, value:dailyValid?daily.value:null, display:money(dailyValid?daily.value:null),
        dateLabel:daily.data_date?`数据日 ${daily.data_date}`:'数据日未取得'},
    };
  });
}
