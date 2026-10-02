(function(){
  let filter='all',q='';
  const cards=[...document.querySelectorAll('.pcard[data-json]')];
  function apply(){
    cards.forEach(c=>{
      const ok=(filter==='all'||c.dataset.status===filter)&&(!q||c.dataset.q.includes(q));
      c.style.display=ok?'':'none';
    });
  }
  document.querySelectorAll('.fchip').forEach(chip=>{
    chip.addEventListener('click',()=>{
      document.querySelectorAll('.fchip').forEach(c=>c.classList.remove('active'));
      chip.classList.add('active');filter=chip.dataset.f;apply();
    });
  });
  document.getElementById('q').addEventListener('input',e=>{q=e.target.value.trim().toLocaleLowerCase('az');apply();});

  // Forma
  const modal=document.getElementById('profileModal'),form=document.getElementById('profileForm');
  const clientSel=form.querySelector('[name=client_id]'),newBox=form.querySelector('[data-new-client]');
  const clientField=form.querySelector('[data-client-field]');
  function syncClient(){newBox.style.display=clientSel.value==='new'?'':'none';}
  clientSel.addEventListener('change',syncClient);
  function syncDeal(){
    const rent=form.querySelector('[name=deal_type]:checked').value==='rent';
    form.querySelector('[name=price_min]').placeholder=rent?'900':'200000';
    form.querySelector('[name=price_max]').placeholder=rent?'1200':'250000';
    form.querySelector('[data-price-label]').textContent=rent?'Min. qiymət (AZN/ay)':'Min. qiymət (AZN)';
    form.querySelector('[data-price-label2]').textContent=rent?'Maks. qiymət (AZN/ay)':'Maks. qiymət (AZN)';
  }
  form.querySelectorAll('[name=deal_type]').forEach(r=>r.addEventListener('change',syncDeal));

  function openForm(data,clientId){
    form.reset();
    form.querySelector('[name=id]').value=data?data.id:'';
    if(data){
      MH.fill(form,{district:data.district,property_type:data.property_type,rooms:data.rooms,
                    price_min:data.price_min,price_max:data.price_max});
      form.querySelector('[name=deal_type][value='+data.deal_type+']').checked=true;
      clientField.style.display='none';newBox.style.display='none';
      form.querySelector('[data-title]').textContent=data.client+' — profili redaktə et';
    }else{
      clientField.style.display='';
      if(clientId&&clientSel.querySelector('option[value="'+clientId+'"]'))clientSel.value=clientId;
      syncClient();
      form.querySelector('[data-title]').textContent='Yeni axtarış profili';
    }
    syncDeal();MH.openModal(modal);
  }
  document.querySelectorAll('[data-new]').forEach(b=>b.addEventListener('click',()=>openForm(null)));
  form.addEventListener('submit',async e=>{
    e.preventDefault();
    const d=MH.formData(form),btn=form.querySelector('[type=submit]');
    MH.busy(btn,true,'Uyğun elanlar axtarılır...');
    try{
      const j=await MH.api(d.id?'/api/profiles/'+d.id:'/api/profiles',d);
      MH.reload(j.message);
    }catch(er){MH.formError(form,er.message);}finally{MH.busy(btn,false);}
  });

  async function act(url,confirmText){
    if(confirmText&&!MH.confirm(confirmText))return;
    try{const j=await MH.api(url,{});MH.reload(j.message);}catch(e){MH.toast(e.message,'err');}
  }
  cards.forEach(card=>{
    const d=JSON.parse(card.dataset.json);
    card.querySelector('.pmenu').addEventListener('click',e=>{
      e.stopPropagation();
      MH.menu(e.currentTarget,[
        {label:'Uyğun elanlara bax',onClick:()=>location.href=window.MH_ELANLAR_URL+'?profile='+d.id},
        {label:'Redaktə et',onClick:()=>openForm(d)},
        {label:d.status==='active'?'Dayandır':'Aktivləşdir',onClick:()=>act('/api/profiles/'+d.id+'/toggle')},
        {sep:true},
        {label:'Sil',danger:true,onClick:()=>act('/api/profiles/'+d.id+'/delete',d.client+' üçün axtarış profili silinsin?')}
      ]);
    });
  });

  const p=new URLSearchParams(location.search);
  if(p.get('new')){openForm(null,p.get('client'));history.replaceState(null,'',location.pathname);}
})();
