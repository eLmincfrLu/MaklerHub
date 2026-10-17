"""scraper/auto.py testi: kilid, cooldown, "topla sonra yaz", baseline və notify_min_id qaydası, nightly,
Tap.az təkrar cəhdləri (timeout), bir axının xətasının digər axınları ləğv etməməsi, davam edən tam sayğı
(deadline → növbəti iş qaldığı yerdən), loop (növbəti işin işə salınması, söndürmə açarı, cooldown, SIGTERM).

Saytlara sorğu getmir (saxta klientlər). Real Neon bazasına qoşulur (DATABASE_URL .env-dən): hər şey bir
tranzaksiyadadır (commit → SAVEPOINT), sonda ROLLBACK. Kilid / vəziyyət / loq faylları müvəqqəti qovluqdadır.
Bazada aktiv kirayə profili olmalıdır. İşlətmək (layihə kökündən):
    PYTHONIOENCODING=utf-8 python tests/test_auto.py
"""
import json
import logging
import os
import signal
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
import requests
from scraper import auto, tap
from scraper.common import BlockedError, ScrapeError

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


# ---------------------------------------------------------------- E: loop (bazasız: saxta fast dövrü)
class Proc:
    def __init__(self, code=0):
        self.returncode, self.stdout, self.stderr = code, "", ""


calls = []
fake_run = lambda args, **kw: calls.append(args) or Proc()
env_keys = ("SCRAPER_ENABLED", "GITHUB_ACTIONS", "GH_TOKEN", "GITHUB_OUTPUT", "GITHUB_REF_NAME")
saved_env = {k: os.environ.get(k) for k in env_keys}
for k in env_keys:
    os.environ.pop(k, None)
out_file = os.path.join(TMP, "gh_output")
os.environ.update(GITHUB_ACTIONS="true", GH_TOKEN="x", GITHUB_OUTPUT=out_file, GITHUB_REF_NAME="main")
check("E1 söndürmə açarı: SCRAPER_ENABLED yoxdur → növbəti iş başladılmır",
      auto.dispatch_next(run=fake_run) == "başladılmadı (söndürmə açarı)" and not calls)
os.environ["SCRAPER_ENABLED"] = "false"
check("E2 SCRAPER_ENABLED=false → növbəti iş başladılmır",
      auto.dispatch_next(run=fake_run) == "başladılmadı (söndürmə açarı)" and not calls)
os.environ["SCRAPER_ENABLED"] = "true"
ok = auto.dispatch_next(run=fake_run)
check("E3 SCRAPER_ENABLED=true → gh workflow run loop.yml -f chain=true, GITHUB_OUTPUT dispatched=true",
      ok == auto.DISPATCHED and calls == [["gh", "workflow", "run", "loop.yml", "--ref", "main", "-f", "chain=true"]]
      and "dispatched=true" in open(out_file, encoding="utf-8").read(), f"{calls}")
check("E4 gh xətası → False (workflow-un ehtiyat addımı işə düşür)",
      auto.dispatch_next(run=lambda a, **kw: Proc(1)) == "İŞƏ SALINMADI (gh xətası)")

real_fast, real_due, real_left = auto.fast_cycle, auto._due, auto.LOOP_MIN_LEFT
fast_calls, sent = [], []
auto.LOOP_MIN_LEFT = 0.3
auto._due = lambda ctx: False
auto.fast_cycle = lambda ctx, state, **kw: fast_calls.append(time.time()) or None
t = time.time()
code = auto.loop(None, {}, minutes=0.05, dispatch_at=0.02, every=1, dispatch=lambda: sent.append(time.time()))
check("E5 loop: --minutes bitəndə dayanır, fast hər `every` san, növbəti iş BİR dəfə dispatch_at-da",
      code == 0 and 2.5 <= time.time() - t <= 4.5 and 2 <= len(fast_calls) <= 4 and len(sent) == 1
      and sent[0] - t >= 1.1, f"müddət={time.time() - t:.1f} fast={len(fast_calls)} dispatch={len(sent)}")
fast_calls.clear(), sent.clear()
st_cd = {"cooldown_until": auto.fmt(auto.now() + timedelta(hours=1)), "blocks": 1}
code = auto.loop(None, st_cd, minutes=0.03, dispatch_at=0.01, every=1, dispatch=lambda: sent.append(1))
check("E6 cooldown: loop sayta sorğu göndərmir, gözləyir, amma növbəti işi yenə işə salır (zəncir qırılmır)",
      code == 0 and not fast_calls and len(sent) == 1)


def sigterm_fast(ctx, state, **kw):
    fast_calls.append(1)
    signal.raise_signal(signal.SIGTERM)


fast_calls.clear(), sent.clear()
auto.fast_cycle = sigterm_fast
auto.install_signals()
t = time.time()
code = auto.loop(None, {}, minutes=1, dispatch_at=0.9, every=1, dispatch=lambda: sent.append(1))
check("E7 SIGTERM: loop dərhal dayanır (kod 0), növbəti dövr başlamır",
      code == 0 and len(fast_calls) == 1 and time.time() - t < 3, f"fast={len(fast_calls)}")
for k, v in saved_env.items():
    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = v
state_file = {"cooldown_until": None, "blocks": 0, "baseline": True, "marker": "sigterm"}
with open(auto.STATE_PATH, "w", encoding="utf-8") as fh:
    json.dump(state_file, fh)
code = auto.main(["loop", "--minutes", "1"], ctx=auto.Ctx({}, lambda: None))
check("E8 SIGTERM main-də: kod 0, vəziyyət faylı itməyib, kilid buraxılıb",
      code == 0 and auto._read_file().get("marker") == "sigterm" and not os.path.exists(auto.LOCK_PATH))
signal.signal(signal.SIGTERM, signal.SIG_DFL)
signal.signal(signal.SIGINT, signal.default_int_handler)

# E9-E10: yekun sətir reallığı göstərir; nightly içindəki fast(nightly) dövrləri də sayılır
class Grab(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


grab = Grab()
auto.log.addHandler(grab)
auto.fast_cycle = lambda ctx, state, **kw: None
os.environ.pop("SCRAPER_ENABLED", None)
auto.loop(None, {}, minutes=0.02, dispatch_at=0, every=60)
os.environ["SCRAPER_ENABLED"] = "false"
check("E9 söndürmə açarında yekun sətir: 'növbəti iş başladılmadı (söndürmə açarı)'",
      any(l.startswith("loop bitdi") and l.endswith("növbəti iş başladılmadı (söndürmə açarı)") for l in grab.lines),
      f"{[l for l in grab.lines if l.startswith('loop bitdi')]}")


def fake_nightly(ctx, state, fast_count=None, **kw):
    fast_count[0] += 2  # içində 2 fast(nightly) dövrü


real_nightly = auto.nightly
auto.nightly, auto._due = fake_nightly, lambda ctx: True
grab.lines.clear()
auto.loop(None, {}, minutes=0.02, dispatch_at=0, every=60, dispatch=lambda: auto.DISPATCHED)
auto.nightly = real_nightly
auto.log.removeHandler(grab)
check("E10 fast sayğacı nightly içindəki dövrləri də sayır (1 + 2 = 3), 'növbəti iş işə salındı'",
      any(l.startswith("loop bitdi") and "3 fast dövrü" in l and l.endswith("növbəti iş işə salındı")
          for l in grab.lines), f"{[l for l in grab.lines if l.startswith('loop bitdi')]}")
auto.fast_cycle, auto._due, auto.LOOP_MIN_LEFT = real_fast, real_due, real_left
os.remove(auto.STATE_PATH)


# ---------------------------------------------------------------- C: Tap.az təkrar cəhd və axın xətaları (bazasız)
class Resp:
    def __init__(self, status=200, ctype="application/json", data=None):
        self.status_code, self.headers, self.text = status, {"content-type": ctype}, "x"
        self._data = data if data is not None else {"data": {"adSearch": {"ads": {
            "nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}

    def json(self):
        if "json" not in self.headers["content-type"]:
            raise ValueError("json deyil")
        return self._data


def tap_with(answers):
    """answers: ardıcıl cavablar (Resp və ya xəta). → (klient, çağırış sayğacı, gözləmələr)."""
    c = tap.TapClient(delay=(0, 0))
    calls, sleeps = [], []
    seq = list(answers)

    def post(url, data=None, timeout=None):
        calls.append(timeout)
        a = seq.pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    c.s.post = post
    return c, calls, sleeps


real_sleep = tap.time.sleep
tap_sleeps = []
tap.time.sleep = lambda sec: tap_sleeps.append(sec)
try:
    c, calls, _ = tap_with([requests.ReadTimeout("Read timed out"), requests.ReadTimeout("Read timed out"), Resp()])
    tap_sleeps.clear()
    ok = c.search(tap.stream_filter("rent", "month", "new"))
    check("C1 Tap timeout: 3 cəhd (5 və 15 san gözləmə), 3-cü cəhd uğurlu; timeout 45 san",
          ok["nodes"] == [] and len(calls) == 3 and tap_sleeps == [5, 15] and calls[0] == 45,
          f"cəhd={len(calls)} gözləmə={tap_sleeps} timeout={calls[:1]}")
    c, calls, _ = tap_with([requests.ReadTimeout("t1"), requests.ConnectionError("t2"), requests.ReadTimeout("t3")])
    try:
        c.search(tap.stream_filter("rent", "month", "new"))
        raised = None
    except requests.RequestException as e:
        raised = e
    check("C2 Tap: 3 cəhdin hamısı alınmasa xəta qalxır", isinstance(raised, requests.ReadTimeout) and len(calls) == 3)
    for status, ctype, name in ((429, "application/json", "HTTP 429"), (403, "application/json", "HTTP 403"),
                                (200, "text/html", "JSON olmayan cavab")):
        c, calls, _ = tap_with([Resp(status, ctype), Resp()])
        try:
            c.search(tap.stream_filter("rent", "month", "new"))
            raised = None
        except BlockedError as e:
            raised = e
        check(f"C3 Tap {name}: BlockedError, təkrar cəhd YOX", raised is not None and len(calls) == 1)
    c, calls, _ = tap_with([Resp(502), Resp(503), Resp()])
    tap_sleeps.clear()
    c.search(tap.stream_filter("rent", "month", "new"))
    check("C4 Tap HTTP 5xx: təkrar cəhd (3-cü uğurlu)", len(calls) == 3 and tap_sleeps == [5, 15])
finally:
    tap.time.sleep = real_sleep


class ListClient:
    """pages(): flt → səhifələr; flt → xəta (ilk sorğuda qalxır)."""

    def __init__(self, data, errors=None):
        self.data, self.errors, self.asked = data, errors or {}, []
        self.requests, self.req_time = 0, 0.0

    def pages(self, flt, max_pages):
        self.asked.append(flt)
        if flt in self.errors:
            raise self.errors[flt]
        for i, nodes in enumerate(self.data.get(flt, [])[:max_pages]):
            yield None, nodes, False


def st(src, flt):
    return {"source": src, "label": f"{src} {flt}", "flt": flt, "normalize": lambda n: n, "removal": {}}


node = lambda ext: {"external_id": str(ext)}
streams = [st("bina", "b1"), st("tap", "t1"), st("tap", "t2"), st("tap", "t3"), st("tap", "t4"), st("bina", "b2")]
clients = {"bina": ListClient({"b1": [[node(1)]], "b2": [[node(2)]]}),
           "tap": ListClient({"t2": [[node(20)]], "t4": [[node(40)]]},
                             {"t1": requests.ReadTimeout("Read timed out (read timeout=45)"),
                              "t3": ScrapeError("HTTP 502"), "t4": requests.ReadTimeout("x")})}
rows, done, error, failed = auto.collect(streams, clients, pages=1)
check("C5 bir axının timeout-u digər axınları ləğv etmir: Bina və Tap-ın sağlam axını yazılır",
      error is None and [r["external_id"] for r in rows["bina"]] == ["1", "2"]
      and [r["external_id"] for r in rows["tap"]] == ["20"] and failed["tap"] == ["tap t1", "tap t3", "tap t4"]
      and not failed["bina"], f"failed={failed}")
check("C6 alınmayan axın 'tam oxundu' sayılmır (yoxa çıxan işarələnmir)",
      [c for s_, c in done if s_["flt"] in ("t1", "t3")] == [False, False])
streams = [st("tap", "t1"), st("tap", "t3"), st("tap", "t2"), st("bina", "b1")]
clients["tap"].asked.clear()
rows, done, error, failed = auto.collect(streams, clients, pages=1)
check(f"C7 ardıcıl {auto.SOURCE_FAIL_LIMIT} axın alınmasa mənbənin qalan axınları ötürülür, digər mənbə davam edir",
      clients["tap"].asked == ["t1", "t3"] and failed["tap"][-1] == "tap t2 (ötürüldü)"
      and [r["external_id"] for r in rows["bina"]] == ["1"], f"soruşulan={clients['tap'].asked}")
check("C8 mənbənin bütün axınları alınmasa — mənbə xətası; bəziləri — xəbərdarlıq",
      isinstance(auto.source_error(None, failed, "tap", streams), ScrapeError)
      and auto.source_error(None, failed, "bina", streams) is None
      and auto.source_error(None, {"tap": ["tap t1"]}, "tap", streams) is None)
clients["tap"].errors["t2"] = BlockedError("HTTP 429")
streams = [st("tap", "t2"), st("bina", "b1")]
rows, done, error, failed = auto.collect(streams, clients, pages=1)
check("C9 bloklanma isə bütün toplamanı dayandırır", isinstance(error, BlockedError) and not rows["bina"])


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
    # real bazada yarımçıq sayğı ola bilər (loop işləyib) — test öz sayğısını başlatsın (SAVEPOINT içində)
    con.execute("DELETE FROM settings WHERE key IN (?, ?)", (auto.PROGRESS_KEY, auto.LAST_FULL_KEY))
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
          and get("tap", T + 200) is not None and bool(runs("nightly TAMAMLANDI deal=rent")))
    check("B17 nightly içində fast dövrü işləyir", "fast(nightly)" in ran, f"{ran}")
    check("B18 kilid nightly bitəndə buraxılıb", not os.path.exists(auto.LOCK_PATH))

    # B19: fast dövründə Tap axını timeout verir → Bina yazılır, Tap sync_runs ok=0, cooldown YOX, kod 0
    reset_state()

    class TimeoutClient(FakeClient):
        def pages(self, flt, max_pages):
            self.requests += 1
            raise requests.ReadTimeout("HTTPSConnectionPool(host='tap.az'): Read timed out. (read timeout=45)")
            yield

    clients = {"bina": FakeClient("bina", {"b": [[row("bina", B + 300)]]}), "tap": TimeoutClient("tap", {})}
    ctx = auto.Ctx(clients, fake_connect)
    code = auto.main(["fast", "--quiet"], ctx=ctx)
    last = {r["key"]: r["ok"] for r in con.execute(
        "SELECT DISTINCT ON (s.key) s.key, r.ok FROM sync_runs r JOIN sources s ON s.id = r.source_id "
        "ORDER BY s.key, r.id DESC")}
    tap_log = con.execute("SELECT level, message, details FROM logs WHERE source = (SELECT name FROM sources "
                          "WHERE key = 'tap') ORDER BY id DESC LIMIT 1").fetchone()
    check("B19 Tap timeout: Bina yazıldı (ok=1), Tap ok=0 + 'alınmadı' loqda, cooldown yoxdur, kod 0",
          code == 0 and get("bina", B + 300) is not None and last.get("bina") == 1 and last.get("tap") == 0
          and "alınmadı" in tap_log["message"] + tap_log["details"] and auto.cooldown_until(auto._read_file()) is None,
          f"kod={code} ok={last} loq={tap_log['message'] if tap_log else None}")
    bina_log = con.execute("SELECT details FROM logs WHERE source = (SELECT name FROM sources WHERE key = 'bina') "
                           "ORDER BY id DESC LIMIT 1").fetchone()
    check("B20 yazma fazalarının vaxtı logs.details-də", "yazma fazaları: yükləmə=" in bina_log["details"])

    # D: davam edən tam sayğı (3 axın; 1-ci axından sonra vaxt bitir → növbəti iş qaldığı yerdən)
    con.execute("DELETE FROM settings WHERE key IN (?, ?)", (auto.PROGRESS_KEY, auto.LAST_FULL_KEY))
    reset_state()
    STREAMS[:] = [stream("bina", "b"), stream("tap", "t"), stream("bina", "b2")]
    stale = con.execute("SELECT l.id FROM listings l JOIN sources s ON s.id = l.source_id WHERE s.key = 'bina' "
                        "AND l.active = 1 AND l.district = ? AND l.deal_type = 'rent' AND l.property_type = 'Yeni tikili' "
                        "ORDER BY l.id LIMIT 1", (district,)).fetchone()["id"]
    con.execute("UPDATE listings SET last_seen_at = '2020-01-01 00:00:00' WHERE id = ?", (stale,))
    clients = {"bina": FakeClient("bina", {"b": [[row("bina", B + 400)], [row("bina", B + 401)]],
                                           "b2": [[row("bina", B + 402)]]}),
               "tap": FakeClient("tap", {"t": [[row("tap", T + 400)]]})}
    ctx = auto.Ctx(clients, fake_connect)
    st = auto.load_state(con)

    def tick():  # 1-ci axın (2 səhifə) bitəndən sonra "vaxt bitir"
        if clients["bina"].requests >= 2:
            raise auto.Deadline("vaxt bitdi (test)")

    runs_before = con.execute("SELECT COUNT(*) FROM logs WHERE details LIKE '%nightly TAMAMLANDI%'").fetchone()[0]
    err = auto.nightly(ctx, st, deal="rent", tick=tick)
    prog = auto.load_progress(con)
    active = lambda lid: con.execute("SELECT active FROM listings WHERE id = ?", (lid,)).fetchone()["active"]
    done_runs = lambda: con.execute("SELECT COUNT(*) FROM logs WHERE details LIKE '%nightly TAMAMLANDI%'"
                                    ).fetchone()[0] - runs_before
    check("D1 deadline: 1-ci axın bazada 'bitdi' kimi qeyd olunub, sayğı yarımçıqdır, elanları yazılıb",
          err is None and list(prog["done"]) == ["bina: test bina b"] and prog["done"]["bina: test bina b"] is True
          and not prog["finished_at"] and get("bina", B + 401) is not None and get("tap", T + 400) is None,
          f"done={prog['done']}")
    check("D2 yarımçıq sayğıda yoxa çıxan işarələnmir, 'TAMAMLANDI' yoxdur",
          active(stale) == 1 and done_runs() == 0)
    check("D3 yarımçıq sayğı varsa nightly_due → True (vaxtdan asılı olmayaraq)",
          auto.nightly_due(con, at=auto.parse("2026-10-17 01:00:00")) is True)
    req_b = clients["bina"].requests
    code = auto.main(["nightly", "--deal", "sale"], ctx=ctx)
    prog = auto.load_progress(con)
    check("D4 növbəti iş qaldığı yerdən davam edir (1-ci axın təkrar oxunmur, --deal nəzərə alınmır)",
          code == 0 and clients["bina"].requests == req_b + 1 and prog["deal"] == "rent"
          and len(prog["done"]) == 3 and prog["finished_at"] and get("tap", T + 400) is not None,
          f"sorğu={clients['bina'].requests - req_b} done={len(prog['done'])}")
    check("D5 bütün axınlar bitəndən sonra yoxa çıxanlar toplu işarələnir, sync_runs + 'nightly TAMAMLANDI'",
          active(stale) == 0 and done_runs() >= 2
          and auto._setting(con, auto.LAST_FULL_KEY) == prog["finished_at"])
    fin = auto.parse(prog["finished_at"])
    check("D6 nightly_due: tam sayğıdan sonra 20 saat keçməyibsə yox; >20 saat və Bakı ≥03:00 → bəli; 03:00-dan əvvəl yox",
          auto.nightly_due(con, at=fin + timedelta(hours=1)) is False
          and auto.nightly_due(con, at=(fin + timedelta(days=1)).replace(hour=12)) is True
          and auto.nightly_due(con, at=(fin + timedelta(days=2)).replace(hour=2)) is False)
    req_b = clients["bina"].requests
    code = auto.main(["nightly", "--restart", "--deadline-minutes", "0.0001"], ctx=ctx)
    prog = auto.load_progress(con)
    check("D7 --deadline-minutes: vaxt bitibsə heç bir axın başlamır, yeni sayğı yarımçıq saxlanır (kod 0)",
          code == 0 and clients["bina"].requests == req_b and prog["done"] == {} and not prog["finished_at"])
    STREAMS[:] = [stream("bina", "b"), stream("tap", "t")]
finally:
    real_rollback()
    real_close()

print("\n".join(results))
after = snapshot()
names = ("elan", "matches", "notifications", "sync_runs", "settings", "logs")
print("\nƏvvəl:", dict(zip(names, before)))
print("Sonra:", dict(zip(names, after)))
print("✓ bazada fərq yoxdur" if before == after else "✗ BAZA DƏYİŞİB")
