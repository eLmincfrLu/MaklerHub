"""Toplu yazma testi (Store.save_many): köhnə yol (save() sətir-sətir) ilə EYNİ nəticə və sürət.

A: eyni məlumat iki dəfə yazılır — köhnə yol və yeni yol (hər biri SAVEPOINT içində, sonra geri qaytarılır);
   elanlar, rayon irsi, təkrar qrupları, qiymət tarixçəsi, uyğunluqlar, bildirişlər, qapı qərarları və saylar
   müqayisə olunur. Ssenarilər: yeni Bina, Bina↔Bina təkrar yerləşdirmə, rayonu boş Tap (rayon Bina-dan),
   qiymət dəyişikliyi, geri qayıdan elan, şübhəli olan elan, günlük kirayə, qiymətsiz elan, qapı (notify_min_id),
   quiet rejimi.
B: hər sorğuya süni 80 ms gecikmə (ABŞ → Frankfurt) ilə 500 yeni elan < 60 san; sorğu sayı; köhnə yolun
   20 elanla ölçülüb 500-ə hesablanmış vaxtı.

Saytlara sorğu getmir. Real Neon bazasına qoşulur (DATABASE_URL .env-dən), sonda ROLLBACK — heç nə qalmır.
Bazada aktiv kirayə profili olmalıdır. İşlətmək (layihə kökündən):
    PYTHONIOENCODING=utf-8 python tests/test_batch.py
"""
import os
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))
from maklerhub.db import connect
from scraper.store import Store

URL = os.environ["DATABASE_URL"]
DELAY = 0.08

results = []
def check(name, ok, info=""):
    results.append(("✓" if ok else "✗") + " " + name + (f"  [{info}]" if info else ""))


def snapshot():
    c = connect(URL)
    r = tuple(c.execute("SELECT (SELECT COUNT(*) FROM listings), (SELECT COUNT(*) FROM matches), "
                        "(SELECT COUNT(*) FROM notifications), (SELECT COUNT(*) FROM price_history)").fetchone())
    c.close()
    return r


before = snapshot()
con = connect(URL)
real_rollback, real_close, real_execute, real_executemany = con.rollback, con.close, con.execute, con.executemany
con.commit = lambda: (queries.update(["commit"]), real_execute("RELEASE SAVEPOINT c"), real_execute("SAVEPOINT c"))
con.close = lambda: None
queries = Counter()
delay = [0.0]


def execute(sql, params=None):
    queries["n"] += 1
    if delay[0]:
        time.sleep(delay[0])
    return real_execute(sql, params)


def executemany(sql, seq):
    queries["n"] += 1  # psycopg 3: executemany pipeline rejimində → bir gediş-gəliş
    if delay[0]:
        time.sleep(delay[0])
    return real_executemany(sql, seq)


con.execute, con.executemany = execute, executemany

try:
    real_execute("SAVEPOINT base")
    real_execute("SAVEPOINT c")
    p = con.execute("SELECT * FROM profiles WHERE status = 'active' AND deal_type = 'rent' ORDER BY id LIMIT 1").fetchone()
    price = (p["price_min"] + p["price_max"]) // 2
    district = p["district"] if p["district"] != "Bakı, mərkəz" else "Nəsimi"
    rooms = p["rooms"] or 2
    E = 970000000  # test nömrələri

    def row(ext, src="bina", **kw):
        r = {"external_id": str(E + ext), "url": None, "title": f"Batch test {ext}", "deal_type": "rent",
             "property_type": "Yeni tikili", "district": district, "district_src": "bina" if src == "bina" else "map",
             "metro_name": None, "settlement": None, "rooms": rooms, "area": 700 + ext * 7, "floor": ext % 20 + 1,
             "floors": 30, "price": price, "price_period": "month", "images": [], "suspicious": 0}
        r.update(kw)
        return r

    # əvvəlcədən bazada olan elanlar (hər iki yolda eyni, köhnə yolla yazılır)
    pre_bina = [row(1), row(2), row(3), row(4), row(5, price=price + 1)]
    pre_tap = [row(501, "tap", area=700 + 4 * 7, district="", district_src=None)]  # 4-ün təkrarı
    batch_bina = [
        row(1),                                    # dəyişməyib
        row(2, price=price - 10),                  # qiymət dəyişib
        row(3, suspicious=1),                      # şübhəli oldu
        row(5, price=price + 1),                   # geri qayıdır (aşağıda active=0 edilir)
        row(10), row(11),                          # yeni
        row(12, area=700 + 10 * 7, floor=10 % 20 + 1),  # 10-un təkrar yerləşdirilməsi (hər şey eyni)
        row(13, suspicious=1),                     # yeni şübhəli
        row(14, price_period="day", price=50),     # günlük kirayə
        row(15, price=None),                       # qiymətsiz → atlanır
        row(16),                                   # qapıdan keçmir (aşağıda)
    ]
    batch_tap = [
        row(510, "tap", area=700 + 11 * 7 + 1, district="", district_src=None),   # 11-in təkrarı, rayon Bina-dan
        row(511, "tap", area=900, district=district),                               # təkbaşına yeni
        row(501, "tap", area=700 + 4 * 7, district="", district_src=None, price=price - 500),  # qiymət dəyişdi
    ]
    gate_floor = E + 16

    def gate(r):
        n = int(r["external_id"])
        return n != gate_floor, {"source": "test", "id": n, "min_id": 0, "margin": 0, "floor": gate_floor}

    def state():
        q = ("SELECT s.key, l.external_id, l.id, l.price, l.active, l.suspicious, l.district, l.district_src, "
             "l.metro_name, l.dup_group, l.removed_at FROM listings l JOIN sources s ON s.id = l.source_id "
             "WHERE l.external_id LIKE '97000%'")
        rows = con.execute(q).fetchall()
        key = {r["id"]: (r["key"], r["external_id"]) for r in rows}
        ids = list(key)
        groups = {}
        for r in rows:
            if r["dup_group"] is not None:
                groups.setdefault(r["dup_group"], set()).add(key[r["id"]])
        listings = {key[r["id"]]: (r["price"], r["active"], r["suspicious"], r["district"], r["district_src"],
                                   r["metro_name"], frozenset(groups.get(r["dup_group"], ())), r["removed_at"] is None)
                    for r in rows}
        hist = sorted((key[h["listing_id"]], h["price"]) for h in con.execute(
            "SELECT listing_id, price FROM price_history WHERE listing_id = ANY(?)", (ids,)))
        matches = sorted((m["profile_id"], key[m["listing_id"]], m["score"], m["reasons"]) for m in con.execute(
            "SELECT profile_id, listing_id, score, reasons FROM matches WHERE listing_id = ANY(?)", (ids,)))
        notes = sorted((n["profile_id"], key[n["listing_id"]], n["user_id"], n["title"], n["body"]) for n in con.execute(
            "SELECT profile_id, listing_id, user_id, title, body FROM notifications WHERE listing_id = ANY(?)", (ids,)))
        return {"listings": listings, "history": hist, "matches": matches, "notifications": notes}

    KEYS = ("checked", "new", "updated", "skipped", "suspicious", "reactivated", "district_changed", "matches",
            "notifications")

    def scenario(batch, notify=True):
        real_execute("ROLLBACK TO SAVEPOINT base")
        real_execute("SAVEPOINT c")
        for src, rows in (("bina", pre_bina), ("tap", pre_tap)):
            Store(con, src, notify=False).save_many_rowwise(rows)
        con.execute("UPDATE listings SET active = 0, removed_at = '2026-01-01 00:00:00' WHERE external_id = ? "
                    "AND source_id = (SELECT id FROM sources WHERE key = 'bina')", (str(E + 5),))
        out = {}
        for src, rows in (("bina", batch_bina), ("tap", batch_tap)):
            st = Store(con, src, notify=notify, gate=gate)
            res = st.save_many(rows) if batch else st.save_many_rowwise(rows)
            out[src] = (dict(res), {k: st.stats[k] for k in KEYS},
                        sorted((e["external_id"], e["matches"], e["notified"], e["allowed"]) for e in st.gate_events))
        return out, state()

    for notify in (True, False):
        tag = "bildirişli" if notify else "quiet"
        old_out, old_state = scenario(False, notify)
        new_out, new_state = scenario(True, notify)
        for part in ("listings", "history", "matches", "notifications"):
            diff = set(map(str, old_state[part])) ^ set(map(str, new_state[part]))
            check(f"A {tag}: {part} köhnə yol ilə eynidir ({len(new_state[part])})",
                  old_state[part] == new_state[part], f"fərq: {sorted(diff)[:4]}")
        check(f"A {tag}: nəticə sayları və statistika eynidir", old_out == new_out, f"köhnə={old_out} yeni={new_out}")
        if notify:
            L = new_state["listings"]
            check("A ssenarilər həqiqətən baş verdi (təkrar qrupları, rayon irsi, geri qayıtma, bildiriş, qapı)",
                  len(L[("bina", str(E + 10))][6]) == 2 and L[("tap", str(E + 510))][4] == "dup"
                  and L[("tap", str(E + 510))][3] == district and L[("bina", str(E + 5))][1] == 1
                  and new_state["notifications"] and any(e[0] == str(gate_floor) and not e[3]
                                                         for e in new_out["bina"][2]),
                  f"bina={new_out['bina'][0]} tap={new_out['tap'][0]} bildiriş={len(new_state['notifications'])}")
            check("A gate_events eynidir və boş deyil", bool(new_out["bina"][2]))

    # ---------------------------------------------------------------- B: süni gecikmə ilə sürət
    import random
    rnd = random.Random(7)
    districts = [r["district"] for r in con.execute(
        "SELECT DISTINCT district FROM listings WHERE district <> '' ORDER BY district LIMIT 13")]

    def bulk(n, start, src):
        out = []
        for i in range(n):
            out.append(row(start + i, src, district=rnd.choice(districts) if src == "bina" or i % 3 else "",
                           district_src=("bina" if src == "bina" else "map"), rooms=rnd.randint(1, 4),
                           area=round(30 + rnd.random() * 150, 1), floor=rnd.randint(1, 16), floors=17,
                           price=rnd.randint(300, 2500), property_type=rnd.choice(["Yeni tikili", "Köhnə tikili"])))
        return out

    real_execute("ROLLBACK TO SAVEPOINT base")
    real_execute("SAVEPOINT c")
    delay[0] = DELAY
    sample = bulk(20, 2000, "bina")
    queries.clear()
    t = time.time()
    Store(con, "bina", gate=gate).save_many_rowwise(sample)
    old_s, old_q = time.time() - t, queries["n"]
    real_execute("ROLLBACK TO SAVEPOINT base")
    real_execute("SAVEPOINT c")
    rows_b, rows_t = bulk(400, 3000, "bina"), bulk(100, 4000, "tap")
    queries.clear()
    t = time.time()
    sb = Store(con, "bina", gate=gate)
    rb = sb.save_many(rows_b)
    st = Store(con, "tap", gate=gate)
    rt = st.save_many(rows_t)
    new_s, new_q = time.time() - t, queries["n"]
    delay[0] = 0.0
    check(f"B 500 yeni elan (80 ms gecikmə ilə) < 60 san: {new_s:.1f} san, {new_q} sorğu "
          f"({new_q / 500:.2f} sorğu/elan)", new_s < 60 and rb["new"] == 400 and rt["new"] == 100,
          f"bina {sb.phase_line()} | tap {st.phase_line()}")
    check(f"B köhnə yol: 20 elan {old_s:.1f} san, {old_q} sorğu ({old_q / 20:.1f} sorğu/elan) → 500 elan ≈ "
          f"{old_s * 25:.0f} san", True)
    check("B hər 100 elandan sonra commit olunur (400 → 4, 100 → 1)", queries["commit"] == 5, f"commit={queries['commit']}")
finally:
    real_rollback()
    real_close()

print("\n".join(results))
after = snapshot()
print("\nƏvvəl:", before)
print("Sonra:", after)
print("✓ bazada fərq yoxdur" if before == after else "✗ BAZA DƏYİŞİB")
