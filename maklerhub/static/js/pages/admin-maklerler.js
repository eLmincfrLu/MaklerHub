(function(){
  let filter='all',q='';
  const rows=[...document.querySelectorAll('tbody tr')];
  const empty=document.getElementById('empty');
  function apply(){
    let n=0;
    rows.forEach(tr=>{const ok=(filter==='all'||tr.dataset.status===filter)&&(!q||tr.dataset.q.includes(q));tr.style.display=ok?'':'none';if(ok)n++;});
    empty.style.display=n?'none':'block';
  }
  document.querySelectorAll('.filters .fchip').forEach(c=>c.addEventListener('click',()=>{
    document.querySelectorAll('.filters .fchip').forEach(x=>x.classList.remove('active'));
    c.classList.add('active');filter=c.dataset.f;apply();
  }));
  document.getElementById('q').addEventListener('input',e=>{q=e.target.value.trim().toLocaleLowerCase('az');apply();});

  const pwModal=document.getElementById('pwModal');
  function showPw(email,pw,reload){
    pwModal.querySelector('[data-who]').textContent=email+' üçün şifrə:';
    pwModal.querySelector('[data-pw]').textContent=pw;
    MH.openModal(pwModal);
    if(reload)pwModal.addEventListener('transitionend',()=>{},{once:true});
    pwModal._reload=reload;
  }
  pwModal.querySelector('[data-copy-pw]').addEventListener('click',async()=>{
    const t=pwModal.querySelector('[data-who]').textContent+' '+pwModal.querySelector('[data-pw]').textContent;
    try{await navigator.clipboard.writeText(t);}catch(e){}
    MH.toast('Kopyalandı');
  });
  new MutationObserver(()=>{if(!pwModal.classList.contains('show')&&pwModal._reload){pwModal._reload=false;location.reload();}})
    .observe(pwModal,{attributes:true,attributeFilter:['class']});

  const addModal=document.getElementById('addModal'),form=document.getElementById('addForm');
  document.getElementById('addBtn').addEventListener('click',()=>{form.reset();MH.openModal(addModal);});
  form.addEventListener('submit',async e=>{
    e.preventDefault();const btn=form.querySelector('[type=submit]');MH.busy(btn,true);
    try{
      const j=await MH.api('/admin/api/users',MH.formData(form));
      MH.closeModal(addModal);MH.toast(j.message);showPw(j.email,j.password,true);
    }catch(er){MH.formError(form,er.message);}finally{MH.busy(btn,false);}
  });

  async function act(url,body,confirmText){
    if(confirmText&&!MH.confirm(confirmText))return null;
    try{const j=await MH.api(url,body||{});return j;}catch(e){MH.toast(e.message,'err');return null;}
  }
  rows.forEach(tr=>{
    const d=JSON.parse(tr.dataset.json),base='/admin/api/users/'+d.id;
    tr.querySelector('.mmenu').addEventListener('click',e=>{
      e.stopPropagation();
      MH.menu(e.currentTarget,[
        d.status==='suspended'
          ?{label:'Aktivləşdir',onClick:async()=>{const j=await act(base+'/status',{status:'active'});if(j)MH.reload(j.message);}}
          :{label:'Dayandır',onClick:async()=>{const j=await act(base+'/status',{status:'suspended'},d.name+' dayandırılsın? Hesaba giriş bağlanacaq.');if(j)MH.reload(j.message);}},
        d.plan!=='pro'?{label:'Plan: Pro et',onClick:async()=>{const j=await act(base+'/plan',{plan:'pro'});if(j)MH.reload(j.message);}}:null,
        d.plan!=='start'?{label:'Plan: Başlanğıc et',onClick:async()=>{const j=await act(base+'/plan',{plan:'start'});if(j)MH.reload(j.message);}}:null,
        {label:'Sınaq müddətini 7 gün uzat',onClick:async()=>{const j=await act(base+'/extend');if(j)MH.reload(j.message);}},
        {label:'Şifrəni sıfırla',onClick:async()=>{const j=await act(base+'/reset-password',{},d.name+' üçün yeni müvəqqəti şifrə yaradılsın?');if(j)showPw(j.email,j.password,false);}},
        {sep:true},
        {label:'Hesabı sil',danger:true,onClick:async()=>{const j=await act(base+'/delete',{},d.name+' və bütün məlumatları (müştərilər, profillər) həmişəlik silinsin?');if(j)MH.reload(j.message);}}
      ]);
    });
  });
})();
