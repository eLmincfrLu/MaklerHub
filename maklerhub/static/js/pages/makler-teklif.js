/* Təklif forması: şəkil linki şəkil faylı ilə bitmirsə xəbərdarlıq (göndərməyə mane olmur) */
(function(){
  const inp=document.getElementById('image_url'),warn=document.getElementById('imgWarn');
  if(!inp||!warn)return;
  const exts=(inp.dataset.exts||'.jpg,.jpeg,.png,.webp').split(',');
  function check(){
    const v=inp.value.trim().toLowerCase().split('?')[0].split('#')[0];
    warn.hidden=!v||exts.some(e=>v.endsWith(e));
  }
  inp.addEventListener('input',check);
  check();
})();
