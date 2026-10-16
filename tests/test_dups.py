"""Təkrar elanların (dup_group) testi. A hissəsi bazasızdır; B hissəsi bir tranzaksiyadadır, sonda ROLLBACK.

Real Neon bazasına qoşulur (DATABASE_URL .env-dən), saytlara sorğu göndərmir.
Bazada aktiv kirayə profili olmalıdır. İşlətmək (layihə kökündən):
    PYTHONIOENCODING=utf-8 python tests/test_dups.py
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))
from maklerhub import dups
from maklerhub.db import connect
from scraper.store import Store

URL = os.environ["DATABASE_URL"]
results = []
def check(name, ok, info=""):
    results.append(("✓" if ok else "✗") + " " + name + (f"  [{info}]" if info else ""))


# ---------------------------------------------------------------- A: qaydalar (bazasız)
def L(id, skey, district="Yasamal", area=70, price=1000, metro=None, settlement=None, ptype="Yeni tikili",
      floor=5, floors=12):
    return {"id": id, "skey": skey, "district": district, "rooms": 2, "area": area, "price": price,
            "metro_name": metro, "settlement": settlement, "property_type": ptype,
            "floor": floor if skey == "bina" else None, "floors": floors if skey == "bina" else None}

g, inh, amb = dups.group_rows([L(1, "bina"), L(2, "tap", area=72.5, price=1045)])
check("A1 bina↔tap: sahə 2.5 m², qiymət 4.5% fərq → bir qrup", g == {1: 1, 2: 1})
g, _, _ = dups.group_rows([L(1, "bina"), L(2, "tap", area=74)])
check("A2 sahə fərqi 4 m² → qrup yox", g == {})
g, _, _ = dups.group_rows([L(1, "bina"), L(2, "tap", price=1060)])
check("A3 qiymət fərqi 6% → qrup yox", g == {})
g, _, _ = dups.group_rows([L(1, "bina"), L(2, "tap", district="Nəsimi")])
check("A4 fərqli rayon → qrup yox", g == {})
g, _, _ = dups.group_rows([L(1, "bina"), L(2, "tap", ptype="Köhnə tikili")])
check("A5 tikili tipi fərqli → qrup yox", g == {})
g, inh, _ = dups.group_rows([L(1, "bina"), L(2, "tap", district="")])
check("A6 Tap rayonu boş, tək namizəd → qrup + rayon Bina-dan", g == {1: 1, 2: 1} and inh == {2: "Yasamal"})
g, inh, amb = dups.group_rows([L(1, "bina"), L(3, "bina", district="Nəsimi", area=72), L(2, "tap", district="")])
check("A7 Tap rayonu boş, 2 namizəd → heç nə", g == {} and inh == {} and amb == {2: 2})
g, _, _ = dups.group_rows([L(1, "bina"), L(2, "tap"), L(3, "tap", area=71)])
check("A8 Bina-nın 2 Tap namizədi → qrup yox (qarşılıqlı tək deyil)", g == {})
g, _, _ = dups.group_rows([L(1, "bina"), L(2, "bina")])
check("A9 Bina↔Bina, mərtəbə daxil hər şey eyni → təkrar yerləşdirmə", g == {1: 1, 2: 1})
g, _, _ = dups.group_rows([L(1, "bina"), L(2, "bina", floor=6)])
check("A10 Bina↔Bina, mərtəbə fərqli → qrup yox", g == {})
g, _, _ = dups.group_rows([L(1, "bina", metro="Nizami"), L(2, "bina")])
check("A11 Bina↔Bina, yer adı fərqli → qrup yox", g == {})
g, _, _ = dups.group_rows([L(1, "bina", floor=None), L(2, "bina", floor=None)])
check("A12 Bina↔Bina, mərtəbə məlum deyil → qrup yox", g == {})
g, _, _ = dups.group_rows([L(1, "tap"), L(2, "tap")])
check("A13 Tap↔Tap heç vaxt birləşmir", g == {})
places = {("metro_name", "Sahil"): {"Səbail", "Nəsimi"}}
g, _, _ = dups.group_rows([L(1, "bina", district="Nərimanov"), L(2, "tap", district="", metro="Sahil")], places)
check("A14 Tap 'Sahil m.', Bina Nərimanov (Sahil orada görünməyib) → qrup yox", g == {})
g, inh, _ = dups.group_rows([L(1, "bina", district="Səbail"), L(2, "tap", district="", metro="Sahil")], places)
check("A15 Tap 'Sahil m.', Bina Səbail → qrup + rayon", g == {1: 1, 2: 1} and inh == {2: "Səbail"})
g, _, _ = dups.group_rows([L(1, "bina", district="Səbail"), L(2, "tap", district="", metro="Naməlum")], places)
check("A16 rayonu boş Tap, yer adı Bina.az-da tanınmır → qrup yox", g == {})
g, _, _ = dups.group_rows([L(1, "bina", metro="Nizami"), L(2, "tap", metro="Elmlər Akademiyası")])
check("A17 hər ikisində fərqli metro → qrup yox", g == {})
g, _, _ = dups.group_rows([L(1, "bina"), L(2, "bina", ptype="Köhnə tikili"), L(3, "tap")])
check("A18 3 elan, Tap↔Bina#2 tikili tipi fərqli → qrup parçalanır, Bina↔Bina qalır", g == {1: 1, 2: 1})
g, _, _ = dups.group_rows([L(1, "bina"), L(2, "bina"), L(3, "tap", area=71)])
check("A19 3 elan, hər cüt uyğun → bir qrup", g == {1: 1, 2: 1, 3: 1})
check("A20 əsas nüsxə açıq mənbədən", dups.pick([(1, 10), (2, 20)], {20}) == 2
      and dups.pick([(1, 10), (2, 20)], {10, 20}) == 1 and dups.pick([(1, 10)], set()) is None)


# ---------------------------------------------------------------- B: bazada (ROLLBACK)
def snapshot():
    c = connect(URL)
    r = tuple(c.execute("SELECT (SELECT COUNT(*) FROM listings), (SELECT COUNT(*) FROM matches), "
                        "(SELECT COUNT(*) FROM notifications), (SELECT COUNT(*) FROM listings "
                        "WHERE dup_group IS NOT NULL)").fetchone())
    c.close()
    return r


before = snapshot()
con = connect(URL)
con.commit = lambda: (_ for _ in ()).throw(RuntimeError("commit qadağandır"))
real_close = con.close
try:
    p = con.execute("SELECT * FROM profiles WHERE status = 'active' AND deal_type = 'rent' ORDER BY id LIMIT 1").fetchone()
    price = (p["price_min"] + p["price_max"]) // 2
    district = p["district"] if p["district"] != "Bakı, mərkəz" else "Nəsimi"
    n = [0]

    def row(**kw):
        n[0] += 1
        r = {"external_id": f"DUPTEST-{n[0]}", "url": None, "title": f"Dup test {n[0]}", "deal_type": "rent",
             "property_type": "Yeni tikili", "district": district, "metro_name": None, "settlement": None,
             "rooms": p["rooms"] or 2, "area": 300 + n[0] * 20, "floor": 5, "floors": 12, "price": price,
             "price_period": "month", "images": [], "suspicious": 0}
        r.update(kw)
        return r

    def get(ext):
        return con.execute("SELECT * FROM listings WHERE external_id = ?", (ext,)).fetchone()

    def group_matches(*exts):
        ids = [get(e)["id"] for e in exts]
        return con.execute(f"SELECT listing_id FROM matches WHERE profile_id = ? AND listing_id IN "
                           f"({','.join('?' * len(ids))})", [p["id"]] + ids).fetchall()

    def notifs():
        return con.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]

    bina, tap = Store(con, "bina"), Store(con, "tap")

    # B1: əvvəl Bina, sonra eyni mənzil Tap-da
    b = row()
    t = row(area=b["area"] + 2, price=int(price * 1.03))
    n0 = notifs()
    bina.save(b)
    tap.save(t)
    m = group_matches(b["external_id"], t["external_id"])
    check("B1 Bina→Tap: bir qrup, uyğunluq yalnız Bina-da, 1 bildiriş",
          get(b["external_id"])["dup_group"] is not None
          and get(b["external_id"])["dup_group"] == get(t["external_id"])["dup_group"]
          and len(m) == 1 and m[0]["listing_id"] == get(b["external_id"])["id"] and notifs() - n0 == 1,
          f"uyğunluq={len(m)} bildiriş={notifs() - n0}")

    # B2: əvvəl Tap (bildiriş gedir), sonra Bina → uyğunluq Bina-ya köçür, yeni bildiriş yox
    t2 = row()
    b2 = row(area=t2["area"] - 2)
    n0 = notifs()
    tap.save(t2)
    n1 = notifs()
    bina.save(b2)
    m = group_matches(b2["external_id"], t2["external_id"])
    nb = con.execute("SELECT listing_id FROM notifications ORDER BY id DESC LIMIT 1").fetchone()
    check("B2 Tap→Bina: uyğunluq Bina-ya köçür, cəmi 1 bildiriş, bildiriş Bina kartını açır",
          len(m) == 1 and m[0]["listing_id"] == get(b2["external_id"])["id"] and n1 - n0 == 1 and notifs() == n1
          and nb["listing_id"] == get(b2["external_id"])["id"], f"uyğunluq={len(m)} bildiriş={notifs() - n0}")

    # B3: rayonu boş Tap elanı tək Bina namizədindən rayon alır və uyğunlaşır
    b3 = row()
    t3 = row(area=b3["area"], district="")
    bina.save(b3)
    tap.save(t3)
    check("B3 rayonu boş Tap → Bina-dan rayon", get(t3["external_id"])["district"] == district
          and get(t3["external_id"])["dup_group"] == get(b3["external_id"])["dup_group"] is not None)
    tap.save(dict(t3))  # növbəti yükləmədə yenə '' gəlir
    check("B4 təkrar yükləmədə götürülmüş rayon silinmir", get(t3["external_id"])["district"] == district)

    # B5: rayonu boş Tap, iki Bina namizədi (fərqli rayon) → heç nə
    b5a = row()
    b5b = row(area=b5a["area"] + 1, floor=7, district="Pirallahı" if district != "Pirallahı" else "Qaradağ")
    t5 = row(area=b5a["area"], district="")
    for s, r in ((bina, b5a), (bina, b5b), (tap, t5)):
        s.save(r)
    check("B5 iki namizəd → qrup və rayon yox", get(t5["external_id"])["dup_group"] is None
          and get(t5["external_id"])["district"] == "")

    # B6: rematch_all-dan sonra da qrup bir dəfə görünür
    from maklerhub import matching
    matching.rematch_profile(con, p["id"])
    m = group_matches(b["external_id"], t["external_id"])
    check("B6 rematch_profile: qrup üçün 1 uyğunluq (Bina)", len(m) == 1
          and m[0]["listing_id"] == get(b["external_id"])["id"])

    # B7: makler Bina.az-ı söndürüb → əsas nüsxə Tap; hamısı söndürülüb → görünmür
    con.execute("UPDATE users SET src_bina = 0 WHERE id = ?", (p["user_id"],))
    matching.rematch_profile(con, p["id"])
    m = group_matches(b["external_id"], t["external_id"])
    check("B7 Bina söndürülüb → uyğunluq Tap nüsxəsində", len(m) == 1
          and m[0]["listing_id"] == get(t["external_id"])["id"])
    matching.match_listing(con, get(b["external_id"]), [p])
    m = group_matches(b["external_id"], t["external_id"])
    check("B8 yeni yükləmədə də (match_listing) Tap nüsxəsi əsas qalır", len(m) == 1
          and m[0]["listing_id"] == get(t["external_id"])["id"])
    con.execute("UPDATE users SET src_bina = 0, src_tap = 0 WHERE id = ?", (p["user_id"],))
    matching.rematch_profile(con, p["id"])
    check("B9 bütün mənbələr söndürülüb → qrup üçün uyğunluq yox",
          len(group_matches(b["external_id"], t["external_id"])) == 0)
finally:
    con.rollback()
    real_close()

print("\n".join(results))
after = snapshot()
names = ("elan", "matches", "notifications", "qrupdakı elan")
print("\nƏvvəl:", dict(zip(names, before)))
print("Sonra:", dict(zip(names, after)))
print("✓ bazada fərq yoxdur" if before == after else "✗ BAZA DƏYİŞİB")
