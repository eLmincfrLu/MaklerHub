/* Makler · Ayarlar: tablar, profil, xidmət sahəsi, şifrə */
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

function clearErr(form){const e=form.querySelector('.mh-err');if(e){e.textContent='';e.classList.remove('show');}}

/* Profil */
const pf=document.getElementById('p-profil');
pf.addEventListener('submit',async e=>{
  e.preventDefault();clearErr(pf);
  const btn=pf.querySelector('.btn');MH.busy(btn,true);
  try{
    const j=await MH.api('/api/me/profile',MH.formData(pf));
    document.querySelectorAll('[data-uname]').forEach(el=>el.textContent=j.name);
    pf.querySelector('[name=phone]').value=j.phone;
    MH.toast(j.message);
  }catch(er){MH.formError(pf,er.message);}finally{MH.busy(btn,false);}
});

/* Xidmət sahəsi */
const sf=document.getElementById('p-saha');
function picked(box){
  const n=box.querySelectorAll('[data-district]:checked').length;
  box.querySelector('[data-picked]').textContent=n?('Seçilib: '+n+' rayon'):'Hələ rayon seçilməyib';
}
sf.querySelectorAll('.deal').forEach(box=>{
  box.querySelector('[data-on]').addEventListener('change',e=>box.classList.toggle('off',!e.target.checked));
  box.querySelectorAll('[data-district]').forEach(c=>c.addEventListener('change',()=>picked(box)));
  picked(box);
});
sf.addEventListener('submit',async e=>{
  e.preventDefault();clearErr(sf);
  const data={};
  sf.querySelectorAll('.deal').forEach(box=>{
    data[box.dataset.deal]={
      on:box.querySelector('[data-on]').checked,
      min:box.querySelector('[data-min]').value.trim(),
      max:box.querySelector('[data-max]').value.trim(),
      districts:[...box.querySelectorAll('[data-district]:checked')].map(c=>c.value)
    };
  });
  const btn=sf.querySelector('.btn');MH.busy(btn,true);
  try{const j=await MH.api('/api/me/areas',data);MH.toast(j.message);}
  catch(er){MH.formError(sf,er.message);}finally{MH.busy(btn,false);}
});

/* Şifrə */
const pw=document.getElementById('p-sifre');
pw.addEventListener('submit',async e=>{
  e.preventDefault();clearErr(pw);
  const btn=pw.querySelector('.btn');MH.busy(btn,true);
  try{const j=await MH.api('/api/password',MH.formData(pw));pw.reset();MH.toast(j.message||'Şifrə yeniləndi');}
  catch(er){MH.formError(pw,er.message);}finally{MH.busy(btn,false);}
});
