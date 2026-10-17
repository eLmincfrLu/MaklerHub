"""dups.group_rows (indeksli) ↔ köhnə cüt-cüt variant (tests/dups_ref.py) birə-bir müqayisəsi.

A: real baza — YALNIZ OXU (READ ONLY tranzaksiya, heç nə yazılmır): bütün aktiv bölmələr (kirayə və satış, bütün
   otaq sayları); groups, inherit, ambiguous eyni olmalıdır; bölmə üzrə vaxtlar.
B: sintetik kənar hallar: sahə fərqi düz 3 m², qiymət fərqi düz 5%, boş rayon (irs), metro ziddiyyəti, 3+ qrup,
   ikimənalı namizədlər, Tap↔Bina sahə pəncərəsinin sərhədləri, üzən nöqtə, təsadüfi data (çox toqquşma ilə).
İşlətmək (layihə kökündən):
    PYTHONIOENCODING=utf-8 python tests/test_group_rows.py          (A + B)
    PYTHONIOENCODING=utf-8 python tests/test_group_rows.py --no-db  (yalnız B)
"""
import os
import random
import sys
import time
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from maklerhub import dups
from dups_ref import group_rows_ref

results = []
def check(name, ok, info=""):
    results.append(("✓" if ok else "✗") + " " + name + (f"  [{info}]" if info else ""))


def diff(a, b):
    """İki nəticə arasındakı fərqli açarlar (ilk 5)."""
    out = []
    for name, x, y in zip(("groups", "inherit", "ambiguous"), a, b):
        keys = sorted(k for k in set(x) | set(y) if x.get(k) != y.get(k))
        if keys:
            out.append(f"{name}: {[(k, x.get(k), y.get(k)) for k in keys[:5]]}")
    return out


# ---------------------------------------------------------------- A: real baza (yalnız oxu)
if "--no-db" not in sys.argv:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    from maklerhub.db import connect
    con = connect(os.environ["DATABASE_URL"])
    try:
        con.execute("SET TRANSACTION READ ONLY")
        places = dups.bina_places(con)
        rows = con.execute(f"SELECT {dups.COLS} FROM listings l JOIN sources s ON s.id = l.source_id "
                           "WHERE l.active = 1 AND l.rooms IS NOT NULL ORDER BY l.id").fetchall()
    finally:
        con.rollback()
        con.close()
    parts = defaultdict(list)
    for r in rows:
        parts[(r["deal_type"], r["price_period"], r["rooms"])].append(r)
    bad, t_ref, t_new, timing = [], 0.0, 0.0, []
    for key, items in sorted(parts.items(), key=lambda kv: -len(kv[1])):
        t = time.time()
        ref = group_rows_ref(items, places)
        tr = time.time() - t
        t = time.time()
        new = dups.group_rows(items, places)
        tn = time.time() - t
        t_ref, t_new = t_ref + tr, t_new + tn
        timing.append((key, len(items), tr, tn, len(new[0])))
        if new != ref:
            bad.append((key, diff(ref, new)))
    check(f"A real baza: {len(parts)} bölmə, {len(rows)} aktiv elan — groups/inherit/ambiguous BİRƏ-BİR eyni",
          not bad, f"fərqli bölmələr: {bad[:3]}")
    print("Bölmə üzrə vaxt (köhnə → yeni):")
    for key, n, tr, tn, g in timing[:10]:
        print(f"  {key}: {n} elan, qrupda {g} — köhnə {tr:.2f}s → yeni {tn:.3f}s")
    print(f"  CƏMİ: köhnə {t_ref:.1f}s → yeni {t_new:.2f}s ({t_ref / max(t_new, 1e-9):.0f}x)")


# ---------------------------------------------------------------- B: sintetik
_id = [0]


def L(skey, area, price, district="Nəsimi", floor=5, floors=12, metro=None, settlement=None, ptype="Yeni tikili",
      src=None):
    _id[0] += 1
    return {"id": _id[0], "skey": skey, "district": district, "stored_district": district,
            "district_src": src or ("bina" if skey == "bina" else "map"), "metro_name": metro, "settlement": settlement,
            "rooms": 2, "area": area, "price": price, "price_period": "month", "deal_type": "rent",
            "property_type": ptype, "floor": floor if skey == "bina" else None,
            "floors": floors if skey == "bina" else None, "dup_group": None}


PLACES = {("metro_name", "Nərimanov"): {"Nərimanov"}, ("metro_name", "28 May"): {"Nəsimi"},
          ("settlement", "Masazır"): {"Abşeron"}}


def same(name, rows, expect=None):
    ref = group_rows_ref(rows, PLACES)
    new = dups.group_rows(rows, PLACES)
    ok = ref == new and (expect is None or expect(new))
    check(name, ok, "; ".join(diff(ref, new)) or ("gözlənilən nəticə deyil: " + str(new) if not ok else ""))


b, t = L("bina", 60, 1000), L("tap", 63, 1000)
same("B1 sahə fərqi düz 3 m² → birləşir", [b, t], lambda r: r[0].get(t["id"]) == b["id"])
b, t = L("bina", 60, 1000), L("tap", 63.01, 1000)
same("B2 sahə fərqi 3,01 m² → birləşmir", [b, t], lambda r: not r[0])
b, t = L("bina", 60, 1000), L("tap", 60, 950)
same("B3 qiymət fərqi düz 5% → birləşir", [b, t], lambda r: r[0].get(t["id"]) == b["id"])
b, t = L("bina", 60, 1000), L("tap", 60, 949)
same("B4 qiymət fərqi 5%-dən çox → birləşmir", [b, t], lambda r: not r[0])
b, t = L("bina", 60, 1000, metro="28 May"), L("tap", 61, 1010, district="", metro="28 May", src=None)
same("B5 boş rayonlu Tap, metro tanınır → birləşir, rayon Bina-dan (irs)", [b, t],
     lambda r: r[0].get(t["id"]) == b["id"] and r[1].get(t["id"]) == "Nəsimi")
b, t = L("bina", 60, 1000), L("tap", 62.5, 1040, district="", src=None)
same("B6 metrosuz boş rayonlu Tap: birləşir, amma sahə > 2 m² → rayon irs alınmır", [b, t],
     lambda r: r[0].get(t["id"]) == b["id"] and t["id"] not in r[1])
b, t = L("bina", 60, 1000, metro="28 May"), L("tap", 60, 1000, metro="Nərimanov")
same("B7 metro ziddiyyəti → birləşmir", [b, t], lambda r: not r[0])
b1, b2, t = L("bina", 60, 1000), L("bina", 60, 1000), L("tap", 61, 1000)
same("B8 3+ qrup: iki Bina təkrar yerləşdirmə + Tap → bir qrup", [b1, b2, t],
     lambda r: r[0].get(t["id"]) == b1["id"] == r[0].get(b2["id"]))
b1, b2, t = L("bina", 60, 1000, floor=3), L("bina", 61, 1000, floor=7), L("tap", 60.5, 1000)
same("B9 ikimənalı: Tap-ın iki fərqli Bina namizədi → birləşmir, ambiguous", [b1, b2, t],
     lambda r: not r[0] and r[2].get(t["id"]) == 2)
b, t1, t2 = L("bina", 60, 1000), L("tap", 60, 1000), L("tap", 61, 1000)
same("B10 Bina-nın iki Tap namizədi → heç biri birləşmir", [b, t1, t2], lambda r: not r[0])
rows = [L("bina", a, 1000, floor=i + 1) for i, a in enumerate((46.99, 47, 50, 53, 53.01))] + [L("tap", 50, 1000)]
same("B11 Tap↔Bina pəncərə sərhədləri (46,99 / 47 / 53 / 53,01)", rows)
rows = [L("bina", 47.1, 1000), L("tap", 50.1, 1000), L("bina", 0.1 + 0.2 + 2.7, 1000, floor=9), L("tap", 3.0, 1000)]
same("B12 üzən nöqtə sərhədləri (50,1 − 47,1; 0,1+0,2+2,7)", rows)
rows = [L("bina", None, 1000), L("tap", None, 1000), L("bina", 60, 1000, floor=None), L("bina", 60, 1000, floor=None),
        L("bina", 60, 1000, district=""), L("bina", 60, 1000, district="")]
same("B13 sahəsiz / mərtəbəsiz / rayonsuz Bina təkrarlanmır", rows, lambda r: not r[0])

rnd = random.Random(42)
bad = 0
for seed in range(300):
    rnd.seed(seed)
    rows = []
    for _ in range(rnd.randint(5, 80)):
        skey = rnd.choice(["bina", "bina", "tap"])
        rows.append(L(skey, rnd.choice([None, 45, 46.5, 48, 49, 50, 50.5, 51, 52, 53, 60.1, 63.1]),
                      rnd.choice([900, 950, 1000, 1040, 1050, 1100]),
                      district=rnd.choice(["Nəsimi", "Nəsimi", "Nərimanov", ""]),
                      floor=rnd.choice([None, 1, 2, 2]), floors=rnd.choice([None, 9, 9]),
                      metro=rnd.choice([None, None, "28 May", "Nərimanov", "Naməlum"]),
                      settlement=rnd.choice([None, None, "Masazır"]),
                      ptype=rnd.choice(["Yeni tikili", "Köhnə tikili"])))
    if group_rows_ref(rows, PLACES) != dups.group_rows(rows, PLACES):
        bad += 1
check("B14 təsadüfi data (300 hal, çox toqquşma) — köhnə ilə eyni", bad == 0, f"fərqli hal: {bad}")

print("\n".join(results))
sys.exit(1 if any(r.startswith("✗") for r in results) else 0)
