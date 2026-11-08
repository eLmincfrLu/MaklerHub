/* Rayon seçici ([data-dp], _district_picker.html). Progressive enhancement:
   - çoxlu seçim: mövcud qeyd qutuları (eyni name/data-district) panelə köçürülür — forma eyni göndərilir;
   - tək seçim: gizli <select> idarə olunur.
   Dəyişiklikdə həmişə "change" hadisəsi göndərilir (teleb.js, makler-ayarlar.js eyni inputlara qulaq asır).
   Axtarış Azərbaycan hərflərinə həssas deyil (ə/e, ı/i, ö/o, ü/u, ş/s, ç/c, ğ/g).
   "Fərq etmir (bütün Bakı)" ([data-any], yalnız /teleb): seçiləndə bütün rayonlar seçilir, sahədə tək "Bütün Bakı"
   etiketi; hər hansı rayon götürüləndə o da götürülür (hamısı əl ilə seçiləndə özü də seçilir).
   Klaviatura: Enter/Space açır və seçir, oxlar gəzir, Esc bağlayır; kənara basanda bağlanır; ≤640px — bottom sheet. */
(function(){
  'use strict';
  var FOLD={'ə':'e','ı':'i','ö':'o','ü':'u','ş':'s','ç':'c','ğ':'g'};
  function fold(s){
    return (s||'').replace(/İ/g,'i').replace(/I/g,'ı').toLowerCase().replace(/̇/g,'')
      .replace(/[əıöüşçğ]/g,function(c){return FOLD[c];});
  }
  var MOBILE=window.matchMedia?window.matchMedia('(max-width:640px)'):{matches:false};
  var TAGS_SHOWN=3,uid=0,current=null,ALL_LABEL='Bütün Bakı';

  function el(tag,cls,text){var e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e;}
  function fire(input){input.dispatchEvent(new Event('change',{bubbles:true}));}
  function caret(){
    var s=document.createElementNS('http://www.w3.org/2000/svg','svg');
    s.setAttribute('width','14');s.setAttribute('height','14');s.setAttribute('viewBox','0 0 24 24');s.setAttribute('aria-hidden','true');
    s.setAttribute('class','dp-caret');
    var p=document.createElementNS('http://www.w3.org/2000/svg','path');
    p.setAttribute('d','m6 9 6 6 6-6');p.setAttribute('fill','none');p.setAttribute('stroke','currentColor');
    p.setAttribute('stroke-width','2');p.setAttribute('stroke-linecap','round');p.setAttribute('stroke-linejoin','round');
    s.appendChild(p);return s;
  }

  function init(root){
    var single=root.hasAttribute('data-single'),id='dp'+(++uid),label=root.getAttribute('data-label')||'Rayon seçin…';
    var max=parseInt(root.getAttribute('data-max')||'0',10)||0;
    var select=single?root.querySelector('select'):null;
    var list=single?null:root.querySelector('.dp-list');
    var inputs=list?Array.prototype.slice.call(list.querySelectorAll('input[type=checkbox]:not([data-any])')):[];
    var anyInput=list?list.querySelector('input[data-any]'):null;
    function allOn(){return inputs.length>0&&inputs.every(function(i){return i.checked;});}

    root.classList.add('dp-on');
    // ---- sahə: etiketlər + açma düyməsi
    var field=el('div','dp-field'),tags=el('div','dp-tags'),toggle=el('button','dp-toggle');
    toggle.type='button';
    toggle.setAttribute('aria-haspopup',single?'listbox':'dialog');
    toggle.setAttribute('aria-expanded','false');
    toggle.setAttribute('aria-controls',id+'-panel');
    var ph=el('span','dp-ph',label);
    toggle.appendChild(ph);toggle.appendChild(caret());
    field.appendChild(tags);field.appendChild(toggle);

    // ---- panel
    var panel=el('div','dp-panel');panel.id=id+'-panel';panel.hidden=true;panel.tabIndex=-1;
    panel.setAttribute('role','dialog');panel.setAttribute('aria-label',single?'Rayon seçin':'Rayonları seçin');
    var head=el('div','dp-head');
    head.appendChild(el('div','dp-grip'));
    var search=el('input','dp-search');search.type='search';search.placeholder='Rayon axtar…';
    search.setAttribute('aria-label','Rayon axtar');search.autocomplete='off';search.setAttribute('enterkeyhint','search');
    head.appendChild(search);
    panel.appendChild(head);

    var hint=null,btnAll=null,btnClear=null,options=[],empty=el('div','dp-empty','Heç nə tapılmadı');empty.hidden=true;
    if(!single){
      var tools=el('div','dp-tools');
      btnAll=el('button','dp-link','Hamısını seç');btnAll.type='button';
      btnClear=el('button','dp-link','Təmizlə');btnClear.type='button';
      if(max){btnAll.hidden=true;hint=el('span','dp-hint','Ən çox '+max+' rayon');}
      tools.appendChild(btnAll);tools.appendChild(btnClear);if(hint)tools.appendChild(hint);
      panel.appendChild(tools);
      list.classList.add('dp-scroll');
      panel.appendChild(list);
    }else{
      var lb=el('div','dp-list dp-scroll');lb.setAttribute('role','listbox');lb.setAttribute('aria-label','Rayon');
      Array.prototype.forEach.call(select.options,function(o){
        var b=el('button','dp-optbtn',o.textContent);b.type='button';b.setAttribute('role','option');b.dataset.value=o.value;
        b.addEventListener('click',function(){select.value=o.value;fire(select);sync();close(true);});
        lb.appendChild(b);options.push(b);
      });
      panel.appendChild(lb);
      select.classList.add('dp-hidden');select.tabIndex=-1;select.setAttribute('aria-hidden','true');
    }
    panel.appendChild(empty);
    var foot=el('div','dp-foot'),count=el('span','dp-count'),done=el('button','dp-done','Hazır');done.type='button';
    if(!single){foot.appendChild(count);foot.appendChild(done);panel.appendChild(foot);}

    var backdrop=el('div','dp-backdrop');backdrop.hidden=true;
    root.insertBefore(field,root.firstChild);
    root.appendChild(panel);
    document.body.appendChild(backdrop);

    // ---- vəziyyət
    function sync(){
      tags.innerHTML='';
      if(single){
        var v=select.value,txt=v?select.options[select.selectedIndex].textContent:label;
        ph.textContent=txt;toggle.classList.toggle('has',!!v);
        options.forEach(function(b){b.setAttribute('aria-selected',b.dataset.value===v?'true':'false');});
        toggle.setAttribute('aria-label','Rayon: '+txt);
        return;
      }
      var on=inputs.filter(function(i){return i.checked;});
      var all=!!anyInput&&allOn();
      if(all){
        var ta=el('span','dp-tag dp-tag-all');ta.appendChild(el('span','dp-tag-t',ALL_LABEL));
        var xa=el('button','dp-x','✕');xa.type='button';xa.setAttribute('aria-label',ALL_LABEL+' — sil');
        xa.disabled=toggle.matches(':disabled');
        xa.addEventListener('click',function(e){e.stopPropagation();setAll(false);});
        ta.appendChild(xa);tags.appendChild(ta);
      }
      if(!all)on.slice(0,TAGS_SHOWN).forEach(function(i){
        var t=el('span','dp-tag');t.appendChild(el('span','dp-tag-t',i.value));
        var x=el('button','dp-x','✕');x.type='button';x.setAttribute('aria-label',i.value+' — sil');
        x.disabled=toggle.matches(':disabled');
        x.addEventListener('click',function(e){e.stopPropagation();i.checked=false;fire(i);});
        t.appendChild(x);tags.appendChild(t);
      });
      if(!all&&on.length>TAGS_SHOWN){
        var more=el('span','dp-more','+'+(on.length-TAGS_SHOWN));more.title=on.slice(TAGS_SHOWN).map(function(i){return i.value;}).join(', ');
        tags.appendChild(more);
      }
      ph.textContent=on.length?(max?on.length+'/'+max:'Dəyiş'):label;
      toggle.classList.toggle('has',!!on.length);
      toggle.setAttribute('aria-label',all?(ALL_LABEL+' seçilib. Dəyişmək üçün açın'):
        on.length?('Seçilmiş rayonlar: '+on.map(function(i){return i.value;}).join(', ')+'. Dəyişmək üçün açın'):label);
      var full=max&&on.length>=max;
      if(max)inputs.forEach(function(i){if(!i.checked&&!i.closest('fieldset[disabled]'))i.disabled=full;});
      root.classList.toggle('dp-full',!!full);
      if(hint)hint.classList.toggle('on',!!full);
      count.textContent=all?ALL_LABEL+' ('+on.length+' rayon)':
        on.length?(on.length+' seçilib'+(max?' / ən çox '+max:'')):'Heç nə seçilməyib';
    }

    function visibleItems(){
      var items=single?options:(anyInput?[anyInput].concat(inputs):inputs);
      return items.filter(function(x){var row=single?x:x.closest('.dp-opt');return !row.hidden&&!x.disabled;});
    }
    function filter(){
      var q=fold(search.value.trim()),n=0;
      (single?options:(anyInput?[anyInput].concat(inputs):inputs)).forEach(function(x){
        var row=single?x:x.closest('.dp-opt'),txt=single?x.textContent:(x===anyInput?row.textContent:x.value);
        var show=!q||fold(txt).indexOf(q)!==-1||(single&&!x.dataset.value);
        row.hidden=!show;if(show)n++;
      });
      empty.hidden=n>0;
    }

    var moved=false,lastScroll=0;
    function open(){
      if(toggle.matches(':disabled'))return;
      if(current&&current!==api)current.close(false);
      current=api;
      if(MOBILE.matches){
        document.body.appendChild(panel);moved=true;panel.classList.add('dp-sheet');
        backdrop.hidden=false;lastScroll=document.body.style.overflow;document.body.style.overflow='hidden';
      }
      panel.hidden=false;root.classList.add('dp-open');toggle.setAttribute('aria-expanded','true');
      if(MOBILE.matches)panel.focus();else search.focus();
    }
    function close(refocus){
      if(panel.hidden)return;
      panel.hidden=true;root.classList.remove('dp-open');toggle.setAttribute('aria-expanded','false');
      if(moved){root.appendChild(panel);moved=false;panel.classList.remove('dp-sheet');
        backdrop.hidden=true;document.body.style.overflow=lastScroll||'';}
      if(search.value){search.value='';filter();}
      if(current===api)current=null;
      if(refocus)toggle.focus();
    }
    var api={close:close};

    toggle.addEventListener('click',function(){if(panel.hidden)open();else close(true);});
    field.addEventListener('click',function(e){if(e.target===field||e.target===tags)open();});
    toggle.addEventListener('keydown',function(e){if(e.key==='ArrowDown'&&panel.hidden){e.preventDefault();open();}});
    search.addEventListener('input',filter);
    search.addEventListener('keydown',function(e){
      if(e.key==='Enter'||e.key==='ArrowDown'){e.preventDefault();var v=visibleItems();if(v.length)v[0].focus();}
    });
    panel.addEventListener('keydown',function(e){
      if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close(true);return;}
      var items=visibleItems(),i=items.indexOf(document.activeElement);
      if(i===-1)return;
      if(e.key==='ArrowDown'){e.preventDefault();(items[i+1]||items[0]).focus();}
      else if(e.key==='ArrowUp'){e.preventDefault();if(i===0)search.focus();else items[i-1].focus();}
      else if(e.key==='Home'){e.preventDefault();items[0].focus();}
      else if(e.key==='End'){e.preventDefault();items[items.length-1].focus();}
      else if(e.key==='Enter'&&!single){e.preventDefault();var t=items[i];t.checked=!t.checked;fire(t);}
    });
    function setAll(v){
      inputs.forEach(function(i){if(i.checked!==v){i.checked=v;fire(i);}});
      if(anyInput&&anyInput.checked!==v){anyInput.checked=v;}
      sync();
    }
    inputs.forEach(function(i){i.addEventListener('change',function(){
      if(anyInput)anyInput.checked=allOn();
      sync();
    });});
    if(anyInput)anyInput.addEventListener('change',function(){setAll(anyInput.checked);});
    if(btnAll)btnAll.addEventListener('click',function(){
      visibleItems().forEach(function(i){if(i!==anyInput&&!i.checked){i.checked=true;fire(i);}});
    });
    if(btnClear)btnClear.addEventListener('click',function(){
      inputs.forEach(function(i){if(i.checked){i.checked=false;fire(i);}});search.focus();
    });
    done.addEventListener('click',function(){close(true);});
    backdrop.addEventListener('click',function(){close(false);});
    document.addEventListener('mousedown',function(e){
      if(!panel.hidden&&!root.contains(e.target)&&!panel.contains(e.target))close(false);
    });
    document.addEventListener('touchstart',function(e){
      if(!panel.hidden&&!root.contains(e.target)&&!panel.contains(e.target)&&e.target!==backdrop)close(false);
    },{passive:true});
    // forma xəta ilə qayıdanda "Fərq etmir" seçili gələ bilər (server bütün rayonları götürür)
    if(anyInput){if(anyInput.checked&&!allOn())inputs.forEach(function(i){i.checked=true;});anyInput.checked=allOn();}
    // fieldset[disabled] (demo) → açma düyməsi də deaktiv olur (brauzer özü); etiketlərin ✕ düymələri sync-də
    sync();
  }

  Array.prototype.forEach.call(document.querySelectorAll('[data-dp]'),init);
})();
