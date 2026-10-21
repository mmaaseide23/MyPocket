/* Mode state only. Never store account data or send it outside this app. */
(function(){
  'use strict';
  const root=document.documentElement, rendered=root.dataset.demoMode==='1';
  const cookieMode=()=>document.cookie.split(';').some(c=>c.trim()==='mp-demo=1');
  const nativeFetch=window.fetch.bind(window), requests=new Set();
  let switching=false;
  function conceal(){
    root.classList.add('mode-pending');
    requests.forEach(controller=>controller.abort());requests.clear();
    if(window.Chart)Object.values(Chart.instances).forEach(chart=>chart.destroy());
    if(document.body)document.body.replaceChildren();
  }
  function reload(){conceal();location.replace(location.pathname+location.search);}
  // Runs before body parsing and on BFCache restoration: stale pages stay hidden.
  if(rendered!==cookieMode()){root.classList.add('mode-pending');location.replace('/');}
  window.addEventListener('pagehide',conceal);
  window.addEventListener('pageshow',event=>{if(event.persisted||root.classList.contains('mode-pending'))reload();});
  window.addEventListener('storage',event=>{
    if(event.key==='mp-mode-state'){
      if(event.newValue==='pending')conceal();else reload();
    }
  });
  window.addEventListener('focus',()=>{if(rendered!==cookieMode())reload();});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&rendered!==cookieMode())reload();});
  window.fetch=async function(input,options={}){
    const url=new URL(input instanceof Request?input.url:String(input),location.href);
    if(url.origin!==location.origin)return nativeFetch(input,options);
    if(switching||rendered!==cookieMode()){conceal();throw new DOMException('Mode changed','AbortError');}
    const controller=new AbortController(), headers=new Headers(options.headers||(input instanceof Request?input.headers:undefined));
    headers.set('X-MyPocket-Mode',rendered?'demo':'live');requests.add(controller);
    const signal=options.signal||(input instanceof Request?input.signal:undefined);
    const abort=()=>controller.abort();if(signal){if(signal.aborted)abort();else signal.addEventListener('abort',abort,{once:true});}
    try{
      const response=await nativeFetch(input,{...options,headers,signal:controller.signal,cache:'no-store'});
      if(switching||rendered!==cookieMode()||(response.headers.get('X-MyPocket-Mode')&&response.headers.get('X-MyPocket-Mode')!==(rendered?'demo':'live'))){conceal();throw new DOMException('Mode changed','AbortError');}
      return response;
    }finally{requests.delete(controller);if(signal)signal.removeEventListener('abort',abort);}
  };
  document.addEventListener('DOMContentLoaded',()=>{
    const toggle=document.getElementById('demo-toggle');if(!toggle)return;
    toggle.addEventListener('click',async()=>{
      if(switching)return;
      if(rendered&&!confirm('Leave the fictional demo? You will need to unlock MyPocket before your real information is shown.'))return;
      switching=true;conceal();
      try{localStorage.setItem('mp-mode-state','pending');}catch(e){}
      try{
        const response=await nativeFetch(rendered?'/demo/exit':'/demo/enter',{method:'POST',credentials:'same-origin',headers:{'X-MyPocket-Request':'1'},cache:'no-store'});
        if(!response.ok)throw new Error('Mode change failed');
        const result=await response.json();
        try{localStorage.setItem('mp-mode-state',String(Date.now()));}catch(e){}
        location.replace(result.next==='/login'?'/login':'/');
      }catch(e){
        try{localStorage.setItem('mp-mode-state',String(Date.now()));}catch(e){}
        // Do not reveal the previous financial screen if switching failed.
        location.replace('/login');
      }
    });
  });
})();
