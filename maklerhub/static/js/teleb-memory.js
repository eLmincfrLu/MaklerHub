/* Müştərinin tələb linkləri brauzer yaddaşında (Paket C1). Xarici servis yoxdur.
   - /teleb/<token>: [data-mh-save] — aktiv tələbin tokeni localStorage "mh_requests"-ə yazılır
     ({token, saved_at}, ən yenisi əvvəldə, ən çox 5); bağlanmış/bitmiş tələb yaddaşdan silinir.
   - Ana səhifə və /teleb: [data-mh-banner] — yaddaşda token varsa POST /api/teleb/status (token body-də, URL-də yox)
     və kiçik banner. Token yoxdursa sorğu GETMİR (ziyarətçilərin çoxu üçün baza oyanmır).
     Bağlanmış/bitmiş/tapılmayan tələblər göstərilmir və yaddaşdan silinir. "×" — bu sessiyada gizlət.
   Bütün localStorage/sessionStorage əməliyyatları try/catch içindədir (Safari gizli rejim və s.). */
(function(){
  'use strict';
  var KEY='mh_requests',MAX=5,HIDE='mh_banner_hidden',TOKEN=/^[A-Za-z0-9_-]{16,64}$/;

  function load(){
    try{
      var v=JSON.parse(localStorage.getItem(KEY)||'[]');
      if(!Array.isArray(v))return [];
      return v.filter(function(x){return x&&typeof x.token==='string'&&TOKEN.test(x.token);}).slice(0,MAX);
    }catch(e){return [];}
  }
  function store(list){
    try{
      if(list.length)localStorage.setItem(KEY,JSON.stringify(list.slice(0,MAX)));
      else localStorage.removeItem(KEY);
    }catch(e){}
  }
  function remember(token){
    var list=load().filter(function(x){return x.token!==token;});
    list.unshift({token:token,saved_at:new Date().toISOString()});
    store(list);
  }
  function forget(tokens){
    store(load().filter(function(x){return tokens.indexOf(x.token)===-1;}));
  }

  // ---- /teleb/<token>
  var me=document.querySelector('[data-mh-save]');
  if(me){
    var t=me.getAttribute('data-mh-save');
    if(TOKEN.test(t)){if(me.getAttribute('data-mh-alive')==='1')remember(t);else forget([t]);}
  }

  // ---- banner
  var slot=document.querySelector('[data-mh-banner]');
  if(!slot||!window.fetch||!window.JSON)return;
  try{if(sessionStorage.getItem(HIDE)==='1')return;}catch(e){}
  var list=load();
  if(!list.length)return;

  function el(tag,cls,text){var e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e;}
  function plural(n,word){return n+' '+word;}  // azərbaycan dilində cəm şəkilçisi saydan sonra işlənmir
  function badge(r){
    if(r.new_offers>0)return el('span','mhb-new',plural(r.new_offers,'yeni təklif'));
    return el('span','mhb-cnt',r.total_offers>0?plural(r.total_offers,'təklif'):'təklif gözlənilir');
  }
  function hide(box){
    try{sessionStorage.setItem(HIDE,'1');}catch(e){}
    box.classList.add('mhb-out');
    setTimeout(function(){slot.hidden=true;slot.innerHTML='';},260);
  }
  function render(items){
    var base=slot.getAttribute('data-base')||'/teleb/';
    var box=el('div','mhb'+(items.length>1?' mhb-multi':''));
    box.setAttribute('role','status');
    var x=el('button','mhb-x','×');x.type='button';x.setAttribute('aria-label','Banneri gizlət');
    x.addEventListener('click',function(){hide(box);});
    if(items.length===1){
      var it=items[0],a=el('a','mhb-main');a.href=base+encodeURIComponent(it.token);
      a.appendChild(el('span','mhb-dot'));
      var txt=el('span','mhb-txt');
      txt.appendChild(el('b',null,'Aktiv tələbin var'));
      txt.appendChild(el('span','mhb-sum',it.r.summary));
      a.appendChild(txt);
      a.appendChild(badge(it.r));
      a.appendChild(el('span','mhb-go','Bax →'));
      box.appendChild(a);
    }else{
      // ana səhifədə (tünd hero) siyahı yığılır: başlıq bir sətirdir, basanda siyahı məzmunun üstündə açılır
      var fold=slot.classList.contains('dark');
      var head=el(fold?'button':'div','mhb-head');
      head.appendChild(el('span','mhb-dot'));
      head.appendChild(el('b',null,'Aktiv tələblərin var ('+items.length+')'));
      if(fold){
        head.type='button';head.setAttribute('aria-expanded','false');
        var fresh=items.reduce(function(n,it){return n+(it.r.new_offers||0);},0);
        if(fresh)head.appendChild(el('span','mhb-new',plural(fresh,'yeni təklif')));
        head.appendChild(el('span','mhb-go','Bax ↓'));
        head.addEventListener('click',function(){
          var open=box.classList.toggle('open');head.setAttribute('aria-expanded',open?'true':'false');
        });
      }
      box.appendChild(head);
      var ul=el('div','mhb-list');
      items.forEach(function(it){
        var row=el('a','mhb-row');row.href=base+encodeURIComponent(it.token);
        row.appendChild(el('span','mhb-sum',it.r.summary));
        row.appendChild(badge(it.r));
        row.appendChild(el('span','mhb-go','Bax →'));
        ul.appendChild(row);
      });
      box.appendChild(ul);
    }
    box.appendChild(x);
    slot.innerHTML='';slot.appendChild(box);slot.hidden=false;
  }

  fetch(slot.getAttribute('data-api')||'/api/teleb/status',{
    method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',cache:'no-store',
    referrerPolicy:'strict-origin',  // Origin göndərilsin; Referer-də yol (token) olmasın
    body:JSON.stringify({tokens:list.map(function(x){return x.token;})})
  }).then(function(r){return r.ok?r.json():null;}).then(function(j){
    if(!j||!j.ok||!Array.isArray(j.results))return;
    var alive=[],dead=[];
    list.forEach(function(x,i){
      var r=j.results[i];
      if(!r)return;
      if(r.found&&r.status==='active')alive.push({token:x.token,r:r});
      else dead.push(x.token);
    });
    if(dead.length)forget(dead);
    if(alive.length)render(alive);
  }).catch(function(){});
})();
