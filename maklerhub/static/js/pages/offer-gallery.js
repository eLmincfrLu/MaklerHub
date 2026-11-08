/* Müştəri səhifəsi: təklif qalereyası (sürüşdürmə native scroll-snap ilə), nöqtələr, tam ekran baxış.
   Tam ekran ESC və brauzerin "geri" düyməsi ilə bağlanır. Xarici kitabxana yoxdur. */
(function(){
  'use strict';
  var gals=document.querySelectorAll('[data-gal]');
  if(!gals.length)return;

  // ---- nöqtələr ----
  Array.prototype.forEach.call(gals,function(g){
    var track=g.querySelector('.gal-track'),dots=g.querySelectorAll('.gal-dots button'),n=g.querySelector('.gal-n');
    var imgs=track.querySelectorAll('img');
    function idx(){return Math.round(track.scrollLeft/Math.max(1,track.clientWidth));}
    function mark(){
      var i=idx();
      Array.prototype.forEach.call(dots,function(d,j){d.classList.toggle('on',i===j);d.setAttribute('aria-current',i===j?'true':'false');});
      if(n)n.textContent=(i+1)+' / '+imgs.length;
    }
    Array.prototype.forEach.call(dots,function(d,j){
      d.addEventListener('click',function(){track.scrollTo({left:j*track.clientWidth,behavior:'smooth'});});
    });
    var t;track.addEventListener('scroll',function(){clearTimeout(t);t=setTimeout(mark,60);},{passive:true});
    Array.prototype.forEach.call(imgs,function(img,j){
      img.addEventListener('click',function(){open(imgs,j);});
    });
    mark();
  });

  // ---- tam ekran ----
  var lb=document.createElement('div');lb.className='lb';lb.hidden=true;
  lb.setAttribute('role','dialog');lb.setAttribute('aria-modal','true');lb.setAttribute('aria-label','Şəkil');
  lb.innerHTML='<img alt="" referrerpolicy="no-referrer"><button type="button" class="lb-x" aria-label="Bağla">✕</button>'+
    '<button type="button" class="lb-prev" aria-label="Əvvəlki">‹</button><button type="button" class="lb-next" aria-label="Növbəti">›</button>'+
    '<div class="lb-n"></div>';
  document.body.appendChild(lb);
  var big=lb.querySelector('img'),cur=[],at=0,pushed=false,sx=null;

  function show(){
    // əvvəl artıq yüklənmiş kart şəkli, böyük versiya hazır olanda əvəzlənir (Cloudinary ilk dəfə yaradır)
    var card=cur[at],full=card.dataset.full||card.src,mine=at;
    big.src=card.currentSrc||card.src;
    if(full!==big.src){
      var pre=new Image();pre.referrerPolicy='no-referrer';
      pre.onload=function(){ if(!lb.hidden&&at===mine&&cur[at]===card)big.src=full; };
      pre.src=full;
    }
    lb.querySelector('.lb-n').textContent=cur.length>1?(at+1)+' / '+cur.length:'';
    lb.querySelector('.lb-prev').hidden=lb.querySelector('.lb-next').hidden=cur.length<2;
  }
  function open(list,i){
    cur=list;at=i;show();lb.hidden=false;document.body.style.overflow='hidden';
    try{history.pushState({lb:1},'');pushed=true;}catch(e){pushed=false;}
    lb.querySelector('.lb-x').focus();
  }
  function hide(){lb.hidden=true;big.removeAttribute('src');document.body.style.overflow='';}
  function close(){ if(lb.hidden)return; if(pushed){pushed=false;history.back();}else{hide();} }
  function step(d){at=(at+d+cur.length)%cur.length;show();}

  window.addEventListener('popstate',function(){ if(!lb.hidden){pushed=false;hide();} });
  lb.querySelector('.lb-x').addEventListener('click',close);
  lb.querySelector('.lb-prev').addEventListener('click',function(e){e.stopPropagation();step(-1);});
  lb.querySelector('.lb-next').addEventListener('click',function(e){e.stopPropagation();step(1);});
  lb.addEventListener('click',function(e){if(e.target===lb)close();});
  document.addEventListener('keydown',function(e){
    if(lb.hidden)return;
    if(e.key==='Escape')close();else if(e.key==='ArrowLeft')step(-1);else if(e.key==='ArrowRight')step(1);
  });
  lb.addEventListener('touchstart',function(e){sx=e.touches[0].clientX;},{passive:true});
  lb.addEventListener('touchend',function(e){
    if(sx===null||cur.length<2)return;var dx=e.changedTouches[0].clientX-sx;sx=null;
    if(Math.abs(dx)>50)step(dx<0?1:-1);
  });
})();
