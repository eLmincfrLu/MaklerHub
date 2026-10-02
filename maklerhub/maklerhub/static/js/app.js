/* MaklerHub — ümumi JS: API sorğuları, modal pəncərələr, menyular, bildirişlər */
(function(){
  const csrfMeta=document.querySelector('meta[name="csrf-token"]');
  const CSRF=csrfMeta?csrfMeta.content:'';
  const MH=window.MH=window.MH||{};

  /* ---------- API ---------- */
  MH.api=async function(url,data,method){
    const opt={method:method||(data===undefined?'GET':'POST'),credentials:'same-origin',
      headers:{'X-CSRF-Token':CSRF,'Accept':'application/json'}};
    if(data instanceof FormData){opt.body=data;}
    else if(data!==undefined){opt.headers['Content-Type']='application/json';opt.body=JSON.stringify(data);}
    let res,json={};
    try{res=await fetch(url,opt);}catch(e){throw new Error('Serverlə əlaqə yoxdur');}
    try{json=await res.json();}catch(e){}
    if(res.status===401){location.href='/giris?next='+encodeURIComponent(location.pathname);}
    if(!res.ok||json.ok===false){const er=new Error(json.error||'Xəta baş verdi');er.data=json;throw er;}
    return json;
  };

  /* ---------- Bildiriş (toast) ---------- */
  let toastTimer;
  MH.toast=function(msg,kind){
    const t=document.getElementById('mhToast');if(!t)return;
    t.textContent=msg;t.className='mh-toast show'+(kind==='err'?' err':'');
    clearTimeout(toastTimer);toastTimer=setTimeout(()=>t.classList.remove('show'),2600);
  };
  MH.flashNext=function(msg){try{sessionStorage.setItem('mhFlash',msg);}catch(e){}};
  try{const f=sessionStorage.getItem('mhFlash');if(f){sessionStorage.removeItem('mhFlash');setTimeout(()=>MH.toast(f),350);}}catch(e){}
  MH.reload=function(msg){if(msg)MH.flashNext(msg);location.reload();};

  /* ---------- Modal ---------- */
  MH.openModal=function(id){
    const ov=typeof id==='string'?document.getElementById(id):id;if(!ov)return;
    ov.classList.add('show');
    const err=ov.querySelector('.mh-err');if(err){err.textContent='';err.classList.remove('show');}
    setTimeout(()=>{const f=ov.querySelector('input:not([type=hidden]):not([type=checkbox]),select,textarea');if(f)f.focus();},60);
  };
  MH.closeModal=function(el){
    const ov=typeof el==='string'?document.getElementById(el):(el.closest?el.closest('.mh-overlay'):el);
    if(ov)ov.classList.remove('show');
  };
  MH.formError=function(form,msg){
    const e=form.querySelector('.mh-err');
    if(e){e.textContent=msg;e.classList.add('show');}else MH.toast(msg,'err');
  };
  MH.formData=function(form){
    const o={};
    form.querySelectorAll('[name]').forEach(f=>{
      if(f.type==='checkbox')o[f.name]=f.checked;
      else if(f.type==='radio'){if(f.checked)o[f.name]=f.value;}
      else o[f.name]=f.value;
    });
    return o;
  };
  MH.fill=function(form,obj){
    Object.keys(obj||{}).forEach(k=>{
      const f=form.querySelector('[name="'+k+'"]');if(!f)return;
      if(f.type==='checkbox')f.checked=!!obj[k];else f.value=obj[k]==null?'':obj[k];
    });
  };
  MH.busy=function(btn,on,label){
    if(!btn)return;
    if(on){btn.dataset.label=btn.textContent;btn.textContent=label||'Gözləyin...';btn.disabled=true;}
    else{btn.textContent=btn.dataset.label||btn.textContent;btn.disabled=false;}
  };
  document.addEventListener('click',e=>{
    if(e.target.classList&&e.target.classList.contains('mh-overlay'))e.target.classList.remove('show');
    const c=e.target.closest&&e.target.closest('[data-close]');
    if(c){e.preventDefault();MH.closeModal(c);}
  });
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'){document.querySelectorAll('.mh-overlay.show').forEach(o=>o.classList.remove('show'));MH.closeMenu();}
  });

  /* ---------- Açılan menyu (⋯) ---------- */
  let menuEl=null;
  MH.closeMenu=function(){if(menuEl){menuEl.remove();menuEl=null;}};
  MH.menu=function(anchor,items){
    const same=menuEl&&menuEl._anchor===anchor;MH.closeMenu();if(same)return;
    const m=document.createElement('div');m.className='mh-menu';m._anchor=anchor;
    items.filter(Boolean).forEach(it=>{
      if(it.sep){m.appendChild(document.createElement('hr'));return;}
      const b=document.createElement('button');b.type='button';b.textContent=it.label;
      if(it.danger)b.className='danger';
      b.addEventListener('click',ev=>{ev.stopPropagation();MH.closeMenu();it.onClick&&it.onClick();});
      m.appendChild(b);
    });
    document.body.appendChild(m);
    const r=anchor.getBoundingClientRect();
    let left=r.right+scrollX-m.offsetWidth;if(left<8)left=8;
    let top=r.bottom+scrollY+6;
    if(r.bottom+m.offsetHeight+12>innerHeight)top=r.top+scrollY-m.offsetHeight-6;
    m.style.left=left+'px';m.style.top=top+'px';
    requestAnimationFrame(()=>m.classList.add('show'));
    menuEl=m;
  };
  document.addEventListener('click',e=>{if(menuEl&&!menuEl.contains(e.target)&&e.target!==menuEl._anchor&&!menuEl._anchor.contains(e.target))MH.closeMenu();});
  addEventListener('resize',()=>MH.closeMenu());

  MH.confirm=function(text){return window.confirm(text);};

  /* ---------- data-href ---------- */
  document.querySelectorAll('[data-href]').forEach(el=>{
    el.style.cursor='pointer';
    el.addEventListener('click',()=>{location.href=el.dataset.href;});
  });

  /* ---------- Makler naviqasiyası ---------- */
  const navEl=document.querySelector('body > nav');
  if(navEl){addEventListener('scroll',()=>{navEl.classList.toggle('scrolled',scrollY>4);},{passive:true});}
  const ub=document.getElementById('userMenuBtn');
  if(ub){
    ub.addEventListener('click',e=>{
      e.stopPropagation();
      MH.menu(ub,[
        {label:'Ayarlar',onClick:()=>location.href=window.MH_URLS.settings},
        ub.dataset.admin==='1'?{label:'Admin panel',onClick:()=>location.href='/admin'}:null,
        {sep:true},
        {label:'Çıxış et',danger:true,onClick:()=>location.href=window.MH_URLS.logout}
      ]);
    });
  }

  /* ---------- Elan detalları ---------- */
  MH.showMatch=async function(id){
    const ov=document.getElementById('detailModal');if(!ov)return;
    try{
      const j=await MH.api(window.MH_URLS.match+id);
      ov.querySelector('.mh-modal').innerHTML=j.html;
      MH.openModal(ov);
      const cp=ov.querySelector('[data-copy]');
      if(cp)cp.addEventListener('click',async()=>{
        const txt=ov.querySelector('#shareText').value;
        try{await navigator.clipboard.writeText(txt);}catch(e){const ta=ov.querySelector('#shareText');ta.select();document.execCommand('copy');}
        MH.toast('Mətn kopyalandı');
      });
    }catch(e){MH.toast(e.message,'err');}
  };
})();
