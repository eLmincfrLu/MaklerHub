/* Hesab menyusu ([data-usermenu]): klik/toxunma ilə açılır, kənara basanda və ESC ilə bağlanır,
   ox düymələri ilə menyu bəndləri arasında gəzmək olur. */
(function(){
  'use strict';
  document.querySelectorAll('[data-usermenu]').forEach(function(root){
    var btn=root.querySelector('[aria-haspopup]'),menu=root.querySelector('#'+btn.getAttribute('aria-controls'));
    if(!btn||!menu)return;
    function items(){return Array.prototype.slice.call(menu.querySelectorAll('.lu-item'));}
    function open(focusFirst){
      menu.hidden=false;btn.setAttribute('aria-expanded','true');
      if(focusFirst){var it=items();if(it.length)it[0].focus();}
    }
    function close(refocus){
      if(menu.hidden)return;
      menu.hidden=true;btn.setAttribute('aria-expanded','false');
      if(refocus)btn.focus();
    }
    btn.addEventListener('click',function(e){e.stopPropagation();if(menu.hidden)open(e.detail===0);else close(false);});
    btn.addEventListener('keydown',function(e){if(e.key==='ArrowDown'){e.preventDefault();open(true);}});
    menu.addEventListener('keydown',function(e){
      var it=items(),i=it.indexOf(document.activeElement);
      if(e.key==='Escape'){e.preventDefault();close(true);}
      else if(e.key==='ArrowDown'){e.preventDefault();it[(i+1)%it.length].focus();}
      else if(e.key==='ArrowUp'){e.preventDefault();it[(i-1+it.length)%it.length].focus();}
      else if(e.key==='Tab'&&((e.shiftKey&&i===0)||(!e.shiftKey&&i===it.length-1))){close(false);}
    });
    document.addEventListener('click',function(e){if(!root.contains(e.target))close(false);});
    document.addEventListener('touchstart',function(e){if(!root.contains(e.target))close(false);},{passive:true});
    document.addEventListener('keydown',function(e){if(e.key==='Escape')close(true);});
  });
})();
