"""SQLite bağlantısı, sxemin yaradılması, ayarlar və loq köməkçiləri."""
import os
import sqlite3

import click
from flask import current_app, g

from .helpers import ts

DEFAULT_SETTINGS = {
    # Sinxronizasiya
    "sync_interval": "5",          # dəqiqə
    "rate_limit": "30",            # sorğu/dəq
    "timeout": "10",               # saniyə
    "retries": "3",
    "auto_slow": "1",
    # Bildirişlər
    "notify_email": "admin@maklerhub.az",
    "notify_source_down": "1",
    "notify_new_makler": "1",
    "notify_daily": "0",
    # Planlar
    "trial_days": "14",
    "plan_start_price": "29",
    "plan_start_clients": "10",
    "plan_start_profiles": "15",
    "plan_pro_price": "59",
    "plan_pro_clients": "50",
    "plan_pro_profiles": "100",
    # Təhlükəsizlik
    "admin_2fa": "1",
    "session_timeout": "120",      # dəqiqə
    "login_fail_limit": "5",
}


def connect(path):
    con = sqlite3.connect(path, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    return con


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def create_schema(con):
    with open(os.path.join(os.path.dirname(__file__), "schema.sql"), encoding="utf-8") as fh:
        con.executescript(fh.read())
    for k, v in DEFAULT_SETTINGS.items():
        con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    con.commit()


def init_db(path, seed=True):
    """Bazanı sıfırdan yaradır (və istəyə görə nümunə məlumatları doldurur)."""
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = connect(path)
    create_schema(con)
    if seed:
        from .seed import seed as run_seed
        run_seed(con)
    else:
        from .seed import seed_minimal
        seed_minimal(con)
    con.commit()
    con.close()


# ---------------------------------------------------------------- ayarlar
def get_settings(con):
    data = dict(DEFAULT_SETTINGS)
    for row in con.execute("SELECT key, value FROM settings"):
        data[row["key"]] = row["value"]
    return data


def get_setting(con, key):
    row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else DEFAULT_SETTINGS.get(key)


def set_setting(con, key, value):
    con.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


# ---------------------------------------------------------------- loqlar
def add_log(con, level, source, message, details="", when=None):
    con.execute(
        "INSERT INTO logs(created_at, level, source, message, details) VALUES (?, ?, ?, ?, ?)",
        (when or ts(), level, source, message, details),
    )


# ---------------------------------------------------------------- CLI
def init_app(app):
    app.teardown_appcontext(close_db)

    @app.cli.command("init-db")
    @click.option("--empty", is_flag=True, help="Nümunə məlumatları olmadan (yalnız admin hesabı).")
    def init_db_command(empty):
        """Verilənlər bazasını sıfırdan yaradır."""
        init_db(app.config["DATABASE"], seed=not empty)
        click.echo("Verilənlər bazası yaradıldı: " + app.config["DATABASE"])

    @app.cli.command("sync")
    def sync_command():
        """Bütün aktiv mənbələri dərhal sinxronlaşdırır."""
        from .sources import sync_all
        con = connect(app.config["DATABASE"])
        try:
            sync_all(con)
            con.commit()
        finally:
            con.close()
        click.echo("Sinxronizasiya tamamlandı.")

    # İlk işə salınmada baza avtomatik yaradılır
    if not os.path.exists(app.config["DATABASE"]):
        init_db(app.config["DATABASE"], seed=False)
    else:
        con = connect(app.config["DATABASE"])
        create_schema(con)  # yeni cədvəllər/ayarlar varsa əlavə olunur
        con.close()
