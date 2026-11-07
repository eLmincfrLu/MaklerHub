"""Avtomatik təmizlik (pulsuz Neon yaddaşı üçün) və lazy yeniləmələrin tezlik məhdudiyyəti. Cron yoxdur.

- run_cleanup(con): köhnə bildirişlər, giriş cəhdləri, loqlar və bağlı sessiyalar silinir.
- Sorğudan SONRA (cavab göndərilib qurtarandan sonra, response.call_on_close) saatda ən çox 1 dəfə işləyir;
  yalnız bazaya onsuz da toxunan sorğularda (Neon-u özü oyatmasın). Xəta istifadəçiyə təsir etmir, loqa yazılır.
- lazy_expire(db): teleb.expire_requests (tələb müddəti + anonimləşdirmə) dəqiqədə ən çox 1 dəfə.

Tezlik settings cədvəlində saxlanılır (last_cleanup_at, last_expire_at): "vaxtı keçibsə yenilə" bir atomik UPSERT-dir,
ona görə bir neçə proses/axın eyni anda işə salsa da yalnız biri qalib gəlir. Hər sorğuda bazaya getməmək üçün
prosesin yaddaşında da növbəti yoxlama vaxtı saxlanılır.
"""
import json
import logging
import threading
import time
from datetime import timedelta

from .helpers import now, ts

log = logging.getLogger(__name__)

CLEANUP_EVERY = timedelta(hours=1)
EXPIRE_EVERY = timedelta(minutes=1)
CLEANUP_KEY, CLEANUP_STATS_KEY, EXPIRE_KEY = "last_cleanup_at", "last_cleanup_stats", "last_expire_at"

# (cədvəl, açıqlama, WHERE, gün) — gün: bundan köhnə sətirlər silinir
RULES = (
    ("notifications_read", "Oxunmuş bildirişlər (30 gün)",
     "DELETE FROM notifications WHERE is_read = 1 AND created_at < ?", 30),
    ("notifications_unread", "Oxunmamış bildirişlər (90 gün)",
     "DELETE FROM notifications WHERE is_read = 0 AND created_at < ?", 90),
    ("login_attempts", "Giriş cəhdləri (7 gün)", "DELETE FROM login_attempts WHERE created_at < ?", 7),
    ("logs", "Sistem loqları (90 gün)", "DELETE FROM logs WHERE created_at < ?", 90),
    # bağlı sessiyalar; demo hesablarda sessiya heç vaxt bağlanmır (çox cihaz), ona görə 30 gün görünməyən demo sessiyası da
    ("sessions", "Bağlı sessiyalar (30 gün)",
     "DELETE FROM sessions WHERE last_seen_at < ? AND (active = 0 OR user_id IN (SELECT id FROM users WHERE is_demo = 1))",
     30),
)
LABELS = {key: label for key, label, _, _ in RULES}

_lock = threading.Lock()
_next_check = {}  # açar → time.monotonic() — bundan tez bazaya baxılmır


def reset_throttle():
    """Testlər üçün: prosesin yaddaşındakı tezlik məhdudiyyətini sıfırlayır."""
    with _lock:
        _next_check.clear()


def _due_in_memory(key):
    with _lock:
        return time.monotonic() >= _next_check.get(key, 0)


def _remember(key, seconds):
    with _lock:
        _next_check[key] = time.monotonic() + seconds


def claim(con, key, every):
    """Vaxtı gəlibsə settings[key]-i indiki vaxta yazır və True qaytarır (atomik; commit çağıran tərəfdədir)."""
    stamp = now()
    row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is not None and row["value"] >= ts(stamp - every):
        return False  # kilidsiz oxu: vaxtı deyilsə sətir kilidlənmir
    return con.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value "
        "WHERE settings.value < ?", (key, ts(stamp), ts(stamp - every))).rowcount == 1


# ---------------------------------------------------------------- təmizlik
def run_cleanup(con):
    """Köhnə sətirləri silir, nəticəni settings-də saxlayır və commit edir. Qaytarır: {açar: silinən say}."""
    stamp = now()
    counts = {}
    for key, _label, sql, days in RULES:
        counts[key] = con.execute(sql, (ts(stamp - timedelta(days=days)),)).rowcount
    con.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (CLEANUP_KEY, ts(stamp)))
    con.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (CLEANUP_STATS_KEY, json.dumps(counts)))
    con.commit()
    return counts


def last_cleanup(con):
    """Admin üçün: (vaxt və ya None, [(açıqlama, say), ...])."""
    rows = {r["key"]: r["value"] for r in con.execute(
        "SELECT key, value FROM settings WHERE key IN (?, ?)", (CLEANUP_KEY, CLEANUP_STATS_KEY))}
    try:
        stats = json.loads(rows.get(CLEANUP_STATS_KEY) or "{}")
    except ValueError:
        stats = {}
    return rows.get(CLEANUP_KEY), [(LABELS.get(k, k), v) for k, v in stats.items()]


def cleanup_if_due(con):
    """Saatda ən çox 1 dəfə run_cleanup. Qaytarır: counts və ya None (vaxtı deyil)."""
    if not claim(con, CLEANUP_KEY, CLEANUP_EVERY):
        con.rollback()
        return None
    con.commit()  # iddia dərhal görünsün: digər proseslər eyni anda başlamasın
    return run_cleanup(con)


def _background_cleanup(app):
    """Cavab göndəriləndən sonra (call_on_close). Ayrı bağlantı; heç bir xəta yuxarı qalxmır."""
    from . import db as dbmod
    try:
        with app.app_context():
            pool = dbmod._get_pool(app.config["DATABASE"])
            con = dbmod.Conn(pool.getconn(), pool)
            try:
                counts = cleanup_if_due(con)
                if counts is not None and sum(counts.values()):
                    log.info("Avtomatik təmizlik: %s", counts)
            except Exception as exc:
                try:
                    con.rollback()
                    dbmod.add_log(con, "err", "Sistem", f"Avtomatik təmizlik uğursuz oldu ({exc.__class__.__name__})",
                                  str(exc)[:300])
                    con.commit()
                except Exception:
                    pass
                log.exception("Avtomatik təmizlik uğursuz oldu")
            finally:
                con.close()
    except Exception:
        log.exception("Avtomatik təmizlik işə düşmədi")


SKIP_ENDPOINTS = {"static", "public.health", "public.robots"}


def init_app(app):
    @app.after_request
    def schedule_cleanup(response):
        # yalnız bazaya onsuz da qoşulmuş sorğularda (Neon yatıbsa onu təmizlik üçün oyatmırıq)
        from flask import g, request
        if app.config.get("CLEANUP_DISABLED") or request.endpoint in SKIP_ENDPOINTS or "db" not in g:
            return response
        if _due_in_memory(CLEANUP_KEY):
            _remember(CLEANUP_KEY, 300)  # bu proses növbəti dəfə 5 dəqiqədən tez bazaya baxmır
            response.call_on_close(lambda: _background_cleanup(app))
        return response


# ---------------------------------------------------------------- lazy yeniləmə (tələb müddəti, anonimləşdirmə)
def lazy_expire(db):
    """teleb.expire_requests dəqiqədə ən çox 1 dəfə (bütün proseslər üzrə). Commit edir. Qaytarır: işlədimi."""
    if not _due_in_memory(EXPIRE_KEY):
        return False
    _remember(EXPIRE_KEY, EXPIRE_EVERY.total_seconds())
    from .teleb import expire_requests
    if not claim(db, EXPIRE_KEY, EXPIRE_EVERY):
        return False  # heç nə yazılmayıb (WHERE ödənmədi) — rollback lazım deyil
    expire_requests(db)
    db.commit()
    return True
