(function(){
  const list=document.getElementById('list');
  const more=document.getElementById('moreBtn');
  const sortSel=document.getElementById('sortSel');
  const state={src:document.querySelector('.filters .fchip.active[data-src]')?.dataset.src||'all',
               sort:sortSel.value, profile:list.dataset.profile||'', offset:list.querySelectorAll('.card').length};

  function qs(){
    const p=new URLSearchParams({src:state.src,sort:state.sort,offset:state.offset});
    if(state.profile)p.set('profile',state.profile);
    return p.toString();
  }
  function syncUrl(){
    const p=new URLSearchParams();
    if(state.src!=='all')p.set('src',state.src);
    if(state.sort!=='match')p.set('sort',state.sort);
    if(state.profile)p.set('profile',state.profile);
    history.replaceState(null,'',location.pathname+(p.toString()?'?'+p:''));
  }
  function bindCards(root){
    root.querySelectorAll('.card[data-match]:not([data-bound])').forEach(c=>{
      c.dataset.bound='1';c.style.cursor='pointer';
      c.addEventListener('click',()=>MH.showMatch(c.dataset.match));
    });
  }
  async function load(append){
    if(!append)state.offset=0;
    try{
      const j=await MH.api('/api/elanlar?'+qs());
      if(append){list.insertAdjacentHTML('beforeend',j.html);}
      else{list.innerHTML=j.html||'<div class="mh-empty">Bu filtrə uyğun elan tapılmadı.</div>';}
      state.offset=list.querySelectorAll('.card').length;
      more.style.display=j.has_more?'block':'none';
      bindCards(list);syncUrl();
    }catch(e){MH.toast(e.message,'err');}
  }

  document.querySelectorAll('.filters .fchip[data-src]').forEach(chip=>{
    chip.addEventListener('click',()=>{
      document.querySelectorAll('.filters .fchip[data-src]').forEach(c=>c.classList.remove('active'));
      chip.classList.add('active');state.src=chip.dataset.src;load(false);
    });
  });
  const pc=document.getElementById('profileChip');
  if(pc)pc.addEventListener('click',()=>{state.profile='';pc.remove();load(false);});
  sortSel.addEventListener('change',()=>{state.sort=sortSel.value;load(false);});
  more.addEventListener('click',()=>{MH.busy(more,true,'Yüklənir...');load(true).finally(()=>MH.busy(more,false));});
  bindCards(list);
})();
