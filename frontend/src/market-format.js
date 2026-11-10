export const contractRoot = code => String(code || '').match(/^[A-Z]+/)?.[0] || '';
export const shortContract = code => String(code || '').split('.')[0];
export const formatNumber = (value, market) => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(market?.decimals ?? 1) : '—';
export const formatPrice = (value, market) => value > 0 ? formatNumber(value, market) : '—';
export const currentOpportunity = (op, instrument) => (op.trading_day || op.signal_bar?.slice(0,10)) === instrument?.trading_day;
