"""listings.fresh (B bölməsi "Yeni gələnlər") testi. Bir tranzaksiya, sonda ROLLBACK. Heç nə commit olunmur.

Qayda: elan ilk yazılanda bildiriş qapısının qərarı listings.fresh-ə düşür (nömrə > notify_min_id − N → 1, əks halda 0;
qapı yoxdursa və ya nömrə ədəd deyilsə → 1). B = profildən sonra bazaya düşən VƏ fresh=1; A = qalanı. Bildiriş də
eyni qaydadır (matching.is_new), ona görə B-dəki elanlar bildiriş alan elanlarla üst-üstə düşür.

Test bazasına qoşulur (YALNIZ TEST_DATABASE_URL — Neon test branch-i, tests/testdb.py), saytlara sorğu göndərmir.
Bazada aktiv kirayə profili olmalıdır. İşlətmək (layihə kökündən):
    PYTHONIOENCODING=utf-8 python tests/test_fresh.py
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testdb
TEST_URL = testdb.url()  # yalnız TEST_DATABASE_URL (qoruyucu: tests/testdb.py)
from maklerhub import create_app, makler, matching
from maklerhub.db import connect
from maklerhub.helpers import day_start, ts
from scraper.auto import make_gate
from scraper.store import Store

URL = TEST_URL
MIN_ID = 9_000_000_000           # tranzaksiya daxilində notify_min_id_* (sonda ROLLBACK)
NEW, OLD = MIN_ID + 1, 8_000_000_000  # qapıdan keçən / keçməyən nömrələr (real elanlarla toqquşmur)
results = []
def check(name, ok, info=""):
    results.append(("✓" if ok else "✗") + " " + name + (f"  [{info}]" if info else ""))


def snapshot():
    c = connect(URL)
    r = tuple(c.execute("SELECT (SELECT COUNT(*) FROM listings), (SELECT COUNT(*) FROM listings WHERE fresh = 0), "
                        "(SELECT COUNT(*) FROM matches), (SELECT COUNT(*) FROM notifications), "
                        "(SELECT string_agg(key || '=' || value, ',' ORDER BY key) FROM settings "
                        "WHERE key LIKE 'notify_m%')").fetchone())
    c.close()
    return r


app = create_app({"DATABASE": URL})
before = snapshot()
con = connect(URL)
real_close = con.close
# save_many hər hissədən sonra commit edir: burada SAVEPOINT-ə çevrilir, sonda hamısı ROLLBACK
con.execute("SAVEPOINT c")
con.commit = lambda: (con.execute("RELEASE SAVEPOINT c"), con.execute("SAVEPOINT c"))
try:
    # test qısa olsun və test elanları real elanlarla qruplaşmasın: real aktiv elanlar tranzaksiya daxilində active=0
    con.execute("UPDATE listings SET active = 0 WHERE active = 1")
    for src in ("bina", "tap"):
        for k, v in ((f"notify_min_id_{src}", str(MIN_ID)), (f"notify_margin_{src}", "800")):
            con.execute("INSERT INTO settings(key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (k, v))
    p = con.execute("SELECT * FROM profiles WHERE status = 'active' AND deal_type = 'rent' ORDER BY id LIMIT 1").fetchone()
    user = con.execute("SELECT * FROM users WHERE id = ?", (p["user_id"],)).fetchone()
    vis = sorted(matching.visible_source_ids(con, user))
    price = (p["price_min"] + p["price_max"]) // 2
    district = p["district"] if p["district"] != "Bakı, mərkəz" else "Nəsimi"
    n = [0]
    mine = []  # test elanlarının external_id-ləri

    def row(ext, **kw):
        n[0] += 1
        r = {"external_id": str(ext), "url": None, "title": f"Fresh test {n[0]}", "deal_type": "rent",
             "property_type": "Yeni tikili", "district": district, "metro_name": None, "settlement": None,
             "rooms": p["rooms"] or 2, "area": 300 + n[0] * 20, "floor": 5, "floors": 12, "price": price,
             "price_period": "month", "images": [], "suspicious": 0}
        r.update(kw)  # sahə hər elanda fərqlidir: öz aralarında təkrar qrupuna düşməsinlər
        mine.append(r["external_id"])
        return r

    def get(ext):
        return con.execute("SELECT * FROM listings WHERE external_id = ?", (ext,)).fetchone()

    def ids_of(exts):
        return [get(e)["id"] for e in exts]

    def section(name, exts=None):
        """Profil səhifəsinin bölməsi (makler.py-dakı eyni SQL): test elanlarından hansıları orada görünür."""
        sql = makler.MATCH_SQL.format(ids=makler._in(vis)) + makler.SECTIONS[name] + " AND p.id = ? AND l.id = ANY(?)"
        return {r["lid"] for r in con.execute(sql, (user["id"], p["id"], ids_of(exts or mine)))}

    def notified(exts=None):
        return {r["listing_id"] for r in con.execute(
            "SELECT DISTINCT listing_id FROM notifications WHERE profile_id = ? AND listing_id = ANY(?)",
            (p["id"], ids_of(exts or mine)))}

    def notifs():
        return con.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]

    today0 = makler.today_count(con, user, vis)
    bina = Store(con, "bina", gate=make_gate(con, "bina"))
    tap = Store(con, "tap", gate=make_gate(con, "tap"))

    # 1) köhnə nömrəli elan profildən SONRA yazılır (qaldırılan elan / tam sayğı) → fresh=0, A, bildiriş yox
    old1 = row(OLD + 1)
    n0 = notifs()
    bina.save(old1)
    lid = get(old1["external_id"])["id"]
    check("1 köhnə nömrə (save): fresh=0, A-da, B-də yox, bildiriş yox",
          get(old1["external_id"])["fresh"] == 0 and lid in section("old", [old1["external_id"]])
          and not section("new", [old1["external_id"]]) and notifs() == n0,
          f"fresh={get(old1['external_id'])['fresh']} bildiriş={notifs() - n0}")

    # 2) yeni nömrəli elan → fresh=1, B, bildiriş
    new1 = row(NEW + 1)
    n0 = notifs()
    bina.save(new1)
    lid = get(new1["external_id"])["id"]
    check("2 yeni nömrə (save): fresh=1, B-də, A-da yox, 1 bildiriş",
          get(new1["external_id"])["fresh"] == 1 and section("new", [new1["external_id"]]) == {lid}
          and not section("old", [new1["external_id"]]) and notifs() - n0 == 1, f"bildiriş={notifs() - n0}")

    # 3) toplu yol (save_many → _save_part) eyni qərarı yazır
    batch = [row(NEW + 10 + i) for i in range(3)] + [row(OLD + 10 + i) for i in range(3)]
    n0 = notifs()
    bina.save_many(batch)
    fr = [get(r["external_id"])["fresh"] for r in batch]
    check("3 save_many: yeni nömrələr fresh=1, köhnələr 0; bildiriş yalnız yenilərə (3)",
          fr == [1, 1, 1, 0, 0, 0] and notifs() - n0 == 3
          and section("new", [r["external_id"] for r in batch]) == set(ids_of(r["external_id"] for r in batch[:3])),
          f"fresh={fr} bildiriş={notifs() - n0}")

    # 4) nömrə ədəd deyil / qapı yoxdur → fresh=1
    odd = row("TAP-ABC")
    bina.save(odd)
    nogate = row(OLD + 50)
    Store(con, "bina").save(nogate)
    check("4 nömrə ədəd deyil → fresh=1; qapı yoxdur (gate=None) → köhnə nömrə də fresh=1",
          get(odd["external_id"])["fresh"] == 1 and get(nogate["external_id"])["fresh"] == 1)
    quiet_old, quiet_new = row(OLD + 60), row(NEW + 60)
    q = Store(con, "bina", notify=False, gate=make_gate(con, "bina"))  # write_burst --quiet: qapı yenə verilir
    n0 = notifs()
    q.save_many([quiet_old, quiet_new])
    check("4b --quiet (bildirişsiz, qapı ilə): fresh yenə qapının qərarıdır, bildiriş yox",
          get(quiet_old["external_id"])["fresh"] == 0 and get(quiet_new["external_id"])["fresh"] == 1
          and notifs() == n0)

    # 5) tam sayğı simulyasiyası: 40 köhnə nömrəli uyğun elan nightly kimi (notify=True, qapı ilə) yazılır
    b_before = section("new")
    full = [row(OLD + 100 + i) for i in range(40)]
    nightly = Store(con, "bina", notify=True, gate=make_gate(con, "bina"))
    n0 = notifs()
    nightly.save_many(full, chunk=15)
    fexts = [r["external_id"] for r in full]
    check("5 tam sayğı: 40 köhnə nömrə → B dəyişmir, hamısı A-da, bildiriş yox",
          section("new") == b_before and len(section("old", fexts)) == 40 and notifs() == n0
          and nightly.stats["matches"] == 40, f"A={len(section('old', fexts))} uyğunluq={nightly.stats['matches']}")

    # 6) təkrar qrupu: B əsas nüsxənin (uyğunluq orada) fresh-inə baxır; bildiriş də eyni qayda
    b_old = row(OLD + 200)
    t_new = row(NEW + 200, area=b_old["area"] + 2, price=int(price * 1.03))
    n0 = notifs()
    bina.save(b_old)
    tap.save(t_new)
    g1, g2 = get(b_old["external_id"]), get(t_new["external_id"])
    check("6a köhnə Bina (fresh=0) + yeni nömrəli Tap nüsxəsi → bir qrup, B-də yox, bildiriş yox",
          g1["dup_group"] is not None and g1["dup_group"] == g2["dup_group"] and g2["fresh"] == 1
          and not section("new", [b_old["external_id"], t_new["external_id"]]) and notifs() == n0,
          f"bildiriş={notifs() - n0}")
    b_new = row(NEW + 300)
    t_old = row(OLD + 300, area=b_new["area"] - 2)
    n0 = notifs()
    bina.save(b_new)
    tap.save(t_old)
    g1, g2 = get(b_new["external_id"]), get(t_old["external_id"])
    check("6b yeni Bina (fresh=1) + köhnə nömrəli Tap nüsxəsi → B-də Bina kartı, cəmi 1 bildiriş",
          g1["dup_group"] is not None and g1["dup_group"] == g2["dup_group"] and g2["fresh"] == 0
          and section("new", [b_new["external_id"], t_old["external_id"]]) == {g1["id"]} and notifs() - n0 == 1,
          f"bildiriş={notifs() - n0}")

    # 7) B-dəki elanlar = bildiriş alan elanlar (bu profil üçün; --quiet ilə yazılanlar istisna: orada bildiriş
    #    ümumiyyətlə yaranmır, amma təzə nömrəli elan B-də görünür)
    loud = [e for e in mine if e != quiet_new["external_id"]]
    b_set, n_set = section("new", loud), notified(loud)
    check("7 B bölməsi bildiriş alan elanlarla üst-üstə düşür", b_set == n_set and len(b_set) >= 5,
          f"B={len(b_set)} bildiriş={len(n_set)} fərq={len(b_set ^ n_set)}")
    qid = get(quiet_new["external_id"])["id"]
    check("7a --quiet ilə yazılan təzə nömrə: B-də var, bildirişi yoxdur",
          qid in section("new") and qid not in notified())
    b_set = section("new")
    a_set = section("old")
    check("7b A ilə B kəsişmir, birlikdə profilin bütün test uyğunluqlarıdır",
          not (a_set & b_set) and len(a_set | b_set) == con.execute(
              "SELECT COUNT(*) FROM matches WHERE profile_id = ? AND listing_id = ANY(?)",
              (p["id"], ids_of(mine))).fetchone()[0], f"A={len(a_set)} B={len(b_set)}")

    # 8) "Yeni" nişanı (match_view) və "Bu gün yeni" statistikası eyni qayda ilə
    sql = makler.MATCH_SQL.format(ids=makler._in(vis)) + " AND p.id = ? AND l.id = ANY(?)"
    with app.test_request_context():
        views = [makler.match_view(con, r) for r in con.execute(sql, (user["id"], p["id"], ids_of(mine)))]
    badge = {v["lid"] for v in views if v["new"]}
    check("8 'Yeni' nişanı yalnız B-dəki elanlarda", badge == b_set, f"nişan={len(badge)} B={len(b_set)}")
    user_matches = con.execute(
        "SELECT l.fresh FROM matches m JOIN profiles pr ON pr.id = m.profile_id JOIN listings l ON l.id = m.listing_id "
        "WHERE pr.user_id = ? AND pr.status = 'active' AND l.id = ANY(?) AND m.created_at >= ? AND l.source_id = ANY(?)",
        (user["id"], ids_of(mine), ts(day_start()), vis)).fetchall()
    want = sum(1 for r in user_matches if r["fresh"])
    got = makler.today_count(con, user, vis) - today0
    check("8b 'Bu gün yeni' yalnız fresh=1 sayır", got == want and want < len(user_matches),
          f"artım={got} fresh=1={want} hamısı={len(user_matches)}")

    # 9) A bölməsinin adı
    with open(os.path.join(ROOT, "maklerhub", "templates", "makler", "elanlar.html"), encoding="utf-8") as fh:
        html = fh.read()
    check("9 A bölməsinin adı: 'Mövcud və köhnə elanlar'", "Mövcud və köhnə elanlar" in html
          and "Profil yaradılanda mövcud olanlar" not in html)
finally:
    con.rollback()
    real_close()

print("\n".join(results))
after = snapshot()
print("\nƏvvəl:", before[:4])
print("Sonra:", after[:4])
print("✓ bazada fərq yoxdur" if before == after else "✗ BAZA DƏYİŞİB!")
sys.exit(0 if all(r.startswith("✓") for r in results) and before == after else 1)
