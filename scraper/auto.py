"""Avtomatik işə düşmə (Gün 15). Platformadan asılı deyil: Task Scheduler, GitHub Actions və ya əl ilə çağırılır.

    PYTHONIOENCODING=utf-8 python -m scraper.auto ping        (hər sayta 1 sorğu; bazaya qoşulmur)
    PYTHONIOENCODING=utf-8 python -m scraper.auto baseline    (hər axından 3 səhifə, bildirişsiz; notify_min_id)
    PYTHONIOENCODING=utf-8 python -m scraper.auto fast        (hər 15 dəq: bütün axınların 1-ci səhifəsi)
    PYTHONIOENCODING=utf-8 python -m scraper.auto nightly     (tam sayğını başlat / qaldığı yerdən davam et)
    PYTHONIOENCODING=utf-8 python -m scraper.auto nightly --deal sale --deadline-minutes 330 [--restart]
    PYTHONIOENCODING=utf-8 python -m scraper.auto loop --minutes 340   (davamlı rejim, GitHub Actions loop.yml)
    PYTHONIOENCODING=utf-8 python -m scraper.auto status      (cooldown və son sinxronizasiyalar)

Qaydalar:
- fast: əvvəl bütün axınlar yaddaşa toplanır (bu müddətdə bazaya qoşulmur), sonra BİR qısa yazma partlayışı
  (Store.save_many). Bloklanma olsa toplanan hissə yenə yazılır və dövr dayanır.
- Bloklanma (401/403/429/JSON olmayan cavab) → cooldown 60 → 120 → 240 → 360 dəq (ardıcıl bloklanmalarda);
  uğurlu dövrdən sonra sayğac sıfırlanır. Vəziyyət həm lokal faylda (scraper/state/cooldown.json), həm
  settings.scraper_state-dədir. fast başlayanda YALNIZ fayla baxır (Neon-u oyatmamaq üçün); bazadakı vəziyyət
  elanlar toplandıqdan sonra yazma bağlantısında oxunur və daha təzədirsə (məs. CI-da fayl yoxdur) götürülür.
  Fayl yoxdursa və ilk sorğu bloklanırsa toplama dərhal dayanır, cooldown fayla və bazaya yazılır.
  nightly / baseline başlanğıcda bazaya baxır (onsuz da uzun müddət bazaya yazırlar).
- Kilid: scraper/state/auto.lock (PID + vaxt; prosesi ölmüş və ya köhnə kilid götürülür).
  MAKLERHUB_NO_LOCK=1 kilidi söndürür (CI-da concurrency istifadə olunur).
- Bildiriş qapısı: elan yalnız external_id > notify_min_id_<mənbə> − notify_margin_<mənbə> olanda bildiriş
  yaradır (settings; baseline əmri yazır). Baseline olmadan fast bildirişlə işləmir (--quiet lazımdır).
- nightly: DAVAM EDƏN tam sayğı. Hər axın ayrıca qısa bağlantı ilə yazılır və bitən axın elə həmin tranzaksiyada
  settings.nightly_progress-ə qeyd olunur; vaxt bitəndə (--deadline-minutes / loop --minutes), SIGTERM-də və ya
  bloklanmada növbəti iş qaldığı yerdən davam edir (bazar günü satış da). Yoxa çıxanlar yalnız BÜTÜN axınlar
  bitəndən sonra toplu işarələnir, sonra sync_runs və "nightly TAMAMLANDI". 48 saatdan köhnə yarımçıq sayğı atılır.
  İçində hər 15 dəq bir fast dövrü işləyir (sorğular yenə ardıcıl, 3-5 san fasilə ilə).
- loop: `--minutes` ərzində hər 15 dəq fast; Bakı 03:00-dan sonra və son TAM sayğıdan 20 saatdan çox keçibsə
  (və ya yarımçıq sayğı varsa) nightly. (minutes − 10)-cu dəqiqədə növbəti iş `gh workflow run loop.yml` ilə
  işə salınır (yalnız SCRAPER_ENABLED=true olanda). Cooldown olanda iş gözləyir (zəncir qırılmır).
- Bir axının xətası (timeout, HTTP 5xx, GraphQL xətası) digər axınları ləğv etmir: o axın "alınmadı" kimi loqa
  yazılır (yoxa çıxanlar onda işarələnmir). Bir mənbədə ardıcıl SOURCE_FAIL_LIMIT axın alınmasa, həmin mənbənin
  dövrdəki qalan axınları ötürülür (sayt əlçatmazdır, dövr uzanmasın). Bloklanma isə bütün toplamanı dayandırır.
- Yazma fazalarının vaxtı (yükləmə / insert / təkrarlar / uyğunluq / bildiriş) loqa və logs.details-ə yazılır.
- Loq: scraper/logs/auto.log (1 MB × 5, fırlanan). Hər dövr hər mənbə üçün sync_runs sətri yazır.
"""
import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from argparse import Namespace
from collections import Counter
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

import requests

from . import bina, run, tap
from .common import BlockedError, ScrapeError, StuckError

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STATE_DIR = os.path.join(HERE, "state")
LOG_DIR = os.path.join(HERE, "logs")
LOCK_PATH = os.path.join(STATE_DIR, "auto.lock")
STATE_PATH = os.path.join(STATE_DIR, "cooldown.json")

NO_LOCK_ENV = "MAKLERHUB_NO_LOCK"
LOG_QUIET_ENV = "MAKLERHUB_LOG_QUIET"       # 1: yalnız fayla yaz (Task Scheduler)
STATE_KEY = "scraper_state"
SOURCES = ("bina", "tap")
FAST_EVERY = 15 * 60                         # nightly içində fast dövrü (san)
COOLDOWN_STEPS = (60, 120, 240, 360)         # dəq
LOCK_MAX_AGE = 9 * 3600                      # bundan köhnə kilid ölü sayılır (bazar günü nightly ~6 saat)
BASELINE_PAGES = 3
SOURCE_FAIL_LIMIT = 2                        # bir mənbədə ardıcıl bu qədər axın alınmasa, qalanı ötürülür
STALL_PAGES = 2                              # ardıcıl bu qədər səhifədə yeni elan yoxdursa → kursor irəliləmir
TOTAL_SLACK = 0.10                           # oxunan elan > totalCount × 1,1 (və +16-dan çox) → kursor irəliləmir
PROGRESS_EVERY = 5 * 60                      # nightly: axın daxilində proqres loqu (san)
DEFAULT_MARGIN = {"bina": 800, "tap": 2000}  # ~6 saatlıq nömrə artımı (Bina ~3300/gün, Tap ~8000/gün)
FMT = "%Y-%m-%d %H:%M:%S"
PROGRESS_KEY = "nightly_progress"            # settings: davam edən tam sayğının irəliləyişi (JSON)
LAST_FULL_KEY = "nightly_last_full"          # settings: son TAM bitmiş sayğının vaxtı
NIGHTLY_HOUR = 3                             # Bakı vaxtı: tam sayğı bu saatdan sonra başlayır
NIGHTLY_GAP = timedelta(hours=20)            # son tam sayğıdan ən azı bu qədər keçməlidir
COUNT_MAX_AGE = timedelta(hours=48)          # bundan köhnə yarımçıq sayğı atılır, yenisi başlayır
LOOP_MIN_LEFT = 6 * 60                       # loop: bundan az vaxt qalıbsa yeni fast dövrü başlamır (san)

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
    """Handler-lər "scraper" loggerindədir: scraper.auto və scraper.tap (təkrar cəhdlər) eyni fayla yazır."""
    os.makedirs(LOG_DIR, exist_ok=True)
    root = logging.getLogger("scraper")
    root.setLevel(logging.INFO)
    if root.handlers:
        return
    form = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = RotatingFileHandler(os.path.join(LOG_DIR, "auto.log"), maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(form)
    root.addHandler(fh)
    if os.environ.get(LOG_QUIET_ENV) != "1":
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(form)
        root.addHandler(sh)


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


def _db_state(con):
    row = con.execute("SELECT value FROM settings WHERE key = ?", (STATE_KEY,)).fetchone()
    return json.loads(row["value"]) if row else {}


def load_state(con=None):
    """con varsa bazadan (və fayl yenilənir), yoxdursa / baza xətasında lokal fayldan."""
    if con is not None:
        try:
            state = _db_state(con)
            _write_file(state)
            return state
        except Exception as e:  # baza xətası → ehtiyat fayl
            log.warning("vəziyyət bazadan oxunmadı (%s), lokal fayl işlədilir", e)
    return _read_file()


def merge_db_state(state, con):
    """Yazma bağlantısında: bazadakı vəziyyət fayldakından təzədirsə (və ya fayl yoxdursa) onu götürür.
    Bazada aktiv cooldown varsa, fayl isə bilmirsə, cooldown saxlanılır (True qaytarır)."""
    db = _db_state(con)
    file_missing = not state.get("updated_at")
    newer = (db.get("updated_at") or "") > (state.get("updated_at") or "")
    if db and (file_missing or newer):
        keep = {k: state[k] for k in ("baseline",) if k in state}
        state.clear()
        state.update(db, **keep)
    adopted = cooldown_until(state) is not None
    if adopted:
        log.warning("bazada aktiv cooldown var (%s-ə qədər), lokal fayl %s — fayl yenilənir, cooldown saxlanılır",
                    state["cooldown_until"], "yox idi" if file_missing else "köhnə idi")
    return adopted


def save_state(state, con=None):
    """Lokal fayla və (con varsa) bazaya yazır. Commit çağırana aiddir."""
    state["updated_at"] = fmt(now())
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


def collect(streams, clients, pages, on_page=None):
    """Axınları şəbəkədən oxuyur (bazaya toxunmur).
    → (rows {mənbə: [elan]}, done [(axın, tam oxundu)], xəta, failed {mənbə: [alınmayan axın etiketi]}).

    Bloklanma (BlockedError) → toplama dayanır (xəta qaytarılır); o vaxta qədər toplananlar qalır.
    Sonsuz dövrəyə qoruyucu: endCursor dəyişmir (klient), ardıcıl STALL_PAGES səhifədə yeni ID yoxdur və ya oxunan
    elan sayı totalCount-u 10%-dən çox keçir → StuckError, axın "alınmadı" (kursor irəliləmir).
    on_page(səhifə, elan, totalCount): növbəti səhifədən əvvəl çağırılır (son səhifədən sonra yox).
    Başqa xəta (timeout, HTTP 5xx, GraphQL) → yalnız həmin axın alınmır: o vaxta qədər oxunan səhifələri qalır,
    "tam oxundu" sayılmır (yoxa çıxanlar işarələnmir), növbəti axına keçilir. Bir mənbədə ardıcıl
    SOURCE_FAIL_LIMIT axın alınmasa, həmin mənbənin qalan axınları bu dövrdə ötürülür."""
    rows = {src: [] for src in {s["source"] for s in streams}}
    seen = {src: set() for src in rows}
    failed = {src: [] for src in rows}
    streak = Counter()
    done, error = [], None
    for st in streams:
        src = st["source"]
        if streak[src] >= SOURCE_FAIL_LIMIT:
            failed[src].append(st["label"] + " (ötürüldü)")
            done.append((st, False))
            continue
        complete = False
        own, page, read, stall = set(), 0, 0, 0   # bu axının ID-ləri, səhifə, oxunan elan, yenisiz səhifələr
        try:
            for total, nodes, has_next in clients[src].pages(st["flt"], pages):
                page += 1
                read += len(nodes)
                fresh = 0
                for node in nodes:
                    row = st["normalize"](node)
                    if row["external_id"] not in own:
                        own.add(row["external_id"])
                        fresh += 1
                    if row["external_id"] in seen[src]:
                        continue
                    seen[src].add(row["external_id"])
                    rows[src].append(row)
                complete = not has_next
                stall = stall + 1 if nodes and not fresh else 0
                if has_next and stall >= STALL_PAGES:
                    raise StuckError(f"ardıcıl {stall} səhifədə yeni elan yoxdur (səhifə {page})")
                if has_next and total and read > total * (1 + TOTAL_SLACK) and read > total + 16:
                    raise StuckError(f"oxunan {read} elan > totalCount {total} + 10%")
                if on_page and has_next:  # son səhifədən sonra yox: axın tam oxunubsa yarımçıq sayılmasın
                    on_page(page, len(own), total)
        except (BlockedError, Deadline) as e:
            error = e
            break
        except (ScrapeError, requests.RequestException) as e:
            streak[src] += 1
            failed[src].append(st["label"])
            done.append((st, False))
            if isinstance(e, StuckError):
                log.warning("axın alınmadı: kursor irəliləmir — %s: %s", st["label"], e)
            else:
                log.warning("axın alınmadı: %s — %s: %s", st["label"], type(e).__name__, str(e)[:300])
            if streak[src] == SOURCE_FAIL_LIMIT:
                log.warning("%s: ardıcıl %s axın alınmadı — bu dövrdə qalan %s axınları ötürülür",
                            src, SOURCE_FAIL_LIMIT, src)
            continue
        streak[src] = 0
        done.append((st, complete))
    return rows, done, error, failed


def source_error(error, failed, src, streams):
    """Mənbənin dövr nəticəsi: bloklanma → xəta; bütün axınları alınmadısa → xəta; bəziləri → None (xəbərdarlıq)."""
    if error is not None:
        return error
    total = sum(1 for st in streams if st["source"] == src)
    if failed.get(src) and len(failed[src]) >= total:
        return ScrapeError(f"bütün {total} axın alınmadı: {failed[src][0]}")
    return None


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


class NoBaseline(Exception):
    pass


def write_burst(ctx, rows, label, error, notify, started, t0, removal=None, before=None, after=None, failed=None,
                streams=()):
    """Toplananları BİR bağlantı ilə yazır; hər mənbə üçün sync_runs. → {mənbə: stats}, gate events.

    failed: {mənbə: [alınmayan axınlar]} (collect) — mənbənin bütün axınları alınmayıbsa sync_runs ok=0.
    before(con): yazmadan əvvəl (vəziyyət / baseline yoxlaması); False → heç nə yazılmır, NoBaseline.
    after(con): bağlanmadan əvvəl (vəziyyəti eyni bağlantı ilə yazmaq üçün)."""
    from .store import Store
    con = ctx.connect()
    out, events = {}, []
    try:
        if before is not None and not before(con):
            if after is not None:
                after(con)
            raise NoBaseline()
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
            err = source_error(error, failed or {}, src, streams)
            store.finish(ok=err is None, error=err, latency=_latency(ctx.clients[src]), label=label,
                         failed=(failed or {}).get(src, ()))
            out[src] = dict(store.stats, fazalar=store.phase_line())
        if events:
            from maklerhub.db import add_log
            add_log(con, "info", "Bildiriş qaydası",
                    f"{len(events)} yeni uyğun elan: {sum(e['notified'] for e in events)} bildiriş, "
                    f"{sum(not e['allowed'] for e in events)} nömrəyə görə bildirişsiz",
                    "\n".join(_event_line(e) for e in events))
            con.commit()
        if after is not None:
            after(con)
    finally:
        con.close()
    for e in events:
        log.info(_event_line(e))
    return out, events


def _update_state(state, error, mode):
    """Bloklanma → cooldown; uğurlu dövr → sıfırla (bazadan götürülmüş aktiv cooldown varsa saxlanılır)."""
    if isinstance(error, BlockedError):
        minutes = mark_block(state, error)
        log.warning("BLOKLANMA: %s → cooldown %s dəq (%s-ə qədər, ardıcıl %s)", error, minutes,
                    state["cooldown_until"], state["blocks"])
    elif error is None and cooldown_until(state) is None:
        mark_ok(state, mode)


def _finish_state(ctx, state, error, mode, con=None):
    """Vəziyyəti fayla və bazaya yazır. con verilməyibsə ayrı qısa bağlantı açılır."""
    _update_state(state, error, mode)
    own = con is None
    con = con or ctx.connect()
    try:
        save_state(state, con)
        con.commit()
    finally:
        if own:
            con.close()


def _summary(stats):
    return " | ".join(f"{src}: yoxlanan={s.get('checked', 0)} yeni={s.get('new', 0)} yenilənən={s.get('updated', 0)} "
                      f"silinən={s.get('removed', 0)} uyğunluq={s.get('matches', 0)} bildiriş={s.get('notifications', 0)}"
                      + (f" [{s['fazalar']}]" if s.get("fazalar") else "")
                      for src, s in stats.items())


def _failed_line(failed):
    lines = [f"{src}: {len(f)} axın alınmadı ({', '.join(f)})" for src, f in (failed or {}).items() if f]
    return (" | ALINMADI: " + "; ".join(lines)) if lines else ""


# ---------------------------------------------------------------- əmrlər
def fast_cycle(ctx, state, notify=True, update_state=True, mode="fast"):
    """Bütün axınların 1-ci səhifəsi: topla → bir yazma partlayışı. Xətanı qaytarır."""
    t0, started = time.time(), now()
    streams = all_streams()
    rows, done, error, failed = collect(streams, ctx.clients, pages=1)
    collect_s = time.time() - t0
    label = f"{mode} collect={collect_s:.0f}s{'' if notify else ' quiet'}"
    tw = time.time()

    def before(con):
        # baza ilk dəfə burada açılır: bazadakı cooldown / baseline vəziyyəti
        merge_db_state(state, con)
        state["baseline"] = has_baseline(con)
        if notify and not state["baseline"]:
            log.error("%s: baseline yoxdur (settings.notify_min_id_*) — elanlar YAZILMADI. Əvvəl: "
                      "python -m scraper.auto baseline (və ya --quiet)", mode)
            return False
        return True

    def after(con):
        _finish_state(ctx, state, error, mode, con=con)

    stats, _ = write_burst(ctx, rows, label, error, notify, started, t0,
                           before=before if update_state else None, after=after if update_state else None,
                           failed=failed, streams=streams)
    log.info("%s: toplama %.0f san, yazma %.1f san, %s%s%s", mode, collect_s, time.time() - tw, _summary(stats),
             _failed_line(failed), f" | XƏTA: {error}" if error else "")
    return error


def baseline(ctx, state):
    """Hər axından 3 səhifə, bildirişsiz; sonra hər mənbənin ən böyük ədədi external_id-si → notify_min_id."""
    t0, started = time.time(), now()
    streams = all_streams()
    rows, _done, error, failed = collect(streams, ctx.clients, pages=BASELINE_PAGES)
    if error is None and any(failed.values()):
        error = ScrapeError("alınmayan axınlar var: " + "; ".join(x for f in failed.values() for x in f))
    stats, _ = write_burst(ctx, rows, f"baseline collect={time.time() - t0:.0f}s", error, False, started, t0)
    log.info("baseline: %s%s", _summary(stats), _failed_line(failed))
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
    state["baseline"] = True
    _finish_state(ctx, state, None, "baseline")
    return None


def nightly_deal(at=None):
    """Hər gün kirayə, bazar günü (weekday 6) satış da."""
    return "both" if (at or now()).weekday() == 6 else "rent"


# ---------------------------------------------------------------- deadline və siqnallar
class Deadline(Exception):
    """İş vaxtı bitdi (--minutes / --deadline-minutes) və ya SIGTERM/SIGINT gəldi: vəziyyət yazılıb çıxılır."""


_SIGNAL = {"hit": False}


def _on_signal(signum, _frame):
    if _SIGNAL["hit"]:
        return
    _SIGNAL["hit"] = True
    raise Deadline(f"siqnal {signal.Signals(signum).name}")


def install_signals():
    """GitHub ləğv / timeout zamanı əvvəl SIGINT, ~7,5 san sonra SIGTERM göndərir: Deadline qaldırılır,
    yazılmış hissə commit olunub, vəziyyət (cooldown, sayğının irəliləyişi) yazılır."""
    _SIGNAL["hit"] = False
    for name in ("SIGTERM", "SIGINT"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), _on_signal)


def _hm(sec):
    sec = max(int(sec), 0)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}"


# ---------------------------------------------------------------- nightly: davam edən tam sayğı
def stream_key(st):
    return f"{st['source']}: {st['label']}"


def load_progress(con):
    row = con.execute("SELECT value FROM settings WHERE key = ?", (PROGRESS_KEY,)).fetchone()
    return json.loads(row["value"]) if row else None


def save_progress(con, prog):
    """Commit çağırana aiddir (axının elanları ilə eyni tranzaksiyada)."""
    con.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (PROGRESS_KEY, json.dumps(prog, ensure_ascii=False)))


def count_streams(deal):
    deals = ["rent", "sale"] if deal == "both" else [deal]
    return [s for d in deals for src in SOURCES for s in streams_for(src, d)]


def nightly_due(con, at=None):
    """Sayğı başlamalıdır / davam etməlidir: yarımçıq sayğı varsa — həmişə; yoxdursa Bakı 03:00-dan sonra VƏ son
    TAM sayğıdan 20 saatdan çox keçibsə."""
    at = at or now()
    prog = load_progress(con)
    if prog and not prog.get("finished_at") and at - parse(prog["started_at"]) < COUNT_MAX_AGE:
        return True
    last = parse(_setting(con, LAST_FULL_KEY))
    return at.hour >= NIGHTLY_HOUR and (last is None or at - last > NIGHTLY_GAP)


def nightly(ctx, state, deal="auto", deadline=None, restart=False, tick=None, last_fast=None, fast_count=None):
    """Tam sayğı (davam edən): hansı axınların bitdiyi settings.nightly_progress-dədir; vaxt bitəndə (deadline,
    siqnal) və ya bloklanmada növbəti iş qaldığı yerdən davam edir. Silinmiş elanlar yalnız BÜTÜN axınlar
    bitəndən sonra, toplu və MƏNBƏ üzrə işarələnir: mənbənin bütün axınları tam oxunubsa; alınmayan axını olan
    mənbənin elanlarına toxunulmur. Sonra sync_runs və "nightly TAMAMLANDI". İçində hər 15 dəq fast dövrü.
    Axın daxilində hər ~5 dəq proqres loqu (səhifə, elan, totalCount varsa ETA).

    → xəta (BlockedError) və ya None; Deadline qalxmır (irəliləyiş yazılır, None qaytarılır)."""
    from .store import Store
    t_job = time.time()
    last_fast = last_fast or [time.time()]
    tick = tick or (lambda: None)

    def check():
        tick()
        if deadline is not None and time.time() >= deadline:
            raise Deadline("vaxt bitdi")

    con = ctx.connect()
    try:
        prog = load_progress(con)
        at = now()
        if prog and not prog.get("finished_at") and at - parse(prog["started_at"]) >= COUNT_MAX_AGE:
            log.warning("nightly: yarımçıq sayğı çox köhnədir (%s başlayıb) — yeni sayğı başlayır", prog["started_at"])
            prog = None
        if restart or not prog or prog.get("finished_at"):
            d = nightly_deal(at) if deal == "auto" else deal
            prog = {"started_at": fmt(at), "deal": d, "streams": [stream_key(s) for s in count_streams(d)],
                    "done": {}, "failed": [], "stats": {}, "finished_at": None}
            save_progress(con, prog)
            con.commit()
            log.info("nightly: YENİ sayğı deal=%s, %s axın", d, len(prog["streams"]))
        else:
            if deal not in ("auto", prog["deal"]):
                log.info("nightly: yarımçıq sayğı davam edir (deal=%s), --deal %s nəzərə alınmır", prog["deal"], deal)
            log.info("nightly: sayğı DAVAM EDİR (%s başlayıb, deal=%s): %s/%s axın bitib", prog["started_at"],
                     prog["deal"], len(prog["done"]), len(prog["streams"]))
    finally:
        con.close()

    by_key = {stream_key(s): s for s in count_streams(prog["deal"])}
    total = len(prog["streams"])
    todo = [by_key[k] for k in prog["streams"] if k not in prog["done"] and k in by_key]
    started = parse(prog["started_at"])
    t_count = time.time() - (now() - started).total_seconds()   # sayğının başlanğıcı (epoch)
    stores, error, stop = {}, None, None
    done_here = 0
    cur = {"label": "", "i": 0, "t0": time.time(), "logged": time.time()}

    def on_page(page=0, n=0, total_count=None):
        check()
        if page and time.time() - cur["logged"] >= PROGRESS_EVERY:
            cur["logged"] = time.time()
            eta = ""
            if total_count:
                per_page = (time.time() - cur["t0"]) / page
                eta = f", ETA axın {_hm(per_page * max(-(-total_count // max(n // page, 1)) - page, 0))}"
            log.info("nightly axın %s/%s [%s] səhifə %s, elan %s%s%s", cur["i"], total, cur["label"], page, n,
                     f"/{total_count}" if total_count else "", eta)
        if time.time() - last_fast[0] >= FAST_EVERY:
            last_fast[0] = time.time()
            err = fast_cycle(ctx, state, update_state=False, mode="fast(nightly)")
            if fast_count is not None:  # loop-un yekun sətri fast(nightly) dövrlərini də sayır
                fast_count[0] += 1
            if isinstance(err, (BlockedError, Deadline)):
                raise err

    def store_for(con, src):
        if src not in stores:
            stores[src] = Store(con, src, notify=True, gate=make_gate(con, src), started=started, t0=t_count)
            stores[src].stats = Counter(prog["stats"].get(src, {}))
        store = stores[src]
        store.attach(con)
        return store

    for st in todo:
        try:
            on_page()  # axınlar arası: deadline / siqnal və vaxtı çatıbsa fast dövrü
        except Deadline as e:
            stop = e
            break
        except BlockedError as e:
            error = e
            break
        cur.update(label=st["label"], i=len(prog["done"]) + 1, t0=time.time(), logged=time.time())
        rows, done, error, bad = collect([st], ctx.clients, pages=100000, on_page=on_page)
        if isinstance(error, Deadline):
            stop, error = error, None
        src, key = st["source"], stream_key(st)
        finished = stop is None and error is None   # axın işlənib (tam oxunub və ya "alınmadı")
        con = ctx.connect()
        try:
            store = store_for(con, src)
            if rows.get(src):
                store.save_many(rows[src])
            if finished:
                prog["done"][key] = bool(done and done[0][1])
                if bad.get(src):
                    prog["failed"].append(key)
            prog["stats"][src] = dict(store.stats)
            save_progress(con, prog)
            store.commit()
            for e in store.gate_events:
                log.info(_event_line(e))
            store.gate_events = []
        finally:
            con.close()
        if finished:
            done_here += 1
            left = total - len(prog["done"])
            spent = time.time() - t_job
            log.info("nightly axın %s/%s %s: %s elan, %s | keçən %s, ETA %s", len(prog["done"]), total, st["label"],
                     len(rows.get(src, [])),
                     "ALINMADI" if bad.get(src) else ("tam oxundu" if prog["done"][key] else "TAM OXUNMADI"),
                     _hm(time.time() - t_count), _hm(spent / done_here * left))
        if stop is not None or error is not None:
            break

    complete = len([k for k in prog["streams"] if k in prog["done"] or k not in by_key]) == total
    if not complete:
        why = f"bloklanma: {error}" if error is not None else f"{stop}"
        log.info("nightly dayandı (%s): %s/%s axın bitib — növbəti işdə qaldığı yerdən davam edəcək",
                 why, len(prog["done"]), total)
        _finish_state(ctx, state, error, "nightly")
        return error

    # bütün axınlar bitdi: yoxa çıxanlar TOPLU və MƏNBƏ üzrə, sonra sync_runs + "nightly TAMAMLANDI"
    con = ctx.connect()
    skipped = []
    try:
        for src in SOURCES:
            store_for(con, src)
            keys = [k for k in prog["streams"] if k in by_key and by_key[k]["source"] == src]
            bad = [k for k in keys if not prog["done"].get(k)]
            if bad:
                skipped.append(src)
                log.warning("nightly: %s — %s axın alınmadı / tam oxunmadı, %s elanları üçün silinmə yoxlanışı "
                            "ÖTÜRÜLDÜ", src, len(bad), src)
                continue
            for key in keys:
                stores[src].mark_removed(**by_key[key]["removal"])
        prog["finished_at"] = fmt(now())
        for src, store in stores.items():
            prog["stats"][src] = dict(store.stats)
        save_progress(con, prog)
        con.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (LAST_FULL_KEY, prog["finished_at"]))
        con.commit()
        failed = {}
        for key in prog["failed"]:
            failed.setdefault(key.split(":")[0], []).append(key.split(": ", 1)[1])
        all_streams_ = [by_key[k] for k in prog["streams"] if k in by_key]
        for src, store in stores.items():
            err = source_error(None, failed, src, all_streams_)
            store.finish(ok=err is None, error=err, latency=_latency(ctx.clients[src]),
                         label=f"nightly TAMAMLANDI deal={prog['deal']} başlayıb={prog['started_at']} "
                               f"alınmayan={len(prog['failed'])}"
                               + (" silinmə-yoxlanışı=ötürüldü" if src in skipped else ""),
                         failed=failed.get(src, ()))
    finally:
        con.close()
    log.info("nightly TAMAMLANDI (%s → %s, %s axın, alınmayan %s, deal=%s%s): %s%s", prog["started_at"],
             prog["finished_at"], total, len(prog["failed"]), prog["deal"],
             f", silinmə yoxlanışı ötürüldü: {', '.join(skipped)}" if skipped else "",
             _summary({k: dict(v.stats) for k, v in stores.items()}), _failed_line(failed))
    _finish_state(ctx, state, None, "nightly")
    return None


# ---------------------------------------------------------------- loop: davamlı rejim (GitHub Actions)
DISPATCHED = "işə salındı"


def dispatch_next(run=subprocess.run):
    """Növbəti loop işini işə salır (gh workflow run loop.yml). SCRAPER_ENABLED != true → başlamır (söndürmə açarı).
    → nəticə mətni (yekun loq sətri üçün): DISPATCHED və ya səbəbi ilə "başladılmadı (...)"."""
    if os.environ.get("SCRAPER_ENABLED") != "true":
        log.info("loop: SCRAPER_ENABLED != true — növbəti iş başladılmır (zəncir dayanır)")
        return "başladılmadı (söndürmə açarı)"
    if not (os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("GH_TOKEN")):
        log.info("loop: GitHub Actions mühiti deyil — növbəti iş başladılmır")
        return "başladılmadı (GitHub Actions mühiti deyil)"
    ref = os.environ.get("GITHUB_REF_NAME") or "main"
    try:
        p = run(["gh", "workflow", "run", "loop.yml", "--ref", ref, "-f", "chain=true"],
                capture_output=True, text=True, timeout=60)
        ok = p.returncode == 0
        info = (p.stderr or p.stdout or "").strip()[:300]
    except (OSError, subprocess.SubprocessError) as e:
        ok, info = False, f"{type(e).__name__}: {e}"
    if ok:
        log.info("loop: növbəti iş işə salındı (loop.yml, ref=%s) — bu iş bitəndə başlayacaq", ref)
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a", encoding="utf-8") as fh:
                fh.write("dispatched=true\n")
    else:
        log.error("loop: növbəti iş işə salınmadı (%s) — ehtiyat: workflow-un son addımı və 2 saatlıq cron", info)
    return DISPATCHED if ok else "İŞƏ SALINMADI (gh xətası)"


def _due(ctx):
    con = ctx.connect()
    try:
        return nightly_due(con)
    finally:
        con.close()


def loop(ctx, state, minutes, dispatch_at=None, every=FAST_EVERY, dispatch=dispatch_next):
    """`minutes` dəqiqə ərzində hər `every` san fast dövrü; vaxtı çatanda nightly sayğısı (içində fast davam edir).
    `dispatch_at` dəqiqədə (standart: minutes − 10) növbəti iş işə salınır ki, ötürmədə fasilə olmasın.
    Cooldown olanda iş gözləyir (zəncir qırılmasın, sayta sorğu getmir). → çıxış kodu."""
    t0 = time.time()
    end = t0 + minutes * 60
    dispatch_t = t0 + (max(minutes - 10, 0) if dispatch_at is None else dispatch_at) * 60
    sent = [None]   # dispatch() nəticəsi (None: hələ vaxtı çatmayıb)
    last_fast = [0.0]

    def tick():
        if sent[0] is None and time.time() >= dispatch_t:
            sent[0] = "?"
            sent[0] = dispatch() or "?"

    def wait_until(t):
        while time.time() < t:
            tick()
            time.sleep(max(0.0, min(30.0, t - time.time())))
        tick()

    log.info("loop: %s dəq, fast hər %s dəq, növbəti iş %s-cı dəqiqədə", minutes, every // 60,
             round((dispatch_t - t0) / 60))
    cycles = [0]    # fast dövrləri, nightly içindəki fast(nightly) da daxil
    try:
        while True:
            tick()
            if end - time.time() < LOOP_MIN_LEFT:
                break
            until = cooldown_until(state)
            if until:
                left = (until - now()).total_seconds()
                log.info("loop: cooldown %s-ə qədər — gözlənilir (sayta sorğu yoxdur)", fmt(until))
                wait_until(min(time.time() + left, end))
                continue
            if time.time() - last_fast[0] >= every:
                last_fast[0] = time.time()
                err = fast_cycle(ctx, state, notify=True)
                cycles[0] += 1
                if isinstance(err, Deadline):
                    raise err
                if err is None and _due(ctx):
                    nightly(ctx, state, deadline=end, tick=tick, last_fast=last_fast, fast_count=cycles)
                continue
            wait_until(min(last_fast[0] + every, end))
    except Deadline as e:
        log.info("loop: dayandı (%s)", e)
    except NoBaseline:
        return 2
    tick()
    log.info("loop bitdi: %.0f dəq, %s fast dövrü, növbəti iş %s", (time.time() - t0) / 60, cycles[0],
             sent[0] or "başladılmadı (iş vaxtından əvvəl dayandı)")
    return 0


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
        prog = load_progress(con)
        if prog:
            print("tam sayğı:", f"{prog['started_at']} başlayıb, deal={prog['deal']}, "
                  f"{len(prog['done'])}/{len(prog['streams'])} axın bitib, "
                  + (f"TAMAMLANDI {prog['finished_at']}" if prog.get("finished_at") else "davam edir"))
        print("son tam sayğı:", _setting(con, LAST_FULL_KEY) or "—")
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
    n = sub.add_parser("nightly", help="tam sayğı (yarımçıq varsa qaldığı yerdən davam edir)")
    n.add_argument("--deal", choices=["auto", "rent", "sale", "both"], default="auto",
                   help="YENİ sayğı üçün; auto: hər gün kirayə, bazar günü satış da")
    n.add_argument("--deadline-minutes", type=float, default=None,
                   help="bu qədər dəqiqədən sonra dayanır (irəliləyiş saxlanır, növbəti dəfə davam edir)")
    n.add_argument("--restart", action="store_true", help="yarımçıq sayğını at, yenisini başlat")
    lp = sub.add_parser("loop", help="davamlı rejim: hər 15 dəq fast, vaxtı çatanda nightly")
    lp.add_argument("--minutes", type=float, default=340, help="işin müddəti (dəq)")
    lp.add_argument("--dispatch-at", type=float, default=None,
                    help="növbəti işi bu dəqiqədə işə sal (standart: minutes − 10)")
    sub.add_parser("status", help="cooldown, sayğı və son sinxronizasiyalar")
    args = ap.parse_args(argv)

    if args.cmd == "ping":
        return ping()
    setup_logging()
    ctx = ctx or Ctx()
    if args.cmd == "status":
        return status(ctx)

    lock = Lock(mode=args.cmd)
    if not lock.acquire():
        log.info("%s: başqa işləmə davam edir (kilid: %s) — çıxılır", args.cmd, lock.info())
        return 0
    install_signals()
    state = {}
    try:
        if args.cmd in ("fast", "loop"):
            # Neon-u oyatmamaq üçün başlanğıcda yalnız lokal fayl; baza toplamadan sonra (fast_cycle.before)
            state = _read_file()
            if not getattr(args, "quiet", False) and state.get("baseline") is False:
                log.error("%s: baseline yoxdur (son yoxlama: bazada notify_min_id_* yox idi). Əvvəl: "
                          "python -m scraper.auto baseline", args.cmd)
                return 2
        if args.cmd == "loop":
            return loop(ctx, state, args.minutes, dispatch_at=args.dispatch_at)
        if args.cmd == "fast":
            until = cooldown_until(state)
            if until:
                log.info("fast: cooldown davam edir (%s-ə qədər, ardıcıl bloklanma %s, lokal fayl) — çıxılır",
                         fmt(until), state.get("blocks"))
                return 0
            try:
                error = fast_cycle(ctx, state, notify=not args.quiet)
            except NoBaseline:
                return 2
            return 0 if error is None or isinstance(error, Deadline) else (3 if isinstance(error, BlockedError) else 1)
        try:
            con = ctx.connect()
        except Exception as e:
            log.error("%s: bazaya qoşulmaq olmadı (%s) — çıxılır", args.cmd, type(e).__name__)
            return 1
        try:
            state = load_state(con)
            baseline_ok = state["baseline"] = has_baseline(con)
        finally:
            con.close()
        until = cooldown_until(state)
        if until:
            log.info("%s: cooldown davam edir (%s-ə qədər, ardıcıl bloklanma %s) — çıxılır",
                     args.cmd, fmt(until), state.get("blocks"))
            return 0
        if args.cmd == "baseline":
            error = baseline(ctx, state)
        else:
            if not baseline_ok:
                log.error("nightly: baseline yoxdur (settings.notify_min_id_*). Əvvəl: python -m scraper.auto baseline")
                return 2
            deadline = time.time() + args.deadline_minutes * 60 if args.deadline_minutes else None
            error = nightly(ctx, state, deal=args.deal, deadline=deadline, restart=args.restart)
        return 0 if error is None else (3 if isinstance(error, BlockedError) else 1)
    except Deadline as e:
        # siqnal yazma anında gəldi: yazılmış hissə commit olunub; vəziyyət lokal fayla (cache) yazılır
        log.warning("%s: dayandırıldı (%s) — vəziyyət yazılır", args.cmd, e)
        _write_file(state)
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
