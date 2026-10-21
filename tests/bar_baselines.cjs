// Real vendored Chart.js, real shared helpers, fictional numbers and canvas only.
const assert=require('node:assert/strict'),fs=require('node:fs');
let clock=10000,frames=[];Date.now=()=>clock;
global.window={requestAnimationFrame:cb=>frames.push(cb)};
const RealChart=require('../mypocket/static/vendor/chart.umd.min.js');
// BasicPlatform normally disables animation. Preserve browser-like animation
// so this regression catches an old constructor animation overwriting layout.
class BrowserLikePlatform extends RealChart.BasicPlatform {updateConfig(){}}
function canvas(width=460){
 const c={width,height:220,style:{}};
 const ctx=new Proxy({canvas:c,measureText:t=>({width:String(t).length*7}),getLineDash:()=>[]},{get:(target,key)=>key in target?target[key]:()=>{}});
 c.getContext=()=>ctx;return c;
}
global.Chart=function(c,config){return new RealChart(c,{...config,platform:BrowserLikePlatform});};
global.Chart.defaults=RealChart.defaults;global.Chart.register=RealChart.register.bind(RealChart);
global.document={documentElement:{},addEventListener:(_event,callback)=>callback()};global.getComputedStyle=()=>({getPropertyValue:()=> '#888'});
const src=fs.readFileSync('mypocket/templates/base.html','utf8');
new Function(src.slice(src.indexOf('    const fmtUSD ='),src.indexOf('    // ─── Theme toggle')))();
function finishFrames(){clock+=2000;const pending=frames;frames=[];pending.forEach(cb=>cb());}
function historyLayout(chart){
 chart.options.scales.x.ticks={maxRotation:0,autoSkip:true,maxTicksLimit:8};
 chart.options.plugins.barValueLabels=true;chart.update('none');
}
function checkOrdinary(chart){
 const y=chart.scales.y,zero=y.getPixelForValue(0);
 assert(y.min<=0 && y.max>=0,'value scale must contain zero');
 chart.data.datasets.forEach((dataset,i)=>{
  if(!chart.isDatasetVisible(i))return;
  chart.getDatasetMeta(i).data.forEach((bar,j)=>{
   const value=dataset.data[j];if(value==null)return;
   assert(Math.abs(bar.base-zero)<=.51,`bar base ${bar.base} differs from zero ${zero}`);
   assert(Math.abs(bar.y-y.getPixelForValue(value))<.01,'bar head must match actual value');
   if(value>0)assert(bar.y<zero);if(value<0)assert(bar.y>zero);
   if(value===0)assert(Math.abs(bar.y-bar.base)<.01);
  });
 });
}
const labels=['Jul 2026 (partial)','Aug 2026','Sep 2026','Oct 2026 (partial)'];
for(const width of [320,375,390,430,460,960]){
 for(const values of [[10,70,60,40],[-10,-70,-60,-40],[-20,0,50,-5],[0,0,0,0],[10,null,0,30]]){
  const chart=window.charts.bar(canvas(width),labels,values);historyLayout(chart);
  checkOrdinary(chart);finishFrames();checkOrdinary(chart);
  chart.resize(width===320?460:320,260);finishFrames();checkOrdinary(chart);chart.destroy();
 }
 const chart=window.charts.groupedBar(canvas(width),labels,[{label:'Income',data:[10,200,-20,0],color:'#0a0'},{label:'Spending',data:[5,70,-10,0],color:'#a00'}]);
 historyLayout(chart);checkOrdinary(chart);finishFrames();checkOrdinary(chart);
 chart.hide(0);finishFrames();checkOrdinary(chart);chart.show(0);finishFrames();checkOrdinary(chart);chart.destroy();
}
// Disabling animation must not flatten intentional ranges or stacks to zero.
const range=window.charts.bar(canvas(),['Interval'],[[10,20]]);historyLayout(range);finishFrames();
assert(Math.abs(range.getDatasetMeta(0).data[0].base-range.scales.y.getPixelForValue(10))<.01);range.destroy();
const stack=window.charts.groupedBar(canvas(),['Positive','Negative'],[{label:'A',data:[10,-10]},{label:'B',data:[20,-20]}]);
stack.options.scales.x.stacked=true;stack.options.scales.y.stacked=true;historyLayout(stack);finishFrames();
assert(Math.abs(stack.getDatasetMeta(1).data[0].base-stack.scales.y.getPixelForValue(10))<.01);
assert(Math.abs(stack.getDatasetMeta(1).data[1].base-stack.scales.y.getPixelForValue(-10))<.01);stack.destroy();

// Check rendered label rectangles against actual Chart.js bar geometry. Dense
// monthly labels must not collide; roomy charts retain all exact value labels.
for(const width of [375,390,430,960]){
 const count=width===960?4:12;
 const chart=window.charts.bar(canvas(width),Array.from({length:count},(_,i)=>`Month ${i+1}`),Array(count).fill(5));
 historyLayout(chart);finishFrames();
 const labels=[];chart.ctx.fillText=(text,x,y)=>labels.push({text,left:x-String(text).length*3.5,right:x+String(text).length*3.5,top:y-11,bottom:y});
 RealChart.registry.plugins.get('barValueLabels').afterDatasetsDraw(chart,{},{});
 for(let i=0;i<labels.length;i++){
  const a=labels[i];assert(a.left>=0&&a.right<=width&&a.top>=0&&a.bottom<=chart.height);
  for(let j=i+1;j<labels.length;j++){
   const b=labels[j];assert(a.right<=b.left||b.right<=a.left||a.bottom<=b.top||b.bottom<=a.top,'value labels must not overlap');
  }
 }
 if(width===960)assert.equal(labels.length,count,'keep labels when they fit');
 else assert(labels.length<count,'hide labels that collide on narrow charts');
 assert.equal(chart.options.plugins.tooltip.callbacks.label({dataset:{},parsed:{y:5}}),'$5.00');
 chart.destroy();
}
