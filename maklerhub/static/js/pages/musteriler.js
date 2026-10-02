(function(){
  let filter='all',q='';
  const rows=[...document.querySelectorAll('tbody tr')];
  const empty=document.getElementById('empty');
  function apply(){
    let shown=0;
    rows.forEach(tr=>{
      const ok=(filter==='all'||tr.dataset.status===filter)&&(!q||tr.dataset.q.includes(q));
      tr.style.display=ok?'':'none';if(ok)shown++;
    });
    if(rows.length)empty.style.display=shown?'none':'block';
  }
  document.querySelectorAll('.filters .fchip').forEach(chip=>{
    chip.addEventListener('click',()=>{
      document.querySelectorAll('.filters .fchip').forEach(c=>c.classList.remove('active'));
      chip.classList.add('active');filter=chip.dataset.f;apply();
    });
  });
  document.getElementById('q').addEventListener('input',e=>{q=e.target.value.trim().toLocaleLowerCase('az');apply();});

  // Yeni / redaktə
  const modal=document.getElementById('clientModal'),form=document.getElementById('clientForm');
  function openForm(data){
    form.reset();MH.fill(form,data||{id:''});
    form.querySelector('[data-title]').textContent=data?'Müştərini redaktə et':'Yeni müştəri';
    MH.openModal(modal);
  }
  document.getElementById('addClient').addEventListener('click',()=>openForm(null));
  form.addEventListener('submit',async e=>{
    e.preventDefault();
    const d=MH.formData(form),btn=form.querySelector('[type=submit]');
    MH.busy(btn,true);
    try{
      const j=await MH.api(d.id?'/api/clients/'+d.id:'/api/clients',d);
      MH.reload(j.message);
    }catch(er){MH.formError(form,er.message);}finally{MH.busy(btn,false);}
  });

  async function act(url,body,confirmText){
    if(confirmText&&!MH.confirm(confirmText))return;
    try{const j=await MH.api(url,body||{});MH.reload(j.message);}catch(e){MH.toast(e.message,'err');}
  }

  rows.forEach(tr=>{
    const d=JSON.parse(tr.dataset.json);
    tr.querySelector('.rowmenu').addEventListener('click',e=>{
      e.stopPropagation();
      MH.menu(e.currentTarget,[
        {label:'Əlaqə saxlanıldı',onClick:()=>act('/api/clients/'+d.id+'/touch')},
        {label:'Redaktə et',onClick:()=>openForm(d)},
        d.profile_id&&d.status==='active'?{label:'Uyğun elanlara bax',onClick:()=>location.href=window.MH_ELANLAR_URL+'?profile='+d.profile_id}:null,
        !d.profile_id?{label:'Axtarış profili yarat',onClick:()=>location.href=window.MH_PROFILES_URL+'?new=1&client='+d.id}:null,
        {sep:true},
        d.status!=='deal'?{label:'Sövdə bağlandı',onClick:()=>act('/api/clients/'+d.id+'/status',{status:'deal'})}:null,
        d.status!=='active'?{label:'Aktiv axtarışa qaytar',onClick:()=>act('/api/clients/'+d.id+'/status',{status:'active'})}:null,
        d.status!=='archived'?{label:'Arxivləşdir',onClick:()=>act('/api/clients/'+d.id+'/status',{status:'archived'})}:null,
        {sep:true},
        {label:'Sil',danger:true,onClick:()=>act('/api/clients/'+d.id+'/delete',{},d.name+' və onun axtarış profilləri silinsin?')}
      ]);
    });
  });
})();
