"""Yoxa çıxan elan məntiqinin testi. Bir tranzaksiya, sonda ROLLBACK. Heç nə commit olunmur.

Real Neon bazasına qoşulur (DATABASE_URL .env-dən), Bina.az-a sorğu göndərmir.
Bazada Nərimanov kirayə elanları olmalıdır. İşlətmək (layihə kökündən):
    PYTHONIOENCODING=utf-8 python tests/test_removed.py
"""
import os, sys, tempfile
from datetime import timedelta
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))
from maklerhub.db import connect
from maklerhub.helpers import now, ts
from scraper import run, bina
from scraper.store import Store

URL = os.environ["DATABASE_URL"]
SP = tempfile.mkdtemp(prefix="maklerhub_test_")  # run.py-ın JSON çıxışı layihəyə düşməsin

def snapshot():
    c = connect(URL)
    r = tuple(c.execute("SELECT (SELECT COUNT(*) FROM listings), (SELECT COUNT(*) FROM listings WHERE active=1), "
                        "(SELECT COUNT(*) FROM matches), (SELECT COUNT(*) FROM price_history), "
                        "(SELECT COUNT(*) FROM sync_runs), (SELECT COUNT(*) FROM logs)").fetchone())
    c.close()
    return r

before = snapshot()
con = connect(URL)
con.commit = lambda: (_ for _ in ()).throw(RuntimeError("commit qadağandır"))  # təsadüfi commit olmasın
real_close = con.close
con.close = lambda: None

def node_from(row):
    return {"id": row["external_id"], "isLeased": row["deal_type"] == "rent", "area": {"value": row["area"]},
            "rooms": row["rooms"], "price": {"total": row["price"], "currency": "AZN"}, "isPaidDaily": False,
            "location": {"fullName": "", "name": ""}, "path": row["url"].replace(bina.BASE, ""),
            "floor": row["floor"], "floors": row["floors"], "preview": {}}

class FakeClient:
    """Bina.az əvəzinə: hazır səhifələr verir. has_next=True → axın sonuna qədər oxunmayıb (max pages)."""
    def __init__(self, nodes, has_next):
        self.nodes, self.has_next, self.requests, self.req_time = nodes, has_next, 1, 0.0
    def pages(self, flt, max_pages):
        yield 100, self.nodes, self.has_next

def run_full(category, nodes, has_next):
    store = Store(con)
    store.commit = lambda: None
    store.finish = lambda **kw: None
    run._open_store = lambda: store
    bina.BinaClient = lambda: FakeClient(nodes, has_next)
    run.OUT_DIR = SP
    sys.argv = ["run", "bina", "--full", "--deal", "rent", "--district", "Nərimanov", "--category", category]
    run.main()
    return store

def get(ext):
    return con.execute("SELECT id, active, removed_at, last_seen_at FROM listings WHERE source_id=1 AND external_id=?",
                       (ext,)).fetchone()

results = []
def check(name, ok, info=""):
    results.append(("✓" if ok else "✗") + " " + name + (f"  [{info}]" if info else ""))

try:
    old = ts(now() - timedelta(hours=48))
    recent = ts(now() - timedelta(hours=2))
    # Nərimanov | Yeni tikili | kirayə axını: X matching-li elan (a, d üçün), Y (c üçün)
    new_rows = con.execute("""SELECT l.* FROM listings l WHERE district='Nərimanov' AND property_type='Yeni tikili'
        AND deal_type='rent' AND active=1 ORDER BY (SELECT COUNT(*) FROM matches m WHERE m.listing_id=l.id) DESC, id""").fetchall()
    X, Y = new_rows[0], new_rows[1]
    rest = new_rows[2:]
    # Nərimanov | Köhnə tikili | kirayə axını: Z (b üçün)
    Z = con.execute("""SELECT * FROM listings WHERE district='Nərimanov' AND property_type='Köhnə tikili'
        AND deal_type='rent' AND active=1 ORDER BY id LIMIT 1""").fetchone()
    con.execute("UPDATE listings SET last_seen_at=? WHERE id IN (?, ?)", (old, X["id"], Z["id"]))
    con.execute("UPDATE listings SET last_seen_at=? WHERE id=?", (recent, Y["id"]))
    # digər elanlar "indi görülür" sayılsın deyə səhifədə verilir
    print(f"X={X['external_id']} Y={Y['external_id']} Z={Z['external_id']}")

    # --- (a) + (c): axın sonuna qədər oxunub, X və Y səhifədə yoxdur
    run_full("new", [node_from(r) for r in rest], has_next=False)
    x, y = get(X["external_id"]), get(Y["external_id"])
    check("(a) tam oxunub, 48 saat görünməyib -> active=0, removed_at dolu", x["active"] == 0 and x["removed_at"] is not None,
          f"active={x['active']} removed_at={x['removed_at']}")
    check("(c) 2 saat əvvəl görünüb -> toxunulmur", y["active"] == 1 and y["removed_at"] is None,
          f"active={y['active']} removed_at={y['removed_at']}")
    others_ok = all(get(r["external_id"])["active"] == 1 for r in rest)
    check("    səhifədəki digər elanlar aktiv qalır", others_ok)

    # --- (b): axın sonuna qədər oxunmayıb (has_next=True), Z səhifədə yoxdur
    run_full("old", [], has_next=True)
    z = get(Z["external_id"])
    check("(b) tam oxunmayıb (max pages) -> toxunulmur", z["active"] == 1 and z["removed_at"] is None,
          f"active={z['active']} removed_at={z['removed_at']}")

    # --- (d): X geri qayıdır. Matching-in yenidən işlədiyini görmək üçün X-in uyğunluqlarını əvvəlcə silirik
    m_before = con.execute("SELECT COUNT(*) FROM matches WHERE listing_id=?", (X["id"],)).fetchone()[0]
    con.execute("DELETE FROM matches WHERE listing_id=?", (X["id"],))
    st = run_full("new", [node_from(X)] + [node_from(r) for r in rest], has_next=False)
    x = get(X["external_id"])
    m_after = con.execute("SELECT COUNT(*) FROM matches WHERE listing_id=?", (X["id"],)).fetchone()[0]
    check("(d) geri qayıdır -> active=1, removed_at NULL", x["active"] == 1 and x["removed_at"] is None,
          f"active={x['active']} removed_at={x['removed_at']} geri qayıdan={st.stats['reactivated']}")
    check("(d) matching yenidən işlədi", m_before > 0 and m_after == m_before,
          f"əvvəl {m_before} uyğunluq, silindi, yenidən yarandı: {m_after}")
finally:
    con.rollback()
    real_close()

print("\n".join(results))
after = snapshot()
names = ("elan", "aktiv", "matches", "price_history", "sync_runs", "logs")
print("\nƏvvəl:", dict(zip(names, before)))
print("Sonra:", dict(zip(names, after)))
print("✓ bazada fərq yoxdur" if before == after else "✗ BAZA DƏYİŞİB")
