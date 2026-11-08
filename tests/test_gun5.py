"""Gün 5: təmizlik, demo rejimi, təhlükəsizlik başlıqları, robots.txt, Sentry (YALNIZ test bazasında)."""
import json
import secrets
from datetime import timedelta

import pytest

from conftest import csrf, login
from test_flow import RENT_AREA, ids_in, send

def ago(days=0, hours=0):
    from maklerhub.helpers import now, ts
    return ts(now() - timedelta(days=days, hours=hours))


# ---------------------------------------------------------------- G1: təmizlik
def _seed_old_rows(db, uid, tag):
    def note(read, days):
        return db.execute("INSERT INTO notifications(user_id, kind, text, is_read, created_at) VALUES (?, 'x', ?, ?, ?)",
                          (uid, f"{tag}-{read}-{days}", read, ago(days))).lastrowid

    def attempt(days):
        return db.execute("INSERT INTO login_attempts(email, ip, success, created_at) VALUES (?, '', 0, ?)",
                          (f"{tag}-{days}@x.az", ago(days))).lastrowid

    def logrow(days):
        return db.execute("INSERT INTO logs(created_at, level, source, message) VALUES (?, 'info', 'Sistem', ?)",
                          (ago(days), f"{tag}-{days}")).lastrowid

    def sess(active, days):
        return db.execute("INSERT INTO sessions(user_id, token, active, created_at, last_seen_at) VALUES (?,?,?,?,?)",
                          (uid, secrets.token_hex(16), active, ago(days + 1), ago(days))).lastrowid
    gone = {"notifications": [note(1, 31), note(0, 91)], "login_attempts": [attempt(8)], "logs": [logrow(91)],
            "sessions": [sess(0, 31)]}
    kept = {"notifications": [note(1, 10), note(0, 60)], "login_attempts": [attempt(1)], "logs": [logrow(10)],
            "sessions": [sess(0, 5), sess(1, 40)]}
    db.commit()
    return gone, kept


def _exists(db, table, ids):
    return {r[0] for r in db.execute(f"SELECT id FROM {table} WHERE id = ANY(?)", (ids,))}


def test_cleanup_deletes_old_keeps_new(app, db, make_makler):
    from maklerhub import maintenance
    _, uid = make_makler(login_now=False)
    gone, kept = _seed_old_rows(db, uid, "g1-" + secrets.token_hex(3))
    counts = maintenance.run_cleanup(db)
    for table in gone:
        assert _exists(db, table, gone[table]) == set(), table
        assert _exists(db, table, kept[table]) == set(kept[table]), table
    assert counts["notifications_read"] >= 1 and counts["notifications_unread"] >= 1 and counts["logs"] >= 1
    at, stats = maintenance.last_cleanup(db)
    assert at and dict(stats)["Sistem loqları (90 gün)"] == counts["logs"]


def test_cleanup_at_most_once_per_hour(app, db):
    from maklerhub import maintenance
    assert maintenance.cleanup_if_due(db) is not None       # açar yoxdur → işləyir
    assert maintenance.cleanup_if_due(db) is None           # 1 saat keçməyib
    db.execute("UPDATE settings SET value = ? WHERE key = 'last_cleanup_at'", (ago(hours=2),))
    db.commit()
    assert maintenance.cleanup_if_due(db) is not None
    # lazy yeniləmə: dəqiqədə ən çox 1 dəfə
    maintenance.reset_throttle()
    assert maintenance.lazy_expire(db) is True
    maintenance.reset_throttle()                              # yaddaş təmiz olsa da baza açarı saxlayır
    assert maintenance.lazy_expire(db) is False


def _last_cleanup_at(db):
    row = db.execute("SELECT value FROM settings WHERE key = 'last_cleanup_at'").fetchone()
    return row[0] if row else None


def test_cleanup_runs_after_request_and_errors_do_not_break(app, db, make_request, monkeypatch):
    from maklerhub import maintenance
    _, token = make_request()
    app.config["CLEANUP_DISABLED"] = False
    try:
        # bazaya toxunmayan sorğu (/saglamliq) təmizliyi işə salmır
        r = app.test_client().get("/saglamliq")
        r.close()
        assert r.status_code == 200 and _last_cleanup_at(db) is None
        r = app.test_client().get(f"/teleb/{token}")
        assert r.status_code == 200
        r.close()  # cavab bağlananda (göndəriləndən sonra) işləyir
        assert _last_cleanup_at(db) is not None

        # xəta: sorğu sınmır, loqa yazılır
        db.execute("DELETE FROM settings WHERE key = 'last_cleanup_at'")
        db.commit()
        maintenance.reset_throttle()

        def boom(_con):
            raise RuntimeError("sınaq xətası")
        monkeypatch.setattr(maintenance, "run_cleanup", boom)
        r = app.test_client().get(f"/teleb/{token}")
        assert r.status_code == 200
        r.close()
        assert db.execute("SELECT 1 FROM logs WHERE message LIKE 'Avtomatik təmizlik uğursuz oldu%' "
                          "AND details LIKE '%sınaq xətası%'").fetchone()
    finally:
        app.config["CLEANUP_DISABLED"] = True


def test_admin_cleanup_button(app, db, admin_client):
    html = admin_client.get("/admin/ayarlar").get_data(as_text=True)
    assert "Təmizliyi indi işlət" in html
    tok = csrf(admin_client, "/admin/ayarlar")
    r = admin_client.post("/admin/ayarlar/temizlik", data={"csrf_token": tok})
    assert r.status_code == 302
    html = admin_client.get("/admin/ayarlar").get_data(as_text=True)
    assert "Təmizlik tamamlandı" in html and "Oxunmuş bildirişlər (30 gün)" in html and "hələ olmayıb" not in html


# ---------------------------------------------------------------- G2: demo izolyasiyası
@pytest.fixture
def make_demo_makler(app, db, make_makler):
    def _make(areas=RENT_AREA):
        email, uid = make_makler(areas, login_now=False)
        db.execute("UPDATE users SET is_demo = 1 WHERE id = ?", (uid,))
        db.commit()
        client, r = login(app, email, "TestPass123")
        assert r.status_code == 302
        return client, uid, email
    return _make


def _demo_request(db, make_request, **kw):
    rid, token = make_request(**kw)
    db.execute("UPDATE customer_requests SET is_demo = 1 WHERE id = ?", (rid,))
    db.commit()
    return rid, token


def test_demo_and_real_are_isolated(app, db, make_makler, make_demo_makler, make_request):
    real_rid, _ = make_request(districts=["Yasamal"])
    demo_rid, _ = _demo_request(db, make_request, districts=["Yasamal"])
    demo, demo_uid, _ = make_demo_makler()
    real, real_uid = make_makler(RENT_AREA)

    ids = ids_in(demo.get("/kabinet?view=all").get_data(as_text=True))
    assert demo_rid in ids and real_rid not in ids
    assert demo.get(f"/kabinet/teleb/{real_rid}").status_code == 404
    assert send(demo, demo_rid).status_code == 302
    tok = csrf(demo, f"/kabinet/teleb/{demo_rid}")
    assert demo.post(f"/kabinet/teleb/{real_rid}/teklif", data={"csrf_token": tok, "title": "x"}).status_code == 404
    assert not db.execute("SELECT 1 FROM offers WHERE request_id = ? AND agent_id = ?", (real_rid, demo_uid)).fetchone()

    ids = ids_in(real.get("/kabinet?view=all").get_data(as_text=True))
    assert real_rid in ids and demo_rid not in ids
    assert real.get(f"/kabinet/teleb/{demo_rid}").status_code == 404
    assert real.post(f"/kabinet/teleb/{demo_rid}/teklif", data={"csrf_token": csrf(real, "/kabinet"),
                                                                "title": "x"}).status_code == 404

    # servis səviyyəsində də (marşrutdan yan keçsə belə)
    from maklerhub import offers
    oid, error = offers.send_offer(db, real_rid, demo_uid, {"title": "t", "price": 600, "district": "Yasamal",
                                                            "rooms": 2, "area": None, "image_url": None,
                                                            "description": ""})
    assert oid is None and error

    # demo makler öz təklifini geri çəkə bilir
    oid = db.execute("SELECT id FROM offers WHERE request_id = ? AND agent_id = ?", (demo_rid, demo_uid)).fetchone()[0]
    r = demo.post(f"/kabinet/teklif/{oid}/geri-cek", data={"csrf_token": csrf(demo, "/kabinet/teklifler")})
    assert r.status_code == 302
    assert db.execute("SELECT status FROM offers WHERE id = ?", (oid,)).fetchone()[0] == "withdrawn"


def test_demo_request_does_not_notify_real_agents(app, db, make_makler, make_demo_makler, make_request):
    from maklerhub.offers import notify_new_request
    _, real_uid = make_makler(RENT_AREA)
    _, demo_uid, _ = make_demo_makler()
    demo_rid, _ = _demo_request(db, make_request, districts=["Yasamal"])
    real_rid, _ = make_request(districts=["Yasamal"])
    base = {"deal_type": "rent", "districts": ["Yasamal"], "budget_min": 500, "budget_max": 900, "summary": "s"}
    notify_new_request(db, dict(base, id=demo_rid, is_demo=1))
    notify_new_request(db, dict(base, id=real_rid, is_demo=0))
    db.commit()

    def got(uid, rid):
        return db.execute("SELECT 1 FROM notifications WHERE user_id = ? AND request_id = ?", (uid, rid)).fetchone()
    assert got(demo_uid, demo_rid) and not got(real_uid, demo_rid)
    assert got(real_uid, real_rid) and not got(demo_uid, real_rid)


def test_demo_makler_cannot_change_settings(app, db, make_demo_makler):
    demo, uid, _ = make_demo_makler()
    html = demo.get("/ayarlar").get_data(as_text=True)
    assert "yalnız baxış üçündür" in html
    # Paket B: düymələr gizlənmir, deaktiv sahədə (fieldset disabled) aydın deaktiv görünür
    assert html.count('disabled class="ro-zone" data-tip="Demo hesabda dəyişdirilmir"') == 3
    for label in ("Dəyişiklikləri yadda saxla", "Xidmət sahəsini yadda saxla", "Şifrəni yenilə"):
        assert label in html
    tok = csrf(demo, "/ayarlar")
    h = {"X-CSRF-Token": tok}
    r1 = demo.post("/api/me/profile", json={"first_name": "Hack", "last_name": "X", "phone": "+994501112233"}, headers=h)
    r2 = demo.post("/api/me/areas", json={"rent": {"on": False}, "sale": {"on": False}}, headers=h)
    r3 = demo.post("/api/password", json={"current": "TestPass123", "new": "Yeni12345", "new2": "Yeni12345"}, headers=h)
    assert (r1.status_code, r2.status_code, r3.status_code) == (403, 403, 403)
    u = db.execute("SELECT first_name, password_hash FROM users WHERE id = ?", (uid,)).fetchone()
    assert u["first_name"] != "Hack"
    from werkzeug.security import check_password_hash
    assert check_password_hash(u["password_hash"], "TestPass123")
    assert db.execute("SELECT COUNT(*) FROM agent_areas WHERE user_id = ?", (uid,)).fetchone()[0] > 0


def test_demo_login_from_many_devices(app, db, make_demo_makler, make_makler):
    _, uid, email = make_demo_makler()
    clients = []
    for _ in range(4):  # cihaz limiti 2-dir; demo üçün tətbiq olunmur
        c, r = login(app, email, "TestPass123")
        assert r.status_code == 302 and r.headers["Location"].endswith("/kabinet")
        clients.append(c)
    for c in clients:  # tək sessiya qaydası da yoxdur: hamısı eyni anda işləyir
        assert c.get("/kabinet").status_code == 200
    # real makler üçün limit qalır
    real_email, _ = make_makler(login_now=False)
    statuses = [login(app, real_email, "TestPass123")[1].status_code for _ in range(3)]
    assert statuses[:2] == [302, 302] and statuses[2] == 403  # Paket A: ayrıca "Cihaz limiti dolub" səhifəsi


def test_demo_wrong_passwords_do_not_lock_demo(app, db, make_demo_makler):
    _, _, email = make_demo_makler()
    for _ in range(6):
        login(app, email, "yanlis-sifre1")
    c, r = login(app, email, "TestPass123")
    assert r.status_code == 302


# ---------------------------------------------------------------- G3: seed-demo / clear-demo
def _counts(db):
    q = lambda sql: db.execute(sql).fetchone()[0]  # noqa: E731
    return {"demo_users": q("SELECT COUNT(*) FROM users WHERE is_demo = 1"),
            "demo_reqs": q("SELECT COUNT(*) FROM customer_requests WHERE is_demo = 1"),
            "demo_offers": q("SELECT COUNT(*) FROM offers o JOIN customer_requests r ON r.id = o.request_id "
                             "WHERE r.is_demo = 1"),
            "demo_contacts": q("SELECT COUNT(*) FROM contacts c JOIN customer_requests r ON r.id = c.request_id "
                               "WHERE r.is_demo = 1"),
            "demo_lic": q("SELECT COUNT(*) FROM licenses WHERE note = 'demo'"),
            "real_users": q("SELECT COUNT(*) FROM users WHERE is_demo = 0"),
            "real_reqs": q("SELECT COUNT(*) FROM customer_requests WHERE is_demo = 0"),
            "real_offers": q("SELECT COUNT(*) FROM offers o JOIN customer_requests r ON r.id = o.request_id "
                             "WHERE r.is_demo = 0"),
            "real_notifs": q("SELECT COUNT(*) FROM notifications n LEFT JOIN users u ON u.id = n.user_id "
                             "LEFT JOIN customer_requests r ON r.id = n.request_id "
                             "WHERE COALESCE(u.is_demo, 0) = 0 AND COALESCE(r.is_demo, 0) = 0"),
            "real_lic": q("SELECT COUNT(*) FROM licenses WHERE note != 'demo'")}


def test_seed_demo_twice_no_duplicates_and_clear(app, db, make_makler, make_request):
    from maklerhub.auth import demo_password
    client, _ = make_makler(RENT_AREA)
    rid, _ = make_request()
    assert send(client, rid).status_code == 302  # real məlumat
    runner = app.test_cli_runner()
    assert runner.invoke(args=["clear-demo"]).exit_code == 0  # əvvəlki testlərin demo sətirləri
    before = _counts(db)
    for _ in range(2):
        res = runner.invoke(args=["seed-demo"])
        assert res.exit_code == 0, res.output
        assert res.output.count("/teleb/") == 2
    after = _counts(db)
    assert (after["demo_users"], after["demo_reqs"], after["demo_lic"]) == (3, 8, 3)
    assert 6 <= after["demo_offers"] <= 8 and after["demo_contacts"] >= 2
    for k in ("real_users", "real_reqs", "real_offers", "real_notifs", "real_lic"):
        assert after[k] == before[k], k
    assert db.execute("SELECT COUNT(*) FROM offers o JOIN customer_requests r ON r.id = o.request_id "
                      "WHERE r.is_demo = 1 AND o.status = 'rejected'").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM offers o JOIN customer_requests r ON r.id = o.request_id "
                      "WHERE r.is_demo = 1 AND o.image_url IS NOT NULL").fetchone()[0] == 0
    phones = {r[0] for r in db.execute("SELECT phone FROM customer_requests WHERE is_demo = 1")}
    assert phones == {f"+9945000000{i:02d}" for i in range(1, 9)}

    # demo1 daxil olur, demo tələbləri görür (/giris?demo=1 avtomatik doldurması Paket A-da silindi — test_paket_a)
    c, r = login(app, "demo1@maklerhub.az", demo_password())
    assert r.status_code == 302
    demo_ids = {x[0] for x in db.execute("SELECT id FROM customer_requests WHERE is_demo = 1")}
    seen = set(ids_in(c.get("/kabinet?view=all").get_data(as_text=True)))
    assert seen and seen <= demo_ids
    res = runner.invoke(args=["clear-demo"])
    assert res.exit_code == 0, res.output
    gone = _counts(db)
    assert all(gone[k] == 0 for k in ("demo_users", "demo_reqs", "demo_offers", "demo_contacts", "demo_lic"))
    for k in ("real_users", "real_reqs", "real_offers", "real_notifs", "real_lic"):
        assert gone[k] == before[k], k


def test_admin_panel_excludes_demo(app, db, admin_client, make_request):
    rid, _ = _demo_request(db, make_request)
    html = admin_client.get("/admin").get_data(as_text=True)
    assert f'href="/admin/teleb/{rid}"' not in html and "demo məlumat çıxarılıb" in html
    html = admin_client.get("/admin?demo=1").get_data(as_text=True)
    assert f'href="/admin/teleb/{rid}"' in html and "Demo məlumat" in html
    db.execute("DELETE FROM customer_requests WHERE id = ?", (rid,))
    db.commit()


# ---------------------------------------------------------------- G4: başlıqlar, robots, köhnə marşrutlar
def test_security_headers_and_robots(app, make_request, monkeypatch):
    c = app.test_client()
    r = c.get("/")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "Strict-Transport-Security" not in r.headers
    _, token = make_request()
    r = c.get(f"/teleb/{token}")
    assert r.headers["Referrer-Policy"] == "no-referrer" and r.headers["X-Frame-Options"] == "DENY"
    monkeypatch.setenv("MAKLERHUB_BEHIND_PROXY", "1")
    assert c.get("/saglamliq").headers["Strict-Transport-Security"] == "max-age=31536000"
    monkeypatch.delenv("MAKLERHUB_BEHIND_PROXY")

    r = c.get("/robots.txt")
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and r.mimetype == "text/plain"
    for path in ("/teleb/", "/kabinet", "/admin", "/giris", "/qeydiyyat"):
        assert f"Disallow: {path}\n" in body
    assert "Allow: /\n" in body and "Disallow: /\n" not in body and "Disallow: /teleb\n" not in body


def test_old_routes_are_gone(app, admin_client, make_makler):
    client, _ = make_makler(RENT_AREA)
    for path in ("/axtarislar", "/elanlar", "/musteriler", "/analitika"):
        r = client.get(path)
        assert r.status_code == 404, path
    r = admin_client.get("/admin/menbe")
    assert r.status_code == 404
    html = r.get_data(as_text=True)
    assert 'href="/teleb"' in html and 'href="/"' in html


# ---------------------------------------------------------------- G5: Sentry
def test_sentry_off_without_dsn_and_scrubs_pii(app, monkeypatch):
    from maklerhub import monitoring
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert monitoring.init_sentry() is False
    assert app.test_client().get("/saglamliq").status_code == 200
    event = {"request": {"url": "https://maklerhub.onrender.com/teleb/AbCdEfGhIjKlMnOpQrStUvWx",
                         "data": {"name": "Aysel", "phone": "+994501234567"}, "cookies": {"session": "x"},
                         "query_string": "tesdiq=5", "headers": {"Cookie": "session=x", "User-Agent": "UA"}},
             "user": {"ip_address": "1.2.3.4"},
             "exception": {"values": [{"value": "nömrə +994 50 123 45 67, e-poçt ad@nümunə.az"}]},
             "breadcrumbs": {"values": [{"message": "ŞİFRƏ BƏRPA LİNKİ (a@b.az): https://x/teleb/AbCdEfGhIjKlMnOpQrSt"}]}}
    out = json.dumps(monitoring.before_send(event), ensure_ascii=False)
    for secret in ("Aysel", "994501234567", "50 123 45 67", "ad@nümunə.az", "a@b.az", "AbCdEfGhIjKlMnOpQrSt",
                   "session=x", "1.2.3.4", "tesdiq"):
        assert secret not in out, secret
    assert "/teleb/[token]" in out and "UA" in out
