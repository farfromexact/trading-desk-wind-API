import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeChartBars, followLatestRange, structureMarkers, spaceMarkerLabels } from './chart-data.js';

test('final candles replace previews, while a new live candle stays visible',()=>{
  const closed=[{end:'10:35',close:101},{end:'10:40',close:102}];
  const live=[{end:'10:40',close:999,provisional:true},{end:'10:45',close:103,provisional:true}];
  const rows=mergeChartBars(closed,live);
  assert.deepEqual(rows.map(x=>x.close),[101,102,103]);
  assert.equal(rows[1].provisional,undefined);
  assert.equal(rows[2].provisional,true);
  assert.equal(closed.length,2);
});

test('append follows the latest candle and retains the zoom width',()=>{
  assert.deepEqual(followLatestRange({from:30,to:106},100,101),{from:31,to:107});
});

test('reading historical candles does not jump back on a quote update',()=>{
  assert.equal(followLatestRange({from:10,to:80},100,101),null);
  assert.equal(followLatestRange(null,0,100),null);
});

const at=t=>'2026-11-10T'+t+':00+08:00';
const pivot=(t,scale='internal')=>({kind:'pivot',occurred_at:at(t),scale,side:'high'});

test('one-minute chart shows the known five-minute anchors with their source',()=>{
  const markers=structureMarkers([pivot('10:35')],[{end:at('10:34')},{end:at('10:35')},{end:at('10:36')}],1);
  assert.equal(markers.length,1);
  assert.equal(markers[0].text,'5m iH');
  assert.equal(markers[0].time,Date.parse(at('10:35'))/1000);
});

test('15-minute chart groups five-minute events in their containing candle',()=>{
  const events=[pivot('10:35'),pivot('10:40','swing'),pivot('10:45'),pivot('11:00')];
  const markers=structureMarkers(events,[{end:at('10:30')},{end:at('10:45')}],15);
  assert.equal(markers.length,1);
  assert.equal(markers[0].text,'5m iH / sH');
  assert.equal(markers[0].time,Date.parse(at('10:45'))/1000);
});

test('markers before loaded history and beyond the right edge are not invented',()=>{
  const markers=structureMarkers([pivot('09:25'),pivot('09:35'),pivot('10:05')],[{end:at('09:45')},{end:at('10:00')}],15);
  assert.equal(markers.length,1);
  assert.equal(markers[0].time,Date.parse(at('09:45'))/1000);
});

test('crowded labels hide their text but retain every structural point and source',()=>{
  const points=[1,2,3].map(time=>({time,position:'aboveBar',text:'5m iH / sH'}));
  const compact=spaceMarkerLabels(points,[1,2,3],{from:0,to:20},200);
  assert.equal(compact.length,3);
  assert.equal(compact.filter(p=>p.text).length,1);
  assert.equal(points.filter(p=>p.text).length,3);
  const zoomed=spaceMarkerLabels(points,[1,2,3],{from:0,to:3},900);
  assert.equal(zoomed.filter(p=>p.text).length,3);
});
