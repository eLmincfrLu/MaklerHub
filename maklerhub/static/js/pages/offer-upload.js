/* Təklif forması: şəkil önizləməsi, "əsas et" / sil, brauzerdə 2000px-ə kiçiltmə, "Yüklənir…".
   JS işləməsə forma adi qaydada göndərilir (server hər şeyi özü yoxlayır). */
(function(){
  'use strict';
  var form=document.getElementById('offerForm'),input=document.getElementById('images'),
      box=document.getElementById('imgPreview'),btn=document.getElementById('offerSubmit');
  if(!form)return;
  var busyText='Yüklənir…';
  function busy(){ if(btn){btn.disabled=true;btn.classList.add('is-busy');btn.textContent=busyText;} }
  // DataTransfer yoxdursa (çox köhnə brauzer): yalnız adi göndəriş + "Yüklənir…"
  var canEdit=!!(input&&box&&window.DataTransfer&&window.URL&&URL.createObjectURL);
  if(!canEdit){form.addEventListener('submit',busy);return;}
  try{new DataTransfer();}catch(e){form.addEventListener('submit',busy);return;}

  var MAX=parseInt(input.dataset.max||'5',10),MAX_MB=parseInt(input.dataset.maxMb||'5',10),
      LONG=2000,TYPES=['image/jpeg','image/png','image/webp'],FORM_LIMIT=25*1024*1024;
  var items=[];  // {file, url}
  var note='';

  function el(tag,cls,text){var e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e;}

  function render(){
    box.innerHTML='';
    box.hidden=!items.length&&!note;
    items.forEach(function(it,i){
      var card=el('div','oi-item'+(i===0?' main':''));
      var img=el('img');img.src=it.url;img.alt='';card.appendChild(img);
      if(i===0)card.appendChild(el('span','oi-badge','Əsas'));
      var acts=el('div','oi-acts');
      var mk=el('button',null,'Əsas et');mk.type='button';mk.disabled=i===0;
      mk.addEventListener('click',function(){items.unshift(items.splice(i,1)[0]);sync();});
      var rm=el('button',null,'Sil');rm.type='button';
      rm.addEventListener('click',function(){URL.revokeObjectURL(it.url);items.splice(i,1);note='';sync();});
      acts.appendChild(mk);acts.appendChild(rm);card.appendChild(acts);
      box.appendChild(card);
    });
    if(items.length)box.appendChild(el('div','oi-count',items.length+' / '+MAX+' şəkil · birinci şəkil əsasdır'));
    if(note)box.appendChild(el('div','oi-err',note));
  }

  function sync(){
    var dt=new DataTransfer();
    items.forEach(function(it){dt.items.add(it.file);});
    input.files=dt.files;
    render();
  }

  input.addEventListener('change',function(){
    var picked=Array.prototype.slice.call(input.files||[]);
    var known=items.map(function(it){return it.file;});
    note='';
    picked.forEach(function(f){
      if(known.indexOf(f)!==-1)return;  // sync-dən sonra input öz siyahımızı göstərir
      if(TYPES.indexOf(f.type)===-1){note='Yalnız JPEG, PNG və ya WEBP şəkil seçin.';return;}
      if(items.length>=MAX){note='Ən çox '+MAX+' şəkil yükləmək olar.';return;}
      items.push({file:f,url:URL.createObjectURL(f)});
    });
    sync();
  });

  // brauzerdə kiçiltmə: uzun tərəf > 2000px və ya fayl limitə yaxındırsa JPEG-ə yenidən kodlanır
  function shrink(file){
    return new Promise(function(resolve){
      var img=new Image(),url=URL.createObjectURL(file);
      img.onload=function(){
        var w=img.naturalWidth,h=img.naturalHeight,k=Math.min(1,LONG/Math.max(w,h));
        URL.revokeObjectURL(url);
        if(k===1&&file.size<=(MAX_MB-0.5)*1024*1024){resolve(file);return;}
        var c=document.createElement('canvas');c.width=Math.round(w*k);c.height=Math.round(h*k);
        var ctx=c.getContext('2d');ctx.fillStyle='#fff';ctx.fillRect(0,0,c.width,c.height);
        ctx.drawImage(img,0,0,c.width,c.height);
        c.toBlob(function(b){
          resolve(b?new File([b],'sekil.jpg',{type:'image/jpeg'}):file);
        },'image/jpeg',0.88);
      };
      img.onerror=function(){URL.revokeObjectURL(url);resolve(file);};  // server yoxlayıb rədd edəcək
      img.src=url;
    });
  }

  var sending=false;
  form.addEventListener('submit',function(e){
    if(sending)return;
    if(!items.length){busy();return;}
    e.preventDefault();
    busy();
    Promise.all(items.map(function(it){return shrink(it.file);})).then(function(files){
      var total=files.reduce(function(s,f){return s+f.size;},0);
      if(total>FORM_LIMIT){
        note='Şəkillər birlikdə çox böyükdür. Daha az şəkil seçin.';render();
        btn.disabled=false;btn.classList.remove('is-busy');btn.textContent='Təklifi göndər';return;
      }
      var dt=new DataTransfer();files.forEach(function(f){dt.items.add(f);});
      input.files=dt.files;
      sending=true;
      HTMLFormElement.prototype.submit.call(form);
    });
  });
})();
