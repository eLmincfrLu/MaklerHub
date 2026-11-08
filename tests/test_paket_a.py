"""Paket A: admin əlaqəsi, ictimai səhifələrdə bot qoruması, ana səhifə, daxil olmuş istifadəçi başlığı,
/giris və /qeydiyyat izah kartı, CSRF-də formanın saxlanması (YALNIZ test bazasında)."""
import re
from datetime import timedelta

import pytest

from conftest import csrf, login
from test_flow import RENT_AREA

EMAIL = "elmincfrlu@gmail.com"
PHONE_DIGITS = "508364635"
CARD_TITLE = "Lisenziyanı uzatmaq və suallar üçün"


@pytest.fixture
def restore_contact(db):
    yield
    from maklerhub.db import DEFAULT_SETTINGS, set_setting
    for k in ("admin_contact_name", "admin_contact_phone", "admin_contact_email", "notify_email"):
        set_setting(db, k, DEFAULT_SETTINGS[k])
    db.commit()


# ---------------------------------------------------------------- 1. settings
def test_contact_defaults_exist(app, db):
    rows = {r[0]: r[1] for r in db.execute("SELECT key, value FROM settings WHERE key LIKE 'admin_contact_%' "
                                           "OR key = 'notify_email'")}
    assert rows["admin_contact_name"] == "Elmin Cəfərli"
    assert rows["admin_contact_phone"] == "+994508364635"
    assert rows["admin_contact_email"] == EMAIL == rows["notify_email"]


def test_admin_edits_contact_everywhere_and_rejects_bad(app, db, admin_client, make_makler, restore_contact):
    tok = csrf(admin_client, "/admin/ayarlar")
    for bad in ({"phone": "12345"}, {"phone": "+994 40 123 45 67"}, {"email": "yanlis@"}, {"name": ""}):
        data = dict({"name": "Test Admin", "phone": "+994 55 111 22 33", "email": "elaqe@nümunə.az"}, **bad)
        r = admin_client.post("/admin/ayarlar/elaqe", data=dict(data, csrf_token=tok))
        assert r.status_code == 302
        assert db.execute("SELECT value FROM settings WHERE key = 'admin_contact_name'").fetchone()[0] == "Elmin Cəfərli"
    html = admin_client.get("/admin/ayarlar").get_data(as_text=True)
    assert "düzgün deyil" in html or "boş" in html

    r = admin_client.post("/admin/ayarlar/elaqe", data={"csrf_token": tok, "name": "Kamran Əliyev",
                                                         "phone": "055 111 22 33", "email": "Yeni@Example.com"})
    assert r.status_code == 302
    s = {r_[0]: r_[1] for r_ in db.execute("SELECT key, value FROM settings")}
    assert (s["admin_contact_name"], s["admin_contact_phone"], s["admin_contact_email"], s["notify_email"]) == \
        ("Kamran Əliyev", "+994551112233", "yeni@example.com", "yeni@example.com")
    assert db.execute("SELECT 1 FROM logs WHERE message = 'Admin əlaqəsi dəyişdirildi' "
                      "AND details LIKE '%+994551112233%'").fetchone()
    # bütün səhifələrdə yenilənir
    c = app.test_client()
    for url in ("/sertler", "/mexfilik", "/"):
        h = c.get(url).get_data(as_text=True)
        assert "moc.elpmaxe@iney" in h and EMAIL[::-1] not in h, url
    assert "Kamran Əliyev" in c.get("/sertler").get_data(as_text=True)
    m, _ = make_makler(RENT_AREA)
    h = m.get("/ayarlar").get_data(as_text=True)
    assert "Kamran Əliyev" in h and "+994 55 111 22 33" in h and "https://wa.me/994551112233" in h


# ---------------------------------------------------------------- 2. əlaqə kartı (4 yer) və bot qoruması
def test_contact_card_in_settings_and_license_banner(app, db, make_makler):
    from maklerhub.helpers import now, ts
    m, uid = make_makler(RENT_AREA)
    h = m.get("/ayarlar").get_data(as_text=True)
    assert CARD_TITLE in h and EMAIL in h and "+994 50 836 46 35" in h and "tel:+994508364635" in h
    assert 'href="https://wa.me/994508364635"' in h
    assert CARD_TITLE not in m.get("/kabinet").get_data(as_text=True)  # 30 gün qalıb — banner yoxdur
    db.execute("UPDATE licenses SET expires_at = ? WHERE user_id = ?", (ts(now() + timedelta(days=5)), uid))
    db.commit()
    h = m.get("/kabinet").get_data(as_text=True)
    assert "Lisenziyanızın bitməsinə 5 gün qalıb" in h and CARD_TITLE in h and "mh-contact short" in h


def test_contact_card_on_license_and_device_pages(app, db, make_makler):
    m, uid = make_makler(RENT_AREA)
    db.execute("UPDATE licenses SET expires_at = '2020-01-01 00:00:00' WHERE user_id = ?", (uid,))
    db.commit()
    r = m.get("/kabinet")
    assert r.status_code == 403 and CARD_TITLE in r.get_data(as_text=True) and "wa.me/994508364635" in r.get_data(as_text=True)

    email, _ = make_makler(RENT_AREA, login_now=False)
    login(app, email, "TestPass123")
    login(app, email, "TestPass123")
    _, r = login(app, email, "TestPass123")  # 3-cü cihaz
    h = r.get_data(as_text=True)
    assert r.status_code == 403 and "Cihaz limiti" in h and CARD_TITLE in h and "tel:+994508364635" in h


def test_public_pages_hide_plain_contact(app):
    c = app.test_client()
    for url in ("/", "/sertler", "/mexfilik"):
        h = c.get(url).get_data(as_text=True)
        assert EMAIL not in h and PHONE_DIGITS not in h and "50 836 46 35" not in h, url
        assert 'class="mh-obf"' in h and "contact-reveal.js" in h, url
    h = c.get("/sertler").get_data(as_text=True)
    assert "elmincfrlu [at] gmail [dot] com" in h and EMAIL[::-1] in h and "+994 · 50 · 836 · 46 · 35" in h


# ---------------------------------------------------------------- 3. ana səhifə
def test_landing_has_no_comparison_or_demo_link(app):
    h = app.test_client().get("/").get_data(as_text=True)
    assert 'id="ferq"' not in h and "Fərq nədir" not in h and "Bina.az" not in h and "lx-compare" not in h
    assert "demo=1" not in h and "Demo: makler" not in h
    r = app.test_client().get("/giris?demo=1")
    h = r.get_data(as_text=True)
    assert r.status_code == 200 and "demo1@maklerhub.az" not in h and "Demo makler" not in h and 'value=""' in h


# ---------------------------------------------------------------- 4. daxil olmuş istifadəçi
def _logout_is_primary(html):
    return re.search(r'class="btn btn-gold"[^>]*>\s*Çıxış', html) is not None


def test_header_for_signed_in_users(app, admin_client, make_makler):
    m, _ = make_makler(RENT_AREA, first="Rəşad", last="Məmmədov")
    h = m.get("/").get_data(as_text=True)
    assert "Kabinetə keç →" in h and 'class="lu-av"' in h and ">RM<" in h and "Rəşad" in h
    assert not _logout_is_primary(h) and 'class="lu-item lu-out" type="submit">Çıxış' in h
    assert 'id="luMenu" hidden' in h and "user-menu.js" in h and 'href="/qeydiyyat">Maklerəm' not in h and "Makler girişi" not in h
    h = admin_client.get("/").get_data(as_text=True)
    assert "Admin panel →" in h and not _logout_is_primary(h) and 'class="lu-item lu-out"' in h
    h = app.test_client().get("/").get_data(as_text=True)  # qonaq: köhnə başlıq
    assert "Makler girişi" in h and "Tələbini yaz" in h and "lu-menu" not in h


def test_signed_in_register_page_and_logout_continue(app, make_makler, admin_client):
    m, _ = make_makler(RENT_AREA, first="Rəşad", last="Məmmədov")
    r = m.get("/qeydiyyat")
    h = r.get_data(as_text=True)
    assert r.status_code == 200 and "Rəşad Məmmədov</b> (Makler) kimi daxil olmusunuz" in h
    assert "Yeni makler hesabı yaratmaq üçün əvvəl çıxış edin" in h and "Çıxış et və davam et" in h
    assert 'href="/kabinet">Kabinetə qayıt' in h and 'name="license"' not in h
    h = admin_client.get("/giris").get_data(as_text=True)
    assert "(Admin) kimi daxil olmusunuz" in h and "Panelə qayıt" in h

    tok = csrf(m, "/qeydiyyat")
    r = m.post("/cixis?next=/qeydiyyat", data={"csrf_token": tok})
    assert r.status_code == 302 and r.headers["Location"].endswith("/qeydiyyat")
    h = m.get("/qeydiyyat").get_data(as_text=True)
    assert 'name="license"' in h and "kimi daxil olmusunuz" not in h
    assert m.get("/kabinet").status_code == 302   # həqiqətən çıxıb

    m2, _ = make_makler(RENT_AREA)
    r = m2.post("/cixis?next=//evil.example", data={"csrf_token": csrf(m2, "/kabinet")})
    assert r.headers["Location"].endswith("/") and "evil" not in r.headers["Location"]
    m3, _ = make_makler(RENT_AREA)
    assert m3.get("/cixis").status_code == 302 and m3.get("/kabinet").status_code == 302  # köhnə GET linki


# ---------------------------------------------------------------- 5. köhnə CSRF tokeni
def test_stale_csrf_keeps_register_and_login_values(app):
    c = app.test_client()
    c.get("/qeydiyyat")
    data = {"csrf_token": "kohne-token", "fname": "Rəşad", "lname": "Məmmədov", "email": "resad@example.com",
            "phone": "+994 50 123 45 67", "license": "MH-ABCD-EFGH-JKLM", "password": "Sirr12345",
            "password2": "Sirr12345", "terms": "on"}
    r = c.post("/qeydiyyat", data=data)
    h = r.get_data(as_text=True)
    assert r.status_code == 200 and "Səhifə yeniləndi, zəhmət olmasa yenidən göndərin." in h
    for v in ('value="Rəşad"', 'value="Məmmədov"', 'value="resad@example.com"', 'value="+994 50 123 45 67"',
              'value="MH-ABCD-EFGH-JKLM"'):
        assert v in h, v
    assert "Sirr12345" not in h
    # yeni tokenlə yenidən göndərmək işləyir (kod yanlışdır — amma CSRF xətası yox, adi yoxlama)
    tok = re.search(r'name="csrf_token" value="([^"]+)"', h).group(1)
    h2 = c.post("/qeydiyyat", data=dict(data, csrf_token=tok)).get_data(as_text=True)
    assert "Səhifə yeniləndi" not in h2 and "Lisenziya kodu yanlışdır" in h2

    r = c.post("/giris", data={"csrf_token": "kohne", "email": "resad@example.com", "password": "Sirr12345"})
    h = r.get_data(as_text=True)
    assert r.status_code == 200 and "Səhifə yeniləndi" in h and 'value="resad@example.com"' in h and "Sirr12345" not in h
