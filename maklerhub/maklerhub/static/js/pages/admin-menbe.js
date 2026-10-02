document.querySelectorAll('.spark').forEach(spark=>{
  const vals=spark.dataset.vals.split(',').map(Number);
  const max=Math.max(...vals)||1;
  vals.forEach((v,i)=>{
    const bar=document.createElement('div');
    bar.style.height='0%';
    spark.appendChild(bar);
    setTimeout(()=>{ bar.style.transition='height .7s cubic-bezier(.2,.7,.2,1)'; bar.style.height=(v/max*100)+'%'; }, 200+i*35);
  });
});

(function(){
  const modal=document.getElementById('srcModal'),form=document.getElementById('srcForm');
  document.querySelectorAll('.srcbig').forEach(card=>{
    const d=JSON.parse(card.dataset.json);
    card.querySelector('[data-sync]').addEventListener('click',async e=>{
      const b=e.currentTarget;MH.busy(b,true,'Sinxronlaşdırılır...');
      try{const j=await MH.api('/admin/api/sources/'+d.key+'/sync',{});MH.reload(j.message);}
      catch(er){MH.toast(er.message,'err');MH.busy(b,false);}
    });
    card.querySelector('[data-settings]').addEventListener('click',()=>{
      form.dataset.key=d.key;
      form.querySelector('[data-title]').textContent=d.name+' — tənzimləmələr';
      form.querySelector('[name=enabled]').checked=!!d.enabled;
      form.querySelector('[name=base_url]').value=d.base_url||'';
      form.querySelector('[name=rate]').value=(d.rate||window.MH_RATE)+' sorğu/dəq'+(d.rate?' (yavaşlıq səbəbindən azaldılıb)':'');
      MH.openModal(modal);
    });
  });
  form.addEventListener('submit',async e=>{
    e.preventDefault();const btn=form.querySelector('[type=submit]');MH.busy(btn,true);
    try{
      const j=await MH.api('/admin/api/sources/'+form.dataset.key,{enabled:form.querySelector('[name=enabled]').checked,
        base_url:form.querySelector('[name=base_url]').value});
      MH.reload(j.message);
    }catch(er){MH.formError(form,er.message);MH.busy(btn,false);}
  });
})();
