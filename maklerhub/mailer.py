"""E-poçt göndərmə — Brevo HTTP API (Render pulsuz planı SMTP portlarını bloklayır, ona görə SMTP YOX).

- send_email(to, subject, html, text, con=None, headers=None): sinxron göndərmə. BREVO_API_KEY yoxdursa göndərmir,
  məzmunu loqa yazır (lokal inkişaf) və False qaytarır. Qlobal gündəlik limit: DAILY_LIMIT (Brevo pulsuz: 300).
- run_background(app, fn): fn(con) ayrı axında (cavabı ləngitməsin), öz baza bağlantısı ilə; xəta yuxarı qalxmır,
  loqa yazılır. Testlərdə MAIL_SYNC=True — eyni axında.
- Şəbəkə YALNIZ _post-dadır (testlərdə conftest onu mock edir).
Açar heç vaxt loqa/HTML-ə yazılmır; Sentry monitoring.scrub_text ilə maskalayır.
"""
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request

from .helpers import ts

log = logging.getLogger(__name__)

API_URL = "https://api.brevo.com/v3/smtp/email"
TIMEOUT = 10
DAILY_LIMIT = 250
QUOTA_KEY = "mail_quota"          # settings: "YYYY-MM-DD|say"
SENDER_NAME = "MaklerHub"


def configured():
    return bool((os.environ.get("BREVO_API_KEY") or "").strip())


def base_url():
    """Məktublardakı linklər və loqo üçün baza ünvan: MAKLERHUB_BASE_URL (Render: https://maklerhub.onrender.com),
    yoxdursa sorğunun host_url-u (ehtiyat; sorğu konteksti lazımdır). Sonda "/" olmur."""
    env = (os.environ.get("MAKLERHUB_BASE_URL") or "").strip().rstrip("/")
    if env:
        return env
    from flask import request
    return request.host_url.rstrip("/")


def sender_email(con):
    env = (os.environ.get("MAKLERHUB_MAIL_FROM") or "").strip()
    if env:
        return env
    from .db import get_setting
    return get_setting(con, "admin_contact_email") if con is not None else ""


# ---------------------------------------------------------------- gündəlik limit
def _today():
    return ts()[:10]


def quota(con):
    """(gün, bu gün göndərilən say)."""
    row = con.execute("SELECT value FROM settings WHERE key = ?", (QUOTA_KEY,)).fetchone()
    day, _, n = (row["value"] if row else "").partition("|")
    return (day, int(n)) if day == _today() and n.isdigit() else (_today(), 0)


def limit_reached(con):
    return quota(con)[1] >= DAILY_LIMIT


def _reserve(con):
    """Atomik: bu günün sayını 1 artırır (limit dolmayıbsa). Commit edir. Qaytarır: icazə verildimi."""
    today = _today()
    con.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO NOTHING", (QUOTA_KEY, f"{today}|0"))
    for _ in range(5):
        row = con.execute("SELECT value FROM settings WHERE key = ?", (QUOTA_KEY,)).fetchone()
        old = row["value"]
        day, _, n = old.partition("|")
        count = int(n) if day == today and n.isdigit() else 0
        if count >= DAILY_LIMIT:
            con.commit()
            return False
        if con.execute("UPDATE settings SET value = ? WHERE key = ? AND value = ?",
                       (f"{today}|{count + 1}", QUOTA_KEY, old)).rowcount == 1:
            if count + 1 == DAILY_LIMIT:
                from .db import add_log
                add_log(con, "warn", "Sistem", f"Gündəlik e-poçt limiti doldu ({DAILY_LIMIT})",
                        "bu gün daha məktub göndərilməyəcək")
            con.commit()
            return True
    con.commit()
    return False


# ---------------------------------------------------------------- göndərmə
def _post(payload):
    """Brevo-ya bir sorğu. Qaytarır: (HTTP status, cavab JSON və ya {})."""
    req = urllib.request.Request(API_URL, data=json.dumps(payload).encode("utf-8"), method="POST", headers={
        "api-key": os.environ.get("BREVO_API_KEY", "").strip(), "accept": "application/json",
        "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
            return resp.status, json.loads(body or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read() or b"{}")
        except ValueError:
            data = {}
        return exc.code, data


last_result = {}  # son göndərmənin nəticəsi (status, message_id varlığı, xəta mətni) — yoxlama/diaqnostika üçün


def send_email(to, subject, html, text, con=None, headers=None):
    """Sinxron göndərmə; 1 təkrar cəhd (şəbəkə xətası, 429, 5xx). Qaytarır: göndərildimi."""
    last_result.clear()
    if not configured():
        log.warning("E-POÇT (BREVO_API_KEY yoxdur, göndərilmədi) → %s | %s\n%s", to, subject, text)
        last_result.update(status=None, error="not-configured")
        return False
    if con is not None and not _reserve(con):
        log.warning("Gündəlik e-poçt limiti (%s) dolub — məktub göndərilmədi: %s", DAILY_LIMIT, subject)
        last_result.update(status=None, error="daily-limit")
        return False
    payload = {"sender": {"name": SENDER_NAME, "email": sender_email(con)}, "to": [{"email": to}],
               "subject": subject, "htmlContent": html, "textContent": text}
    if headers:
        payload["headers"] = headers
    status, data, error = None, {}, None
    for attempt in range(2):
        try:
            status, data = _post(payload)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            status, data, error = None, {}, exc.__class__.__name__
        if status is not None and status < 500 and status != 429:
            break
        if attempt == 0:
            time.sleep(1)
    ok = status in (200, 201, 202)
    last_result.update(status=status, message_id=bool(data.get("messageId") or data.get("messageIds")),
                       error=None if ok else (data.get("message") or data.get("code") or error or "unknown"))
    if not ok:
        log.warning("E-poçt göndərilmədi: status=%s xəta=%s", status, last_result["error"])
        if con is not None:
            from .db import add_log
            add_log(con, "err", "Sistem", f"E-poçt göndərilmədi (status {status})", str(last_result["error"])[:200])
            con.commit()
    return ok


def run_background(app, fn):
    """fn(con) — cavabdan asılı olmayaraq ayrı axında, öz bağlantısı ilə. Xəta istifadəçiyə təsir etmir."""
    def work():
        from . import db as dbmod
        try:
            with app.app_context():
                pool = dbmod._get_pool(app.config["DATABASE"])
                con = dbmod.Conn(pool.getconn(), pool)
                try:
                    fn(con)
                except Exception as exc:
                    try:
                        con.rollback()
                        dbmod.add_log(con, "err", "Sistem", f"E-poçt işi uğursuz oldu ({exc.__class__.__name__})",
                                      str(exc)[:200])
                        con.commit()
                    except Exception:
                        pass
                    log.exception("E-poçt işi uğursuz oldu")
                finally:
                    con.close()
        except Exception:
            log.exception("E-poçt işi işə düşmədi")

    if app.config.get("MAIL_SYNC"):
        work()
    else:
        threading.Thread(target=work, name="mailer", daemon=True).start()


def send_async(app, to, subject, html, text, headers=None):
    run_background(app, lambda con: send_email(to, subject, html, text, con=con, headers=headers))
