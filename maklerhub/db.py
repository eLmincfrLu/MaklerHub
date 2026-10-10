"""PostgreSQL (Neon) bağlantısı, sxemin yaradılması, ayarlar və loq köməkçiləri.

Köhnə kodla uyğunluq üçün nazik qat: `con.execute("... ?", params)` yazılışı saxlanılır,
`?` avtomatik `%s`-ə çevrilir, `INSERT OR IGNORE` və `cur.lastrowid` dəstəklənir,
sətirlərə həm ad (`row["x"]`), həm indeks (`row[0]`) ilə müraciət etmək olar.
"""
import os
import re

import click
import psycopg
from flask import current_app, g
from psycopg.adapt import Loader
from psycopg_pool import ConnectionPool

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


# ---------------------------------------------------------------- uyğunlaşdırıcı qat
class _FloatLoader(Loader):
    """NUMERIC (AVG/SUM nəticələri) Python float kimi qayıtsın — JSON-da sətir olmasın."""

    def load(self, data):
        return float(bytes(data))


psycopg.adapters.register_loader("numeric", _FloatLoader)


class Row:
    """sqlite3.Row kimi: row["ad"], row[0], row.keys(), dict(row)."""
    __slots__ = ("_names", "_idx", "_vals")

    def __init__(self, names, idx, vals):
        self._names, self._idx, self._vals = names, idx, vals

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self._vals[key]
        try:
            return self._vals[self._idx[key]]
        except KeyError:
            return self._vals[self._idx[key.lower()]]

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)

    def keys(self):
        return list(self._names)

    def get(self, key, default=None):
        try:
            return self[key]
        except (KeyError, IndexError):
            return default

    def __repr__(self):
        return "Row(" + ", ".join(f"{n}={v!r}" for n, v in zip(self._names, self._vals)) + ")"


def _row_factory(cursor):
    names = [c.name for c in cursor.description] if cursor.description else []
    idx = {}
    for i, n in enumerate(names):
        idx.setdefault(n, i)

    def make(values):
        return Row(names, idx, values)
    return make


# id sütunu olan cədvəllər (INSERT-dən sonra cur.lastrowid üçün RETURNING id əlavə olunur)
_ID_TABLES = {"users", "clients", "profiles", "sources", "sync_runs", "listings", "price_history", "matches",
              "logs", "login_attempts", "licenses", "license_attempts", "sessions", "devices", "phone_views",
              "notifications"}
_INSERT_RE = re.compile(r"^\s*INSERT\s+(OR\s+IGNORE\s+)?INTO\s+(\w+)", re.I)


def _prepare(sql, params):
    """SQLite yazılışını Postgres-ə çevirir. Qaytarır: (sql, params, lastrowid_lazımdır)."""
    wants_id = False
    if params is not None:
        params = tuple(int(p) if isinstance(p, bool) else p for p in params)
        sql = sql.replace("%", "%%").replace("?", "%s")
    m = _INSERT_RE.match(sql)
    if m:
        body = sql.rstrip().rstrip(";")
        if m.group(1):
            body = re.sub(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", body, count=1, flags=re.I)
            body += " ON CONFLICT DO NOTHING"
        if m.group(2).lower() in _ID_TABLES and "returning" not in body.lower():
            body += " RETURNING id"
            wants_id = True
        sql = body
    return sql, params, wants_id


class Cursor:
    def __init__(self, cur, lastrowid=None):
        self._cur = cur
        self.lastrowid = lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    def fetchone(self):
        return self._cur.fetchone() if self._cur.description else None

    def fetchall(self):
        return self._cur.fetchall() if self._cur.description else []

    def __iter__(self):
        return iter(self.fetchall())


class Conn:
    def __init__(self, raw, pool=None):
        self._raw, self._pool = raw, pool

    def execute(self, sql, params=None):
        sql, params, wants_id = _prepare(sql, params)
        cur = self._raw.cursor(row_factory=_row_factory)
        cur.execute(sql, params)
        last = None
        if wants_id:
            row = cur.fetchone()
            last = row[0] if row else None
        return Cursor(cur, last)

    def executemany(self, sql, seq):
        seq = list(seq)
        sql, _, _ = _prepare(sql, ())
        cur = self._raw.cursor(row_factory=_row_factory)
        cur.executemany(sql, [tuple(int(p) if isinstance(p, bool) else p for p in row) for row in seq])
        return Cursor(cur)

    def script(self, sql):
        """Parametrsiz çox-əmrli SQL (sxem üçün)."""
        self._raw.execute(sql)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        if self._pool is not None:
            try:
                self._raw.rollback()  # oxu-sorğularında açıq qalan tranzaksiyanı səssizcə bağla
            except Exception:
                pass
            self._pool.putconn(self._raw)
        else:
            self._raw.close()
        self._raw = None


def normalize_url(url):
    url = (url or "").strip()
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def connect(url):
    """Ayrıca (hovuzsuz) bağlantı: CLI, planlayıcı və s. üçün."""
    return Conn(psycopg.connect(normalize_url(url), connect_timeout=15))


_pool = None


def _get_pool(url):
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            normalize_url(url), min_size=0, max_size=int(os.environ.get("MAKLERHUB_POOL_MAX", "8")),
            kwargs={"connect_timeout": 15}, check=ConnectionPool.check_connection,
            max_idle=240, timeout=30, open=False)
        _pool.open()
    return _pool


def get_db():
    if "db" not in g:
        pool = _get_pool(current_app.config["DATABASE"])
        g.db = Conn(pool.getconn(), pool)
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def create_schema(con):
    with open(os.path.join(os.path.dirname(__file__), "schema.sql"), encoding="utf-8") as fh:
        con.script(fh.read())
    for k, v in DEFAULT_SETTINGS.items():
        con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    seed_plans(con)
    con.commit()


# Planların adları dizayna uyğundur; limitlər sonra (Gün 20-22) təyin olunur (NULL = hələ təyin olunmayıb)
DEFAULT_PLANS = [
    ("free", "Free", 0), ("pro", "Pro", 1), ("business", "Business", 2), ("agency", "Agency", 3),
]


def seed_plans(con):
    for key, name, sort in DEFAULT_PLANS:
        con.execute("INSERT OR IGNORE INTO plans(key, name, sort, max_devices) VALUES (?,?,?,2)", (key, name, sort))


def _schema_exists(con):
    return con.execute("SELECT to_regclass('public.users') IS NOT NULL").fetchone()[0]


def init_db(url, seed=False):
    """Bazanı SIFIRDAN yaradır (bütün cədvəllər silinir!). seed=True → dizayndakı nümunə məlumatlar."""
    con = connect(url)
    try:
        con.script("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
        con.commit()
        create_schema(con)
        if seed:
            from .seed import seed as run_seed
            run_seed(con)
        else:
            from .seed import seed_minimal
            seed_minimal(con)
        con.commit()
    finally:
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
    url = normalize_url(app.config.get("DATABASE"))
    if not url:
        raise RuntimeError("DATABASE_URL təyin olunmayıb (.env faylı və ya Render Environment).")
    app.config["DATABASE"] = url

    @app.cli.command("init-db")
    @click.option("--demo", is_flag=True, help="Dizayndakı nümunə məlumatlarla doldur.")
    @click.option("--empty", is_flag=True, hidden=True, help="(köhnə seçim, artıq standartdır)")
    @click.option("--yes", is_flag=True, help="Təsdiq soruşma.")
    def init_db_command(demo, empty, yes):
        """Verilənlər bazasını SIFIRDAN yaradır (bütün məlumat silinir!)."""
        if not yes and not click.confirm("Bütün cədvəllər və məlumat SİLİNƏCƏK. Davam edilsin?"):
            click.echo("Ləğv edildi.")
            return
        init_db(url, seed=demo and not empty)
        click.echo("Verilənlər bazası yaradıldı.")

    @app.cli.command("sync")
    def sync_command():
        """Bütün aktiv mənbələri dərhal sinxronlaşdırır."""
        from .sources import sync_all
        con = connect(url)
        try:
            sync_all(con)
            con.commit()
        finally:
            con.close()
        click.echo("Sinxronizasiya tamamlandı.")

    # İlk işə salınmada sxem avtomatik yaradılır (boş baza: yalnız mənbələr, admin və makler hesabı)
    con = connect(url)
    try:
        fresh = not _schema_exists(con)
        create_schema(con)  # yeni cədvəllər/ayarlar varsa əlavə olunur (IF NOT EXISTS)
        if fresh:
            from .seed import seed_minimal
            seed_minimal(con)
        con.commit()
    finally:
        con.close()