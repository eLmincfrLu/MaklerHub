"""Gün 4: admin panel (F6), mobil render (F8) — YALNIZ test bazasında."""
import os

from conftest import csrf, login
from test_flow import RENT_AREA, send
from test_gun4 import customer_post, offer_id

MOBILE = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148"}


def admin_post(client, url, data=None):
    return client.post(url, data=dict(data or {}, csrf_token=csrf(client, "/admin/")))


def test_admin_pages_render(app, admin_client, make_makler, make_request):
    rid, token = make_request(phone="+994507776655", name="Nərmin Həsənova")
    a, ua = make_makler(RENT_AREA)
    send(a, rid)
    customer_post(app, token, f"/teklif/{offer_id_any(app, rid)}/sikayet", {"reason": "spam"})
    for url in ("/admin/", "/admin/telebler", "/admin/telebler?status=active", f"/admin/teleb/{rid}", "/admin/maklerler",
                "/admin/lisenziyalar", "/admin/sikayetler", "/admin/sikayetler?status=all", "/admin/loglar",
                "/admin/ayarlar", "/admin/api/logs"):
        assert admin_client.get(url, headers=MOBILE).status_code == 200, url
    # axtarış telefonla (yerli format) və adla; admin telefonu görür
    assert f"#{rid}" in admin_client.get("/admin/telebler?q=0507776655").get_data(as_text=True)
    assert f"#{rid}" in admin_client.get("/admin/telebler?q=Nərmin").get_data(as_text=True)
    assert "tel:+994507776655" in admin_client.get(f"/admin/teleb/{rid}").get_data(as_text=True)


def offer_id_any(app, rid):
    from maklerhub import db as dbmod
    con = dbmod.connect(app.config["DATABASE"])
    try:
        return con.execute("SELECT id FROM offers WHERE request_id = ? ORDER BY id LIMIT 1", (rid,)).fetchone()[0]
    finally:
        con.close()


def test_suspend_reset_extend(app, db, admin_client, make_makler):
    client, uid = make_makler(RENT_AREA)
    email = client.email
    assert client.get("/kabinet").status_code == 200
    # dayandır → açıq sessiya bağlanır, yeni giriş olmur
    admin_post(admin_client, f"/admin/makler/{uid}/status", {"status": "suspended"})
    assert db.execute("SELECT status FROM users WHERE id = ?", (uid,)).fetchone()[0] == "suspended"
    assert client.get("/kabinet").status_code == 302
    _, r = login(app, email, "TestPass123")
    assert r.status_code == 200 and "dayandırılıb" in r.get_data(as_text=True)
    admin_post(admin_client, f"/admin/makler/{uid}/status", {"status": "active"})
    c2, r = login(app, email, "TestPass123")
    assert r.status_code == 302
    # cihazları sıfırla
    assert db.execute("SELECT COUNT(*) FROM devices WHERE user_id = ?", (uid,)).fetchone()[0] >= 1
    admin_post(admin_client, f"/admin/makler/{uid}/cihazlar")
    assert db.execute("SELECT COUNT(*) FROM devices WHERE user_id = ?", (uid,)).fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM sessions WHERE user_id = ? AND active = 1", (uid,)).fetchone()[0] == 0
    assert c2.get("/kabinet").status_code == 302
    # lisenziyanı uzat: bitmiş lisenziya → indidən +N
    db.execute("UPDATE licenses SET expires_at = '2020-01-01 00:00:00' WHERE user_id = ?", (uid,))
    db.commit()
    _, r = login(app, email, "TestPass123")
    assert r.status_code == 403
    admin_post(admin_client, f"/admin/makler/{uid}/uzat", {"days": "10"})
    from maklerhub.helpers import now, parse
    exp = parse(db.execute("SELECT expires_at FROM licenses WHERE user_id = ?", (uid,)).fetchone()[0])
    assert 9.9 < (exp - now()).total_seconds() / 86400 <= 10.01
    _, r = login(app, email, "TestPass123")
    assert r.status_code == 302
    # aktiv lisenziya → mövcud bitmə tarixinə +N
    admin_post(admin_client, f"/admin/makler/{uid}/uzat", {"days": "5"})
    exp2 = parse(db.execute("SELECT expires_at FROM licenses WHERE user_id = ?", (uid,)).fetchone()[0])
    assert round((exp2 - exp).total_seconds() / 86400) == 5
    admin_post(admin_client, f"/admin/makler/{uid}/uzat", {"days": "0"})
    assert parse(db.execute("SELECT expires_at FROM licenses WHERE user_id = ?", (uid,)).fetchone()[0]) == exp2
    assert db.execute("SELECT COUNT(*) FROM logs WHERE message LIKE 'Lisenziya uzadıldı%'").fetchone()[0] >= 2


def test_delete_request_cascades(app, db, admin_client, make_makler, make_request):
    rid, token = make_request()
    a, ua = make_makler(RENT_AREA)
    send(a, rid)
    oid = offer_id(db, rid, ua)
    customer_post(app, token, f"/teklif/{oid}/qebul")
    customer_post(app, token, f"/teklif/{oid}/sikayet", {"reason": "other"})
    assert db.execute("SELECT COUNT(*) FROM contacts WHERE request_id = ?", (rid,)).fetchone()[0] == 1
    admin_post(admin_client, f"/admin/teleb/{rid}/sil")
    for sql in ("SELECT COUNT(*) FROM customer_requests WHERE id = ?", "SELECT COUNT(*) FROM offers WHERE request_id = ?",
                "SELECT COUNT(*) FROM contacts WHERE request_id = ?", "SELECT COUNT(*) FROM complaints WHERE request_id = ?",
                "SELECT COUNT(*) FROM notifications WHERE request_id = ?"):
        assert db.execute(sql, (rid,)).fetchone()[0] == 0, sql
    assert db.execute("SELECT COUNT(*) FROM notifications WHERE link = ?", (f"/kabinet/teleb/{rid}",)).fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM logs WHERE message = ?", (f"Admin tələbi sildi: #{rid}",)).fetchone()[0] == 1
    assert app.test_client().get(f"/teleb/{token}").status_code == 404


def test_admin_close_request_and_complaint_review(app, db, admin_client, make_makler, make_request):
    rid, token = make_request()
    a, ua = make_makler(RENT_AREA)
    send(a, rid)
    oid = offer_id(db, rid, ua)
    customer_post(app, token, f"/teklif/{oid}/sikayet", {"reason": "fake"})
    cid = db.execute("SELECT id FROM complaints WHERE offer_id = ?", (oid,)).fetchone()[0]
    admin_post(admin_client, f"/admin/sikayet/{cid}/baxildi")
    assert db.execute("SELECT status FROM complaints WHERE id = ?", (cid,)).fetchone()[0] == "reviewed"
    admin_post(admin_client, f"/admin/teleb/{rid}/bagla")
    assert tuple(db.execute("SELECT status, close_reason FROM customer_requests WHERE id = ?", (rid,)).fetchone()) == \
        ("closed", "admin")


def test_makler_gets_403_on_admin_urls(make_makler, make_request):
    client, uid = make_makler(RENT_AREA)
    rid, _ = make_request()
    tok = csrf(client, "/kabinet")
    for url in ("/admin/", "/admin/telebler", f"/admin/teleb/{rid}", "/admin/maklerler", "/admin/lisenziyalar",
                "/admin/sikayetler", "/admin/loglar", "/admin/ayarlar", "/admin/api/logs"):
        assert client.get(url).status_code == 403, url
    for url in (f"/admin/teleb/{rid}/sil", f"/admin/teleb/{rid}/bagla", f"/admin/makler/{uid}/status",
                f"/admin/makler/{uid}/uzat", "/admin/lisenziyalar/yarat"):
        assert client.post(url, data={"csrf_token": tok, "status": "active", "days": "99", "count": "5"}).status_code == 403


def test_admin_password_change(app, admin_client):
    pw = os.environ["MAKLERHUB_ADMIN_PASSWORD"]
    tok = csrf(admin_client, "/admin/ayarlar")
    r = admin_client.post("/api/password", json={"current": pw, "new": "Yeni12345", "new2": "Yeni12345"},
                          headers={"X-CSRF-Token": tok})
    assert r.status_code == 200
    r = admin_client.post("/api/password", json={"current": "Yeni12345", "new": pw, "new2": pw},
                          headers={"X-CSRF-Token": tok})
    assert r.status_code == 200


def test_mobile_new_pages(app, make_makler, make_request, db):
    client, uid = make_makler(RENT_AREA)
    rid, token = make_request()
    send(client, rid)
    oid = offer_id(db, rid, uid)
    for url in ("/kabinet", "/kabinet/bildirisler", f"/kabinet/teleb/{rid}", "/kabinet/teklifler"):
        assert client.get(url, headers=MOBILE).status_code == 200, url
    c = app.test_client()
    for url in (f"/teleb/{token}", f"/teleb/{token}?tesdiq={oid}", "/sertler", "/mexfilik"):
        assert c.get(url, headers=MOBILE).status_code == 200, url
