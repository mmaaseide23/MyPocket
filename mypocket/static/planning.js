/* Local account-total projection. No plan storage, external requests, or payload logging. */
(function () {
  'use strict';
  document.addEventListener('DOMContentLoaded', function () {
    const app = document.getElementById('planningApp');
    if (!app) return;
    const el = id => document.getElementById(`plan-${id}`);
    const defaults = {rate:'',years:20,monthly:0,inflation:2.5};
    const fields = Object.fromEntries(Object.keys(defaults).map(key => [key,el(key)]));
    const money = value => new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:0}).format(value);
    const contributionMoney = value => new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumSignificantDigits:15}).format(value);
    const compact = value => new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',notation:'compact',maximumFractionDigits:1}).format(value);
    const pct = value => new Intl.NumberFormat('en-US',{style:'percent',maximumSignificantDigits:6}).format(value/100);
    const date = value => new Date(value).toLocaleString(undefined,{year:'numeric',month:'short',day:'numeric',hour:'numeric',minute:'2-digit',timeZoneName:'short'});
    const warnings = {
      duplicate_accounts:'Duplicate account links were detected; the most recently synced copy is counted once.',
      csv_accounts:'CSV-only investment accounts are excluded to avoid counting old imported copies.',
      non_usd:'Non-USD investment accounts are excluded. No currency conversion is assumed.',
      unknown_sync_date:'Some account totals have no recorded sync time; their freshness is unknown.',
      stale_totals:'Some account totals were last synced more than 24 hours ago. This projection uses those saved values.',
      future_sync_date:'Some saved sync times are in the future. Their freshness cannot be confirmed; check the app’s clock.',
    };
    let saved = null, chart = null, request = null, generation = 0;
    let changeRows = [], nextChangeId = 0;
    function clearSaved() {
      saved = null;
      el('initial').textContent = '—'; el('sync-dates').textContent = '';
      el('account-warnings').replaceChildren(); el('yearly').replaceChildren();
      ['end','contributed','growth','real','base-caption','schedule-note'].forEach(id=>{el(id).textContent='';});
      el('output').hidden = true;
      if(chart){chart.data.labels=[];chart.data.datasets=[];chart.destroy();chart=null;}
    }
    function cancel() {
      generation++; if(request)request.abort(); request=null;
      clearSaved(); el('summary').setAttribute('aria-busy','false'); el('reload').disabled=false;
      el('data-status').textContent='Saved totals cleared.';
      el('status').className='plan-status';el('status').textContent='Waiting for saved account totals…';
    }
    function resetAssumptions() {
      Object.entries(defaults).forEach(([key,value])=>{
        fields[key].value=value;fields[key].setAttribute('aria-invalid','false');
        el(`${key}-error`).textContent='';el(`${key}-error`).hidden=true;
      });
      clearChanges();
      el('slider').value=defaults.years;
      ['options','details','method'].forEach(id=>{el(id).open=false;});
    }
    function clearChanges() {
      changeRows.forEach(row=>Object.values(row.fields).forEach(input=>{input.value='';}));
      changeRows=[];nextChangeId=0;el('changes').replaceChildren();
      el('schedule-summary').textContent='';el('schedule-note').textContent='';el('schedule-note').hidden=true;
      el('add-change').disabled=false;
    }
    function addChange() {
      const used=new Set(changeRows.map(row=>(row.fields.year.valueAsNumber-1)*12+row.fields.month.valueAsNumber));
      let start=fields.years.valueAsNumber>=2?13:fields.years.valueAsNumber>=1?2:1;
      while(used.has(start)&&start<=720)start+=12;
      if(start>720)start=Array.from({length:720},(_,i)=>i+1).find(month=>!used.has(month));
      if(!start)return;
      const id=++nextChangeId, node=document.createElement('fieldset'), legend=document.createElement('legend');
      node.className='plan-change-row';legend.textContent=`Change ${id}`;node.append(legend);
      const grid=document.createElement('div');grid.className='plan-change-fields';node.append(grid);
      const row={node,fields:{},error:document.createElement('p'),note:document.createElement('p')};
      row.error.id=`plan-change-${id}-error`;row.error.className='plan-field-error';row.error.hidden=true;
      row.note.id=`plan-change-${id}-note`;row.note.className='plan-help';row.note.hidden=true;
      for(const [key,labelText,min,max,value] of [
        ['year','Projection year',1,60,Math.floor((start-1)/12)+1],
        ['month','Month in that year',1,12,(start-1)%12+1],
        ['monthly','New monthly amount ($)',0,1e9,fields.monthly.value||0],
      ]) {
        const field=document.createElement('div'),label=document.createElement('label'),unit=document.createElement('div'),input=document.createElement('input');
        if(key==='monthly')field.className='plan-change-amount';
        input.id=`plan-change-${id}-${key}`;input.type='number';input.inputMode=key==='monthly'?'decimal':'numeric';
        input.min=String(min);input.max=String(max);input.step=key==='monthly'?'any':'1';input.value=String(value);input.autocomplete='off';input.required=true;
        input.setAttribute('aria-describedby',`plan-changes-help ${row.error.id} ${row.note.id}`);
        label.htmlFor=input.id;label.textContent=labelText;unit.className='plan-unit';unit.append(input);field.append(label,unit);grid.append(field);
        row.fields[key]=input;input.addEventListener('input',render);
      }
      const remove=document.createElement('button');remove.type='button';remove.className='plan-change-button';remove.textContent='Remove';
      remove.setAttribute('aria-label',`Remove contribution change ${id}`);
      remove.addEventListener('click',()=>{
        Object.values(row.fields).forEach(input=>{input.value='';});
        changeRows=changeRows.filter(item=>item!==row);node.remove();el('add-change').disabled=false;render();el('add-change').focus();
      });
      node.append(row.error,row.note,remove);changeRows.push(row);el('changes').append(node);
      el('add-change').disabled=changeRows.length>=720;render();row.fields.year.focus();
    }
    function readChanges(years,monthly) {
      const entries=[];let invalid=false;
      changeRows.forEach(row=>{
        let error='';const values={};
        for(const [key,input] of Object.entries(row.fields)) {
          const value=input.valueAsNumber;
          const bad=!Number.isFinite(value)||value<Number(input.min)||value>Number(input.max)||(key!=='monthly'&&!Number.isInteger(value));
          input.setAttribute('aria-invalid',String(bad));values[key]=value;
          if(bad&&!error)error=key==='monthly'?'Enter a monthly amount from $0 to $1 billion.':key==='year'?'Use a whole projection year from 1 to 60.':'Use a whole month from 1 to 12.';
        }
        row.error.textContent=error;row.error.hidden=!error;row.note.hidden=true;row.note.textContent='';
        if(error)invalid=true;
        else entries.push({startMonth:(values.year-1)*12+values.month,monthly:values.monthly,row});
      });
      entries.sort((a,b)=>a.startMonth-b.startMonth);
      const counts=new Map();entries.forEach(change=>counts.set(change.startMonth,(counts.get(change.startMonth)||0)+1));
      entries.forEach(change=>{
        if(counts.get(change.startMonth)>1) {
          invalid=true;change.row.error.textContent='Another change starts in this month. Keep one amount per start month.';change.row.error.hidden=false;
          ['year','month'].forEach(key=>change.row.fields[key].setAttribute('aria-invalid','true'));
        }else if(Number.isInteger(years)&&change.startMonth>years*12) {
          change.row.note.textContent='Beyond the selected horizon — excluded from this projection.';change.row.note.hidden=false;
        }
      });
      el('schedule-summary').textContent='';
      if(invalid)el('options').open=true;
      else if(entries.length&&Number.isFinite(monthly)) {
        const parts=entries[0].startMonth===1?[]:[`Start: ${contributionMoney(monthly)}/month.`];
        entries.forEach(change=>parts.push(`Year ${Math.floor((change.startMonth-1)/12)+1}, month ${(change.startMonth-1)%12+1}: ${contributionMoney(change.monthly)}/month${change.startMonth>years*12?' (excluded beyond horizon)':''}.`));
        el('schedule-summary').textContent=parts.join(' ');
      }
      return {invalid,changes:entries.map(({startMonth,monthly})=>({startMonth,monthly}))};
    }
    function renderChart(result) {
      if(!window.Chart){el('chart-fallback').hidden=false;el('chart').hidden=true;return;}
      const style=getComputedStyle(app), muted=getComputedStyle(document.documentElement).getPropertyValue('--color-zinc-400').trim();
      const data={labels:result.points.map(p=>p.year),datasets:[
        {label:'Projected total',data:result.points.map(p=>p.value),borderColor:style.getPropertyValue('--plan-base').trim(),borderWidth:3,pointRadius:result.points.length===1?3:0,pointHitRadius:15,tension:0},
        {label:'Starting value + deposits',data:result.points.map(p=>p.contributed),borderColor:muted,borderDash:[9,5],borderWidth:1.5,pointRadius:0,pointHitRadius:15,tension:0},
      ]};
      if(chart){chart.data=data;chart.update('none');return;}
      chart=new Chart(el('chart'),{type:'line',data,options:{responsive:true,maintainAspectRatio:false,animation:false,
        interaction:{intersect:false,mode:'index'},plugins:{legend:{display:false},tooltip:{callbacks:{title:items=>`Year ${items[0].label}`,label:item=>`${item.dataset.label}: ${money(item.parsed.y)}`}}},
        scales:{x:{title:{display:true,text:'Years from now'},grid:{display:false},ticks:{maxTicksLimit:7}},y:{beginAtZero:true,ticks:{callback:compact,maxTicksLimit:6}}},
      }});
      if(window.charts)window.charts._track(chart);
    }
    function render() {
      el('output').hidden=true;el('status').className='plan-status';el('schedule-note').hidden=true;
      if(!saved || saved.status!=='ready')return;
      const values={};let invalid=false;
      Object.entries(fields).forEach(([key,input])=>{
        if(key==='rate' && input.value===''){
          input.setAttribute('aria-invalid','false');el('rate-error').hidden=true;invalid=true;return;
        }
        const value=key==='monthly' && input.value===''?0:input.valueAsNumber;
        let error='';
        if(!Number.isFinite(value))error=key==='rate'?'Enter your assumed average annual return.':'Enter a number.';
        else if(value<Number(input.min)||value>Number(input.max))error=`Use a value from ${Number(input.min).toLocaleString('en-US')} to ${Number(input.max).toLocaleString('en-US')}.`;
        else if(key==='years'&&!Number.isInteger(value))error='Use a whole number of years.';
        values[key]=value;input.setAttribute('aria-invalid',String(Boolean(error)));
        el(`${key}-error`).textContent=error;el(`${key}-error`).hidden=!error;
        if(error && ['monthly','inflation'].includes(key))el('options').open=true;
        invalid=invalid||Boolean(error);
      });
      const schedule=readChanges(values.years,values.monthly);invalid=invalid||schedule.invalid;
      if(invalid){el('status').textContent=fields.rate.value===''?'Enter your assumed average annual return to see your projected total.':'Correct the highlighted assumptions to see your projected total.';return;}
      el('slider').value=values.years;el('slider').setAttribute('aria-valuetext',`${values.years} years`);
      const result=InvestmentProjection.project({initial:saved.starting_value,monthly:values.monthly,years:values.years,annualReturn:values.rate,inflation:values.inflation,contributionChanges:schedule.changes});
      el('horizon-text').textContent=values.years;el('horizon-word').textContent=values.years===1?'year':'years';
      el('end').textContent=money(result.endValue);el('contributed').textContent=money(result.contributed);
      el('growth').textContent=money(result.growth);el('real').textContent=money(result.realValue);
      el('base-caption').textContent=`${pct(values.rate)} assumed annual return${result.appliedChanges?' · scheduled contributions':values.monthly>0?' · '+contributionMoney(values.monthly)+'/month':''}`;
      el('schedule-note').textContent=result.excludedChanges?`${result.excludedChanges} contribution change${result.excludedChanges===1?'':'s'} beyond the horizon ${result.excludedChanges===1?'is':'are'} excluded. Review in More options.`:'';
      el('schedule-note').hidden=!result.excludedChanges;
      const fragment=document.createDocumentFragment();
      result.points.forEach(point=>{
        const row=document.createElement('tr');
        [String(point.year),money(point.contributed),money(point.value)].forEach((value,index)=>{
          const cell=document.createElement(index===0?'th':'td');cell.textContent=value;if(index===0)cell.scope='row';row.append(cell);
        });fragment.append(row);
      });
      el('yearly').replaceChildren(fragment);el('output').hidden=false;renderChart(result);
      el('status').className='plan-status plan-sr';
      el('status').textContent=`${values.years}-year illustration: ${money(result.endValue)}, or ${money(result.realValue)} in today’s dollars. Rounded to the nearest dollar.${result.excludedChanges?' '+el('schedule-note').textContent:''}`;
    }
    async function load() {
      cancel();const current=generation;request=new AbortController();
      el('summary').setAttribute('aria-busy','true');el('reload').disabled=true;
      el('data-status').textContent='Loading saved account totals…';
      try{
        const response=await fetch('/api/planning/accounts',{method:'GET',credentials:'same-origin',cache:'no-store',headers:{'X-MyPocket-Request':'1'},signal:request.signal});
        if(!response.ok)throw new Error(response.status===401?'auth':'unavailable');
        const data=await response.json();if(current!==generation)return;
        saved=data;
        if(Number.isFinite(data.starting_value))el('initial').textContent=money(data.starting_value);
        data.warnings.forEach(code=>{if(warnings[code]){const li=document.createElement('li');li.textContent=warnings[code];el('account-warnings').append(li);}});
        if(data.last_synced_start){
          const start=date(data.last_synced_start), end=date(data.last_synced_end);
          el('sync-dates').textContent=`Last recorded account sync${start!==end?'s':''}: ${start}${start!==end?' to '+end:''}. These are sync times, not live price timestamps.`;
        }
        if(data.status==='ready'&&Number.isFinite(data.starting_value)){
          el('data-status').textContent=`${data.account_count} connected investment account${data.account_count===1?'':'s'} · USD`;
          render();
        }else{
          const text=data.status==='empty'?'No active connected USD investment accounts are available.':data.status==='missing'?`${data.missing_count} of ${data.account_count} investment accounts lack a usable total. Projection paused until all totals are available.`:'The combined total is outside this growth model’s supported range ($0–$1 billion). Projection unavailable.';
          el('data-status').textContent=text;el('status').textContent='Projection unavailable. No example or partial starting value is substituted.';
        }
      }catch(error){
        if(current!==generation||error.name==='AbortError')return;
        clearSaved();el('data-status').textContent=error.message==='auth'?'Sign in to MyPocket, then reload this page.':'Saved account totals could not be loaded. Try Reload saved totals.';
        el('status').textContent='Projection unavailable. No example values are substituted.';
      }finally{if(current===generation){request=null;el('summary').setAttribute('aria-busy','false');el('reload').disabled=false;}}
    }
    Object.values(fields).forEach(input=>input.addEventListener('input',render));
    el('slider').addEventListener('input',()=>{fields.years.value=el('slider').value;render();});
    el('reload').addEventListener('click',load);
    el('add-change').addEventListener('click',addChange);
    window.addEventListener('pagehide',()=>{cancel();resetAssumptions();});
    window.addEventListener('pageshow',event=>{if(event.persisted){resetAssumptions();load();}});
    new MutationObserver(()=>{if(!el('output').hidden)render();}).observe(document.documentElement,{attributes:true,attributeFilter:['class']});
    resetAssumptions();load();
  });
})();
