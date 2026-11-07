/* Müştəri tələbi: rayon limiti, büdcə vahidi, qeyd sayğacı, linki kopyala */
(function(){
  const form=document.getElementById('telebForm');
  if(form){
    const max=parseInt(form.dataset.max||'3',10);
    const boxes=[...form.querySelectorAll('input[name=districts]')];
    const count=document.getElementById('distCount');
    function syncDistricts(){
      const n=boxes.filter(b=>b.checked).length;
      boxes.forEach(b=>{if(!b.checked)b.disabled=n>=max;});
      if(count)count.textContent=n+'/'+max;
    }
    boxes.forEach(b=>b.addEventListener('change',syncDistricts));
    syncDistricts();

    const units=form.querySelectorAll('[data-unit]');
    const lo=form.querySelector('[name=budget_min]'),hi=form.querySelector('[name=budget_max]');
    function syncDeal(){
      const d=(form.querySelector('input[name=deal]:checked')||{}).value;
      units.forEach(u=>u.textContent=d==='sale'?'AZN':'AZN/ay');
      if(lo)lo.placeholder=d==='sale'?'məs. 80 000':'məs. 500';
      if(hi)hi.placeholder=d==='sale'?'məs. 150 000':'məs. 900';
    }
    form.querySelectorAll('input[name=deal]').forEach(r=>r.addEventListener('change',syncDeal));
    syncDeal();

    const note=form.querySelector('[name=note]'),nc=document.getElementById('noteCount');
    if(note&&nc){const upd=()=>nc.textContent=note.value.length+'/'+note.maxLength;note.addEventListener('input',upd);upd();}

    form.addEventListener('submit',()=>{const b=form.querySelector('button[type=submit]');if(b){b.disabled=true;b.textContent='Göndərilir...';}});
  }

  const copy=document.getElementById('copyLink');
  if(copy){
    copy.addEventListener('click',async()=>{
      const inp=document.getElementById('myLink');
      try{await navigator.clipboard.writeText(inp.value);}catch(e){inp.select();document.execCommand('copy');}
      const t=copy.textContent;copy.textContent='Kopyalandı ✓';setTimeout(()=>copy.textContent=t,1800);
    });
  }
})();
