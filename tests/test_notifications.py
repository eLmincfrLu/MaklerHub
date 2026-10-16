"""Bildiriş məntiqinin testi. Bir tranzaksiya, sonda ROLLBACK. Heç nə commit olunmur.

Real Neon bazasına qoşulur (DATABASE_URL .env-dən), Bina.az-a sorğu göndərmir.
Bazada aktiv kirayə profili olmalıdır. İşlətmək (layihə kökündən):
    PYTHONIOENCODING=utf-8 python tests/test_notifications.py
"""
import os, sys
from datetime import timedelta
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))
from maklerhub.db import connect
from maklerhub.helpers import now, ts
from scraper.store import Store

URL = os.environ["DATABASE_URL"]


def snapshot():
    c = connect(URL)
    r = tuple(c.execute("SELECT (SELECT COUNT(*) FROM listings), (SELECT COUNT(*) FROM matches), "
                        "(SELECT COUNT(*) FROM notifications), (SELECT COUNT(*) FROM price_history)").fetchone())
    c.close()
    return r


before = snapshot()
con = connect(URL)
con.commit = lambda: (_ for _ in ()).throw(RuntimeError("commit qadağandır"))
real_close = con.close

results = []
def check(name, ok, info=""):
    results.append(("✓" if ok else "✗") + " " + name + (f"  [{info}]" if info else ""))

def notifs():
    return con.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]

try:
    p = con.execute("SELECT * FROM profiles WHERE status = 'active' AND deal_type = 'rent' ORDER BY id LIMIT 1").fetchone()
    mid_price = (p["price_min"] + p["price_max"]) // 2
    district = p["district"] if p["district"] != "Bakı, mərkəz" else "Nəsimi"
    n = [0]

    def row(**kw):
        n[0] += 1
        r = {"external_id": f"TEST-{n[0]}", "url": None, "title": f"Test elan {n[0]}", "deal_type": "rent",
             "property_type": "Yeni tikili", "district": district, "metro_name": None, "rooms": p["rooms"] or 2,
             "area": 60 + n[0], "floor": 5, "floors": 12, "price": mid_price, "price_period": "month", "images": [],
             "suspicious": 0}
        r.update(kw)  # sahə hər elanda fərqlidir: eyni elanlar təkrar qrupuna (dup_group) düşməsin
        return r

    def added(store, r):
        b = notifs()
        store.save(r)
        return notifs() - b

    st = Store(con)  # notify=True (standart)
    check("(1) profildən sonra gələn uyğun elan -> 1 bildiriş", added(st, row()) == 1)
    check("(2) suspicious=1 -> bildiriş yox", added(st, row(suspicious=1)) == 0)
    check("(3) günlük kirayə -> bildiriş yox", added(st, row(price_period="day")) == 0)
    other = con.execute("SELECT 1 FROM profiles WHERE district = 'Pirallahı'").fetchone()
    check("(4) heç bir profilə uyğun olmayan (Pirallahı) -> bildiriş yox",
          other is None and added(st, row(district="Pirallahı")) == 0)

    # (5) köhnə elan: profil yaradılmazdan əvvəl bazaya düşüb, indi qiyməti dəyişib və ilk dəfə uyğun gəlir
    old = row(price=p["price_max"] * 3)
    st.save(old)
    con.execute("UPDATE listings SET created_at = ? WHERE external_id = ?",
                (ts(now() - timedelta(days=30)), old["external_id"]))
    old["price"] = mid_price
    check("(5) köhnə elan yeni uyğunluq yaradır -> bildiriş yox", added(st, old) == 0)

    q = Store(con, notify=False)  # --quiet
    check("(6) --quiet: uyğun yeni elan -> bildiriş yox, amma uyğunluq var",
          added(q, row()) == 0 and q.stats["matches"] == 1, f"uyğunluq={q.stats['matches']}")

    # (7) eyni elan ikinci dəfə görünür -> təkrar bildiriş yox
    again = row()
    st.save(again)
    check("(7) eyni elan təkrar görünür -> təkrar bildiriş yox", added(st, again) == 0)

    nrow = con.execute("SELECT * FROM notifications WHERE kind = 'new_match' ORDER BY id DESC LIMIT 1").fetchone()
    check("(8) bildiriş oxunmamışdır və profil/elan bağlıdır",
          nrow["read_at"] is None and nrow["profile_id"] == p["id"] and nrow["user_id"] == p["user_id"],
          f"{nrow['title']} | {nrow['body']}")
finally:
    con.rollback()
    real_close()

print("\n".join(results))
after = snapshot()
names = ("elan", "matches", "notifications", "price_history")
print("\nƏvvəl:", dict(zip(names, before)))
print("Sonra:", dict(zip(names, after)))
print("✓ bazada fərq yoxdur" if before == after else "✗ BAZA DƏYİŞİB")
