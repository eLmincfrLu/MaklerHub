"""scraper/auto.py testi: kilid, cooldown, "topla sonra yaz", baseline və notify_min_id qaydası, nightly.

Saytlara sorğu getmir (saxta klientlər). Real Neon bazasına qoşulur (DATABASE_URL .env-dən): hər şey bir
tranzaksiyadadır (commit → SAVEPOINT), sonda ROLLBACK. Kilid / vəziyyət / loq faylları müvəqqəti qovluqdadır.
Bazada aktiv kirayə profili olmalıdır. İşlətmək (layihə kökündən):
    PYTHONIOENCODING=utf-8 python tests/test_auto.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))
from maklerhub.db import connect
from scraper import auto
from scraper.common import BlockedError

URL = os.environ["DATABASE_URL"]
TMP = tempfile.mkdtemp(prefix="mh-auto-")
auto.STATE_DIR, auto.LOG_DIR = TMP, TMP
auto.LOCK_PATH, auto.STATE_PATH = os.path.join(TMP, "auto.lock"), os.path.join(TMP, "cooldown.json")

results = []
def check(name, ok, info=""):
    results.append(("✓" if ok else "✗") + " " + name + (f"  [{info}]" if info else ""))


# ---------------------------------------------------------------- A: kilid və cooldown (bazasız)
lk = auto.Lock(path=os.path.join(TMP, "a.lock"))
check("A1 kilid alınır", lk.acquire())
check("A2 ikinci işləmə kilidi ala bilmir", not auto.Lock(path=lk.path).acquire())
lk.release()
check("A3 buraxılandan sonra yenə alınır", auto.Lock(path=lk.path).acquire())
os.remove(lk.path)
dead = subprocess.Popen([sys.executable, "-c", "pass"])
dead.wait()
with open(lk.path, "w") as fh:
    json.dump({"pid": dead.pid, "started": time.time()}, fh)
check("A4 prosesi ölmüş kilid götürülür", auto.Lock(path=lk.path).acquire())
with open(lk.path, "w") as fh:
    json.dump({"pid": os.getpid(), "started": 0}, fh)
check("A5 çox köhnə kilid götürülür", auto.Lock(path=lk.path, max_age=3600).acquire())
os.environ[auto.NO_LOCK_ENV] = "1"
check("A6 MAKLERHUB_NO_LOCK=1 → kilid yoxlanmır", auto.Lock(path=lk.path).acquire())
del os.environ[auto.NO_LOCK_ENV]
check("A7 cooldown 60 → 120 → 240 → 360 (maks)",
      [auto.cooldown_minutes(i) for i in range(1, 6)] == [60, 120, 240, 360, 360])
st, t = {}, auto.parse("2026-10-17 10:00:00")
m = [auto.mark_block(st, "HTTP 429", at=t) for _ in range(3)]
check("A8 ardıcıl bloklanma uzanır, uğurlu dövr sıfırlayır",
      m == [60, 120, 240] and st["cooldown_until"] == "2026-10-17 14:00:00"
      and auto.cooldown_until(st, at=t) is not None and (auto.mark_ok(st, "fast", at=t) or True)
      and st["blocks"] == 0 and auto.cooldown_until(st, at=t) is None)
check("A9 nightly: bazar günü satış da, digər günlər kirayə",
      auto.nightly_deal(auto.parse("2026-10-18 03:00:00")) == "both"
      and auto.nightly_deal(auto.parse("2026-10-19 03:00:00")) == "rent")
items = list(range(58))
check("A10 --chunk i/n siyahını üst-üstə düşmədən bölür",
      sum((auto.chunk(items, f"{i}/3") for i in (1, 2, 3)), []) == items and len(auto.chunk(items, "1/3")) == 20)


# ---------------------------------------------------------------- B: bazada (SAVEPOINT, sonda ROLLBACK)
def snapshot():
    c = connect(URL)
    r = tuple(c.execute("SELECT (SELECT COUNT(*) FROM listings), (SELECT COUNT(*) FROM matches), "
                        "(SELECT COUNT(*) FROM notifications), (SELECT COUNT(*) FROM sync_runs), "
                        "(SELECT COUNT(*) FROM settings), (SELECT COUNT(*) FROM logs)").fetchone())
    c.close()
    return r


events = []


class FakeClient:
    """pages(flt, n): data[flt] səhifələri; block=True → ilk sorğuda BlockedError."""

    def __init__(self, name, data, block=False):
        self.name, self.data, self.block = name, data, block
        self.requests, self.req_time = 0, 0.0

    def pages(self, flt, max_pages):
        pages = self.data.get(flt, [])
        for i, nodes in enumerate(pages[:max_pages]):
            self.requests += 1
            events.append(("req", self.name))
            if self.block:
                raise BlockedError("HTTP 403: sayt sorğunu rədd etdi. Dayanılır.")
            yield None, nodes, i < len(pages) - 1


before = snapshot()
con = connect(URL)
real_rollback, real_close = con.rollback, con.close
con.execute("SAVEPOINT t")
con.commit = lambda: (con.execute("RELEASE SAVEPOINT t"), con.execute("SAVEPOINT t"))
con.rollback = lambda: con.execute("ROLLBACK TO SAVEPOINT t")
con.close = lambda: None


def fake_connect():
    events.append(("connect",))
    return con


try:
    p = con.execute("SELECT * FROM profiles WHERE status = 'active' AND deal_type = 'rent' ORDER BY id LIMIT 1").fetchone()
    price = (p["price_min"] + p["price_max"]) // 2
    district = p["district"] if p["district"] != "Bakı, mərkəz" else "Nəsimi"
    n = [0]

    def row(src, ext, **kw):
        n[0] += 1
        r = {"external_id": str(ext), "url": None, "title": f"Auto test {n[0]}", "deal_type": "rent",
             "property_type": "Yeni tikili", "district": district, "district_src": "bina" if src == "bina" else "map",
             "metro_name": None, "settlement": None, "rooms": p["rooms"] or 2, "area": 500 + n[0] * 10,
             "floor": n[0], "floors": 30, "price": price, "price_period": "month", "images": [], "suspicious": 0}
        r.update(kw)
        return r

    def stream(src, flt):
        return {"source": src, "label": f"test {src} {flt}", "flt": flt, "normalize": lambda node: node,
                "removal": {"deal_type": "rent", "property_type": "Yeni tikili",
                            **({"district": district} if src == "bina" else {"price_period": "month"})}}

    STREAMS = [stream("bina", "b"), stream("tap", "t")]
    auto.all_streams = lambda deal="both", sources=auto.SOURCES: STREAMS
    auto.streams_for = lambda src, deal: [s for s in STREAMS if s["source"] == src]
    B, T = 990000000, 990000000  # test nömrələri (real Bina ~6.5M, Tap ~49M)

    def get(src, ext):
        return con.execute("SELECT l.* FROM listings l JOIN sources s ON s.id = l.source_id WHERE s.key = ? "
                           "AND l.external_id = ?", (src, str(ext))).fetchone()

    def runs(label_like):
        return con.execute("SELECT l.details FROM logs l WHERE l.details LIKE ? ORDER BY l.id DESC",
                           (f"%{label_like}%",)).fetchall()

    # B1-B2: topla sonra yaz + bloklanma
    events.clear()
    clients = {"bina": FakeClient("bina", {"b": [[row("bina", B + 1)], [row("bina", B + 2)]]}),
               "tap": FakeClient("tap", {"t": [[row("tap", T + 1)]]}, block=True)}
    ctx = auto.Ctx(clients, fake_connect)
    state = {}
    err = auto.fast_cycle(ctx, state, notify=False)
    first_connect = next(i for i, e in enumerate(events) if e[0] == "connect")
    check("B1 toplama bitənə qədər bazaya qoşulmur (bütün sorğular birinci qoşulmadan əvvəl)",
          all(e[0] == "req" for e in events[:first_connect]) and all(e[0] != "req" for e in events[first_connect:]),
          f"hadisələr: {[e[0] for e in events]}")
    check("B2 bloklanma: toplanan Bina elanı yazıldı, cooldown 60 dəq, sync_runs ok=0",
          isinstance(err, BlockedError) and get("bina", B + 1) is not None and get("tap", T + 1) is None
          and state["blocks"] == 1 and auto.cooldown_until(state) is not None
          and con.execute("SELECT ok FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()["ok"] == 0,
          f"state={state}")
    check("B3 1-ci səhifə rejimi: fast yalnız 1 səhifə oxuyur", get("bina", B + 2) is None
          and clients["bina"].requests == 1)
    db_state = auto._db_state(con)
    check("B4 cooldown həm fayla, həm bazaya yazılıb (eyni bağlantıda, ikinci qoşulma yoxdur)",
          auto._read_file().get("cooldown_until") == db_state.get("cooldown_until") == state["cooldown_until"]
          and sum(1 for e in events if e[0] == "connect") == 1, f"qoşulma={sum(1 for e in events if e[0] == 'connect')}")

    def reset_state():
        auto.save_state({}, con)
        if os.path.exists(auto.STATE_PATH):
            os.remove(auto.STATE_PATH)

    def fresh_clients(block=False, k=5):
        return {"bina": FakeClient("bina", {"b": [[row("bina", B + k)]]}, block=block),
                "tap": FakeClient("tap", {"t": [[row("tap", T + k)]]})}

    # B5: lokal faylda cooldown → bazaya da, sayta da toxunmadan çıxır
    events.clear()
    clients = fresh_clients()
    ctx = auto.Ctx(clients, fake_connect)
    code = auto.main(["fast", "--quiet"], ctx=ctx)
    check("B5 fayldakı cooldown: fast nə sayta, nə bazaya qoşulmadan çıxır",
          code == 0 and not events, f"hadisələr={events}")

    # B6: fayl yoxdur (CI), bazada aktiv cooldown → toplama gedir, sonra bazadakı cooldown götürülür
    os.remove(auto.STATE_PATH)
    events.clear()
    code = auto.main(["fast", "--quiet"], ctx=ctx)
    f = auto._read_file()
    check("B6 fayl yoxdur, bazada cooldown: bazaya yalnız toplamadan sonra qoşulur, cooldown fayla keçir, "
          "sayğac sıfırlanmır",
          [e[0] for e in events][-1] == "connect" and events[0][0] == "req"
          and f.get("cooldown_until") == db_state["cooldown_until"] and f.get("blocks") == 1, f"fayl={f}")

    # B7: fayl yoxdur, bazada da vəziyyət yoxdur, ilk sorğu bloklanır → dərhal dayanır, fayla və bazaya yazılır
    reset_state()
    events.clear()
    clients = fresh_clients(block=True)
    ctx = auto.Ctx(clients, fake_connect)
    code = auto.main(["fast", "--quiet"], ctx=ctx)
    f, d = auto._read_file(), auto._db_state(con)
    check("B7 fayl yoxdur + ilk sorğu bloklanır: 1 sorğudan sonra dayanır, cooldown fayl və bazada",
          code == 3 and [e[0] for e in events].count("req") == 1 and clients["tap"].requests == 0
          and f.get("blocks") == d.get("blocks") == 1 and auto.cooldown_until(f) is not None
          and f.get("cooldown_until") == d.get("cooldown_until"), f"hadisələr={[e[0] for e in events]}")

    # B8: baseline olmadan bildirişli fast: (fayl bilmir) toplayır, amma heç nə yazmır; sonra fayl bilir → dərhal çıxır
    reset_state()
    con.execute("DELETE FROM settings WHERE key LIKE 'notify_min_id_%' OR key LIKE 'notify_margin_%' "
                "OR key = 'notify_baseline_at'")
    clients = fresh_clients(k=6)
    ctx = auto.Ctx(clients, fake_connect)
    code = auto.main(["fast"], ctx=ctx)
    check("B8 baseline yoxdur: kod 2, elan yazılmadı, fayl 'baseline: false' bilir",
          code == 2 and clients["bina"].requests == 1 and get("bina", B + 6) is None and auto._read_file().get("baseline") is False)
    events.clear()
    code = auto.main(["fast"], ctx=ctx)
    check("B9 növbəti dəfə baseline yoxdursa sayta da, bazaya da qoşulmadan çıxır (kod 2)",
          code == 2 and not events, f"hadisələr={events}")

    # B10-B11: baseline (3 səhifə, bildirişsiz) → notify_min_id = ən böyük nömrə
    n_before = con.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
    clients = {"bina": FakeClient("bina", {"b": [[row("bina", B + 10)], [row("bina", B + 30)], [row("bina", B + 20)],
                                                 [row("bina", B + 99)]]}),
               "tap": FakeClient("tap", {"t": [[row("tap", T + 50)], [row("tap", T + 70)]]})}
    ctx = auto.Ctx(clients, fake_connect)
    code = auto.main(["baseline"], ctx=ctx)
    setting = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM settings WHERE key LIKE 'notify_%'")}
    check("B10 baseline: hər mənbədən 3 səhifə, notify_min_id = ən böyük nömrə, N default",
          code == 0 and clients["bina"].requests == 3 and setting.get("notify_min_id_bina") == str(B + 30)
          and setting.get("notify_min_id_tap") == str(T + 70) and setting.get("notify_margin_bina") == "800"
          and setting.get("notify_margin_tap") == "2000" and "notify_baseline_at" in setting, f"{setting}")
    check("B11 baseline bildiriş yaratmır, amma uyğunluq yaranır",
          con.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == n_before
          and con.execute("SELECT COUNT(*) FROM matches WHERE listing_id = ?",
                          (get("bina", B + 10)["id"],)).fetchone()[0] >= 1)

    # B12-B15: notify_min_id − N qaydası
    con.execute("UPDATE settings SET value = '100' WHERE key = 'notify_margin_bina'")  # test üçün kiçik N
    hi, lo = B + 30 - 100 + 1, B + 30 - 100     # hədd = min_id − N
    clients = {"bina": FakeClient("bina", {"b": [[row("bina", hi), row("bina", lo)]]}),
               "tap": FakeClient("tap", {"t": [[]]})}
    ctx = auto.Ctx(clients, fake_connect)
    code = auto.main(["fast"], ctx=ctx)

    def notifs(ext):
        return con.execute("SELECT COUNT(*) FROM notifications WHERE listing_id = ?",
                           (get("bina", ext)["id"],)).fetchone()[0]
    check("B12 nömrə > notify_min_id − N → bildiriş getdi", code == 0 and notifs(hi) == 1, f"bildiriş={notifs(hi)}")
    check("B13 nömrə ≤ notify_min_id − N → uyğunluq var, bildiriş yox", notifs(lo) == 0
          and con.execute("SELECT COUNT(*) FROM matches WHERE listing_id = ?",
                          (get("bina", lo)["id"],)).fetchone()[0] >= 1)
    gl = con.execute("SELECT details FROM logs WHERE source = 'Bildiriş qaydası' ORDER BY id DESC LIMIT 1").fetchone()
    check("B14 qərar loqa yazılır (bildiriş getdi / bildirişsiz qaldı)",
          gl is not None and f"{hi}" in gl["details"] and "bildiriş getdi" in gl["details"]
          and f"{lo}" in gl["details"] and "bildirişsiz qaldı" in gl["details"])
    check("B15 sync_runs dövr müddətini və toplama vaxtını yazır",
          bool(runs("fast collect=")) and con.execute("SELECT COUNT(*) FROM sync_runs WHERE started_at >= ?",
                                                      (auto.fmt(auto.now() - timedelta(minutes=10)),)).fetchone()[0] >= 2)

    # B16-B18: nightly (axın üzrə yazılır, tam oxunan axında yoxa çıxanlar, içində fast dövrü)
    ran = []
    real_fast = auto.fast_cycle
    auto.fast_cycle = lambda *a, **kw: ran.append(kw.get("mode")) or None
    auto.FAST_EVERY = 0
    clients = {"bina": FakeClient("bina", {"b": [[row("bina", B + 200)], [row("bina", B + 201)]]}),
               "tap": FakeClient("tap", {"t": [[row("tap", T + 200)]]})}
    ctx = auto.Ctx(clients, fake_connect)
    code = auto.main(["nightly", "--deal", "rent"], ctx=ctx)
    auto.fast_cycle, auto.FAST_EVERY = real_fast, 15 * 60
    check("B16 nightly: bütün səhifələr oxunur və yazılır", code == 0 and get("bina", B + 201) is not None
          and get("tap", T + 200) is not None and bool(runs("nightly deal=rent")))
    check("B17 nightly içində fast dövrü işləyir", "fast(nightly)" in ran, f"{ran}")
    check("B18 kilid nightly bitəndə buraxılıb", not os.path.exists(auto.LOCK_PATH))
finally:
    real_rollback()
    real_close()

print("\n".join(results))
after = snapshot()
names = ("elan", "matches", "notifications", "sync_runs", "settings", "logs")
print("\nƏvvəl:", dict(zip(names, before)))
print("Sonra:", dict(zip(names, after)))
print("✓ bazada fərq yoxdur" if before == after else "✗ BAZA DƏYİŞİB")
