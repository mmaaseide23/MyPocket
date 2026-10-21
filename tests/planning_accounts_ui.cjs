// Fictional DOM and responses only; no browser, database, storage, or outside requests.
const assert=require('node:assert/strict');
class El {
 constructor(){this.value='';this.min='0';this.max='100';this.listeners={};this.hidden=false;this.textContent='';this.children=[];this.attrs={};}
 get valueAsNumber(){return this.value===''?NaN:Number(this.value);}
 addEventListener(name,fn){this.listeners[name]=fn;}
 setAttribute(k,v){this.attrs[k]=v;}
 append(...children){children.forEach(child=>{child.parent=this;});this.children.push(...children);}
 focus(){this.focused=true;}
 remove(){this.parent.children=this.parent.children.filter(child=>child!==this);}
 replaceChildren(...children){this.children=children;}
 fire(name){return this.listeners[name]();}
}
const ids=new Map(), el=id=>{if(!ids.has(id))ids.set(id,new El());return ids.get(id);};
for(const key of ['rate','years','monthly','inflation']){
 el(`plan-${key}`).min=key==='rate'?'-99':key==='inflation'?'-20':'0';
 el(`plan-${key}`).max=key==='monthly'?'1000000000':key==='years'?'60':'100';
}
const forbidden=()=>{throw Error('Storage forbidden');};
for(const key of ['localStorage','sessionStorage','indexedDB'])Object.defineProperty(global,key,{get:forbidden});
global.document={documentElement:new El(),getElementById:el,createElement:()=>new El(),createDocumentFragment:()=>new El(),addEventListener:(_name,fn)=>fn()};
global.getComputedStyle=()=>({getPropertyValue:()=>''});global.MutationObserver=class{observe(){}};
const events={}, charts=[];
global.Chart=class {constructor(_el,config){this.data=config.data;charts.push(this);}update(){}destroy(){this.destroyed=true;}};
global.window={Chart,addEventListener:(name,fn)=>events[name]=fn};
global.InvestmentProjection=require('../mypocket/static/projection.js');
let requests=0,pending=[];
global.fetch=(url,options)=>{requests++;assert.equal(url,'/api/planning/accounts');assert.equal(options.cache,'no-store');assert.equal(options.credentials,'same-origin');assert.equal(options.method,'GET');assert(!options.body);return new Promise(resolve=>pending.push(resolve));};
const ready={status:'ready',starting_value:10000,account_count:2,missing_count:0,last_synced_start:'2026-10-20T12:00:00+00:00',last_synced_end:'2026-10-20T12:00:00+00:00',warnings:[]};
const finish=async data=>{pending.shift()({ok:true,json:async()=>data});await new Promise(resolve=>setImmediate(resolve));};
const enter=(key,value)=>{el(`plan-${key}`).value=String(value);el(`plan-${key}`).fire('input');};
require('../mypocket/static/planning.js');
(async()=>{
 assert.equal(requests,1);assert.equal(el('plan-initial').textContent,'—');assert(el('plan-output').hidden);
 await finish(ready);assert.equal(el('plan-initial').textContent,'$10,000');assert(el('plan-output').hidden);assert.equal(el('plan-rate').value,'');
 enter('rate',10);enter('years',1);enter('inflation',0);assert.equal(el('plan-end').textContent,'$11,000');
 enter('rate',0);enter('monthly',100);assert.equal(el('plan-end').textContent,'$11,200');assert.equal(el('plan-growth').textContent,'$0');
 enter('monthly','');assert.equal(el('plan-end').textContent,'$10,000');
 enter('rate',-10);assert.equal(el('plan-end').textContent,'$9,000');enter('years',0);assert.equal(el('plan-end').textContent,'$10,000');
 enter('years',1.5);assert(el('plan-output').hidden);enter('years',1);
 el('plan-options').open=false;enter('monthly',-1);assert(el('plan-options').open);assert(el('plan-output').hidden);enter('monthly',0);
 // Contribution edits are local and all output representations use the schedule.
 enter('rate',0);enter('years',3);enter('monthly',100);enter('inflation',0);
 const requestCount=requests;
 const descendants=node=>[node,...node.children.flatMap(descendants)];
 const input=id=>descendants(el('plan-changes')).find(node=>node.id===id);
 const change=(id,key,value)=>{const node=input(`plan-change-${id}-${key}`);node.value=String(value);node.fire('input');};
 el('plan-add-change').fire('click');
 assert(input('plan-change-1-year').focused);
 change(1,'monthly',0); // year 2 pause
 el('plan-add-change').fire('click');change(2,'monthly',50); // year 3 restart
 assert.equal(el('plan-end').textContent,'$11,800');assert.equal(el('plan-contributed').textContent,'$11,800');
 assert.equal(charts.at(-1).data.datasets[0].data.at(-1),11800);
 assert.equal(charts.at(-1).data.datasets[1].data.at(-1),11800);
 assert.equal(el('plan-yearly').children[0].children.at(-1).children[2].textContent,'$11,800');
 enter('inflation',10);assert.equal(el('plan-real').textContent,'$8,866');
 change(2,'year',1);change(2,'monthly',25); // out-of-order editor rows still apply by date
 assert.equal(el('plan-end').textContent,'$10,300');
 assert(el('plan-schedule-summary').textContent.indexOf('Year 1')<el('plan-schedule-summary').textContent.indexOf('Year 2'));
 el('plan-options').open=false;change(2,'year',2);assert(el('plan-output').hidden);assert(el('plan-options').open);
 assert.equal(input('plan-change-1-month').attrs['aria-invalid'],'true');
 change(2,'year',3);change(2,'month',0);assert(el('plan-output').hidden);
 change(2,'month',1);change(2,'monthly','');assert(el('plan-output').hidden);
 change(2,'monthly',-1);assert(el('plan-output').hidden);change(2,'monthly',50);
 el('plan-options').open=false;enter('years',1);assert(!el('plan-output').hidden);
 assert.equal(el('plan-end').textContent,'$11,200');assert(!el('plan-schedule-note').hidden);
 assert(el('plan-schedule-note').textContent.includes('2 contribution changes'));
 enter('years',3);assert(el('plan-schedule-note').hidden);
 const rows=el('plan-changes').children;
 const remove=descendants(rows[1]).find(node=>node.attrs['aria-label']==='Remove contribution change 2');
 remove.fire('click');assert.equal(el('plan-end').textContent,'$11,200');assert(el('plan-add-change').focused);
 assert.equal(requests,requestCount);
 enter('years',1);enter('monthly',0);enter('inflation',0);
 let reload=el('plan-reload').fire('click');assert.equal(el('plan-initial').textContent,'—');assert(el('plan-output').hidden);
 await finish({...ready,status:'missing',starting_value:null,missing_count:1});await reload;assert(el('plan-output').hidden);assert.equal(el('plan-initial').textContent,'—');
 reload=el('plan-reload').fire('click');await finish({...ready,starting_value:0});await reload;assert.equal(el('plan-end').textContent,'$0');
 reload=el('plan-reload').fire('click');events.pagehide();await finish(ready);await reload;
 assert.equal(el('plan-initial').textContent,'—');assert.equal(el('plan-rate').value,'');assert(el('plan-output').hidden);
 assert.equal(el('plan-changes').children.length,0);assert.equal(el('plan-schedule-summary').textContent,'');assert.equal(el('plan-schedule-note').textContent,'');
 assert(charts.every(c=>c.destroyed && c.data.datasets.length===0)); // No chart retains financial values after exit.
 events.pageshow({persisted:true});await finish({...ready,status:'empty',starting_value:null,account_count:0});assert(el('plan-data-status').textContent.includes('No active'));
 reload=el('plan-reload').fire('click');pending.shift()({ok:false,status:401});await reload;
 assert(el('plan-data-status').textContent.includes('Sign in'));assert.equal(el('plan-initial').textContent,'—');assert(!el('plan-reload').disabled);
})().catch(error=>{process.stderr.write(error.stack);process.exitCode=1;});
