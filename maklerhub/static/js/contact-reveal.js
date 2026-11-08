/* Bot qoruması: ictimai səhifələrdə e-poçt/telefon HTML-də tərsinə (data-r) gəlir; burada yığılıb link olur.
   JS yoxdursa "ad [at] domen [dot] com" kimi oxunaqlı mətn qalır. */
(function(){
  'use strict';
  function phoneText(p){
    var d=p.replace(/\D/g,'');
    if(d.length===12&&d.indexOf('994')===0){d=d.slice(3);return '+994 '+d.slice(0,2)+' '+d.slice(2,5)+' '+d.slice(5,7)+' '+d.slice(7);}
    return p;
  }
  var els=document.querySelectorAll('.mh-obf[data-r]');
  for(var i=0;i<els.length;i++){
    var el=els[i],v=el.getAttribute('data-r').split('').reverse().join('');
    if(!v)continue;
    if(el.getAttribute('data-k')==='m'){el.href='mailto:'+v;el.textContent=v;}
    else{el.href='tel:'+v;el.textContent=phoneText(v);}
    el.removeAttribute('rel');
  }
})();
