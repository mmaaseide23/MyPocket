import shutil
import subprocess
from pathlib import Path


def test_plaid_ui_requires_preview_and_confirmation_and_escapes_provider_text():
    node = shutil.which("node")
    assert node, "Node is required to validate the UI flow"
    path = Path(__file__).parents[1] / "mypocket/static/plaid.js"
    assert path.exists(), "Plaid UI flow is not implemented"
    harness = r"""
const assert=require('node:assert/strict');
class El {
 constructor(tag='div') {this.tag=tag;this.children=[];this.dataset={};this.listeners={};this.hidden=false;this.disabled=false;this.checked=false;this.value='';this.textContent='';}
 append(...els){this.children.push(...els);}
 replaceChildren(...els){this.children=[...els];}
 addEventListener(name,cb){this.listeners[name]=cb;}
 async fire(name){if(this.listeners[name]) await this.listeners[name]({currentTarget:this});}
}
const ids=new Map();
for (const id of ['plaidApp','plaidStatus','plaidReview','plaidMappings','plaidPreview','plaidPlan','plaidPlanRows','plaidWarning','plaidConfirm','plaidApprove']) ids.set(id,new El());
ids.get('plaidApp').dataset.environment='production';
const linkButton=new El('button');linkButton.dataset.link='';
const reviewButton=new El('button');reviewButton.dataset.review='7';
const syncButton=new El('button');syncButton.dataset.sync='7';
global.document={getElementById:id=>ids.get(id),createElement:tag=>new El(tag),querySelectorAll:selector=>({'[data-link]':[linkButton],'[data-review]':[reviewButton],'[data-sync]':[syncButton]}[selector]||[])};
let linkConfig;let reloads=0;const calls=[];let blocked=false;
global.window={location:{reload(){reloads++;}}};
global.Plaid={create(config){linkConfig=config;return {open(){},destroy(){}};}};
global.fetch=async (url,opts)=>{
 assert.equal(opts.headers['X-MyPocket-Request'],'1');
 const body=JSON.parse(opts.body);calls.push({url,body});
 let data;
 if(url.endsWith('/link_token'))data={flow_id:'flow',link_token:'link',update:false};
 if(url.endsWith('/exchange'))data={enrollment_id:7,status:'pending_review'};
 if(url.endsWith('/review'))data={review_id:'review',accounts:[{external_id:'remote',name:'<img onerror=boom>',mask:'0000',type:'checking',balance:20}],existing_accounts:[{id:1,name:'Existing Citi',type:'checking',last_date:'2026-07-02'}]};
 if(url.endsWith('/preview'))data={plan_id:blocked?null:'plan',blocked,warning:'Review dates',accounts:[{name:'Existing Citi',mode:body.selections[0].mode,import_from:'2026-07-03',preserved:3,history_skipped:3,transactions_to_import:1,pending:0,balance:20,archived:3,received_from:'2026-01-02',received_through:'2026-07-03',legacy_pending:blocked?1:0,legacy_pending_from:'2026-07-02',legacy_pending_through:'2026-07-02'}]};
 if(url.endsWith('/approve'))data={accounts:1,transactions_created:1,errors:[]};
 if(url.endsWith('/sync'))data={errors:['Reconnect required']};
 return {ok:true,json:async()=>data};
};
"""
    assertions = r"""
(async()=>{
 await linkButton.fire('click');
 assert.equal(linkConfig.token,'link');
 await linkConfig.onSuccess('public-token');
 assert(!calls.some(x=>x.url.endsWith('/approve')));
 assert.equal(reloads,1);
 await reviewButton.fire('click');
 const container=ids.get('plaidMappings');
 const select=container.children[0].children.find(x=>x.tag==='select');
 assert(select && select.value==='');
 assert(container.children[0].children.some(x=>x.textContent.includes('<img onerror=boom>')));
 await ids.get('plaidPreview').fire('click');
 assert(!calls.some(x=>x.url.endsWith('/preview')));
 select.value='existing:1';await select.fire('change');
 await ids.get('plaidPreview').fire('click');
 assert(calls.some(x=>x.url.endsWith('/preview')));
 await ids.get('plaidApprove').fire('click');
 assert(!calls.some(x=>x.url.endsWith('/approve')));
 ids.get('plaidConfirm').checked=true;await ids.get('plaidConfirm').fire('change');
 await ids.get('plaidApprove').fire('click');
 assert.equal(calls.filter(x=>x.url.endsWith('/approve')).length,1);
 assert(calls.find(x=>x.url.endsWith('/approve')).body.confirmed);
 blocked=true;
 await ids.get('plaidPreview').fire('click');
 assert(!ids.get('plaidPlan').hidden);
 assert(ids.get('plaidPlanRows').children.some(x=>x.textContent.includes('1 unresolved legacy pending')));
 assert(ids.get('plaidConfirm').disabled && ids.get('plaidApprove').disabled);
 ids.get('plaidConfirm').checked=true;await ids.get('plaidConfirm').fire('change');
 await ids.get('plaidApprove').fire('click');
 assert(ids.get('plaidApprove').disabled);
 assert.equal(calls.filter(x=>x.url.endsWith('/approve')).length,1);
 blocked=false;
 await ids.get('plaidPreview').fire('click');
 assert(!ids.get('plaidConfirm').disabled && !ids.get('plaidConfirm').checked);
 assert(select.children.some(o=>o.value==='replace:1'));
 select.value='replace:1';await select.fire('change');
 await ids.get('plaidPreview').fire('click');
 assert.equal(calls.filter(x=>x.url.endsWith('/preview')).at(-1).body.selections[0].mode,'replace');
 assert(ids.get('plaidPlanRows').children.some(x=>x.textContent.includes('3 old transactions')));
 assert(!ids.get('plaidConfirm').checked);
 await syncButton.fire('click');
 assert.match(ids.get('plaidStatus').textContent,/Reconnect required/);
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
    result = subprocess.run(
        [node], input=harness + path.read_text() + assertions, text=True, capture_output=True
    )
    assert result.returncode == 0, result.stderr
