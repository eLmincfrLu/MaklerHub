"""Axın testləri (H2-H8): kabinet, telefon gizliliyi, təkliflər, müddət, müştəri səhifəsi, şərtlər, IP, mobil."""
import re

from conftest import csrf

RENT_AREA = [("rent", ["Yasamal", "Nəsimi"], 400, 1000)]


def ids_in(html):
    """Kabinet siyahısındakı tələb id-ləri (görünmə ardıcıllığı ilə)."""
    return [int(x) for x in re.findall(r'href="/kabinet/teleb/(\d+)"', html)]


def offer_form(**over):
    data = {"title": "Təmirli 2 otaqlı", "price": "800", "district": "Yasamal", "rooms": "2", "area": "65",
            "image_url": "https://example.com/ev.jpg", "description": "Metroya yaxın"}
    data.update(over)
    return data


def send(client, rid, **over):
    tok = csrf(client, f"/kabinet/teleb/{rid}")
    return client.post(f"/kabinet/teleb/{rid}/teklif", data=dict(offer_form(**over), csrf_token=tok))


# ---------------------------------------------------------------- H2: kabinet
def test_kabinet_good_all_and_deal_gate(make_makler, make_request):
    client, _ = make_makler(RENT_AREA)
    good, _ = make_request(districts=["Yasamal"], bmin=500, bmax=900)          # 100
    weak, _ = make_request(districts=["Xətai"], bmin=500, bmax=900)            # 45
    sale, _ = make_request(deal="sale", districts=["Yasamal"], bmin=100_000, bmax=150_000)
    good_ids = ids_in(client.get("/kabinet").get_data(as_text=True))
    all_ids = ids_in(client.get("/kabinet?view=all").get_data(as_text=True))
    assert good in good_ids and weak not in good_ids
    assert good in all_ids and weak in all_ids
    assert sale not in good_ids and sale not in all_ids
    assert client.get(f"/kabinet/teleb/{sale}").status_code == 404          # başqa əməliyyat: detal da bağlı


def test_kabinet_filters_and_sort(make_makler, make_request):
    client, _ = make_makler(RENT_AREA)
    old, _ = make_request(districts=["Yasamal"], rooms=2, bmin=500, bmax=900, age_days=3, urgency="flexible")
    new, _ = make_request(districts=["Nəsimi"], rooms=3, bmin=None, bmax=2000, age_days=0, urgency="urgent")  # 55
    mid, _ = make_request(districts=["Yasamal"], rooms=1, bmin=900, bmax=1200, age_days=1, urgency="month")

    def ids(q):
        return [i for i in ids_in(client.get("/kabinet?" + q).get_data(as_text=True)) if i in (old, new, mid)]

    assert ids("district=Nəsimi") == [new]
    assert ids("rooms=3") == [new]
    assert ids("bmin=1500") == [new]
    assert ids("bmax=600") == [old]
    assert ids("sort=score") == [old, mid, new]          # 100, ~66, 55
    assert ids("sort=new") == [new, mid, old]
    assert ids("sort=urgency") == [new, mid, old]        # təcili, 1 ay, tələsmirəm
    assert ids("deal=rent&district=Yasamal&rooms=1") == [mid]


def test_kabinet_without_area_shows_warning(make_makler, make_request):
    client, _ = make_makler([])
    rid, _ = make_request()
    html = client.get("/kabinet").get_data(as_text=True)
    assert "Tələbləri görmək üçün xidmət sahənizi doldurun" in html
    assert not ids_in(html)


# ---------------------------------------------------------------- H3: telefon heç vaxt
def test_phone_never_in_makler_html(make_makler, make_request):
    client, _ = make_makler(RENT_AREA)
    rid, _ = make_request(phone="+994551234567", name="Səbinə Quliyeva")
    pages = [client.get(u).get_data(as_text=True) for u in
             ("/kabinet", "/kabinet?view=all", f"/kabinet/teleb/{rid}")]
    send(client, rid)
    pages += [client.get(u).get_data(as_text=True) for u in (f"/kabinet/teleb/{rid}", "/kabinet/teklifler")]
    for html in pages:
        for p in ("+994551234567", "994551234567", "551234567", "0551234567", "055 123 45 67", "+994 55 123 45 67",
                  "55 123 45 67", "Quliyeva"):
            assert p not in html
    assert "Səbinə" in pages[0]


# ---------------------------------------------------------------- H4: təkliflər
def test_offer_rules(app, db, make_makler, make_request):
    rid, token = make_request(districts=["Yasamal"])
    makers = [make_makler(RENT_AREA) for _ in range(6)]
    for c, _ in makers[:5]:
        assert send(c, rid).status_code == 302
    # eyni makler ikinci dəfə
    r = send(makers[0][0], rid)
    assert r.status_code == 400 and "artıq təklif göndərmisiniz" in r.get_data(as_text=True)
    # 6-cı makler: limit
    r = send(makers[5][0], rid)
    assert r.status_code == 400
    assert db.execute("SELECT COUNT(*) FROM offers WHERE request_id = ?", (rid,)).fetchone()[0] == 5
    # 5 dolub → 6-cı maklerin siyahısında görünmür, 1-cinin siyahısında görünür
    assert rid not in ids_in(makers[5][0].get("/kabinet?view=all").get_data(as_text=True))
    assert rid in ids_in(makers[0][0].get("/kabinet?view=all").get_data(as_text=True))
    # geri çəkmə → yer açılır, 6-cı göndərə bilir; geri çəkən yenidən göndərə bilmir
    oid = db.execute("SELECT id FROM offers WHERE request_id = ? AND agent_id = ?", (rid, makers[0][1])).fetchone()[0]
    tok = csrf(makers[0][0], f"/kabinet/teleb/{rid}")
    assert makers[0][0].post(f"/kabinet/teklif/{oid}/geri-cek", data={"csrf_token": tok}).status_code == 302
    assert db.execute("SELECT status FROM offers WHERE id = ?", (oid,)).fetchone()[0] == "withdrawn"
    assert "yenidən təklif göndərmək olmur" in makers[0][0].get(f"/kabinet/teleb/{rid}").get_data(as_text=True)
    assert send(makers[5][0], rid).status_code == 302
    assert send(makers[0][0], rid).status_code == 400
    # müştəri üçün bildiriş
    assert db.execute("SELECT COUNT(*) FROM notifications WHERE request_id = ? AND kind = 'new_offer'",
                      (rid,)).fetchone()[0] == 6


def test_offer_rejected_for_closed_expired_license_and_http(db, make_makler, make_request):
    client, uid = make_makler(RENT_AREA)
    closed, _ = make_request(status="closed")
    r = send(client, closed)
    assert r.status_code == 400 and "bağlanıb" in r.get_data(as_text=True)
    expired, _ = make_request(age_days=31)          # status hələ 'active' — lazy yeniləmədən əvvəl də rədd
    db.execute("UPDATE customer_requests SET status = 'active' WHERE id = ?", (expired,))
    db.commit()
    assert send(client, expired).status_code == 400
    ok_req, _ = make_request()
    r = send(client, ok_req, image_url="http://example.com/ev.jpg")
    assert r.status_code == 400 and "https://" in r.get_data(as_text=True)
    for bad in ({"title": ""}, {"title": "x" * 81}, {"price": "abc"}, {"district": "Saxta"}, {"rooms": "9"},
                {"description": "x" * 501}, {"image_url": "https://" + "a" * 500 + ".az"}):
        assert send(client, ok_req, **bad).status_code == 400
    assert db.execute("SELECT COUNT(*) FROM offers WHERE agent_id = ?", (uid,)).fetchone()[0] == 0


def test_offer_rejected_for_expired_license_with_valid_csrf(db, make_makler, make_request):
    client, uid = make_makler(RENT_AREA)
    rid, _ = make_request()
    tok = csrf(client, f"/kabinet/teleb/{rid}")
    db.execute("UPDATE licenses SET expires_at = '2020-01-01 00:00:00' WHERE user_id = ?", (uid,))
    db.commit()
    r = client.post(f"/kabinet/teleb/{rid}/teklif", data=dict(offer_form(), csrf_token=tok))
    assert r.status_code == 403 and "Lisenziyanız bitib" in r.get_data(as_text=True)
    assert client.get("/kabinet").status_code == 403
    assert db.execute("SELECT COUNT(*) FROM offers WHERE agent_id = ?", (uid,)).fetchone()[0] == 0


# ---------------------------------------------------------------- H5: müddət
def test_request_expires_after_30_days(app, db, make_makler, make_request):
    client, _ = make_makler(RENT_AREA)
    rid, token = make_request(age_days=31)
    fresh, _ = make_request(age_days=29)
    ids = ids_in(client.get("/kabinet?view=all").get_data(as_text=True))
    assert rid not in ids and fresh in ids
    assert db.execute("SELECT status FROM customer_requests WHERE id = ?", (rid,)).fetchone()[0] == "expired"
    html = app.test_client().get(f"/teleb/{token}").get_data(as_text=True)
    assert "Tələbin müddəti bitib" in html


def test_anonymize_after_90_days(app, db, make_request):
    from maklerhub.teleb import expire_requests
    rid, _ = make_request(status="closed", phone="+994509998877")
    db.execute("UPDATE customer_requests SET closed_at = '2020-01-01 00:00:00' WHERE id = ?", (rid,))
    expire_requests(db)
    db.commit()
    row = db.execute("SELECT name, phone, note, ip FROM customer_requests WHERE id = ?", (rid,)).fetchone()
    assert tuple(row) == ("", "", "", "")


# ---------------------------------------------------------------- H6: müştəri səhifəsi
def test_customer_page_shows_offers_not_withdrawn(app, db, make_makler, make_request):
    rid, token = make_request()
    a, _ = make_makler(RENT_AREA)
    b, uid_b = make_makler(RENT_AREA)
    assert send(a, rid, title="Görünən təklif A").status_code == 302
    assert send(b, rid, title="Geri çəkilən təklif B").status_code == 302
    oid = db.execute("SELECT id FROM offers WHERE agent_id = ?", (uid_b,)).fetchone()[0]
    b.post(f"/kabinet/teklif/{oid}/geri-cek", data={"csrf_token": csrf(b, f"/kabinet/teleb/{rid}")})
    r = app.test_client().get(f"/teleb/{token}")
    html = r.get_data(as_text=True)
    assert "Görünən təklif A" in html and "Geri çəkilən təklif B" not in html
    assert "Test S." in html and "Sınaqov" not in html
    assert 'referrerpolicy="no-referrer"' in html and 'loading="lazy"' in html
    assert r.headers.get("Referrer-Policy") == "no-referrer"


def test_my_offers_page_and_status_filter(make_makler, make_request):
    client, _ = make_makler(RENT_AREA)
    rid, _ = make_request()
    send(client, rid, title="Mənim təklifim")
    assert "Mənim təklifim" in client.get("/kabinet/teklifler").get_data(as_text=True)
    assert "Mənim təklifim" in client.get("/kabinet/teklifler?status=sent").get_data(as_text=True)
    assert "Mənim təklifim" not in client.get("/kabinet/teklifler?status=accepted").get_data(as_text=True)


# ---------------------------------------------------------------- H7: şərtlər, ProxyFix, CSRF
def test_legal_pages(app):
    c = app.test_client()
    for url in ("/sertler", "/mexfilik"):
        r = c.get(url)
        assert r.status_code == 200 and "90 gün" in c.get("/mexfilik").get_data(as_text=True)
    for url in ("/teleb", "/qeydiyyat", "/"):
        assert 'href="#"' not in c.get(url).get_data(as_text=True)


def teleb_post(client, tok, phone, headers=None):
    data = {"csrf_token": tok, "name": "Spam", "phone": phone, "deal": "rent", "districts": ["Yasamal"], "rooms": "2",
            "budget_min": "", "budget_max": "900", "urgency": "month", "consent": "on", "website": ""}
    return client.post("/teleb", data=data, headers=headers or {})


def test_spoofed_xff_does_not_bypass_ip_limit(app):
    c = app.test_client()
    tok = csrf(c, "/teleb")
    codes = []
    for i in range(4):
        # müştəri özü saxta IP göndərir, Render proxy-si real IP-ni (9.9.9.9) sona əlavə edir
        r = teleb_post(c, tok, f"050 444 55 {60 + i}", {"X-Forwarded-For": f"6.6.6.{i}, 9.9.9.9"})
        codes.append(r.status_code)
    assert codes == [302, 302, 302, 200]


def test_teleb_csrf_failure_keeps_values(app):
    c = app.test_client()
    csrf(c, "/teleb")
    r = c.post("/teleb", data={"csrf_token": "köhnə", "name": "Kənan", "phone": "0551112233", "deal": "rent",
                               "districts": ["Yasamal"], "rooms": "2", "budget_max": "900", "urgency": "month",
                               "consent": "on"})
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Zəhmət olmasa yenidən göndərin" in html
    assert 'value="Kənan"' in html and 'value="Yasamal" checked' in html
    new_tok = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
    assert new_tok != "köhnə"
    r = c.post("/teleb", data={"csrf_token": new_tok, "name": "Kənan", "phone": "0551112233", "deal": "rent",
                               "districts": ["Yasamal"], "rooms": "2", "budget_max": "900", "urgency": "month",
                               "consent": "on"}, headers={"X-Forwarded-For": "8.8.4.4"})
    assert r.status_code == 302


# ---------------------------------------------------------------- H8: mobil
def test_mobile_pages_render(app, make_makler, make_request):
    client, _ = make_makler(RENT_AREA)
    rid, token = make_request()
    send(client, rid)
    ua = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148"}
    for url in ("/kabinet", "/kabinet?view=all", f"/kabinet/teleb/{rid}", "/kabinet/teklifler", "/ayarlar"):
        assert client.get(url, headers=ua).status_code == 200, url
    assert app.test_client().get(f"/teleb/{token}", headers=ua).status_code == 200
