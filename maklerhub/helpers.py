"""Tarix, qiymət və ad formatlama köməkçiləri, Bakı rayonları (Azərbaycan dilində)."""
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

FMT = "%Y-%m-%d %H:%M:%S"
_TZ = "Asia/Baku"

MONTHS_SHORT = ["yan", "fev", "mar", "apr", "may", "iyn", "iyl", "avq", "sen", "okt", "noy", "dek"]
MONTHS_FULL = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avqust",
               "sentyabr", "oktyabr", "noyabr", "dekabr"]
WEEKDAYS = ["Bazar ertəsi", "Çərşənbə axşamı", "Çərşənbə", "Cümə axşamı", "Cümə", "Şənbə", "Bazar günü"]

ROLE_LABELS = {"makler": "Makler", "admin": "Admin"}

# Müştəri tələbi və makler ərazisi üçün sabit siyahı: əvvəl inzibati rayonlar, sonra populyar ərazilər
BAKU_DISTRICTS = [
    "Binəqədi", "Qaradağ", "Xətai", "Xəzər", "Nərimanov", "Nəsimi", "Nizami", "Pirallahı", "Sabunçu", "Səbail",
    "Suraxanı", "Yasamal",
    "Gənclik", "28 May", "Elmlər", "Nəriman Nərimanov m.", "İçərişəhər", "Bayıl", "Badamdar", "Əhmədli",
    "Həzi Aslanov", "Qara Qarayev", "Neftçilər", "Yeni Yasamal", "Masazır", "Xırdalan",
]


# ---------------------------------------------------------------- vaxt
def set_timezone(name):
    global _TZ
    _TZ = name


def now():
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(_TZ)).replace(tzinfo=None, microsecond=0)
        except Exception:
            pass
    return datetime.now().replace(microsecond=0)


def ts(dt=None):
    return (dt or now()).strftime(FMT)


def parse(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.strptime(str(value)[:19], FMT)


def day_start(dt=None):
    d = dt or now()
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


def date_short(value):
    dt = parse(value)
    if not dt:
        return "—"
    return f"{dt.day} {MONTHS_SHORT[dt.month - 1]} {dt.year}"


def log_time(value):
    dt = parse(value)
    return f"{dt.day} {MONTHS_SHORT[dt.month - 1]} {dt:%H:%M:%S}"


def clock(value):
    dt = parse(value)
    return f"{dt:%H:%M:%S}"


def long_date(value=None):
    dt = parse(value) or now()
    return f"{WEEKDAYS[dt.weekday()]}, {dt.day} {MONTHS_FULL[dt.month - 1]} {dt.year}"


def rel(value):
    """'12 dəq əvvəl', '5 saat əvvəl', '3 gün əvvəl', '2 həftə əvvəl'..."""
    dt = parse(value)
    if not dt:
        return "—"
    secs = (now() - dt).total_seconds()
    if secs < 60:
        return "indicə"
    mins = int(secs // 60)
    if mins < 60:
        return f"{mins} dəq əvvəl"
    hours = mins // 60
    if hours < 24:
        return f"{hours} saat əvvəl"
    days = hours // 24
    if days < 7:
        return f"{days} gün əvvəl"
    if days < 30:
        return f"{days // 7} həftə əvvəl"
    if days < 365:
        return f"{days // 30} ay əvvəl"
    return date_short(dt)


# ---------------------------------------------------------------- mətn
def az_upper(s):
    return (s or "").replace("i", "İ").upper()


def initials(name, last=None):
    parts = [name or "", last or ""] if last is not None else (name or "").split()
    letters = [p[0] for p in parts if p][:2]
    return az_upper("".join(letters)) or "?"


def full_name(user):
    return f"{user['first_name']} {user['last_name']}".strip()


# ---------------------------------------------------------------- qiymət
def money(n):
    return f"{int(round(n or 0)):,}"


# ---------------------------------------------------------------- Jinja
def init_app(app):
    app.jinja_env.filters.update(
        rel=rel, date_short=date_short, money=money,
        az_upper=az_upper,
    )
    app.jinja_env.globals.update(
        initials=initials, full_name=full_name, ROLE_LABELS=ROLE_LABELS, BAKU_DISTRICTS=BAKU_DISTRICTS,
    )
