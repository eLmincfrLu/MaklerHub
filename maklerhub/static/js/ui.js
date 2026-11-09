/* MaklerHub — UI davranışı (Paket D3/D4). Bütün base şablonlara qoşulur (defer). Xarici kitabxana yoxdur.
   Toast: server flash mesajları ([data-flash]) ekranın yuxarısına köçürülür; JS işləməsə səhifədə qalır. */
(function () {
  'use strict';

  /* ---------- toast ---------- */
  var flashes = document.querySelectorAll('[data-flash]');
  if (flashes.length) {
    var box = document.createElement('div');
    box.className = 'ui-toasts';
    box.setAttribute('role', 'status');
    box.setAttribute('aria-live', 'polite');
    document.body.appendChild(box);
    Array.prototype.forEach.call(flashes, function (el) {
      var cat = (el.getAttribute('data-flash') || '').toLowerCase();
      var isErr = /err|bad|danger|warn/.test(cat);
      var t = document.createElement('div');
      t.className = 'ui-toast ' + (isErr ? 'is-err' : 'is-ok');
      if (isErr) t.setAttribute('role', 'alert');
      var m = document.createElement('div');
      m.className = 'ui-toast-m';
      while (el.firstChild) m.appendChild(el.firstChild); // serverin hazırladığı (escape olunmuş) məzmun
      var x = document.createElement('button');
      x.type = 'button'; x.className = 'ui-toast-x'; x.setAttribute('aria-label', 'Bağla'); x.textContent = '×';
      t.appendChild(m); t.appendChild(x); box.appendChild(t);
      el.parentNode.removeChild(el);
      var timer = null;
      var close = function () {
        if (timer) clearTimeout(timer);
        t.classList.add('is-out');
        setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 230);
      };
      x.addEventListener('click', close);
      if (!isErr) {
        timer = setTimeout(close, 5000);
        // oxuyarkən (hover/fokus) bağlanmasın
        t.addEventListener('mouseenter', function () { clearTimeout(timer); });
        t.addEventListener('mouseleave', function () { timer = setTimeout(close, 2500); });
      }
    });
  }
})();

/* ---------- Paket D4: animasiyalar ----------
   Yalnız JS-in qoyduğu siniflər məzmunu gizlədir (.ui-seg, .ui-rv); JS işləməsə hər şey görünür.
   prefers-reduced-motion: reduce olanda heç nə edilmir. Admin (.shell) — yalnız səhifə fade-in-i (CSS). */
(function () {
  'use strict';
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduce || document.querySelector('.shell')) return;

  /* kart başlıqları: hər hissə (mətn və <i>) aşağıdan qalxır, 100ms aralıqla */
  Array.prototype.forEach.call(document.querySelectorAll('.stage .card > h1'), function (h) {
    if (h.querySelector('.ui-seg')) return;
    var i = 0;
    Array.prototype.slice.call(h.childNodes).forEach(function (n) {
      var text = n.nodeType === 3 ? n.textContent : null;
      if (text !== null && !text.trim()) return;
      var seg = document.createElement('span');
      seg.className = 'ui-seg';
      seg.style.setProperty('--i', i++);
      if (text !== null) {
        // kənar boşluqlar span-dan kənarda qalsın (inline-block onları yeyir)
        var lead = text.match(/^\s*/)[0], trail = text.match(/\s*$/)[0];
        seg.textContent = text.trim();
        if (lead) h.insertBefore(document.createTextNode(lead), n);
        h.insertBefore(seg, n);
        if (trail) h.insertBefore(document.createTextNode(trail), n);
        h.removeChild(n);
      } else {
        h.insertBefore(seg, n); seg.appendChild(n);
      }
    });
  });

  /* scroll ilə bir dəfə görünmə: yalnız ekrandan KƏNARDA olan bloklar gizlədilir (yuxarıdakılar sıçramasın) */
  if (!('IntersectionObserver' in window)) return;
  var targets = document.querySelectorAll('.offers > .offer, .stage .sum, .stage .em, .stage .lk, .stage details.rejected, ' +
    '.stage details.closebox, .ui-empty');
  var vh = window.innerHeight || 800;
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (!e.isIntersecting) return;
      var el = e.target;
      el.classList.add('ui-in');
      io.unobserve(el);
      // görünmə bitəndən sonra köməkçi siniflər silinir — hover keçidləri gecikmədən işləsin
      var done = function (ev) {
        if (ev && ev.target !== el) return;
        el.removeEventListener('transitionend', done);
        el.classList.remove('ui-rv', 'ui-in'); el.style.removeProperty('--i');
      };
      el.addEventListener('transitionend', done);
      setTimeout(done, 1500);
    });
  }, { rootMargin: '0px 0px -8% 0px', threshold: 0.05 });
  var perParent = [];
  Array.prototype.forEach.call(targets, function (el) {
    if (el.getBoundingClientRect().top < vh) return; // ilk ekrandadır — dərhal görünür
    var p = el.parentNode, k = perParent.indexOf(p);
    if (k < 0) { perParent.push(p); perParent.push(0); k = perParent.length - 2; }
    var idx = perParent[k + 1]++;
    el.style.setProperty('--i', Math.min(idx, 5)); // 60ms stagger, maks. 6 element
    el.classList.add('ui-rv');
    io.observe(el);
  });
})();
