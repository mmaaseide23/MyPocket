const assert=require('node:assert/strict');
const {percentage,visibleIndices,share,tooltip}=require('../mypocket/static/positions.js');
assert.equal(percentage(25),'25.0%');
assert.equal(percentage(.001),'<0.1%');
assert.equal(percentage(0),'0.0%');
assert.equal(percentage(100),'100.0%');
const rows=[{label:'EXAMPLE',value:75},{label:'SAMPLE',value:25},{value:0},{value:null},{value:NaN},{value:-10}];
assert.deepEqual(visibleIndices(rows,()=>true),[0,1]);
assert.equal(share(rows,0,()=>true),75);
assert.equal(share(rows,1,()=>true),25);
assert.equal(share(rows,1,i=>i!==0),100);
assert.equal(share(rows,0,i=>i!==0),null);
assert.equal(share(rows,0,()=>false),null);
assert.equal(share([{value:0}],0,()=>true),null);
assert.equal(tooltip({label:'EXAMPLE · Sample account',value:25},'USD',25),'EXAMPLE · Sample account: $25.00 (25.0%)');
assert(tooltip({label:'EXAMPLE',value:25},'Unspecified',100).includes('25.00 (100.0%)'));
assert.equal(tooltip({label:'ZERO',value:0},'USD',null),'ZERO: $0.00');
const source=require('node:fs').readFileSync(require.resolve('../mypocket/static/positions.js'),'utf8');
assert(!source.includes('fetch('));
assert(!source.includes('/api/positions/'));
console.log('Tooltip formatting, visible-slice denominator, zero/missing values and no requests verified');

// Run the actual event handlers with an in-memory fictional canvas/chart.
const vm=require('node:vm');
const events={},classes=new Set(['sr-only']),announcement={textContent:'',classList:{add:c=>classes.add(c),toggle:(c,on)=>on?classes.add(c):classes.delete(c)}};
const sample=rows.slice(0,2),hidden=new Set();
let active=[],updates=0;
const chart={
  options:{plugins:{tooltip:{callbacks:{}},legend:{}}},
  getDataVisibility:i=>!hidden.has(i),
  toggleDataVisibility:i=>hidden.has(i)?hidden.delete(i):hidden.add(i),
  setActiveElements:value=>{active=value;},
  tooltip:{setActiveElements:()=>{}},
  getDatasetMeta:()=>({data:sample.map(()=>({getCenterPoint:()=>({x:0,y:0})}))}),
  update:()=>{updates++;},destroy:()=>{},
};
const canvas={
  dataset:{allocation:JSON.stringify(sample),currency:'USD'},
  closest:()=>({parentElement:{querySelector:()=>announcement}}),
  addEventListener:(name,handler)=>{events[name]=handler;},
};
vm.runInNewContext(source,{
  document:{addEventListener:(_,handler)=>handler(),querySelectorAll:()=>[canvas]},
  window:{addEventListener:()=>{}},
  Chart:{register:()=>{},unregister:()=>{}},
  charts:{doughnut:()=>chart,palette:[]},
});
const key=value=>{
  let prevented=false;
  events.keydown({key:value,preventDefault:()=>{prevented=true;}});
  assert(prevented,`Key ${value} must not scroll or submit`);
};
events.focus();assert.equal(active[0].index,0);
key('Enter');assert(hidden.has(0));assert.match(announcement.textContent,/EXAMPLE hidden/);
assert(!classes.has('sr-only'),'Hidden keyboard selection must remain visible');
key('ArrowRight');assert.equal(active[0].index,1);assert.match(announcement.textContent,/100.0%/);
assert(classes.has('sr-only'),'Visible selection already has an on-demand chart tooltip');
key(' ');assert.equal(hidden.size,2);assert.equal(active.length,0);
key('Home');assert.match(announcement.textContent,/EXAMPLE hidden/);
key('Enter');assert(!hidden.has(0));assert.match(announcement.textContent,/100.0%/);
key('End');assert.match(announcement.textContent,/SAMPLE hidden/);
key(' ');assert.equal(hidden.size,0);assert.match(announcement.textContent,/25.0%/);
key('Escape');assert.equal(announcement.textContent,'');assert.equal(active.length,0);
chart.options.plugins.legend.onClick(null,{index:0});assert(hidden.has(0));
events.focus();assert.match(announcement.textContent,/EXAMPLE hidden/);
key('Enter');assert.equal(hidden.size,0);assert.match(announcement.textContent,/75.0%/);
assert(updates>0);
console.log('Keyboard hide/show, all-hidden recovery, live percentages and pointer parity verified');
