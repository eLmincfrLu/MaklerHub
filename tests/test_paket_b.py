"""Paket B: rayon seçici (server qaydaları dəyişmir), xidmət sahəsi açarı, demo oxu rejimi, ana səhifənin strukturu
(YALNIZ test bazasında)."""
import re

from conftest import csrf, login
from test_flow import RENT_AREA, ids_in


def hero(html):
    """Hero məzmunu (.content) — "Necə işləyir" bölməsinə qədər."""
    start = html.index('<div class="content">')
    return html[start:html.index('<section class="lx-sec" id="nece">')]


# ---------------------------------------------------------------- /teleb
def test_teleb_picker_markup_and_server_limit(app, db):
    c = app.test_client()
    html = c.get("/teleb").get_data(as_text=True)
    assert 'data-dp data-max="3"' in html and "district-picker.js" in html and "district-picker.css" in html
    # JS-siz fallback: bütün rayonlar adi qeyd qutusu, eyni ad (server dəyişmir), əlifba sırası
    names = re.findall(r'<input type="checkbox" name="districts" value="([^"]+)"', html)
    from maklerhub.helpers import BAKU_DISTRICTS, DISTRICTS_AZ
    assert names == DISTRICTS_AZ and sorted(names) != names and set(names) == set(BAKU_DISTRICTS)
    assert names.index("Qara Qarayev") < names.index("Qaradağ") and names.index("Xırdalan") < names.index("İçərişəhər")

    tok = csrf(c, "/teleb")
    data = {"csrf_token": tok, "name": "Test", "phone": "+994 70 555 44 33", "deal": "rent", "rooms": "2",
            "budget_min": "", "budget_max": "900", "urgency": "month", "consent": "on", "website": "",
            "districts": ["Yasamal", "Nəsimi", "Səbail", "Xətai"]}
    r = c.post("/teleb", data=data)
    assert r.status_code == 200 and "Ən çox 3 rayon seçmək olar." in r.get_data(as_text=True)
    assert not db.execute("SELECT 1 FROM customer_requests WHERE phone = '+994705554433'").fetchone()
    # seçilənlər dəyərlərlə qayıdır
    h = r.get_data(as_text=True)
    assert 'value="Yasamal" checked' in h and 'value="Xətai" checked' in h


# ---------------------------------------------------------------- xidmət sahəsi
def test_service_area_picker_and_toggle_saved(app, db, make_makler):
    m, uid = make_makler([("rent", ["Yasamal"], 400, 900)])
    html = m.get("/ayarlar").get_data(as_text=True)
    assert html.count('data-dp data-max="0"') == 2           # kirayə və satış üçün ayrıca seçici
    assert 'data-district value="Yasamal" checked' in html and 'name="districts"' not in html
    tok = csrf(m, "/ayarlar")
    h = {"X-CSRF-Token": tok}
    # makler-ayarlar.js-in göndərdiyi forma: satış açarı açıq, kirayə bağlı
    r = m.post("/api/me/areas", json={"rent": {"on": False, "min": "400", "max": "900", "districts": ["Yasamal"]},
                                      "sale": {"on": True, "min": "60000", "max": "", "districts": ["Nəsimi", "Səbail"]}},
               headers=h)
    assert r.status_code == 200 and r.get_json()["ok"]
    rows = db.execute("SELECT deal_type, district, price_min, price_max FROM agent_areas WHERE user_id = ? "
                      "ORDER BY district", (uid,)).fetchall()
    assert [tuple(x) for x in rows] == [("sale", "Nəsimi", 60000, None), ("sale", "Səbail", 60000, None)]
    html = m.get("/ayarlar").get_data(as_text=True)
    rent = html[html.index('data-deal="rent"'):html.index('data-deal="sale"')]
    sale = html[html.index('data-deal="sale"'):]
    assert 'class="deal off"' in html and "data-on checked" not in rent and "data-on checked" in sale
    assert 'value="Nəsimi" checked' in sale and 'value="Yasamal" checked' not in rent


def test_demo_settings_disabled_and_403(app, db, make_makler):
    email, uid = make_makler(RENT_AREA, login_now=False)
    db.execute("UPDATE users SET is_demo = 1 WHERE id = ?", (uid,))
    db.commit()
    demo, _ = login(app, email, "TestPass123")
    html = demo.get("/ayarlar").get_data(as_text=True)
    assert html.count('<fieldset disabled class="ro-zone" data-tip="Demo hesabda dəyişdirilmir"') == 3
    assert "readonly.css" in html and "readonly-tip.js" in html and "data-dp" in html
    r = demo.post("/api/me/areas", json={"rent": {"on": False}, "sale": {"on": False}},
                  headers={"X-CSRF-Token": csrf(demo, "/ayarlar")})
    assert r.status_code == 403
    assert db.execute("SELECT COUNT(*) FROM agent_areas WHERE user_id = ?", (uid,)).fetchone()[0] > 0
    # real makler səhifəsində oxu rejimi yoxdur
    m, _ = make_makler(RENT_AREA)
    assert "ro-zone" not in m.get("/ayarlar").get_data(as_text=True)


# ---------------------------------------------------------------- kabinet filtri (tək seçim)
def test_kabinet_single_district_filter(app, make_makler, make_request):
    m, _ = make_makler(RENT_AREA)
    a, _ = make_request(districts=["Yasamal"])
    b, _ = make_request(districts=["Nəsimi"])
    html = m.get("/kabinet?view=all&district=Yasamal").get_data(as_text=True)
    ids = ids_in(html)
    assert a in ids and b not in ids
    assert 'data-dp data-single' in html and '<option value="Yasamal" selected>' in html
    assert 'class="fl-dp"' in html and "district-picker.js" in html


# ---------------------------------------------------------------- ana səhifə
def test_landing_structure_for_guest(app):
    html = app.test_client().get("/").get_data(as_text=True)
    h = hero(html)
    assert len(re.findall(r'class="btn btn-gold[^"]*"', h)) == 1 and "Tələbini yaz →" in h
    assert "1 dəqiqə · qeydiyyatsız · pulsuz" in h and "Maklerəm" not in html and "btn-ghost" not in h
    top = html[html.index('<div class="navbtns">'):html.index('<div class="content">')]
    assert 'href="#maklerler"' in top and 'id="loginBtn"' in top and ">Makler girişi<" in top
    assert 'class="btn btn-gold shine" id="registerBtn"' in top and 'id="registerBtn"' in html
    assert 'id="maklerler"' in html and html.count('class="mk-card"') == 3
    for t in ("Hazır müştəri tələbləri", "Uyğunluq faizi ilə sıralama", "Nömrə yalnız müştəri razı olanda",
              "Necə qoşulmaq olar", "Lisenziya kodu ilə qoşul", "Hesabım var → Daxil ol"):
        assert t in html, t
    assert 'href="/qeydiyyat">Lisenziya kodu ilə qoşul' in html and 'href="/giris">Hesabım var → Daxil ol' in html
    assert 'id="nece"' in html and "lx-final" in html and "landing-anim.js" in html and "js-anim" in html
    # WhatsApp da qorunur (Paket A: ictimai səhifədə nömrə açıq yazılmır)
    assert 'data-k="w"' in html and "508364635" not in html and "wa.me" not in html
    assert "Lisenziya kodu ilə giriş" not in html   # "giriş" yalnız mövcud hesab üçün


def test_landing_signed_in_unchanged(app, admin_client, make_makler):
    m, _ = make_makler(RENT_AREA, first="Rəşad", last="Məmmədov")
    html = m.get("/").get_data(as_text=True)
    assert "Kabinetə keç →" in html and 'class="lu-av"' in html and 'id="registerBtn" hidden' in html
    h = hero(html)
    assert ">Tələbini yaz<" in h and ">Kabinetə keç<" in h and "qeydiyyatsız · pulsuz" not in h
    assert "Lisenziya kodu ilə qoşul" not in html and "Makler girişi" not in html
    assert "Admin panel →" in admin_client.get("/").get_data(as_text=True)
