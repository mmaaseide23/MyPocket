// Actual global Sync handler with fictional responses only, no browser or network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const template = fs.readFileSync('mypocket/templates/base.html','utf8');
const script = '// Sync buttons' + template.split('// ─── Sync buttons')[1].split('</script>')[0];
function harness(result, {ok=true, status=200, delayed=false}={}) {
  const events=[], handlers=[], buttons=[];
  let calls=0, release;
  for(let i=0;i<2;i++) {
    const icon={style:{}},label={textContent:'Latest: before'};
    buttons.push({disabled:false,icon,label,querySelector(selector){return selector.includes('icon')?icon:label;},addEventListener(type,handler){handlers.push(handler);}});
  }
  const context={document:{querySelectorAll:()=>buttons},window:{location:{reload:()=>events.push('reload')}},alert:message=>events.push(message),fetch:async()=>{
    calls++;if(delayed)await new Promise(resolve=>{release=resolve;});
    return {ok,status,json:async()=>result};
  }};
  vm.runInNewContext(script,context);
  return {events,handlers,buttons,get calls(){return calls;},release:()=>release()};
}
(async()=>{
  let h=harness({teller:[],etrade:[{errors:[],accounts:2}],plaid:[{errors:[],accounts:2,transactions_created:0}]});
  await h.handlers[0]();
  assert.match(h.events[0],/Citi \(Plaid\): synced — no transaction changes available/);
  assert.match(h.events[0],/does not force the bank to refresh/);
  assert.equal(h.events[1],'reload');
  assert(h.buttons.every(b=>!b.disabled&&b.icon.style.animation===''&&b.label.textContent==='Latest: before'));
  h=harness({teller:[],etrade:[{errors:[],accounts:2}],plaid:[{status:'needs_reauth',errors:['Plaid requires reconnection.']}]});
  await h.handlers[1]();
  assert.match(h.events[0],/completed with issues/);
  assert.match(h.events[0],/E\*TRADE: synced/);
  assert.match(h.events[0],/Citi \(Plaid\): Plaid requires reconnection/);
  assert(!h.events[0].includes('Citi (Plaid): synced'));
  assert.equal(h.events[1],'reload'); // successful updates refresh even on partial failure
  h=harness({teller:[],etrade:[],plaid:[{errors:['Review required.']}]});
  await h.handlers[0]();assert.equal(h.events.length,1);assert.match(h.events[0],/Review required/);
  h=harness({teller:[],etrade:[{errors:['Provider failed.']}],plaid:[{errors:[],transactions_modified:1,unreviewed_accounts:1}]});
  await h.handlers[0]();assert.match(h.events[0],/transaction updates imported/);assert.match(h.events[0],/existing mapped accounts still synced/);
  h=harness({teller:[],etrade:[],plaid:[]});await h.handlers[0]();assert.match(h.events[0],/Citi \(Plaid\): skipped/);assert.equal(h.events.length,1);
  h=harness({teller:[],etrade:[]});await h.handlers[0]();assert.match(h.events[0],/incomplete sync result/);assert.equal(h.events.length,1);
  h=harness({}, {ok:false,status:500});await h.handlers[0]();assert.match(h.events[0],/Some providers may have completed/);assert(h.buttons.every(b=>!b.disabled));
  h=harness({teller:[],etrade:[],plaid:[{errors:[]}]},{delayed:true});
  const first=h.handlers[0]();await h.handlers[1]();assert.equal(h.calls,1);assert(h.buttons.every(b=>b.disabled));h.release();await first;
})().catch(error=>{console.error(error);process.exitCode=1;});
