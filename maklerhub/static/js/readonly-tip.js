/* Demo hesab: toxunma ekranında deaktiv sahəyə basanda ipucu 2 saniyə görünsün (hover yoxdur). */
(function(){
  'use strict';
  document.querySelectorAll('fieldset.ro-zone').forEach(function(fs){
    var t;
    fs.addEventListener('click',function(){
      fs.classList.add('tip-on');clearTimeout(t);t=setTimeout(function(){fs.classList.remove('tip-on');},2000);
    });
  });
})();
