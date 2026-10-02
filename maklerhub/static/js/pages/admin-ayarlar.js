const sb=document.getElementById('sb'),toast=document.getElementById('toast');
const fields=[...document.querySelectorAll('#forms input,#forms select')];
const init=new Map();
function val(f){return f.type==='checkbox'?f.checked:f.value;}
fields.forEach(f=>init.set(f,val(f)));
function dirty(){return fields.some(f=>val(f)!==init.get(f));}
function check(){sb.classList.toggle('show',dirty());}
fields.forEach(f=>{f.addEventListener('input',check);f.addEventListener('change',check);});
function say(t,bad){
  toast.textContent=t;
  toast.style.borderColor=bad?'var(--bad)':'';toast.style.color=bad?'var(--bad)':'';
  toast.style.background=bad?'rgba(196,106,86,.16)':'';
  toast.classList.add('show');setTimeout(()=>toast.classList.remove('show'),2200);
}
document.getElementById('save').addEventListener('click',async e=>{
  const data={};
  fields.filter(f=>f.name&&val(f)!==init.get(f)).forEach(f=>{data[f.name]=val(f);});
  const b=e.currentTarget;b.disabled=true;
  try{
    await MH.api('/admin/api/settings',data);
    fields.forEach(f=>init.set(f,val(f)));check();say('Ayarlar yadda saxlanıldı');
    if('admin_name' in data)document.querySelectorAll('.aname').forEach(el=>el.textContent=data.admin_name);
  }catch(er){say(er.message,true);}
  finally{b.disabled=false;}
});
document.getElementById('cancel').addEventListener('click',()=>{fields.forEach(f=>{if(f.type==='checkbox')f.checked=init.get(f);else f.value=init.get(f);});check();});

/* Parol */
const pwModal=document.getElementById('pwModal'),pwForm=document.getElementById('pwForm');
document.getElementById('pw').addEventListener('click',()=>{pwForm.reset();MH.openModal(pwModal);});
pwForm.addEventListener('submit',async e=>{
  e.preventDefault();const b=pwForm.querySelector('[type=submit]');MH.busy(b,true);
  try{const j=await MH.api('/api/password',MH.formData(pwForm));MH.closeModal(pwModal);say(j.message||'Parol yeniləndi');}
  catch(er){MH.formError(pwForm,er.message);}finally{MH.busy(b,false);}
});

/* Keş */
document.getElementById('cache').addEventListener('click',async()=>{
  if(!confirm('Elan keşi təmizlənsin?'))return;
  try{const j=await MH.api('/admin/api/cache/clear',{});say(j.message);}catch(er){say(er.message,true);}
});

const links=[...document.querySelectorAll('#tabs a')];
links.forEach(a=>a.addEventListener('click',e=>{e.preventDefault();document.querySelector(a.getAttribute('href')).scrollIntoView({behavior:'smooth',block:'start'});}));
const io=new IntersectionObserver(es=>es.forEach(en=>{if(en.isIntersecting){links.forEach(l=>l.classList.toggle('on',l.getAttribute('href')==='#'+en.target.id));}}),{rootMargin:'-20% 0px -70% 0px'});
document.querySelectorAll('section.card[id]').forEach(s=>io.observe(s));
