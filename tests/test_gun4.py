"""Gün 4: qəbul/rədd, kontakt, bağlama, bildirişlər, şikayətlər (YALNIZ test bazasında)."""
import re

from conftest import csrf
from test_flow import RENT_AREA, send

PHONE = "+994551234567"
PHONE_FORMS = ("+994551234567", "994551234567", "551234567", "0551234567", "+994 55 123 45 67", "55 123 45 67")


def page(app, token):
    return app.test_client().get(f"/teleb/{token}")


def customer_post(app, token, path, data=None):
    c = app.test_client()
    tok = csrf(c, "/teleb")  # sessiyanın CSRF tokeni (token səhifəsi 404 ola bilər)
    return c, c.post(f"/teleb/{token}{path}", data=dict(data or {}, csrf_token=tok))


def offer_id(db, rid, uid):
    return db.execute("SELECT id FROM offers WHERE request_id = ? AND agent_id = ?", (rid, uid)).fetchone()[0]


# ---------------------------------------------------------------- F2: tam axın
def test_full_flow_accept_reveals_phone_only_to_that_agent(app, db, make_makler, make_request):
    rid, token = make_request(phone=PHONE, name="Səbinə Quliyeva")
    a, ua = make_makler(RENT_AREA, first="Rəşad", last="Məmmədov", phone="+994501112233")
    b, ub = make_makler(RENT_AREA, first="Kənan", last="Əliyev")
    assert send(a, rid, title="Təklif A").status_code == 302
    assert send(b, rid, title="Təklif B").status_code == 302
    oa, ob = offer_id(db, rid, ua), offer_id(db, rid, ub)

    html = page(app, token).get_data(as_text=True)
    assert "Maraqlıdır" in html and "Maraqlı deyil" in html
    # təsdiq addımı: GET ?tesdiq= — hələ heç nə dəyişmir
    html = app.test_client().get(f"/teleb/{token}?tesdiq={oa}").get_data(as_text=True)
    assert "Nömrəniz <b>Rəşad M.</b> ilə paylaşılacaq. Davam edək?" in html
    assert db.execute("SELECT status FROM offers WHERE id = ?", (oa,)).fetchone()[0] == "sent"

    c, r = customer_post(app, token, f"/teklif/{oa}/qebul")
    assert r.status_code == 302
    assert db.execute("SELECT status FROM offers WHERE id = ?", (oa,)).fetchone()[0] == "accepted"
    assert db.execute("SELECT agent_id FROM contacts WHERE offer_id = ?", (oa,)).fetchone()[0] == ua

    # A nömrəni görür (detal + Təkliflərim, tel: və wa.me), B görmür
    for url in (f"/kabinet/teleb/{rid}", "/kabinet/teklifler"):
        h = a.get(url).get_data(as_text=True)
        assert "tel:+994551234567" in h and "https://wa.me/994551234567" in h and "Səbinə Quliyeva" in h
        hb = b.get(url).get_data(as_text=True)
        for p in PHONE_FORMS:
            assert p not in hb
        assert "Quliyeva" not in hb
    # müştəri: qəbul olunan təklifdə makler adı və telefonu
    html = page(app, token).get_data(as_text=True)
    assert "Makler sizinlə əlaqə saxlayacaq" in html and "Rəşad Məmmədov" in html and "tel:+994501112233" in html

    c, r = customer_post(app, token, f"/teklif/{ob}/redd")
    assert db.execute("SELECT status FROM offers WHERE id = ?", (ob,)).fetchone()[0] == "rejected"
    html = page(app, token).get_data(as_text=True)
    assert "Maraqlı olmayanlar (1)" in html
    assert not db.execute("SELECT 1 FROM contacts WHERE offer_id = ?", (ob,)).fetchone()


def test_customer_page_masks_own_phone(app, make_request):
    _, token = make_request(phone="+994501234535", name="Aysel Məmmədova")
    html = page(app, token).get_data(as_text=True)
    assert "+994 50 *** ** 35" in html and "Aysel Məmmədova" in html
    for p in ("+994501234535", "501234535", "50 123 45 35", "0501234535"):
        assert p not in html


# ---------------------------------------------------------------- F3: limitlər, bağlama, müdaxilə
def test_accept_limit_closed_request_and_foreign_token(app, db, make_makler, make_request):
    rid, token = make_request()
    other_rid, other_token = make_request()
    makers = [make_makler(RENT_AREA) for _ in range(4)]
    for c, _ in makers:
        assert send(c, rid).status_code == 302
    oids = [offer_id(db, rid, uid) for _, uid in makers]
    for oid in oids[:3]:
        customer_post(app, token, f"/teklif/{oid}/qebul")
    _, r = customer_post(app, token, f"/teklif/{oids[3]}/qebul")
    assert db.execute("SELECT status FROM offers WHERE id = ?", (oids[3],)).fetchone()[0] == "sent"
    assert db.execute("SELECT COUNT(*) FROM offers WHERE request_id = ? AND status = 'accepted'", (rid,)).fetchone()[0] == 3

    # başqa tələbin tokeni ilə bu təklifə müdaxilə → 404, heç nə dəyişmir
    _, r = customer_post(app, other_token, f"/teklif/{oids[3]}/redd")
    assert r.status_code == 404
    _, r = customer_post(app, other_token, f"/teklif/{oids[3]}/sikayet", {"reason": "spam"})
    assert r.status_code == 404
    _, r = customer_post(app, "yoxdur" * 4, f"/teklif/{oids[3]}/qebul")
    assert r.status_code == 404
    assert db.execute("SELECT status FROM offers WHERE id = ?", (oids[3],)).fetchone()[0] == "sent"
    # CSRF olmadan → heç nə dəyişmir
    r = app.test_client().post(f"/teleb/{token}/teklif/{oids[3]}/redd", data={})
    assert r.status_code == 302 and db.execute("SELECT status FROM offers WHERE id = ?", (oids[3],)).fetchone()[0] == "sent"

    # bağla: səbəbsiz rədd, səbəblə bağlanır
    customer_post(app, token, "/bagla", {"reason": "yanlis"})
    assert db.execute("SELECT status FROM customer_requests WHERE id = ?", (rid,)).fetchone()[0] == "active"
    customer_post(app, token, "/bagla", {"reason": "found_here"})
    row = db.execute("SELECT status, close_reason FROM customer_requests WHERE id = ?", (rid,)).fetchone()
    assert tuple(row) == ("closed", "found_here")
    # bağlanmış: qəbul/rədd və yeni təklif rədd, düymələr görünmür
    customer_post(app, token, f"/teklif/{oids[3]}/redd")
    assert db.execute("SELECT status FROM offers WHERE id = ?", (oids[3],)).fetchone()[0] == "sent"
    html = page(app, token).get_data(as_text=True)
    assert "Maraqlıdır" not in html and "Maraqlı deyil" not in html and "MaklerHub ilə tapdım" in html
    new_maker, _ = make_makler(RENT_AREA)
    assert send(new_maker, rid).status_code in (400, 404)


# ---------------------------------------------------------------- F4: bildirişlər
def teleb_form(app, **over):
    c = app.test_client()
    tok = csrf(c, "/teleb")
    data = {"csrf_token": tok, "name": "Leyla", "phone": over.pop("phone"), "deal": "rent", "districts": ["Səbail"],
            "rooms": "2", "budget_min": "500", "budget_max": "900", "urgency": "month", "consent": "on", "website": ""}
    data.update(over)
    r = c.post("/teleb", data=data)
    assert r.status_code == 302, r.get_data(as_text=True)[:400]
    return r.headers["Location"].rsplit("/", 1)[-1]


def notif_count(db, uid, kind):
    return db.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND kind = ?", (uid, kind)).fetchone()[0]


def test_notifications(app, db, make_makler):
    good, ug = make_makler([("rent", ["Səbail"], 400, 1000)])
    weak, uw = make_makler([("rent", ["Binəqədi"], 400, 1000)])          # rayon yox → 45
    sale, us = make_makler([("sale", ["Səbail"], 100000, 200000)])        # başqa əməliyyat
    token = teleb_form(app, phone="050 777 66 01")
    rid = db.execute("SELECT id FROM customer_requests WHERE token = ?", (token,)).fetchone()[0]
    assert notif_count(db, ug, "new_request") == 1
    assert notif_count(db, uw, "new_request") == 0 and notif_count(db, us, "new_request") == 0
    # zəng: oxunmamış sayı, siyahı, link
    assert good.get("/api/notifications").get_json()["unread"] == 1
    html = good.get("/kabinet/bildirisler").get_data(as_text=True)
    assert "Yeni uyğun tələb" in html and "Hamısını oxunmuş et" in html
    nid = db.execute("SELECT id FROM notifications WHERE user_id = ?", (ug,)).fetchone()[0]
    r = good.get(f"/kabinet/bildiris/{nid}")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/kabinet/teleb/{rid}")
    assert good.get("/api/notifications").get_json()["unread"] == 0
    # başqasının bildirişi → 404
    assert weak.get(f"/kabinet/bildiris/{nid}").status_code == 404
    # qəbul/rədd bildirişləri
    send(good, rid)
    weak2, uw2 = make_makler([("rent", ["Səbail"], 400, 1000)])
    send(weak2, rid)
    customer_post(app, token, f"/teklif/{offer_id(db, rid, ug)}/qebul")
    customer_post(app, token, f"/teklif/{offer_id(db, rid, uw2)}/redd")
    assert notif_count(db, ug, "offer_accepted") == 1 and notif_count(db, uw2, "offer_rejected") == 1
    assert good.get("/api/notifications").get_json()["unread"] >= 1
    tok = csrf(good, "/kabinet/bildirisler")
    good.post("/kabinet/bildirisler/oxundu", data={"csrf_token": tok})
    assert good.get("/api/notifications").get_json()["unread"] == 0
    # müştəri: "Yeni" nişanı yalnız son baxışdan sonra gələnlərdə
    assert "newtag" in page(app, token).get_data(as_text=True)        # ilk baxış: hamısı yeni
    assert "newtag" not in page(app, token).get_data(as_text=True)    # ikinci baxış: yeni yoxdur
    late, _ = make_makler([("rent", ["Səbail"], 400, 1000)])
    send(late, rid, title="Gec gələn təklif")
    html = page(app, token).get_data(as_text=True)
    assert re.search(r"Gec gələn təklif <span class=\"newtag\">Yeni</span>", html)
    assert "newtag" not in page(app, token).get_data(as_text=True)


# ---------------------------------------------------------------- F5: şikayətlər
def test_complaints_both_sides_once(app, db, make_makler, make_request):
    rid, token = make_request()
    a, ua = make_makler(RENT_AREA)
    send(a, rid)
    oid = offer_id(db, rid, ua)
    customer_post(app, token, f"/teklif/{oid}/sikayet", {"reason": "fake", "text": "Belə ev yoxdur"})
    customer_post(app, token, f"/teklif/{oid}/sikayet", {"reason": "spam"})
    customer_post(app, token, f"/teklif/{oid}/sikayet", {"reason": "yanlis"})
    rows = db.execute("SELECT from_side, reason, agent_id FROM complaints WHERE offer_id = ?", (oid,)).fetchall()
    assert [tuple(r) for r in rows] == [("customer", "fake", ua)]
    assert "şikayətiniz qəbul olunub" in page(app, token).get_data(as_text=True)

    tok = csrf(a, f"/kabinet/teleb/{rid}")
    a.post(f"/kabinet/teleb/{rid}/sikayet", data={"csrf_token": tok, "reason": "wrong_phone", "text": "Cavab vermir"})
    a.post(f"/kabinet/teleb/{rid}/sikayet", data={"csrf_token": tok, "reason": "other"})
    a.post(f"/kabinet/teleb/{rid}/sikayet", data={"csrf_token": tok, "reason": "fake_request", "text": "x" * 301})
    rows = db.execute("SELECT reason FROM complaints WHERE request_id = ? AND from_side = 'agent'", (rid,)).fetchall()
    assert [r[0] for r in rows] == ["wrong_phone"]
    # başqa əməliyyatla işləyən makler bu tələbdən şikayət edə bilmir
    s, _ = make_makler([("sale", ["Yasamal"], 1, 10 ** 7)])
    assert s.post(f"/kabinet/teleb/{rid}/sikayet", data={"csrf_token": csrf(s, "/kabinet"), "reason": "other"}).status_code == 404


def test_debug_ip_removed(app, admin_client):
    assert admin_client.get("/admin/debug/ip").status_code == 404
