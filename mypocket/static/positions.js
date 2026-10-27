/* Portfolio allocation only. Holding rows make no requests. */
(() => {
  'use strict';
  const amount=(value,currency)=>{try{return new Intl.NumberFormat(undefined,{style:'currency',currency,maximumFractionDigits:2}).format(value);}catch{return `${currency} ${value.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}`;}};
  const percentage=value=>value>0&&value<.1?'<0.1%':`${value.toFixed(1)}%`;
  const visibleIndices=(rows,isVisible)=>rows.flatMap((row,i)=>isVisible(i)&&Number.isFinite(row.value)&&row.value>0?[i]:[]);
  const share=(rows,index,isVisible)=>{
    const indices=visibleIndices(rows,isVisible);
    const total=indices.reduce((sum,i)=>sum+rows[i].value,0);
    return indices.includes(index)&&Number.isFinite(total)&&total>0?rows[index].value/total*100:null;
  };
  const tooltip=(row,currency,pct)=>`${row.label}: ${amount(row.value,currency)}${Number.isFinite(pct)?` (${percentage(pct)})`:''}`;
  if(typeof module!=='undefined'&&module.exports)module.exports={percentage,visibleIndices,share,tooltip};
  if(typeof document==='undefined')return;
  document.addEventListener('DOMContentLoaded',()=>{
    const active=[], pins=new WeakMap();
    const tapPlugin={id:'allocationTap',beforeEvent:(chart,args)=>{
      if(pins.get(chart)?.()&&args.event.type==='mouseout')return false;
    }};
    Chart.register(tapPlugin);
    document.querySelectorAll('canvas[data-allocation]').forEach(canvas=>{
      const rows=JSON.parse(canvas.dataset.allocation);
      const chart=charts.doughnut(canvas,rows.map(r=>r.label),rows.map(r=>r.value),charts.palette);
      const announcement=canvas.closest('.chart-host').parentElement.querySelector('[data-allocation-announcement]');
      const isVisible=i=>chart.getDataVisibility(i);
      const label=i=>tooltip(rows[i],canvas.dataset.currency,share(rows,i,isVisible));
      let selected=null, pinned=false;
      // A tap has no continuing hover. Preserve its tooltip through the
      // synthetic mouseout emitted by touch browsers, until another action.
      pins.set(chart,()=>pinned);
      const clear=()=>{
        selected=null;pinned=false;
        chart.setActiveElements([]);
        chart.tooltip.setActiveElements([],{x:0,y:0});
        announcement.textContent='';
        chart.update('none');
      };
      const show=index=>{
        selected=index;
        const elements=[{datasetIndex:0,index}];
        chart.setActiveElements(elements);
        chart.tooltip.setActiveElements(elements,chart.getDatasetMeta(0).data[index].getCenterPoint());
        announcement.textContent=`${label(index)}. Share of visible positive holdings; account cash and shorts excluded.`;
        chart.update('none');
      };
      // Keep the original ticker-only canvas legend and its slice toggles.
      chart.options.onClick=(_event,elements)=>{
        if(elements.length){pinned=true;show(elements[0].index);}
        else clear();
      };
      chart.options.onHover=event=>{if(event.type==='mousemove')pinned=false;};
      chart.options.plugins.tooltip.callbacks.label=item=>label(item.dataIndex);
      chart.options.plugins.tooltip.callbacks.footer=()=>['Share of visible positive holdings','Cash and shorts excluded'];
      chart.options.plugins.legend.onClick=(_event,item)=>{
        chart.toggleDataVisibility(item.index);
        clear();
      };
      canvas.addEventListener('focus',()=>{
        const first=visibleIndices(rows,isVisible)[0];
        if(first!==undefined)show(first);
      });
      canvas.addEventListener('blur',clear);
      canvas.addEventListener('keydown',event=>{
        if(event.key==='Escape'){event.preventDefault();clear();return;}
        if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(event.key))return;
        event.preventDefault();
        const indices=visibleIndices(rows,isVisible);
        if(!indices.length)return;
        let position=indices.indexOf(selected);
        if(event.key==='Home')position=0;
        else if(event.key==='End')position=indices.length-1;
        else if(position<0)position=0;
        else position=(position+(['ArrowLeft','ArrowUp'].includes(event.key)?-1:1)+indices.length)%indices.length;
        show(indices[position]);
      });
      chart.update();active.push({chart,announcement});
    });
    window.addEventListener('pagehide',()=>{
      active.forEach(({chart,announcement})=>{announcement.textContent='';chart.destroy();});
      active.length=0;Chart.unregister(tapPlugin);
    },{once:true});
  });
})();
