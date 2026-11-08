/* Ana səhifə: bölmələrin scroll ilə bir dəfə görünməsi (IntersectionObserver) və "Maklerlər üçün" linkinə
   yumşaq scroll. Azaldılmış hərəkətdə və ya köhnə brauzerdə hər şey dərhal görünür. */
(function(){
  'use strict';
  var items=Array.prototype.slice.call(document.querySelectorAll('[data-reveal]'));
  var reduce=window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function showAll(){items.forEach(function(el){el.classList.add('is-in');});}

  // eyni valideyndəki ardıcıl elementlər pilləli (stagger) görünsün
  var groups=new Map();
  items.forEach(function(el){
    var p=el.parentNode,n=groups.get(p)||0;
    el.style.setProperty('--d',(Math.min(n,5)*0.09)+'s');groups.set(p,n+1);
  });

  if(reduce||!('IntersectionObserver' in window)){showAll();}
  else{
    var io=new IntersectionObserver(function(entries){
      entries.forEach(function(e){if(e.isIntersecting){e.target.classList.add('is-in');io.unobserve(e.target);}});
    },{threshold:0.12,rootMargin:'0px 0px -6% 0px'});
    items.forEach(function(el){io.observe(el);});
  }

  document.querySelectorAll('a[data-scroll][href^="#"]').forEach(function(a){
    a.addEventListener('click',function(e){
      var t=document.querySelector(a.getAttribute('href'));if(!t)return;
      e.preventDefault();
      t.scrollIntoView({behavior:reduce?'auto':'smooth',block:'start'});
      if(history.replaceState)history.replaceState(null,'',a.getAttribute('href'));
    });
  });
})();
