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

    # Fon rejimində mənbələrin avtomatik sinxronizasiyası (1 = aktiv, 0 = deaktiv)
    SCHEDULER = os.environ.get("MAKLERHUB_SCHEDULER", "1") == "1"

    # Şifrə bərpası üçün e-poçt (boş qalsa, link server konsoluna və sistem loqlarına yazılır)
    MAIL_SERVER = os.environ.get("MAKLERHUB_MAIL_SERVER", "")
    MAIL_PORT = int(os.environ.get("MAKLERHUB_MAIL_PORT", "587"))
    MAIL_USERNAME = os.environ.get("MAKLERHUB_MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAKLERHUB_MAIL_PASSWORD", "")
    MAIL_FROM = os.environ.get("MAKLERHUB_MAIL_FROM", "no-reply@maklerhub.az")
    MAIL_TLS = os.environ.get("MAKLERHUB_MAIL_TLS", "1") == "1"

    MAX_CONTENT_LENGTH = 4 * 1024 * 1024  # avatar yükləmə limiti (4 MB)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("MAKLERHUB_SECURE_COOKIES", "0") == "1"
