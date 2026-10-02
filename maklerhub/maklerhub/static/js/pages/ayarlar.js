function showTab(t){
  const btn=document.querySelector('.tabs button[data-t="'+t+'"]');if(!btn)return;
  document.querySelectorAll('.tabs button').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('show'));
  btn.classList.add('active');
  document.getElementById('p-'+t).classList.add('show');
}
document.querySelectorAll('.tabs button').forEach(btn=>{
  btn.addEventListener('click',()=>{showTab(btn.dataset.t);history.replaceState(null,'','#'+btn.dataset.t);});
});
if(location.hash)showTab(location.hash.slice(1));
setTimeout(()=>{ document.querySelectorAll('.usagebar .fill').forEach(f=>{ f.style.width=f.dataset.w+'%'; }); }, 400);

/* Profil */
const pf=document.getElementById('p-profil');
pf.addEventListener('submit',async e=>{
  e.preventDefault();
  const btn=pf.querySelector('.btn');MH.busy(btn,true);
  try{
    const j=await MH.api('/api/me/profile',MH.formData(pf));
    document.querySelectorAll('[data-uname]').forEach(el=>el.textContent=j.name);
    document.querySelectorAll('[data-avatar]').forEach(el=>{if(!el.querySelector('img'))el.textContent=j.initials;});
    MH.toast(j.message);
  }catch(er){MH.toast(er.message,'err');}finally{MH.busy(btn,false);}
});

/* Avatar */
const af=document.getElementById('avatarFile');
document.getElementById('avatarBtn').addEventListener('click',()=>af.click());
af.addEventListener('change',async()=>{
  if(!af.files.length)return;
  const fd=new FormData();fd.append('avatar',af.files[0]);
  try{
    const j=await MH.api('/api/me/avatar',fd);
    document.querySelectorAll('[data-avatar]').forEach(el=>{el.innerHTML='<img src="'+j.url+'" alt="">';});
    MH.toast(j.message);
  }catch(er){MH.toast(er.message,'err');}
  af.value='';
});

/* Açarlar (bildiriş, inteqrasiya, 2FA) — dərhal yadda saxlanır */
document.querySelectorAll('input[data-pref]').forEach(inp=>{
  inp.addEventListener('change',async()=>{
    const body={};body[inp.dataset.pref]=inp.checked;
    try{
      const j=await MH.api('/api/me/prefs',body);
      const sub=inp.closest('.introw')&&inp.closest('.introw').querySelector('[data-isub]');
      if(sub){
        sub.classList.toggle('on',inp.checked);
        sub.textContent=inp.checked?'● Aktiv · son sinxronizasiya indicə':'Deaktiv · bu mənbədən elan göstərilmir';
      }
      MH.toast(j.message);
    }catch(er){inp.checked=!inp.checked;MH.toast(er.message,'err');}
  });
});

/* Şifrə */
const sf=document.getElementById('p-tehluke');
sf.addEventListener('submit',async e=>{
  e.preventDefault();
  const btn=sf.querySelector('.btn');MH.busy(btn,true);
  try{
    const d=MH.formData(sf);
    const j=await MH.api('/api/password',{current:d.current,new:d.new,new2:d.new2});
    sf.querySelectorAll('input[type=password]').forEach(i=>i.value='');
    sf.querySelector('.mh-err').classList.remove('show');
    MH.toast(j.message);
  }catch(er){MH.formError(sf,er.message);}finally{MH.busy(btn,false);}
});

/* Plan */
let chosen=document.querySelector('.mh-plan.on')?.dataset.plan||'pro';
document.getElementById('planBtn').addEventListener('click',()=>MH.openModal('planModal'));
document.querySelectorAll('.mh-plan').forEach(p=>p.addEventListener('click',()=>{
  document.querySelectorAll('.mh-plan').forEach(x=>x.classList.remove('on'));p.classList.add('on');chosen=p.dataset.plan;
}));
document.getElementById('planSave').addEventListener('click',async e=>{
  MH.busy(e.target,true);
  try{const j=await MH.api('/api/me/plan',{plan:chosen});MH.flashNext(j.message);location.hash='abune';location.reload();}
  catch(er){MH.toast(er.message,'err');MH.busy(e.target,false);}
});
