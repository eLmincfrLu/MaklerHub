"""Avtomatik işə düşmə (Gün 15). Platformadan asılı deyil: Task Scheduler, GitHub Actions və ya əl ilə çağırılır.

    PYTHONIOENCODING=utf-8 python -m scraper.auto ping        (hər sayta 1 sorğu; bazaya qoşulmur)
    PYTHONIOENCODING=utf-8 python -m scraper.auto baseline    (hər axından 3 səhifə, bildirişsiz; notify_min_id)
    PYTHONIOENCODING=utf-8 python -m scraper.auto fast        (hər 15 dəq: bütün axınların 1-ci səhifəsi)
    PYTHONIOENCODING=utf-8 python -m scraper.auto nightly     (03:00: kirayə tam sayğı, bazar günü satış da)
    PYTHONIOENCODING=utf-8 python -m scraper.auto nightly --deal sale --source bina --chunk 1/3
    PYTHONIOENCODING=utf-8 python -m scraper.auto status      (cooldown və son sinxronizasiyalar)

Qaydalar:
- fast: əvvəl bütün axınlar yaddaşa toplanır (bu müddətdə bazaya qoşulmur), sonra BİR qısa yazma partlayışı
  (Store.save_many). Bloklanma olsa toplanan hissə yenə yazılır və dövr dayanır.
- Bloklanma (401/403/429/JSON olmayan cavab) → cooldown 60 → 120 → 240 → 360 dəq (ardıcıl bloklanmalarda);
  uğurlu dövrdən sonra sayğac sıfırlanır. Vəziyyət settings.scraper_state-dədir (əvvəlcə bazadan oxunur),
  lokal fayl (scraper/state/state.json) yalnız ehtiyatdır.
- Kilid: scraper/state/auto.lock (PID + vaxt; prosesi ölmüş və ya köhnə kilid götürülür).
  MAKLERHUB_NO_LOCK=1 kilidi söndürür (CI-da concurrency istifadə olunur).
- Bildiriş qapısı: elan yalnız external_id > notify_min_id_<mənbə> − notify_margin_<mənbə> olanda bildiriş
  yaradır (settings; baseline əmri yazır). Baseline olmadan fast bildirişlə işləmir (--quiet lazımdır).
- nightly: axın üzrə yazılır (hər axın ayrıca qısa bağlantı) və yoxa çıxanlar işarələnir; içində hər 15 dəq
  bir fast dövrü işləyir (sorğular yenə ardıcıl, 3-5 san fasilə ilə).
- Loq: scraper/logs/auto.log (1 MB × 5, fırlanan). Hər dövr hər mənbə üçün sync_runs sətri yazır.
"""
import argparse
import json
import logging
import os
import sys
import time
from argparse import Namespace
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

import requests

from . import bina, run, tap
from .common import BlockedError, ScrapeError

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATE_DIR = os.path.join(HERE, "state")
LOG_DIR = os.path.join(HERE, "logs")
LOCK_PATH = os.path.join(STATE_DIR, "auto.lock")
STATE_PATH = os.path.join(STATE_DIR, "state.json")

NO_LOCK_ENV = "MAKLERHUB_NO_LOCK"
LOG_QUIET_ENV = "MAKLERHUB_LOG_QUIET"       # 1: yalnız fayla yaz (Task Scheduler)
STATE_KEY = "scraper_state"
SOURCES = ("bina", "tap")
FAST_EVERY = 15 * 60                         # nightly içində fast dövrü (san)
COOLDOWN_STEPS = (60, 120, 240, 360)         # dəq
LOCK_MAX_AGE = 9 * 3600                      # bundan köhnə kilid ölü sayılır (bazar günü nightly ~6 saat)
BASELINE_PAGES = 3
DEFAULT_MARGIN = {"bina": 800, "tap": 2000}  # ~6 saatlıq nömrə artımı (Bina ~3300/gün, Tap ~8000/gün)
FMT = "%Y-%m-%d %H:%M:%S"

log = logging.getLogger("scraper.auto")


# ---------------------------------------------------------------- vaxt (maklerhub-u import etmədən: ping üçün)
def now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Baku")).replace(tzinfo=None, microsecond=0)
    except Exception:
        return datetime.now().replace(microsecond=0)


def fmt(dt):
    return dt.strftime(FMT) if dt else None


def parse(value):
    return datetime.strptime(value[:19], FMT) if value else None


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    log.setLevel(logging.INFO)
    if log.handlers:
        return
    form = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = RotatingFileHandler(os.path.join(LOG_DIR, "auto.log"), maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(form)
    log.addHandler(fh)
    if os.environ.get(LOG_QUIET_ENV) != "1":
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(form)
        log.addHandler(sh)


# ---------------------------------------------------------------- kilid
def _pid_alive(pid):
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class Lock:
    """Eyni anda iki işləmə olmasın. MAKLERHUB_NO_LOCK=1 → kilid yoxdur (CI concurrency)."""

    def __init__(self, path=None, mode="", max_age=LOCK_MAX_AGE):
        self.path = path or LOCK_PATH
        self.mode = mode
        self.max_age = max_age
        self.held = False

    def acquire(self):
        if os.environ.get(NO_LOCK_ENV) == "1":
            return True
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if self.stale():
                    log.warning("ölü kilid götürüldü: %s", self.info())
                    try:
                        os.remove(self.path)
                    except FileNotFoundError:
                        pass
                    continue
                return False
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"pid": os.getpid(), "mode": self.mode, "started": time.time()}, fh)
            self.held = True
            return True
        return False

    def info(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def stale(self):
        info = self.info()
        if not info or "pid" not in info:
            return True
        if time.time() - float(info.get("started", 0)) > self.max_age:
            return True
        return not _pid_alive(info["pid"])

    def release(self):
        if self.held:
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass
            self.held = False


# ---------------------------------------------------------------- vəziyyət (cooldown)
def cooldown_minutes(blocks):
    """Ardıcıl bloklanma sayı → fasilə (dəq): 60, 120, 240, sonra hər dəfə 360."""
    return COOLDOWN_STEPS[min(max(blocks, 1), len(COOLDOWN_STEPS)) - 1]


def _read_file():
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _write_file(state):
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)
    except OSError as e:
        log.warning("vəziyyət faylı yazılmadı: %s", e)


def load_state(con=None):
    """Əvvəlcə bazadan (settings.scraper_state); baza əlçatmazdırsa lokal fayldan."""
    if con is not None:
        try:
            row = con.execute("SELECT value FROM settings WHERE key = ?", (STATE_KEY,)).fetchone()
            state = json.loads(row["value"]) if row else {}
            _write_file(state)
            return state
        except Exception as e:  # baza xətası → ehtiyat fayl
            log.warning("vəziyyət bazadan oxunmadı (%s), lokal fayl işlədilir", e)
    return _read_file()


def save_state(state, con=None):
    """Lokal fayla və (con varsa) bazaya yazır. Commit çağırana aiddir."""
    _write_file(state)
    if con is not None:
        con.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (STATE_KEY, json.dumps(state, ensure_ascii=False)))


def cooldown_until(state, at=None):
    until = parse(state.get("cooldown_until"))
    return until if until and until > (at or now()) else None


def mark_block(state, reason, at=None):
    at = at or now()
    state["blocks"] = int(state.get("blocks", 0)) + 1
    minutes = cooldown_minutes(state["blocks"])
    state.update(cooldown_until=fmt(at + timedelta(minutes=minutes)), last_block=fmt(at), reason=str(reason)[:300])
    return minutes


def mark_ok(state, mode, at=None):
    state.update(blocks=0, cooldown_until=None, last_ok=fmt(at or now()), last_mode=mode)


# ---------------------------------------------------------------- axınlar
def streams_for(source, deal):
    """deal: rent | sale | both. Hər axına "source" əlavə olunur."""
    if source == "bina":
        streams, _ = run.bina_streams(Namespace(district="all", deal=deal, category="both", full=False))
    else:
        streams, _ = run.tap_streams(Namespace(district=None, deal=deal, category="both"))
    for s in streams:
        s["source"] = source
    return streams


def all_streams(deal="both", sources=SOURCES):
    return [s for src in sources for s in streams_for(src, deal)]


def chunk(items, spec):
    """'2/3' → siyahının 2-ci üçdə biri (ardıcıl hissələr)."""
    if not spec:
        return items
    i, n = (int(x) for x in spec.split("/"))
    if not 1 <= i <= n:
        raise ValueError(f"--chunk {spec}: 1 ≤ i ≤ n olmalıdır")
    size = -(-len(items) // n)
    return items[(i - 1) * size: i * size]


def collect(streams, clients, pages, on_page=None):
    """Axınları şəbəkədən oxuyur (bazaya toxunmur). → (rows {mənbə: [elan]}, done [(axın, tam oxundu)], xəta).

    Bloklanma / şəbəkə xətası olanda dayanır; o vaxta qədər toplananlar qaytarılır."""
    rows = {src: [] for src in {s["source"] for s in streams}}
    seen = {src: set() for src in rows}
    done, error = [], None
    try:
        for st in streams:
            complete = False
            for _total, nodes, has_next in clients[st["source"]].pages(st["flt"], pages):
                for node in nodes:
                    row = st["normalize"](node)
                    if row["external_id"] in seen[st["source"]]:
                        continue
                    seen[st["source"]].add(row["external_id"])
                    rows[st["source"]].append(row)
                complete = not has_next
                if on_page:
                    on_page()
            done.append((st, complete))
    except (ScrapeError, requests.RequestException) as e:
        error = e
    return rows, done, error


# ---------------------------------------------------------------- bildiriş qapısı
def _setting(con, key):
    row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def has_baseline(con):
    return all(_setting(con, f"notify_min_id_{src}") for src in SOURCES)


def make_gate(con, source):
    """notify_min_id qaydası. Baseline yoxdursa None (qapı yoxdur)."""
    min_id = _setting(con, f"notify_min_id_{source}")
    if not min_id:
        return None
    min_id = int(min_id)
    margin = int(_setting(con, f"notify_margin_{source}") or DEFAULT_MARGIN[source])
    floor = min_id - margin

    def gate(row):
        ext = str(row["external_id"])
        if not ext.isdigit():
            return True, None
        n = int(ext)
        return n > floor, {"source": source, "id": n, "min_id": min_id, "margin": margin, "floor": floor}
    return gate


def _event_line(e):
    if e["allowed"]:
        what = "bildiriş getdi" if e["notified"] else "qapıdan keçdi, amma bildiriş şərti ödənmədi"
        rel = "böyük"
    else:
        what, rel = "bildirişsiz qaldı", "kiçik/bərabər"
    return (f"ilk dəfə görülən elan {e['source']} {e['external_id']}, uyğundur ({e['matches']} profil), "
            f"nömrəsi notify_min_id−N ({e['min_id']}−{e['margin']}={e['floor']})-dən {rel} olduğu üçün {what} "
            f"(max-dan {e['min_id'] - e['id']} aşağı)")


# ---------------------------------------------------------------- bazaya yazma
class Ctx:
    """İş mühiti: klientlər və baza bağlantısı (testlər saxtasını verir)."""

    def __init__(self, clients=None, connect=None):
        self.clients = clients or {"bina": bina.BinaClient(), "tap": tap.TapClient()}
        self.connect = connect or _connect


def _connect():
    from dotenv import load_dotenv
    sys.path.insert(0, ROOT)
    from maklerhub.db import connect
    load_dotenv(os.path.join(ROOT, ".env"))
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL tapılmadı (.env / mühit dəyişəni)")
    return connect(url)


def _latency(client):
    return client.req_time / client.requests if getattr(client, "requests", 0) else 0.0


def write_burst(ctx, rows, label, error, notify, started, t0, removal=None):
    """Toplananları BİR bağlantı ilə yazır; hər mənbə üçün sync_runs. → {mənbə: stats}, gate events."""
    from .store import Store
    con = ctx.connect()
    out, events = {}, []
    try:
        for src in SOURCES:
            if src not in rows:
                continue
            gate = make_gate(con, src) if notify else None
            store = Store(con, src, notify=notify, gate=gate, started=started, t0=t0)
            if rows[src]:
                store.save_many(rows[src])
            for st, complete in (removal or []):
                if st["source"] == src and complete:
                    store.mark_removed(**st["removal"])
            store.commit()
            events += store.gate_events
            store.finish(ok=error is None, error=error, latency=_latency(ctx.clients[src]), label=label)
            out[src] = dict(store.stats)
        if events:
            from maklerhub.db import add_log
            add_log(con, "info", "Bildiriş qaydası",
                    f"{len(events)} yeni uyğun elan: {sum(e['notified'] for e in events)} bildiriş, "
                    f"{sum(not e['allowed'] for e in events)} nömrəyə görə bildirişsiz",
                    "\n".join(_event_line(e) for e in events))
            con.commit()
    finally:
        con.close()
    for e in events:
        log.info(_event_line(e))
    return out, events


def _finish_state(ctx, state, error, mode):
    """Dövrün sonunda vəziyyəti yazır (bloklanma → cooldown). Ayrı qısa bağlantı."""
    if isinstance(error, BlockedError):
        minutes = mark_block(state, error)
        log.warning("BLOKLANMA: %s → cooldown %s dəq (%s-ə qədər, ardıcıl %s)", error, minutes,
                    state["cooldown_until"], state["blocks"])
    elif error is None:
        mark_ok(state, mode)
    con = ctx.connect()
    try:
        save_state(state, con)
        con.commit()
    finally:
        con.close()


def _summary(stats):
    return " | ".join(f"{src}: yoxlanan={s.get('checked', 0)} yeni={s.get('new', 0)} yenilənən={s.get('updated', 0)} "
                      f"silinən={s.get('removed', 0)} uyğunluq={s.get('matches', 0)} bildiriş={s.get('notifications', 0)}"
                      for src, s in stats.items())


# ---------------------------------------------------------------- əmrlər
def fast_cycle(ctx, state, notify=True, update_state=True, mode="fast"):
    """Bütün axınların 1-ci səhifəsi: topla → bir yazma partlayışı. Xətanı qaytarır."""
    t0, started = time.time(), now()
    rows, done, error = collect(all_streams(), ctx.clients, pages=1)
    collect_s = time.time() - t0
    label = f"{mode} collect={collect_s:.0f}s{'' if notify else ' quiet'}"
    tw = time.time()
    stats, _ = write_burst(ctx, rows, label, error, notify, started, t0)
    log.info("%s: toplama %.0f san, yazma %.1f san, %s%s", mode, collect_s, time.time() - tw, _summary(stats),
             f" | XƏTA: {error}" if error else "")
    if update_state:
        _finish_state(ctx, state, error, mode)
    return error


def baseline(ctx, state):
    """Hər axından 3 səhifə, bildirişsiz; sonra hər mənbənin ən böyük ədədi external_id-si → notify_min_id."""
    t0, started = time.time(), now()
    rows, _done, error = collect(all_streams(), ctx.clients, pages=BASELINE_PAGES)
    stats, _ = write_burst(ctx, rows, f"baseline collect={time.time() - t0:.0f}s", error, False, started, t0)
    log.info("baseline: %s", _summary(stats))
    if error is not None:
        log.error("baseline yarımçıq qaldı (%s): elanlar yazıldı, notify_min_id YAZILMADI — sonra təkrarla", error)
        _finish_state(ctx, state, error, "baseline")
        return error
    con = ctx.connect()
    try:
        for src in SOURCES:
            ids = [int(r["external_id"]) for r in rows.get(src, []) if str(r["external_id"]).isdigit()]
            if not ids:
                raise RuntimeError(f"baseline: {src} üçün ədədi external_id yoxdur")
            con.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (f"notify_min_id_{src}", str(max(ids))))
            con.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING",
                        (f"notify_margin_{src}", str(DEFAULT_MARGIN[src])))
            log.info("baseline: notify_min_id_%s = %s (%s elandan)", src, max(ids), len(ids))
        con.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    ("notify_baseline_at", fmt(started)))
        con.commit()
    finally:
        con.close()
    _finish_state(ctx, state, None, "baseline")
    return None


def nightly_deal(at=None):
    """Hər gün kirayə, bazar günü (weekday 6) satış da."""
    return "both" if (at or now()).weekday() == 6 else "rent"


def nightly(ctx, state, deal="auto", sources=SOURCES, chunk_spec=None):
    """Tam sayğı, axın üzrə yazılır; yoxa çıxanlar işarələnir; içində hər 15 dəq fast dövrü."""
    from .store import Store
    deal = nightly_deal() if deal == "auto" else deal
    deals = ["rent", "sale"] if deal == "both" else [deal]
    streams = chunk([s for d in deals for src in sources for s in streams_for(src, d)], chunk_spec)
    log.info("nightly: deal=%s mənbə=%s chunk=%s → %s axın", deal, ",".join(sources), chunk_spec or "—", len(streams))
    t0, started = time.time(), now()
    stores, error = {}, None
    last_fast = [time.time()]

    def on_page():
        if time.time() - last_fast[0] >= FAST_EVERY:
            last_fast[0] = time.time()
            err = fast_cycle(ctx, state, update_state=False, mode="fast(nightly)")
            if isinstance(err, BlockedError):
                raise err

    for i, st in enumerate(streams, 1):
        try:
            rows, done, error = collect([st], ctx.clients, pages=100000, on_page=on_page)
        except BlockedError as e:  # nightly içindəki fast dövrü bloklandı
            rows, done, error = {}, [], e
        con = ctx.connect()
        try:
            src = st["source"]
            if src not in stores:
                gate = make_gate(con, src)
                stores[src] = Store(con, src, notify=True, gate=gate, started=started, t0=t0)
            store = stores[src]
            store.attach(con)
            if rows.get(src):
                store.save_many(rows[src])
            removed = 0
            if done and done[0][1]:
                removed = store.mark_removed(**st["removal"])
            store.commit()
            for e in store.gate_events:
                log.info(_event_line(e))
            store.gate_events = []
        finally:
            con.close()
        log.info("nightly [%s/%s] %s: %s elan, %s, yoxa çıxan %s", i, len(streams), st["label"],
                 len(rows.get(st["source"], [])), "tam oxundu" if done and done[0][1] else "TAM OXUNMADI", removed)
        if error is not None:
            break
    con = ctx.connect()
    try:
        for src, store in stores.items():
            store.attach(con)
            store.finish(ok=error is None, error=error, latency=_latency(ctx.clients[src]),
                         label=f"nightly deal={deal} chunk={chunk_spec or '—'}")
    finally:
        con.close()
    log.info("nightly bitdi (%.0f dəq): %s%s", (time.time() - t0) / 60,
             _summary({k: dict(v.stats) for k, v in stores.items()}), f" | XƏTA: {error}" if error else "")
    _finish_state(ctx, state, error, "nightly")
    return error


def ping():
    """Hər sayta 1 real sorğu: HTTP status, JSON-dur?, elan sayı. Bazaya qoşulmur, heç nə yazmır."""
    ok = True
    checks = [
        ("bina.az", bina.BinaClient(delay=(0, 0)),
         {"cityId": bina.CITY_BAKU, "categoryId": bina.CATEGORIES["new"][0], "leased": True},
         lambda d: f"saytda cəmi {d['data']['itemsConnection']['totalCount']} elan (Bakı, yeni tikili, kirayə)"),
        ("tap.az", tap.TapClient(delay=(0, 0)), tap.stream_filter("rent", "month", "new"),
         lambda d: f"1-ci səhifədə {len(d['data']['adSearch']['ads']['nodes'])} elan (Tap.az cəmi say vermir)"),
    ]
    for name, client, flt, count in checks:
        try:
            r = client.raw(flt)
        except requests.RequestException as e:
            print(f"{name}: ŞƏBƏKƏ XƏTASI {type(e).__name__}: {e}")
            ok = False
            continue
        try:
            data = r.json()
            is_json = True
        except ValueError:
            data, is_json = None, False
        try:
            info = count(data) if is_json and not data.get("errors") else (
                "GraphQL xətası: " + str(data["errors"][0].get("message"))[:200] if is_json else "—")
        except (KeyError, TypeError, IndexError):
            info = "cavab formatı gözlənilən deyil"
        print(f"{name}: HTTP {r.status_code} | JSON: {'bəli' if is_json else 'XEYR'} | {info}")
        ok = ok and r.status_code == 200 and is_json and not (data or {}).get("errors")
    return 0 if ok else 1


def status(ctx):
    con = ctx.connect()
    try:
        state = load_state(con)
        until = cooldown_until(state)
        print("cooldown:", f"{fmt(until)}-ə qədər (ardıcıl bloklanma {state.get('blocks')})" if until else "yoxdur")
        print("son bloklanma:", state.get("last_block") or "—", state.get("reason") or "")
        print("baseline:", {k: _setting(con, k) for k in ("notify_baseline_at", "notify_min_id_bina", "notify_margin_bina",
                                                         "notify_min_id_tap", "notify_margin_tap")})
        for r in con.execute(
                "SELECT s.key, r.started_at, r.ok, r.duration, r.new_count, r.updated, r.removed FROM sync_runs r "
                "JOIN sources s ON s.id = r.source_id ORDER BY r.id DESC LIMIT 6"):
            print(f"  {r['key']:4} {r['started_at']} ok={r['ok']} {r['duration']}s yeni={r['new_count']} "
                  f"yenilənən={r['updated']} silinən={r['removed']}")
    finally:
        con.close()
    return 0


# ---------------------------------------------------------------- CLI
def main(argv=None, ctx=None):
    ap = argparse.ArgumentParser(prog="python -m scraper.auto")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ping", help="hər sayta 1 sorğu (bazasız)")
    sub.add_parser("baseline", help="3 səhifə, bildirişsiz, notify_min_id")
    f = sub.add_parser("fast", help="bütün axınların 1-ci səhifəsi")
    f.add_argument("--quiet", action="store_true", help="bildiriş yaranmır (baseline olmadan da işləyir)")
    n = sub.add_parser("nightly", help="tam sayğı")
    n.add_argument("--deal", choices=["auto", "rent", "sale", "both"], default="auto",
                   help="auto: hər gün kirayə, bazar günü satış da")
    n.add_argument("--source", choices=["both", "bina", "tap"], default="both")
    n.add_argument("--chunk", default=None, help="i/n: axın siyahısının i-ci hissəsi (məs. 1/3)")
    sub.add_parser("status", help="cooldown və son sinxronizasiyalar")
    args = ap.parse_args(argv)

    if args.cmd == "ping":
        return ping()
    setup_logging()
    ctx = ctx or Ctx()
    if args.cmd == "status":
        return status(ctx)
    if args.cmd == "nightly" and args.chunk:
        chunk([0], args.chunk)  # format yoxlaması

    lock = Lock(mode=args.cmd)
    if not lock.acquire():
        log.info("%s: başqa işləmə davam edir (kilid: %s) — çıxılır", args.cmd, lock.info())
        return 0
    try:
        try:
            con = ctx.connect()
        except Exception as e:
            log.error("%s: bazaya qoşulmaq olmadı (%s) — çıxılır", args.cmd, type(e).__name__)
            return 1
        try:
            state = load_state(con)
            baseline_ok = has_baseline(con)
        finally:
            con.close()
        until = cooldown_until(state)
        if until:
            log.info("%s: cooldown davam edir (%s-ə qədər, ardıcıl bloklanma %s) — çıxılır",
                     args.cmd, fmt(until), state.get("blocks"))
            return 0
        if args.cmd == "fast":
            if not args.quiet and not baseline_ok:
                log.error("fast: baseline yoxdur (settings.notify_min_id_*). Əvvəl: python -m scraper.auto baseline "
                          "(və ya --quiet)")
                return 2
            error = fast_cycle(ctx, state, notify=not args.quiet)
        elif args.cmd == "baseline":
            error = baseline(ctx, state)
        else:
            if not baseline_ok:
                log.error("nightly: baseline yoxdur (settings.notify_min_id_*). Əvvəl: python -m scraper.auto baseline")
                return 2
            sources = SOURCES if args.source == "both" else (args.source,)
            error = nightly(ctx, state, deal=args.deal, sources=sources, chunk_spec=args.chunk)
        return 0 if error is None else (3 if isinstance(error, BlockedError) else 1)
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
