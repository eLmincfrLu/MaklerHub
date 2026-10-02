"""Tarix, qiymət, ad və elan formatlama köməkçiləri (Azərbaycan dilində)."""
import json
from datetime import datetime, timedelta

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

ROLE_LABELS = {"makler": "Makler", "alici": "Alıcı", "satici": "Satıcı", "admin": "Admin"}
PLAN_LABELS = {"start": "Başlanğıc", "pro": "Pro", "none": "—"}
SOURCE_COLORS = {"bina": "var(--gold)", "lalafo": "var(--ok)", "tap": "var(--navy)"}


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


def contact_rel(value):
    """Müştəri ilə son əlaqə: 'Bu gün, 09:14', 'Dünən, 18:40', '5 saat əvvəl'..."""
    dt = parse(value)
    if not dt:
        return "—"
    n = now()
    if dt.date() == n.date():
        if (n - dt).total_seconds() < 6 * 3600:
            return rel(dt)
        return f"Bu gün, {dt:%H:%M}"
    if dt.date() == (n - timedelta(days=1)).date():
        return f"Dünən, {dt:%H:%M}"
    return rel(dt)


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


def price_str(price, deal):
    return f"{money(price)} AZN/ay" if deal == "rent" else f"{money(price)} AZN"


def _short(n):
    if n >= 1_000_000:
        v = n / 1_000_000
        return (f"{v:.2f}".rstrip("0").rstrip(".")) + "M"
    v = n / 1000
    return (str(int(v)) if v == int(v) else f"{v:.1f}".rstrip("0").rstrip(".")) + "K"


def range_str(lo, hi, deal, dash="–"):
    if deal == "rent":
        return f"{lo}{dash}{hi} AZN/ay"
    a, b = _short(lo), _short(hi)
    if a.endswith("K") and b.endswith("K"):
        return f"{a[:-1]}{dash}{b} AZN"
    return f"{a}{dash}{b} AZN"


# ---------------------------------------------------------------- axtarış profili
def rooms_label(p):
    if p["rooms"]:
        return f"{p['rooms']}{'+' if p['rooms_plus'] else ''} otaq"
    return None


def profile_crit(p, dash="–"):
    parts = [p["district"]]
    rl = rooms_label(p)
    if rl:
        parts.append(rl)
    elif p["property_type"]:
        parts.append(p["property_type"])
    parts.append(range_str(p["price_min"], p["price_max"], p["deal_type"], dash))
    return " · ".join(parts)


def profile_tags(p):
    tags = [p["district"]]
    rl = rooms_label(p)
    if rl:
        tags.append(rl)
    elif p["property_type"]:
        tags.append(p["property_type"])
    tags.append(range_str(p["price_min"], p["price_max"], p["deal_type"]))
    if rl and p["property_type"]:
        tags.append(p["property_type"])
    if p["deal_type"] == "rent":
        tags.append("Kirayə")
    return tags


# ---------------------------------------------------------------- elan
def fmt_area(a):
    return str(int(a)) if float(a) == int(a) else f"{a:.1f}"


def listing_title(l):
    return f"{l['title']} — {l['suffix']}" if l["suffix"] else l["title"]


def listing_title_long(l):
    s = f"{l['title']}, {price_str(l['price'], l['deal_type'])}"
    if l["deal_type"] == "rent":
        s += " kirayə"
    return s


def listing_meta(l):
    parts = [f"{fmt_area(l['area'])} m²"]
    label = l["renovation"] or l["building"]
    if label:
        parts.append(label)
    if l["floor"] and l["floors"]:
        parts.append(f"{l['floor']}/{l['floors']} mərtəbə")
    if l["metro_m"] and l["metro_m"] <= 1500:
        parts.append(f"metroya {l['metro_m']}m")
    return " · ".join(parts)


def reasons_list(raw):
    try:
        return json.loads(raw or "[]")
    except ValueError:
        return []


def why(reasons, long=False):
    out = []
    for kind, short, lng in reasons:
        out.append(("✓ " if kind == "ok" else "△ ") + (lng if long else short))
    return " · ".join(out)


def ring_offset(score, circ):
    return round(circ * (1 - score / 100.0), 1)


def price_drop_info(con, listing):
    """(köhnə qiymət, faiz, 'N gündə K dəfə ucuzlaşıb') və ya None."""
    rows = con.execute(
        "SELECT price, recorded_at FROM price_history WHERE listing_id = ? AND recorded_at >= ? ORDER BY recorded_at",
        (listing["id"], ts(now() - timedelta(days=30))),
    ).fetchall()
    if len(rows) < 2:
        return None
    drops = sum(1 for a, b in zip(rows, rows[1:]) if b["price"] < a["price"])
    old = max(r["price"] for r in rows)
    if drops == 0 or old <= listing["price"]:
        return None
    days = max(1, (now() - parse(rows[0]["recorded_at"])).days)
    pct = round((old - listing["price"]) / old * 100)
    return {"old": price_str(old, listing["deal_type"]), "pct": pct,
            "text": f"{days} gündə {drops} dəfə ucuzlaşıb"}


# ---------------------------------------------------------------- Jinja
def init_app(app):
    app.jinja_env.filters.update(
        rel=rel, contact_rel=contact_rel, date_short=date_short, money=money,
        az_upper=az_upper,
    )
    app.jinja_env.globals.update(
        initials=initials, full_name=full_name, ROLE_LABELS=ROLE_LABELS, PLAN_LABELS=PLAN_LABELS,
    )
