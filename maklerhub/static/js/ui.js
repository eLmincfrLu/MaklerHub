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
