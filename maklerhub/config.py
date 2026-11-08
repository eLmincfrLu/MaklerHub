"""MaklerHub konfiqurasiyası.

Bütün dəyərləri mühit dəyişənləri (environment variables) ilə dəyişmək olar.
"""
import os
import secrets

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")


def _secret_key():
    """SECRET_KEY: mühitdən, yoxdursa instance/secret_key faylından (ilk dəfə yaradılır)."""
    env = os.environ.get("MAKLERHUB_SECRET")
    if env:
        return env
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    path = os.path.join(INSTANCE_DIR, "secret_key")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(secrets.token_hex(32))
    with open(path, encoding="utf-8") as fh:
        return fh.read().strip()


class Config:
    SECRET_KEY = _secret_key()
    DATABASE = os.environ.get("DATABASE_URL", "")  # Neon: postgresql://...?sslmode=require
    UPLOAD_DIR = os.path.join(INSTANCE_DIR, "uploads")
    TIMEZONE = os.environ.get("MAKLERHUB_TZ", "Asia/Baku")

    # E-poçt: Brevo HTTP API (mailer.py) — BREVO_API_KEY, MAKLERHUB_MAIL_FROM mühitdən oxunur. SMTP YOXDUR
    # (Render pulsuz planı SMTP portlarını bloklayır). MAIL_SYNC=True — məktub eyni axında (testlər).
    MAIL_SYNC = False

    MAX_CONTENT_LENGTH = 26 * 1024 * 1024  # bütün forma: təklif şəkilləri 5 × 5 MB + sahələr (aşanda 413)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("MAKLERHUB_SECURE_COOKIES", "0") == "1"
