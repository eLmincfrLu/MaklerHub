"""Elan mənbələri (Bina.az, Tap.az, Lalafo): adapterlər, sinxronizasiya və fon planlayıcısı.

ARXİTEKTURA
-----------
Hər mənbə üçün bir "adapter" var. Adapter `fetch()` metodu ilə mənbədən
elanları alır və `FetchResult` qaytarır. Sinxronizasiya funksiyası
(`sync_source`) nəticəni bazaya yazır, qiymət dəyişikliklərini izləyir,
uyğunlaşdırma mühərrikini işə salır və sistem loqlarını yaradır.

Saxta (Demo) elan yaradan adapter çıxarılıb. Bina.az elanları lokal scraper
prosesindən yazılır (`python -m scraper.run bina --save`, bax: scraper/store.py).
Adapteri olmayan mənbə üçün `sync_source` heç nə yazmır, sadəcə xəbər qaytarır.
`gen_listing` / `gen_for_profile` yalnız `flask init-db --demo` nümunə bazası üçün qalır.
"""
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta

from . import matching
from .db import add_log, connect, get_settings
from .helpers import day_start, now, ts

log = logging.getLogger("maklerhub.sources")

SOURCE_DEFS = [
    # key, ad, loqo faylı, baza URL
    ("bina", "Bina.az", "bina.png", "https://bina.az"),
    ("tap", "Tap.az", "tap.png", "https://tap.az"),
    ("lalafo", "Lalafo", "lalafo.png", "https://lalafo.az"),
]

# Rayon üzrə orta kv.m qiyməti (AZN) — nümunə elan generatoru üçün
DISTRICT_PRICE = {
    "Səbail": 3200, "Nəsimi": 2800, "Yasamal": 2400, "Nərimanov": 2500, "Xətai": 2300,
    "Nizami": 1900, "Binəqədi": 1500, "Xəzər": 1300, "Sabunçu": 1200, "Suraxanı": 1100, "Qaradağ": 1000,
}
# Abşeron və Pirallahı üçün real kv.m qiyməti ölçülməyib — generator 1800 standartını işlədir
DISTRICTS = list(DISTRICT_PRICE) + ["Abşeron", "Pirallahı"]
PROPERTY_TYPES = ["", "Yeni tikili", "Köhnə tikili", "Studiya", "Ofis", "Həyət evi"]


class SourceError(Exception):
    """Mənbəyə qoşulma xətası (timeout, HTTP xəta və s.)."""


@dataclass
class FetchResult:
    checked: int                                   # mənbədə yoxlanılan elan sayı
    latency: float                                 # orta cavab vaxtı (saniyə)
    new: list = field(default_factory=list)        # yeni elanlar (dict)
    price_changes: list = field(default_factory=list)  # [(listing_id, yeni_qiymət)]
    removed: list = field(default_factory=list)    # [listing_id] — mənbədən silinmiş


# ---------------------------------------------------------------- elan generatoru
def _round(v, step):
    return max(step, int(round(v / step)) * step)


def gen_listing(rng, district=None, deal=None, rooms=None, building=None, renovation=None, price=None,
                area=None, metro_m="auto"):
    district = district or rng.choice(DISTRICTS)
    deal = deal or ("rent" if rng.random() < 0.22 else "sale")
    if building is None:
        r = rng.random()
        building = "Yeni tikili" if r < 0.58 else ("Köhnə tikili" if r < 0.95 else "Ofis")
    if rooms is None:
        rooms = rng.randint(2, 6) if building == "Ofis" else rng.choices([1, 2, 3, 4, 5], [15, 33, 32, 15, 5])[0]
    if area is None:
        area = round(rooms * rng.uniform(22, 31) + rng.uniform(8, 20))
    if renovation is None:
        if building == "Yeni tikili":
            renovation = rng.choices(["", "Təmirli", "Orta təmir"], [40, 45, 15])[0]
        else:
            renovation = rng.choices(["Təmirli", "Orta təmir", "Təmirsiz"], [50, 35, 15])[0]
    if price is None:
        ppm = DISTRICT_PRICE.get(district, 1800) * rng.uniform(0.82, 1.22)
        ppm *= 1.1 if building == "Yeni tikili" else 0.92
        price = _round(area * ppm, 1000) if deal == "sale" else _round(area * ppm * 0.0075, 50)
    if building == "Yeni tikili":
        floors = rng.choice([9, 12, 14, 16, 17, 20])
    else:
        floors = rng.choice([4, 5, 9])
    floor = rng.randint(1, floors)
    if metro_m == "auto":
        metro_m = None if rng.random() < 0.2 else rng.randrange(150, 2000, 50)
    title = f"Ofis, {district} r." if building == "Ofis" else f"{rooms} otaqlı, {district} r."
    suffix = "kirayə" if deal == "rent" else ("studiya" if rooms == 1 and area <= 45 else "")
    return dict(district=district, deal_type=deal, rooms=rooms, area=area, building=building,
                renovation=renovation, price=price, floor=floor, floors=floors, metro_m=metro_m,
                title=title, suffix=suffix)


def gen_for_profile(rng, p, strict=False):
    """Konkret axtarış profilinə yaxın elan yaradır (canlı uyğunluqlar üçün)."""
    district = p["district"]
    if district == "Bakı, mərkəz":
        district = rng.choice(sorted(matching.CENTER))
    rooms = p["rooms"]
    if rooms and p["rooms_plus"]:
        rooms = rooms + rng.choice([0, 0, 1])
    pt = p["property_type"] or ""
    building = None
    if pt == "Yeni tikili":
        building = "Yeni tikili" if strict or rng.random() < 0.8 else "Köhnə tikili"
    elif pt == "Köhnə tikili":
        building = "Köhnə tikili"
    elif pt in ("Ofis", "Həyət evi"):
        building = pt
    elif pt == "Studiya":
        rooms, building = 1, rng.choice(["Yeni tikili", "Köhnə tikili"])
    else:
        building = rng.choice(["Yeni tikili", "Yeni tikili", "Köhnə tikili"])
    area = None
    if pt == "Studiya":
        area = rng.randint(32, 48)
    lo, hi = p["price_min"], p["price_max"]
    top = hi if strict else hi * 1.08
    step = 50 if p["deal_type"] == "rent" else 1000
    price = _round(rng.uniform(lo * 0.95 if not strict else lo, top), step)
    return gen_listing(rng, district=district, deal=p["deal_type"], rooms=rooms, building=building,
                       price=price, area=area)


# ---------------------------------------------------------------- adapterlər
class BaseAdapter:
    """Real mənbə adapteri üçün baza sinif.

    fetch() metodu mənbədən məlumatı alıb FetchResult qaytarmalıdır.
    Qoşulma problemi olduqda SourceError atmalıdır.
    """

    def fetch(self, con, source, settings):  # pragma: no cover - interfeys
        raise NotImplementedError


# Real adapterlər. Bina.az veb prosesdən yox, lokal scraper-dən yazılır:
#   python -m scraper.run bina --save   (bax: scraper/store.py)
# Saxta (Demo) elan yaradan adapter çıxarılıb.
ADAPTERS = {}


# ---------------------------------------------------------------- bazaya yazma
def insert_listing(con, source_id, data, created_at=None, posted_at=None, active=1, external_id=None):
    stamp = created_at or ts()
    ext = external_id or f"{source_id}-{random.randint(1_000_000, 9_999_999)}"
    cur = con.execute(
        """INSERT OR IGNORE INTO listings(source_id, external_id, url, title, suffix, district, rooms, area, floor,
               floors, price, deal_type, building, renovation, metro_m, active, posted_at, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (source_id, ext, data.get("url"), data["title"], data.get("suffix", ""), data["district"], data.get("rooms"),
         data["area"], data.get("floor"), data.get("floors"), data["price"], data["deal_type"],
         data.get("building", ""), data.get("renovation", ""), data.get("metro_m"), active,
         posted_at or stamp, stamp, stamp),
    )
    if not cur.lastrowid:
        return None
    con.execute("INSERT INTO price_history(listing_id, price, recorded_at) VALUES (?, ?, ?)",
                (cur.lastrowid, data["price"], stamp))
    return con.execute("SELECT * FROM listings WHERE id = ?", (cur.lastrowid,)).fetchone()


def sync_source(con, source, manual=False):
    """Bir mənbəni sinxronlaşdırır. Nəticə haqqında dict qaytarır."""
    adapter = ADAPTERS.get(source["key"])
    name = source["name"]
    if adapter is None:
        # Bazaya heç nə yazılmır (nə elan, nə sync_runs) — veb proses saxta elan yaratmır
        return {"ok": False, "error": "veb paneldən sinxronizasiya yoxdur, elanlar lokal scraper ilə yenilənir"}
    settings = get_settings(con)
    retries = int(settings.get("retries", 3))
    started = now()
    result, error, attempts = None, None, 0
    for attempt in range(retries + 1):
        attempts = attempt
        try:
            result = adapter.fetch(con, source, settings)
            break
        except SourceError as exc:
            error = exc
        except Exception as exc:  # gözlənilməz xəta
            log.exception("Adapter xətası: %s", name)
            error = exc

    if result is None:
        con.execute("INSERT INTO sync_runs(source_id, started_at, ok, latency) VALUES (?, ?, 0, ?)",
                    (source["id"], ts(started), float(settings.get("timeout", 10))))
        con.execute("UPDATE sources SET status = 'down' WHERE id = ?", (source["id"],))
        add_log(con, "err", name, f"Sinxronizasiya xətası: {error}",
                f"yenidən cəhd {retries}/{retries} uğursuz oldu")
        fails = con.execute(
            "SELECT ok FROM sync_runs WHERE source_id = ? ORDER BY id DESC LIMIT 3", (source["id"],)
        ).fetchall()
        if settings.get("notify_source_down") == "1" and len(fails) == 3 and not any(f["ok"] for f in fails):
            add_log(con, "warn", "Sistem", f"{name} ardıcıl 3 dəfə uğursuz oldu — admin xəbərdar edildi",
                    f"e-poçt={settings.get('notify_email')}")
        con.commit()
        return {"ok": False, "error": str(error)}

    if attempts > 0:
        add_log(con, "err", name, f"Sinxronizasiya xətası: {error}",
                f"yenidən cəhd {attempts}/{retries} uğurlu oldu")

    stamp = ts()
    new_count = updated = 0
    for data in result.new:
        posted = ts(now() - timedelta(minutes=random.randint(1, int(settings.get("sync_interval", 5)) + 3)))
        row = insert_listing(con, source["id"], data, created_at=stamp, posted_at=posted)
        if row is None:
            continue
        new_count += 1
        matching.match_listing(con, row)

    for listing_id, new_price in result.price_changes:
        row = con.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
        if not row or row["price"] == new_price:
            continue
        con.execute("UPDATE listings SET price = ?, updated_at = ? WHERE id = ?", (new_price, stamp, listing_id))
        con.execute("INSERT INTO price_history(listing_id, price, recorded_at) VALUES (?, ?, ?)",
                    (listing_id, new_price, stamp))
        updated += 1
        matching.match_listing(con, con.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone())

    for listing_id in result.removed:
        con.execute("UPDATE listings SET active = 0, updated_at = ? WHERE id = ?", (stamp, listing_id))

    duration = max(3, int(result.checked * result.latency / 30))
    cur = con.execute(
        """INSERT INTO sync_runs(source_id, started_at, ok, latency, duration, checked, new_count, updated, removed)
           VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)""",
        (source["id"], ts(started), result.latency, duration, result.checked, new_count, updated,
         len(result.removed)),
    )
    add_log(con, "ok", name,
            f"Sinxronizasiya tamamlandı — {result.checked} elan yoxlandı, {new_count} yeni tapıldı",
            f"job=sync-{source['key']} run=#{cur.lastrowid} müddət={duration}s\n"
            f"yeni={new_count} yenilənən={updated} silinən={len(result.removed)}")

    # Yavaş mənbə: sorğu limitini avtomatik azalt
    base_limit = int(settings.get("rate_limit", 30))
    status = "up"
    if result.latency > 3.0:
        status = "warn"
        if settings.get("auto_slow") == "1" and not source["rate_limit"]:
            reduced = max(5, int(base_limit * 0.6))
            con.execute("UPDATE sources SET rate_limit = ? WHERE id = ?", (reduced, source["id"]))
            add_log(con, "warn", name,
                    f"Cavab vaxtı normadan yüksək ({result.latency}s) — sorğu sürəti azaldıldı",
                    f"gecikmə={result.latency}s hədd=3.0s\nrate_limit: {base_limit} → {reduced} sorğu/dəq")
    elif source["rate_limit"]:
        con.execute("UPDATE sources SET rate_limit = NULL WHERE id = ?", (source["id"],))
        add_log(con, "info", name, "Cavab vaxtı normallaşdı — sorğu sürəti bərpa olundu",
                f"gecikmə={result.latency}s rate_limit={base_limit} sorğu/dəq")

    con.execute("UPDATE sources SET status = ?, last_sync = ? WHERE id = ?", (status, stamp, source["id"]))
    con.execute("UPDATE profiles SET last_checked = ? WHERE status = 'active'", (stamp,))
    con.commit()
    return {"ok": True, "checked": result.checked, "new": new_count, "updated": updated,
            "removed": len(result.removed), "latency": result.latency}


def sync_all(con):
    for s in con.execute("SELECT * FROM sources WHERE enabled = 1 ORDER BY sort").fetchall():
        sync_source(con, s)


def run_rematch(con):
    listings, profiles, total, secs = matching.rematch_all(con)
    add_log(con, "ok", "Uyğunlaşdırma", f"Mühərrik {listings:,} elanı yenidən emal etdi",
            f"profil={profiles} uyğunluq={total} müddət={secs}s")
    con.commit()


def cleanup(con):
    """Köhnə loqları və sinxronizasiya qeydlərini silir."""
    con.execute("DELETE FROM logs WHERE created_at < ?", (ts(now() - timedelta(days=30)),))
    con.execute("DELETE FROM sync_runs WHERE started_at < ?", (ts(now() - timedelta(days=35)),))
    con.execute("DELETE FROM login_attempts WHERE created_at < ?", (ts(now() - timedelta(days=2)),))
    con.commit()


# ---------------------------------------------------------------- fon planlayıcısı
_started = False


def start_scheduler(app):
    """Hər `sync_interval` dəqiqədən bir bütün aktiv mənbələri sinxronlaşdıran fon axını."""
    global _started
    if _started:
        return
    _started = True
    db_path = app.config["DATABASE"]

    def loop():
        last_rematch = time.time()
        last_cleanup = 0
        while True:
            con = connect(db_path)
            try:
                interval = max(1, int(get_settings(con).get("sync_interval", 5)))
            except Exception:
                interval = 5
            finally:
                con.close()
            time.sleep(interval * 60)
            con = connect(db_path)
            try:
                sync_all(con)
                if time.time() - last_rematch > 3600:
                    run_rematch(con)
                    last_rematch = time.time()
                if time.time() - last_cleanup > 6 * 3600:
                    cleanup(con)
                    last_cleanup = time.time()
            except Exception:
                log.exception("Planlayıcı xətası")
            finally:
                con.close()

    threading.Thread(target=loop, name="maklerhub-scheduler", daemon=True).start()
    log.info("Mənbə planlayıcısı işə düşdü")


# ---------------------------------------------------------------- admin statistikası
def source_metrics(con, source):
    n = now()
    since30 = ts(n - timedelta(days=30))
    since24 = ts(n - timedelta(hours=24))
    r30 = con.execute("SELECT COUNT(*) total, SUM(ok) good FROM sync_runs WHERE source_id = ? AND started_at >= ?",
                      (source["id"], since30)).fetchone()
    uptime = round((r30["good"] or 0) / r30["total"] * 100, 1) if r30["total"] else 100.0
    r24 = con.execute(
        "SELECT AVG(CASE WHEN ok = 1 THEN latency END) lat, SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) errs FROM sync_runs "
        "WHERE source_id = ? AND started_at >= ?", (source["id"], since24)).fetchone()
    last_ok = con.execute("SELECT checked FROM sync_runs WHERE source_id = ? AND ok = 1 ORDER BY id DESC LIMIT 1",
                          (source["id"],)).fetchone()
    start = day_start(n) - timedelta(days=13)
    daily = {r["d"]: r["c"] for r in con.execute(
        "SELECT substr(started_at, 1, 10) d, SUM(new_count) c FROM sync_runs "
        "WHERE source_id = ? AND started_at >= ? GROUP BY d", (source["id"], ts(start)))}
    spark = [int(daily.get((start + timedelta(days=i)).strftime("%Y-%m-%d"), 0) or 0) for i in range(14)]
    first, last = sum(spark[:7]), sum(spark[7:])
    status = source["status"] if source["enabled"] else "off"
    labels = {"up": "İşləyir", "warn": "Yavaşdır", "down": "Xəta", "off": "Deaktiv"}
    return {
        "uptime": f"{uptime:.1f}%",
        "today": last_ok["checked"] if last_ok else 0,
        "latency": f"{(r24['lat'] or 0):.1f}s",
        "errors": int(r24["errs"] or 0),
        "spark": spark,
        "trend_down": first > 0 and last < first * 0.8,
        "status": status,
        "status_label": labels.get(status, status),
    }
