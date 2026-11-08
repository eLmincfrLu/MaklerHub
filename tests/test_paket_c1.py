"""Paket C1: tələb linkinin yaddaşı (status API), "Linki saxla" bloku, QR, manifest (YALNIZ test bazasında)."""
import json
import re
import secrets

import pytest

from conftest import csrf

ORIGIN = {"Origin": "http://localhost"}
PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def clear_rate():
    from maklerhub import teleb
    teleb._status_hits.clear()
    yield
    teleb._status_hits.clear()


def status(client, tokens, headers=ORIGIN):
    return client.post("/api/teleb/status", json={"tokens": tokens}, headers=headers)


def ago(hours):
    from datetime import timedelta
    from maklerhub.helpers import now, ts
    return ts(now() - timedelta(hours=hours))


def add_offer(db, make_makler, rid, status="sent", created=None):
    created = created or ago(1)
    _, uid = make_makler(login_now=False)
    db.execute("INSERT INTO offers(request_id, agent_id, title, price, district, rooms, status, created_at) "
               "VALUES (?,?,?,?,?,?,?,?)", (rid, uid, "Təklif", 800, "Yasamal", 2, status, created))
    db.commit()


# ---------------------------------------------------------------- status API
def test_status_counts_and_no_personal_data(app, db, make_makler, make_request):
    rid, token = make_request(districts=["Yasamal"], rooms=2, name="Aysel Gizli", phone="+994705550011")
    db.execute("UPDATE customer_requests SET customer_seen_at = ? WHERE id = ?", (ago(2), rid))
    db.commit()
    add_offer(db, make_makler, rid)                                        # yeni
    add_offer(db, make_makler, rid, created=ago(3))         # baxışdan əvvəl
    add_offer(db, make_makler, rid, status="withdrawn")                    # sayılmır
    add_offer(db, make_makler, rid, status="rejected", created=ago(4))
    c = app.test_client()
    r = status(c, [token])
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    res = r.get_json()["results"]
    assert res == [{"found": True, "status": "active", "new_offers": 1, "total_offers": 3,
                    "summary": "Kirayə · 2 otaq · Yasamal"}]
    body = r.get_data(as_text=True)
    assert "Aysel" not in body and "555" not in body and "+994" not in body and token not in body
    # müştəri səhifəyə baxandan sonra yeni təklif yoxdur
    c.get(f"/teleb/{token}")
    assert status(c, [token]).get_json()["results"][0]["new_offers"] == 0


def test_status_closed_expired_and_summary(app, db, make_request):
    from maklerhub.helpers import BAKU_DISTRICTS
    _, t_closed = make_request(status="closed")
    _, t_old = make_request(age_days=40)                           # lazy yeniləmə işləməyib, amma bitib
    _, t_all = make_request(deal="sale", rooms=5, districts=list(BAKU_DISTRICTS), bmin=80000, bmax=150000)
    _, t_many = make_request(districts=["Yasamal", "Nəsimi", "Səbail", "Xətai"])
    res = status(app.test_client(), [t_closed, t_old, t_all, t_many]).get_json()["results"]
    assert [x["status"] for x in res] == ["closed", "expired", "active", "active"]
    assert res[2]["summary"] == "Alış · 5+ otaq · Bütün Bakı"
    assert res[3]["summary"] == "Kirayə · 2 otaq · Yasamal, Nəsimi +2"


def test_status_not_found_only_flag(app, make_request):
    _, token = make_request()
    fake = secrets.token_urlsafe(24)
    res = status(app.test_client(), [fake, token, "qısa", "x" * 200]).get_json()["results"]
    assert res[0] == {"found": False} and res[2] == {"found": False} and res[3] == {"found": False}
    assert res[1]["found"] is True


def test_status_same_work_for_found_and_missing(app, db, make_request, monkeypatch):
    """Tapılan və tapılmayan token üçün eyni sayda SQL sorğusu (vaxt fərqi ilə təxmin çətin olsun)."""
    from maklerhub import teleb
    _, token = make_request()
    counts = []
    real_get_db = teleb.get_db

    class Counter:
        def __init__(self, conn):
            self.conn, self.n = conn, 0

        def execute(self, *a, **kw):
            self.n += 1
            return self.conn.execute(*a, **kw)

        def __getattr__(self, name):
            return getattr(self.conn, name)

    def counting():
        c = Counter(real_get_db())
        counts.append(c)
        return c

    monkeypatch.setattr(teleb, "get_db", counting)
    c = app.test_client()
    for toks in ([token], [secrets.token_urlsafe(24)], ["bad!"], [token] + [secrets.token_urlsafe(24)] * 4):
        counts.clear()
        assert status(c, toks).status_code == 200
        assert sum(x.n for x in counts) == 2, toks


def test_status_limits_and_validation(app, make_request):
    _, token = make_request()
    c = app.test_client()
    assert status(c, [token] * 6).status_code == 400
    assert status(c, []).status_code == 400
    assert status(c, [123]).status_code == 400
    assert c.post("/api/teleb/status", data="x", headers=ORIGIN).status_code == 400
    assert c.post("/api/teleb/status", json={"token": token}, headers=ORIGIN).status_code == 400
    # CSRF tokeni tələb olunmur (yuxarıdakı 200-lər), GET yoxdur
    assert c.get("/api/teleb/status").status_code == 405


def test_status_origin_check(app, make_request):
    _, token = make_request()
    c = app.test_client()
    assert status(c, [token], headers={}).status_code == 403
    assert status(c, [token], headers={"Origin": "https://evil.example"}).status_code == 403
    assert status(c, [token], headers={"Origin": "null"}).status_code == 403
    assert status(c, [token], headers={"Origin": "http://localhost.evil.example"}).status_code == 403
    assert status(c, [token], headers={"Referer": "https://evil.example/localhost"}).status_code == 403
    assert status(c, [token], headers={"Referer": "http://localhost/"}).status_code == 200


def test_status_rate_limit_per_ip(app, make_request):
    _, token = make_request()
    c = app.test_client()
    c.environ_base["REMOTE_ADDR"] = PUBLIC_IP
    codes = [status(c, [token]).status_code for _ in range(21)]
    assert codes[:20] == [200] * 20 and codes[20] == 429
    other = app.test_client()
    other.environ_base["REMOTE_ADDR"] = "93.184.216.35"
    assert status(other, [token]).status_code == 200


# ---------------------------------------------------------------- QR və manifest
def test_qr_and_manifest(app, make_request):
    _, token = make_request()
    c = app.test_client()
    r = c.get(f"/teleb/{token}/qr.svg")
    assert r.status_code == 200 and r.headers["Content-Type"].startswith("image/svg+xml")
    assert r.headers["Cache-Control"] == "private, no-store" and "noindex" in r.headers["X-Robots-Tag"]
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert r.get_data(as_text=True).lstrip().startswith("<svg")
    m = c.get(f"/teleb/{token}/manifest.webmanifest")
    assert m.status_code == 200 and m.headers["Content-Type"].startswith("application/manifest+json")
    assert m.headers["Cache-Control"] == "private, no-store" and "noindex" in m.headers["X-Robots-Tag"]
    data = json.loads(m.get_data(as_text=True))
    assert data["name"] == "MaklerHub — Tələbim" and data["display"] == "standalone"
    assert data["start_url"] == f"/teleb/{token}" and data["scope"] == f"/teleb/{token}"
    sizes = {i["sizes"] for i in data["icons"]}
    assert {"192x192", "512x512"} <= sizes
    for i in data["icons"]:
        assert c.get(i["src"]).status_code == 200
    assert c.get("/static/img/apple-touch-icon.png").status_code == 200


@pytest.mark.parametrize("path", ["qr.svg", "manifest.webmanifest"])
def test_qr_manifest_wrong_token_404(app, path):
    c = app.test_client()
    assert c.get(f"/teleb/{secrets.token_urlsafe(24)}/{path}").status_code == 404
    assert c.get(f"/teleb/qisa/{path}").status_code == 404


# ---------------------------------------------------------------- səhifələr
def test_customer_page_share_block(app, db):
    c = app.test_client()
    tok = csrf(c, "/teleb")
    r = c.post("/teleb", data={"csrf_token": tok, "name": "Test", "phone": "+994 70 555 22 11", "deal": "rent",
                               "rooms": "2", "budget_min": "", "budget_max": "900", "urgency": "month",
                               "consent": "on", "website": "", "districts": ["Yasamal"]})
    assert r.status_code == 302
    url = r.headers["Location"]
    token = url.rsplit("/", 1)[1]
    first = c.get(url).get_data(as_text=True)
    # yenicə yaradılıb: böyük blok yuxarıda; token cookie-yə yazılmır
    assert 'class="lk lk-big"' in first and first.index("lk-big") < first.index('class="sum"')
    for t in ("Bu link sənin tələbinin açarıdır. Təkliflər burada görünəcək. Linki itirməmək üçün özünə göndər.",
              "Kopyala", "Kopyalandı ✓", "WhatsApp", "Telegram", "E-poçt", "QR kod", "Ana ekrana əlavə et",
              "Linki yalnız özünə göndər — linkə sahib olan hər kəs təkliflərini görə bilər."):
        assert t in first, t
    assert "https://wa.me/?text=" in first and "https://t.me/share/url?url=" in first and "mailto:?subject=" in first
    assert f'href="/teleb/{token}/manifest.webmanifest"' in first and "apple-touch-icon" in first
    assert 'name="apple-mobile-web-app-capable"' in first and 'id="qrDlg"' in first and 'id="iosDlg"' in first
    assert f'data-src="/teleb/{token}/qr.svg"' in first and "serviceWorker" not in first
    assert f'data-mh-save="{token}" data-mh-alive="1"' in first and "teleb-memory.js" in first
    assert token not in str(c.get_cookie("session").value if c.get_cookie("session") else "")
    # sonrakı baxış: kiçik blok, aşağıda
    again = c.get(url).get_data(as_text=True)
    assert "lk-big" not in again and 'class="lk"' in again and again.index('class="lk"') > again.index('class="sum"')


def test_closed_request_is_forgotten(app, make_request):
    _, token = make_request(status="closed")
    html = app.test_client().get(f"/teleb/{token}").get_data(as_text=True)
    assert f'data-mh-save="{token}" data-mh-alive="0"' in html


def test_banner_slots_on_landing_and_form(app):
    c = app.test_client()
    for path in ("/", "/teleb"):
        html = c.get(path).get_data(as_text=True)
        assert re.search(r'data-mh-banner data-api="/api/teleb/status"', html), path
        assert "teleb-memory.js" in html and "mh-banner.css" in html


# ---------------------------------------------------------------- Sentry
def test_sentry_scrubs_status_tokens():
    from maklerhub.monitoring import before_send
    tok = secrets.token_urlsafe(24)
    ev = before_send({"request": {"url": f"http://x/teleb/{tok}/qr.svg", "data": {"tokens": [tok]}},
                      "extra": {"tokens": [tok], "body": {"token": tok}},
                      "breadcrumbs": {"values": [{"message": f"GET /teleb/{tok}/manifest.webmanifest"}]}})
    assert tok not in json.dumps(ev, ensure_ascii=False)
