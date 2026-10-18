"""Kateqoriyalar — Mərhələ 0 testi (listings.category, area_unit, profiles.category). Bir tranzaksiya, sonda ROLLBACK.

Yoxlanır: mənzil profili başqa kateqoriyanın elanına heç vaxt uyğun gəlmir; silinmə və aktiv say kateqoriya daxilindədir;
mövcud elan başqa kateqoriyada / alt növdə görünəndə (Tap 48907654 ssenarisi: mənzil → həyət evi) yeni dəyərlər
yazılır, silinmişsə geri qayıdır, köhnə uyğunluq silinir, bildiriş getmir; URL dəyişikliyi yalnız URL-i yeniləyir.
Hər iki yazma yolu: save() və save_many().

Test bazasına qoşulur (YALNIZ TEST_DATABASE_URL — Neon test branch-i, tests/testdb.py), saytlara sorğu göndərmir.
Bazada aktiv kirayə profili olmalıdır. İşlətmək (layihə kökündən):
    PYTHONIOENCODING=utf-8 python tests/test_category.py
"""
import os, sys
from datetime import timedelta
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testdb
TEST_URL = testdb.url()  # yalnız TEST_DATABASE_URL (qoruyucu: tests/testdb.py)
from maklerhub import matching
from maklerhub.db import connect
from maklerhub.helpers import now, ts
from scraper import bina, tap
from scraper.store import Store

URL = TEST_URL
results = []
def check(name, ok, info=""):
    results.append(("✓" if ok else "✗") + " " + name + (f"  [{info}]" if info else ""))


# ---------------------------------------------------------------- A: bazasız
nb = bina.normalize({"id": 1, "isLeased": True, "area": {"value": 50}, "rooms": 2,
                     "price": {"total": 500, "currency": "AZN"}, "isPaidDaily": False, "location": {},
                     "path": "/items/1", "floor": 1, "floors": 5, "preview": {}}, "Yasamal", "Yeni tikili")
nt = tap.normalize({"legacyResourceId": 5, "title": "2-otaqlı yeni tikili kirayə verilir, Nəsimi m., 50 m²",
                    "price": 500, "path": "/elanlar/dasinmaz-emlak/menziller/5"}, "rent", "month", "Yeni tikili")
check("A1 bina/tap normalize: category='menzil', area_unit='m2'",
      (nb["category"], nb["area_unit"], nt["category"], nt["area_unit"]) == ("menzil", "m2", "menzil", "m2"))


def snapshot():
    c = connect(URL)
    r = tuple(c.execute("SELECT (SELECT COUNT(*) FROM listings), (SELECT COUNT(*) FROM listings WHERE active = 1), "
                        "(SELECT COUNT(*) FROM matches), (SELECT COUNT(*) FROM notifications), "
                        "(SELECT COUNT(*) FROM listings WHERE category <> 'menzil')").fetchone())
    c.close()
    return r


# ---------------------------------------------------------------- B: bazada (ROLLBACK)
before = snapshot()
con = connect(URL)
real_close = con.close
# save_many hər hissədən sonra commit edir: burada SAVEPOINT-ə çevrilir, sonda hamısı ROLLBACK
con.execute("SAVEPOINT c")
con.commit = lambda: (con.execute("RELEASE SAVEPOINT c"), con.execute("SAVEPOINT c"))
try:
    # test qısa olsun və test elanları real elanlarla qruplaşmasın: real aktiv elanlar tranzaksiya daxilində active=0
    con.execute("UPDATE listings SET active = 0 WHERE active = 1")
    p = con.execute("SELECT * FROM profiles WHERE status = 'active' AND deal_type = 'rent' ORDER BY id LIMIT 1").fetchone()
    price = (p["price_min"] + p["price_max"]) // 2
    district = p["district"] if p["district"] != "Bakı, mərkəz" else "Nəsimi"
    n = [0]

    def row(src="bina", **kw):
        n[0] += 1
        ext = f"CATTEST-{n[0]}"
        r = {"external_id": ext, "title": f"Kateqoriya test {n[0]}", "deal_type": "rent",
             "category": "menzil", "property_type": "Yeni tikili", "area_unit": "m2",
             "url": (f"https://tap.az/elanlar/dasinmaz-emlak/menziller/{ext}" if src == "tap"
                     else f"https://bina.az/items/{ext}"),
             "district": district, "metro_name": None, "settlement": None, "rooms": p["rooms"] or 2,
             "area": 300 + n[0] * 20, "floor": 5 if src == "bina" else None, "floors": 12 if src == "bina" else None,
             "price": price, "price_period": "month", "images": [], "suspicious": 0}
        r.update(kw)  # sahə hər elanda fərqlidir: öz aralarında təkrar qrupuna düşməsinlər
        return r

    def get(ext):
        return con.execute("SELECT * FROM listings WHERE external_id = ?", (ext,)).fetchone()

    def matches_of(ext):
        return con.execute("SELECT COUNT(*) FROM matches WHERE listing_id = ?", (get(ext)["id"],)).fetchone()[0]

    def notifs():
        return con.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]

    def as_dict(r):
        return {k: r[k] for k in r.keys()}

    bina_st, tap_st = Store(con, "bina"), Store(con, "tap")

    # B1: sərt kateqoriya şərti (evaluate)
    base = row()
    bina_st.save(base)
    l = as_dict(get(base["external_id"]))
    pd = as_dict(p)
    check("B1 mənzil profili + mənzil elanı → uyğun; elan/profil kateqoriyası fərqli → uyğun deyil; ikisi 'ev' → uyğun",
          matching.evaluate(pd, l) is not None and matching.evaluate(pd, dict(l, category="ev")) is None
          and matching.evaluate(dict(pd, category="ev"), l) is None
          and matching.evaluate(dict(pd, category="ev"), dict(l, category="ev")) is not None
          and pd["category"] == "menzil" and l["category"] == "menzil" and l["area_unit"] == "m2")

    # B2: başqa kateqoriyalı yeni elan mənzil profilinə uyğun gəlmir (hər iki yol)
    ev1 = row(category="ev", property_type="Həyət evi")
    ev2 = row(category="ev", property_type="Bağ evi")
    n0 = notifs()
    bina_st.save(ev1)
    bina_st.save_many([ev2])
    check("B2 'ev' elanı (save və save_many): kateqoriya yazılır, mənzil profilinə uyğunluq və bildiriş yoxdur",
          get(ev1["external_id"])["category"] == "ev" and get(ev2["external_id"])["category"] == "ev"
          and get(ev2["external_id"])["property_type"] == "Bağ evi"
          and matches_of(ev1["external_id"]) == 0 and matches_of(ev2["external_id"]) == 0 and notifs() == n0)

    # B3: Tap 48907654 ssenarisi — mənzil kimi yazılıb, silinib, sonra "Həyət evləri"-ndə görünür
    for way in ("save", "save_many"):
        old = row("tap")
        tap_st.save(old)
        ext = old["external_id"]
        had = matches_of(ext)
        fresh0 = get(ext)["fresh"]
        con.execute("UPDATE listings SET active = 0, removed_at = ? WHERE external_id = ?", (ts(), ext))
        moved = dict(old, category="ev", property_type="Həyət evi", title="Həyət evi, Masazır qəs.",
                     url=f"https://tap.az/elanlar/dasinmaz-emlak/heyet-evleri/{ext}")
        n0, upd0 = notifs(), tap_st.stats["updated"]
        res = tap_st.save(moved) if way == "save" else tap_st.save_many([moved])
        r = get(ext)
        check(f"B3 ({way}) silinmiş mənzil 'Həyət evləri'-ndə görünür → aktiv, kateqoriya/alt növ/URL/başlıq yenilənib, "
              f"mənzil uyğunluğu silinib, bildiriş yox",
              had == 1 and r["active"] == 1 and r["removed_at"] is None and r["category"] == "ev"
              and r["property_type"] == "Həyət evi" and r["building"] == "Həyət evi" and r["url"] == moved["url"]
              and r["title"] == moved["title"] and r["fresh"] == fresh0 and matches_of(ext) == 0
              and notifs() == n0 and tap_st.stats["updated"] == upd0 + 1,
              f"nəticə={res} uyğunluq əvvəl={had} sonra={matches_of(ext)}")

    # B4: alt növ dəyişir (Yeni → Köhnə tikili), kateqoriya eyni → yenilənir, uyğunluq qalır
    sub = row()
    bina_st.save(sub)
    res = bina_st.save(dict(sub, property_type="Köhnə tikili"))
    r = get(sub["external_id"])
    check("B4 alt növ dəyişdi: property_type və building yenilənir, 'updated'",
          res == "updated" and r["property_type"] == "Köhnə tikili" and r["building"] == "Köhnə tikili"
          and matches_of(sub["external_id"]) == 1)

    # B5: yalnız URL dəyişib → URL yenilənir, elan 'seen' (yenidən uyğunlaşdırma yoxdur)
    u = row()
    bina_st.save(u)
    upd0 = bina_st.stats["updated"]
    stamp0 = get(u["external_id"])["updated_at"]
    res1 = bina_st.save(dict(u, url=u["url"] + "?v=2"))
    u2 = row()
    bina_st.save(u2)
    out = bina_st.save_many([dict(u2, url=u2["url"] + "?v=2")])
    check("B5 yalnız URL dəyişdi (save və save_many) → URL yenilənir, 'seen', updated_at dəyişmir",
          res1 == "seen" and out.get("seen") == 1 and get(u["external_id"])["url"].endswith("?v=2")
          and get(u2["external_id"])["url"].endswith("?v=2") and get(u["external_id"])["updated_at"] == stamp0
          and bina_st.stats["updated"] == upd0)

    # B6: dəyişməyən sətir — save_many onu tam emala göndərmir
    same = row()
    bina_st.save(same)
    out = bina_st.save_many([dict(same)])
    check("B6 dəyişməyən sətir → seen (kateqoriya və URL eyni)", out == {"seen": 1}, str(dict(out)))

    # B7: silinmə və aktiv say kateqoriya daxilində
    m_old = row(property_type="Köhnə tikili")
    e_old = row(property_type="Köhnə tikili")
    bina_st.save(m_old)
    bina_st.save(e_old)
    con.execute("UPDATE listings SET category = 'ev' WHERE external_id = ?", (e_old["external_id"],))
    con.execute("UPDATE listings SET last_seen_at = ? WHERE external_id IN (?, ?)",
                (ts(now() - timedelta(days=3)), m_old["external_id"], e_old["external_id"]))
    st = Store(con, "bina", started=now())
    rm = {"deal_type": "rent", "property_type": "Köhnə tikili", "district": district}
    cand_m, cand_e = st.removal_candidates(**rm), st.removal_candidates(**rm, category="ev")
    act_m, act_e = st.active_count("rent"), st.active_count("rent", "ev")
    gone_e = st.mark_removed(**rm, category="ev")
    check("B7 silinmə namizədləri və aktiv say kateqoriya üzrə; 'ev' silinməsi mənzilə toxunmur",
          cand_m == 1 and cand_e == 1 and gone_e == 1 and get(e_old["external_id"])["active"] == 0
          and get(m_old["external_id"])["active"] == 1 and act_e == 3,  # ev1, ev2, e_old (B3 elanları Tap-dadır)
          f"namizəd mənzil={cand_m} ev={cand_e} aktiv mənzil={act_m} ev={act_e}")
    gone_m = st.mark_removed(**rm)
    check("B7b standart (category verilmir) = mənzil: yalnız mənzil elanı silinir",
          gone_m == 1 and get(m_old["external_id"])["active"] == 0)

    # B8: rematch_profile yalnız profilin kateqoriyasındakı elanlara baxır
    matching.rematch_profile(con, p["id"])
    evs = [get(e["external_id"])["id"] for e in (ev1, ev2)]
    cnt = con.execute("SELECT COUNT(*) FROM matches WHERE profile_id = ? AND listing_id = ANY(?)",
                      (p["id"], evs)).fetchone()[0]
    check("B8 rematch_profile: mənzil profili 'ev' elanlarına uyğunluq yaratmır", cnt == 0
          and matches_of(base["external_id"]) == 1)

    # B9: profilin kateqoriyası defolt 'menzil' (yeni profil sətri)
    d = con.execute("SELECT column_default FROM information_schema.columns WHERE table_name = 'profiles' "
                    "AND column_name = 'category'").fetchone()[0]
    check("B9 profiles.category defolt 'menzil'", "menzil" in (d or ""))
finally:
    con.rollback()
    real_close()

print("\n".join(results))
after = snapshot()
print("\nƏvvəl:", before)
print("Sonra:", after)
print("✓ bazada fərq yoxdur" if before == after else "✗ BAZA DƏYİŞİB!")
sys.exit(0 if all(r.startswith("✓") for r in results) and before == after else 1)
