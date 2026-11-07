"""Başlanğıc məlumatlar: admin hesabı və istifadə olunmamış lisenziya kodları.

Admin hesabı mühit dəyişənlərindən gəlir (.env və ya Render Environment):
    MAKLERHUB_ADMIN_EMAIL     (yoxdursa admin@maklerhub.az)
    MAKLERHUB_ADMIN_PASSWORD  (yoxdursa təsadüfi şifrə yaradılır və konsola yazılır)
"""
import os
import secrets
import string

from werkzeug.security import generate_password_hash

from .db import add_log
from .helpers import ts

LICENSE_DAYS = 30
SEED_LICENSES = 3
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # qarışdırılan simvollar yoxdur (0/O, 1/I)


def gen_license_code():
    """MH-XXXX-XXXX-XXXX"""
    return "MH-" + "-".join("".join(secrets.choice(_CODE_ALPHABET) for _ in range(4)) for _ in range(3))


def _gen_password():
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(12))
        if any(c.isdigit() for c in pw):
            return pw


def create_licenses(con, count, days=LICENSE_DAYS, note=""):
    codes = []
    while len(codes) < count:
        code = gen_license_code()
        cur = con.execute("INSERT OR IGNORE INTO licenses(code, days, status, note, created_at) VALUES (?,?,'new',?,?)",
                          (code, days, note, ts()))
        if cur.lastrowid:
            codes.append(code)
    return codes


def seed_minimal(con):
    """Boş baza: 1 admin hesabı + 3 istifadə olunmamış lisenziya kodu (konsola yazılır)."""
    email = (os.environ.get("MAKLERHUB_ADMIN_EMAIL") or "").strip().lower() or "admin@maklerhub.az"
    password = os.environ.get("MAKLERHUB_ADMIN_PASSWORD") or ""
    generated = not password
    if generated:
        password = _gen_password()
    stamp = ts()
    con.execute(
        "INSERT OR IGNORE INTO users(first_name, last_name, email, password_hash, role, status, created_at, "
        "password_changed_at) VALUES ('Admin', '', ?, ?, 'admin', 'active', ?, ?)",
        (email, generate_password_hash(password), stamp, stamp),
    )
    codes = create_licenses(con, SEED_LICENSES, note="başlanğıc kod")
    add_log(con, "info", "Sistem", "Baza yaradıldı", f"admin={email}\nlisenziya kodu={len(codes)}")

    print("\n  Admin:", email)
    print("  Şifrə:", password if generated else "(.env-dəki MAKLERHUB_ADMIN_PASSWORD)")
    print(f"  Lisenziya kodları ({LICENSE_DAYS} gün):")
    for code in codes:
        print("   ", code)
    print()


def seed(con):
    """Demo məlumatlar (Gün 5-də yazılacaq). Hələlik seed_minimal ilə eynidir."""
    seed_minimal(con)
