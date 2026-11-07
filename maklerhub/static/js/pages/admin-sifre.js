/* Admin · Ayarlar: şifrə dəyişmə (mövcud /api/password) */
(function(){
  const f=document.getElementById('pwForm');if(!f)return;
  const msg=document.getElementById('pwMsg');
  f.addEventListener('submit',async e=>{
    e.preventDefault();
    const b=f.querySelector('[type=submit]');MH.busy(b,true);
    msg.className='lic-flash';msg.hidden=true;
    try{const j=await MH.api('/api/password',MH.formData(f));f.reset();msg.textContent=j.message||'Şifrə yeniləndi';msg.classList.add('ok');}
    catch(er){msg.textContent=er.message;msg.classList.add('err');}
    finally{msg.hidden=false;MH.busy(b,false);}
  });
})();
