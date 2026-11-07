"""Lisenziya kodları: yaratma, yoxlama, aktivləşdirmə, ləğv; makler cihazlarının sıfırlanması."""
import re
import secrets
from datetime import timedelta

from .helpers import now, ts

DEFAULT_DAYS = 30
MAX_BATCH = 20
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # qarışdırılan simvollar yoxdur (0/O, 1/I)
CODE_RE = re.compile(r"^MH-[%s]{4}-[%s]{4}-[%s]{4}$" % ((CODE_ALPHABET,) * 3))

STATUS_LABELS = {"new": "Yeni", "used": "İstifadədə", "revoked": "Ləğv edilib"}


def gen_code():
    """MH-XXXX-XXXX-XXXX"""
    return "MH-" + "-".join("".join(secrets.choice(CODE_ALPHABET) for _ in range(4)) for _ in range(3))


def normalize_code(value):
    """' mh 7k2p-x9wd-4hq3 ' → 'MH-7K2P-X9WD-4HQ3'. Format yanlışdırsa None."""
    raw = re.sub(r"[^0-9A-Za-z]", "", value or "").upper()
    if raw.startswith("MH"):
        raw = raw[2:]
    if len(raw) != 12:
        return None
    code = f"MH-{raw[:4]}-{raw[4:8]}-{raw[8:]}"
    return code if CODE_RE.match(code) else None


def create_licenses(con, count, days=DEFAULT_DAYS, note=""):
    codes = []
    while len(codes) < count:
        code = gen_code()
        cur = con.execute("INSERT OR IGNORE INTO licenses(code, days, status, note, created_at) VALUES (?,?,'new',?,?)",
                          (code, days, note, ts()))
        if cur.lastrowid:
            codes.append(code)
    return codes


def activate(con, license_id, user_id, days):
    """Kodu makler üçün işə salır (çağıran tərəf eyni tranzaksiyada users.license_id-ni təyin edir)."""
    stamp = now()
    expires = ts(stamp + timedelta(days=days))
    con.execute("UPDATE licenses SET status = 'used', user_id = ?, activated_at = ?, expires_at = ? WHERE id = ?",
                (user_id, ts(stamp), expires, license_id))
    return expires


def revoke(con, license_id):
    return con.execute("UPDATE licenses SET status = 'revoked' WHERE id = ? AND status != 'revoked'",
                       (license_id,)).rowcount


def license_problem(con, user):
    """Makler üçün: lisenziya etibarlıdırsa None, deyilsə səbəb ('missing' | 'revoked' | 'expired')."""
    if user["role"] != "makler":
        return None
    lic = con.execute("SELECT status, expires_at FROM licenses WHERE id = ?", (user["license_id"],)).fetchone() \
        if user["license_id"] else None
    if lic is None:
        return "missing"
    if lic["status"] == "revoked":
        return "revoked"
    if lic["status"] != "used" or not lic["expires_at"] or lic["expires_at"] <= ts():
        return "expired"
    return None


def reset_devices(con, user_id):
    """Admin üçün: makler cihazlarını silir və aktiv sessiyalarını bağlayır (UI Gün 4-də)."""
    con.execute("UPDATE sessions SET active = 0 WHERE user_id = ? AND active = 1", (user_id,))
    return con.execute("DELETE FROM devices WHERE user_id = ?", (user_id,)).rowcount
