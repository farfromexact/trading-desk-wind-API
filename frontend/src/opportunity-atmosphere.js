import { currentOpportunity } from './market-format.js';

// Page-wide alert priority, independent of selected card and history filters.
export function opportunityAtmosphere(state, connected, replay = false) {
  if (!connected || state?.offline || replay) return { tone: 'idle', watching: [], actionable: [] };
  const watching = [], actionable = [];
  for (const instrument of state?.instruments || []) {
    if (!instrument.ready || instrument.phase !== '交易中') continue;
    for (const op of instrument.opportunities || []) {
      if (op.historical || op.contract !== instrument.contract || !currentOpportunity(op, instrument)) continue;
      const signal = { ...op, root: instrument.root };
      if (op.status === 'watching') watching.push(signal);
      if (['confirmed', 'triggered'].includes(op.status) && Number.isFinite(op.entry) && Number.isFinite(op.stop)) actionable.push(signal);
    }
  }
  return { tone: actionable.length ? 'action' : watching.length ? 'watch' : 'idle', watching, actionable };
}
