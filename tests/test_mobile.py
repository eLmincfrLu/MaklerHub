"""Paket M: mobil responsivlik — iPhone 13 emulyasiyası (390px, DPR 3, toxunma, Safari UA; Chrome mühərriki).
Playwright (və ya sistemdə Chrome) yoxdursa bütün testlər skip olunur.
Yoxlanır: üfüqi daşma yoxdur (scrollWidth <= innerWidth) və ana səhifənin əsas blokları üst-üstə düşmür."""
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

PUBLIC = ["/", "/teleb", "/teleb/berpa", "/giris", "/qeydiyyat", "/sertler", "/mexfilik", "/yoxdur-bele-sehife"]

OVERFLOW_JS = "[document.documentElement.scrollWidth, innerWidth]"

BLOCKS_JS = r"""() => {
  const B = {brand: '.hero .top .brand', nav: '.hero .navbtns', banner: '.mhb-slot:not([hidden]) .mhb',
    eyebrow: '.hero .eyebrow', h1: '.hero .content h1', stats: '.hero .stat-chip'};
  const R = {};
  for (const [k, s] of Object.entries(B)) document.querySelectorAll(s).forEach((e, i) => {
    const r = e.getBoundingClientRect(); if (r.height > 0) R[k + (i || '')] = [r.left, r.top, r.right, r.bottom]; });
  return R; }"""


@pytest.fixture(scope="module")
def live(app):
    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture(scope="module")
def iphone():
    with sync_api.sync_playwright() as p:
        browser = None
        for kw in ({"channel": "chrome"}, {}):
            try:
                browser = p.chromium.launch(**kw)
                break
            except Exception:  # noqa: BLE001 — brauzer yoxdur
                continue
        if browser is None:
            pytest.skip("Playwright üçün Chrome/Chromium tapılmadı")
        dev = dict(p.devices["iPhone 13"])
        dev.pop("default_browser_type", None)
        ctx = browser.new_context(**dev)
        page = ctx.new_page()
        yield page
        ctx.close()
        browser.close()


def _no_overflow(page, url):
    page.goto(url, wait_until="load")
    page.wait_for_timeout(400)
    sw, iw = page.evaluate(OVERFLOW_JS)
    assert iw == 390
    assert sw <= iw, f"{url}: üfüqi daşma {sw} > {iw}"


@pytest.mark.parametrize("path", PUBLIC)
def test_public_pages_no_horizontal_overflow(live, iphone, path):
    _no_overflow(iphone, live + path)


def test_customer_page_no_overflow(live, iphone, make_request):
    _, token = make_request(note="superuzunsözsınağıüçünbirsözlükqeydaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    _no_overflow(iphone, f"{live}/teleb/{token}")


def _overlaps(a, b):
    return a[0] < b[2] - 1 and b[0] < a[2] - 1 and a[1] < b[3] - 1 and b[1] < a[3] - 1


def test_landing_blocks_do_not_overlap(live, iphone, make_request):
    _, token = make_request()
    iphone.goto(live + "/", wait_until="load")
    iphone.evaluate("t => localStorage.setItem('mh_requests', JSON.stringify([{token: t, saved_at: Date.now()}]))", token)
    iphone.reload(wait_until="load")
    iphone.wait_for_selector(".mhb-slot:not([hidden]) .mhb", timeout=8000)
    iphone.wait_for_timeout(1800)  # giriş animasiyaları
    rects = iphone.evaluate(BLOCKS_JS)
    iphone.evaluate("localStorage.removeItem('mh_requests')")
    for k in ("brand", "nav", "banner", "eyebrow", "h1", "stats", "stats1", "stats2"):
        assert k in rects, f"{k} görünmür"
    keys = list(rects)
    for k in keys:
        assert rects[k][0] >= -1 and rects[k][2] <= 391, f"{k} ekrandan çıxır: {rects[k]}"
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            assert not _overlaps(rects[a], rects[b]), f"{a} və {b} üst-üstə düşür"
    assert rects["h1"][1] - rects["eyebrow"][3] >= 12, "eyebrow başlığa yapışıb"
    assert rects["banner"][1] >= rects["nav"][3], "banner başlığın altında olmalıdır"
    sw, iw = iphone.evaluate(OVERFLOW_JS)
    assert sw <= iw
