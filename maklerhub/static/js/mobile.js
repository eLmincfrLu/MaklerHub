/* MaklerHub — mobil davranış (Paket M). Bütün base şablonlara qoşulur (defer). Xarici kitabxana yoxdur.
   1) Ana səhifə: ☰ menyu, başlıqdakı "Tələbini yaz" hero düyməsi görünəndə gizlənir (IntersectionObserver),
      başlıq sürüşdürüləndə tünd fon alır.
   2) Overlay-lər (dialog, ☰ menyu, avatar menyusu, rayon seçicinin alt pəncərəsi): açıq olanda arxa fon sürüşmür,
      brauzerin "geri" düyməsi bağlayır (Esc onların öz JS-i ilə işləyir).
   3) Klaviatura açılanda aktiv sahə görünən qalır (scrollIntoView).
   4) Təklif şəkilləri kartda kəsilmir: object-fit:contain + eyni şəklin bulanıq fonu. */
(function () {
  'use strict';
  var mq = window.matchMedia ? window.matchMedia('(max-width:640px)') : { matches: false };
  var root = document.documentElement;

  /* ---------- 1. ana səhifə başlığı ---------- */
  var top = document.querySelector('.hero .top');
  if (top) {
    var onScroll = function () { top.classList.toggle('mm-solid', window.scrollY > 24); };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
    var heroCta = document.querySelector('.hero .hero-cta');
    if (heroCta && 'IntersectionObserver' in window) {
      new IntersectionObserver(function (es) {
        top.classList.toggle('cta-seen', es[0].isIntersecting);
      }, { rootMargin: '-64px 0px 0px 0px' }).observe(heroCta);
    }
  }
  var mm = document.querySelector('[data-mm]');
  if (mm) {
    var mmBtn = mm.querySelector('.mm-btn'), mmMenu = mm.querySelector('.mm-menu');
    var setMm = function (open) {
      mmMenu.hidden = !open; mmBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
      if (open) { var f = mmMenu.querySelector('a'); if (f) f.focus({ preventScroll: true }); }
    };
    mmBtn.addEventListener('click', function (e) { e.stopPropagation(); setMm(mmMenu.hidden); });
    document.addEventListener('click', function (e) { if (!mmMenu.hidden && !mm.contains(e.target)) setMm(false); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !mmMenu.hidden) { setMm(false); mmBtn.focus(); } });
    mmMenu.addEventListener('click', function (e) {
      var a = e.target.closest('a'); if (!a) return;
      var h = a.getAttribute('href');
      if (h && h.charAt(0) === '#') {
        e.preventDefault(); setMm(false);
        var t = document.querySelector(h);
        setTimeout(function () { if (t) t.scrollIntoView({ behavior: 'smooth', block: 'start' }); }, 60);
      }
    });
    mm.closeOverlay = function () { setMm(false); };
  }

  /* ---------- 2. overlay: scroll kilidi + "geri" ---------- */
  var pushed = false, ignorePop = false;
  function openOverlays() {
    var list = [];
    Array.prototype.forEach.call(document.querySelectorAll('dialog[open]'), function (d) {
      list.push({ el: d, close: function () { d.close(); } });
    });
    if (mm && !mm.querySelector('.mm-menu').hidden) list.push({ el: mm, close: mm.closeOverlay });
    if (mq.matches) {
      var lu = document.querySelector('.lu-menu:not([hidden])');
      if (lu) list.push({ el: lu, close: function () { var b = document.querySelector('.lu-who'); if (b) b.click(); } });
    }
    var dp = document.querySelector('.dp-sheet:not([hidden])');
    if (dp) list.push({ el: dp, close: function () {
      dp.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    } });
    return list;
  }
  function sync() {
    var open = openOverlays().length > 0;
    root.classList.toggle('mh-lock', open && mq.matches);
    if (open && !pushed) {
      try { history.pushState({ mhOverlay: 1 }, ''); pushed = true; } catch (e) { pushed = false; }
    } else if (!open && pushed) {
      pushed = false; ignorePop = true;
      try { history.back(); } catch (e) { ignorePop = false; }
    }
  }
  window.addEventListener('popstate', function () {
    if (ignorePop) { ignorePop = false; return; }
    if (!pushed) return;
    pushed = false;
    openOverlays().forEach(function (o) { o.close(); });
    root.classList.remove('mh-lock');
  });
  if ('MutationObserver' in window) {
    var pending = false;
    new MutationObserver(function () {
      if (pending) return; pending = true;
      (window.requestAnimationFrame || setTimeout)(function () { pending = false; sync(); });
    }).observe(document.body, { subtree: true, attributes: true, attributeFilter: ['open', 'hidden', 'class'], childList: true });
  }

  /* ---------- 3. klaviatura: aktiv sahə görünsün ---------- */
  var coarse = window.matchMedia && window.matchMedia('(pointer:coarse)').matches;
  if (coarse) {
    document.addEventListener('focusin', function (e) {
      var el = e.target;
      if (!el.matches || !el.matches('input:not([type=checkbox]):not([type=radio]):not([type=hidden]),select,textarea')) return;
      setTimeout(function () {
        var vv = window.visualViewport, h = vv ? vv.height : window.innerHeight, r = el.getBoundingClientRect();
        if (r.top < 70 || r.bottom > h - 20) el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      }, 320);
    });
  }

  /* ---------- 4. təklif şəkilləri: contain + bulanıq fon ---------- */
  function frame(img) {
    if (img.parentNode.classList.contains('mh-fr')) return;
    var src = img.currentSrc || img.getAttribute('src');
    if (!src) return;
    var fr = document.createElement('span');
    fr.className = 'mh-fr';
    fr.style.setProperty('--fr', 'url("' + src.replace(/"/g, '%22') + '")');
    img.parentNode.insertBefore(fr, img);
    fr.appendChild(img);
  }
  Array.prototype.forEach.call(document.querySelectorAll('.offer .gal-track img, .offer > img'), function (img) {
    if (img.complete) frame(img); else img.addEventListener('load', function () { frame(img); }, { once: true });
  });
})();
