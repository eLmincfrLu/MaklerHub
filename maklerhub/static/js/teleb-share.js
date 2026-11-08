/* /teleb/<token>: "Linki saxla" bloku (Paket C1) — kopyala, paylaş, QR pəncərəsi, "Ana ekrana əlavə et".
   Service worker YOXDUR (şəxsi səhifə keşlənməsin). JS-siz: link sahəsi, WhatsApp/Telegram/E-poçt linkləri işləyir. */
(function(){
  'use strict';
  var box=document.querySelector('[data-share]');
  if(!box)return;
  var link=box.getAttribute('data-link');

  // ---- kopyala (Clipboard API + köhnə fallback)
  function fallbackCopy(text){
    var ta=document.createElement('textarea');ta.value=text;ta.setAttribute('readonly','');
    ta.style.position='fixed';ta.style.top='-1000px';document.body.appendChild(ta);ta.select();
    var ok=false;try{ok=document.execCommand('copy');}catch(e){}
    document.body.removeChild(ta);return ok;
  }
  var copyBtn=box.querySelector('[data-share-copy]'),timer=null;
  if(copyBtn)copyBtn.addEventListener('click',function(){
    function done(){
      copyBtn.classList.remove('done');void copyBtn.offsetWidth;copyBtn.classList.add('done');
      clearTimeout(timer);timer=setTimeout(function(){copyBtn.classList.remove('done');},1900);
    }
    if(navigator.clipboard&&window.isSecureContext){
      navigator.clipboard.writeText(link).then(done,function(){if(fallbackCopy(link))done();});
    }else if(fallbackCopy(link))done();
    else{var inp=box.querySelector('[data-share-input]');if(inp){inp.focus();inp.select();}}
  });
  var inp=box.querySelector('[data-share-input]');
  if(inp)inp.addEventListener('focus',function(){inp.select();});

  // ---- sistem paylaşma menyusu (mobil)
  var shareBtn=box.querySelector('[data-share-native]');
  if(shareBtn&&navigator.share){
    shareBtn.hidden=false;
    shareBtn.addEventListener('click',function(){
      navigator.share({title:'MaklerHub — Tələbim',text:'Tələbimin linki (təkliflər burada görünür):',url:link})
        .catch(function(){});
    });
  }

  // ---- pəncərələr (<dialog>)
  function openDlg(d){
    if(!d)return false;
    if(typeof d.showModal==='function'){d.showModal();return true;}
    return false;
  }
  Array.prototype.forEach.call(document.querySelectorAll('dialog.mh-dlg'),function(d){
    d.addEventListener('click',function(e){if(e.target===d)d.close();});   // kənara basanda bağlanır
    Array.prototype.forEach.call(d.querySelectorAll('[data-close]'),function(b){
      b.addEventListener('click',function(){d.close();});
    });
  });

  // ---- QR (serverdə SVG; şəkil yalnız pəncərə açılanda yüklənir)
  var qrDlg=document.getElementById('qrDlg'),qrBtn=box.querySelector('[data-share-qr]');
  if(qrBtn&&qrDlg)qrBtn.addEventListener('click',function(){
    var img=qrDlg.querySelector('img[data-src]');
    if(img&&!img.getAttribute('src'))img.setAttribute('src',img.getAttribute('data-src'));
    if(!openDlg(qrDlg)&&img)window.open(img.getAttribute('data-src'),'_blank','noopener');
  });

  // ---- Ana ekrana əlavə et
  var a2hs=Array.prototype.slice.call(document.querySelectorAll('[data-a2hs]')),deferred=null;
  var standalone=(window.matchMedia&&window.matchMedia('(display-mode: standalone)').matches)||navigator.standalone===true;
  var ua=navigator.userAgent||'';
  var ios=/iphone|ipad|ipod/i.test(ua)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
  function showA2hs(v){a2hs.forEach(function(b){b.hidden=!v;});}
  if(!standalone){
    window.addEventListener('beforeinstallprompt',function(e){e.preventDefault();deferred=e;showA2hs(true);});
    window.addEventListener('appinstalled',function(){deferred=null;showA2hs(false);});
    if(ios)showA2hs(true);
  }
  a2hs.forEach(function(b){b.addEventListener('click',function(){
    // iOS-da quraşdırma sorğusu yoxdur (yalnız Paylaş → Ana ekrana) — həmişə təlimat
    if(ios){openDlg(document.getElementById('iosDlg'));}
    else if(deferred){
      deferred.prompt();
      deferred.userChoice.then(function(){deferred=null;showA2hs(false);},function(){});
    }
  });});
})();
