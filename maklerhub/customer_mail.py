"""Müştəriyə e-poçt: yeni təklif bildirişi (saatda 1, gündə 3; arada gələnlər xülasə ilə) və "linkimi itirdim".

Bildiriş axını:
- offer_sent(db, request_id, base) — təklif göndəriləndə (sorğu axınında): gözləyən təkliflər + atomik iddia
  (UPDATE ... WHERE saat/gün şərtləri). Uğurlu iddiada məktub arxa fonda göndərilir. Uğursuzsa təklif gözləyir.
- flush_pending(con, base) — maintenance hook-u (5 dəqiqədə ən çox 1 dəfə): vaxtı çatan gözləyən bildirişlər.
Gözləyən təklif: status='sent', id > emailed_upto və müştəri səhifəyə baxandan (customer_seen_at) sonra gəlib.
Tələb bağlananda/bitəndə (status != active və ya müddəti keçib) və demo tələbdə məktub yoxdur.
Məktubda makler adı/telefonu və müştəri telefonu YOXDUR.
"""
import hashlib
import re
from datetime import timedelta

from flask import current_app, render_template

from . import mailer
from .helpers import money, now, ts

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
EMAIL_MAX = 120
PER_HOUR = 1
PER_DAY = 3
RECOVERY_IP_HOURLY = 5
RECOVERY_EMAIL_DAILY = 3
ROOMS = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5+"}
DEALS = {"rent": "Kirayə", "sale": "Alış"}


def normalize_email(value):
    """Kiçik hərf; səhvdirsə None, boşdursa ''."""
    v = (value or "").strip().lower()
    if not v:
        return ""
    if len(v) > EMAIL_MAX or not EMAIL_RE.match(v) or ".." in v:
        return None
    return v


def mask_email(email):
    """elmin@gmail.com → e***@gmail.com"""
    name, _, domain = (email or "").partition("@")
    return f"{name[:1]}***@{domain}" if name and domain else ""


def email_key(email):
    """login_attempts.email-də limit açarı (e-poçtun özü saxlanılmır)."""
    return "#berpa:" + hashlib.sha256(email.encode("utf-8")).hexdigest()[:40]


def _alive_cutoff(db):
    from .db import get_setting
    ttl = int(get_setting(db, "request_ttl_days") or 30)
    return ts(now() - timedelta(days=ttl))


def _pending(db, rid):
    return db.execute(
        "SELECT o.id, o.title, o.price, o.district, o.rooms, r.deal_type FROM offers o "
        "JOIN customer_requests r ON r.id = o.request_id "
        "WHERE o.request_id = ? AND o.status = 'sent' AND o.id > r.emailed_upto "
        "AND (r.customer_seen_at IS NULL OR o.created_at > r.customer_seen_at) ORDER BY o.id", (rid,)).fetchall()


def _claim(db, rid, upto):
    """Atomik: saat/gün limiti ödənirsə məktub hüququnu götürür. Commit çağıranda."""
    stamp, today = now(), ts()[:10]
    return db.execute(
        "UPDATE customer_requests SET last_email_at = ?, emailed_upto = ?, emails_day = ?, "
        "emails_today = CASE WHEN emails_day = ? THEN emails_today + 1 ELSE 1 END "
        "WHERE id = ? AND email_notify = 1 AND email != '' AND status = 'active' AND is_demo = 0 AND created_at >= ? "
        "AND (last_email_at IS NULL OR last_email_at <= ?) "
        "AND (emails_day IS NULL OR emails_day != ? OR emails_today < ?)",
        (ts(stamp), upto, today, today, rid, _alive_cutoff(db), ts(stamp - timedelta(hours=1)), today, PER_DAY)
    ).rowcount == 1


def _offer_view(o):
    unit = " AZN/ay" if o["deal_type"] == "rent" else " AZN"
    return {"title": o["title"], "price": f"{money(o['price'])}{unit}" if o["price"] else "—",
            "district": o["district"] or "—", "rooms": ROOMS.get(o["rooms"], "—") if o["rooms"] else "—"}


def compose_offer_mail(base, token, offers):
    n = len(offers)
    link, stop = f"{base}/teleb/{token}", f"{base}/teleb/{token}/email-dayandir"
    subject = "Tələbinə yeni təklif gəldi" if n == 1 else f"Tələbinə {n} yeni təklif gəldi"
    items = [_offer_view(o) for o in offers[:3]]
    ctx = {"base": base, "link": link, "stop": stop, "n": n, "items": items, "more": max(0, n - 3), "subject": subject}
    html = render_template("email/yeni_teklif.html", **ctx)
    lines = [subject + ".", ""]
    if n > 1:
        lines.append(f"{n} yeni təklif:")
    for it in items:
        lines.append(f"• {it['title']} — {it['price']}, {it['district']}, {it['rooms']} otaq")
    if n > 3:
        lines.append(f"və daha {n - 3} təklif")
    lines += ["", f"Təkliflərə bax: {link}", "", "Bu bildirişləri dayandırmaq üçün:", stop, "", "MaklerHub"]
    return subject, html, "\n".join(lines), {"List-Unsubscribe": f"<{stop}>"}


def _notify(db, rid, base):
    """Bir tələb üçün: gözləyən varsa və limit icazə verirsə iddia + commit. Qaytarır: (e-poçt, məktub) və ya None."""
    offers = _pending(db, rid)
    if not offers:
        return None
    if not _claim(db, rid, offers[-1]["id"]):
        db.rollback()
        return None
    row = db.execute("SELECT email, token FROM customer_requests WHERE id = ?", (rid,)).fetchone()
    db.commit()
    return row["email"], compose_offer_mail(base, row["token"], offers)


def offer_sent(db, rid, base):
    """Təklif göndəriləndən sonra (send_offer commit edib). Məktub arxa fonda; xəta istifadəçiyə təsir etmir."""
    try:
        res = _notify(db, rid, base)
    except Exception as exc:
        db.rollback()
        current_app.logger.warning("Təklif bildirişi hazırlanmadı: %s", exc.__class__.__name__)
        return False
    if res is None:
        return False
    to, (subject, html, text, headers) = res
    mailer.send_async(current_app._get_current_object(), to, subject, html, text, headers=headers)
    return True


def flush_pending(con, base, limit=50):
    """Hook: vaxtı çatmış gözləyən bildirişlər (eyni axında göndərilir — artıq arxa fondayıq). Qaytarır: say."""
    stamp, today = now(), ts()[:10]
    rows = con.execute(
        "SELECT r.id FROM customer_requests r WHERE r.email_notify = 1 AND r.email != '' AND r.status = 'active' "
        "AND r.is_demo = 0 AND r.created_at >= ? AND (r.last_email_at IS NULL OR r.last_email_at <= ?) "
        "AND (r.emails_day IS NULL OR r.emails_day != ? OR r.emails_today < ?) "
        "AND EXISTS (SELECT 1 FROM offers o WHERE o.request_id = r.id AND o.status = 'sent' AND o.id > r.emailed_upto "
        "AND (r.customer_seen_at IS NULL OR o.created_at > r.customer_seen_at)) ORDER BY r.id LIMIT ?",
        (_alive_cutoff(con), ts(stamp - timedelta(hours=1)), today, PER_DAY, limit)).fetchall()
    con.commit()
    sent = 0
    for r in rows:
        res = _notify(con, r["id"], base)
        if res is None:
            continue
        to, (subject, html, text, headers) = res
        if mailer.send_email(to, subject, html, text, con=con, headers=headers):
            sent += 1
    return sent


# ---------------------------------------------------------------- linkimi itirdim
def compose_recovery_mail(base, requests):
    links = [{"url": f"{base}/teleb/{r['token']}", "summary": r["summary"]} for r in requests]
    subject = "MaklerHub — tələbinin linki" if len(links) == 1 else "MaklerHub — tələblərinin linkləri"
    html = render_template("email/berpa.html", base=base, links=links, subject=subject)
    text = "\n".join(["Salam!", "", "Bu e-poçtla bağlı aktiv tələblərin:", ""]
                     + [f"• {x['summary']}\n  {x['url']}" for x in links]
                     + ["", "Linki yalnız özündə saxla — linkə sahib olan hər kəs təkliflərini görə bilər.",
                        "Bu məktubu sən istəməmisənsə, sadəcə nəzərə alma.", "", "MaklerHub"])
    return subject, html, text


def send_recovery(con, email, base):
    """Arxa fonda: e-poçtla bağlı aktiv tələblər varsa linklər bir məktubda. Yoxdursa heç nə."""
    from .helpers import districts_text
    import json
    rows = con.execute(
        "SELECT token, deal_type, rooms, districts FROM customer_requests WHERE email = ? AND status = 'active' "
        "AND is_demo = 0 AND created_at >= ? ORDER BY id DESC LIMIT 5", (email, _alive_cutoff(con))).fetchall()
    con.commit()
    if not rows:
        return False
    reqs = [{"token": r["token"], "summary": f"{DEALS.get(r['deal_type'], r['deal_type'])} · "
             f"{ROOMS.get(r['rooms'], '—')} otaq · {districts_text(json.loads(r['districts'] or '[]'))}"} for r in rows]
    subject, html, text = compose_recovery_mail(base, reqs)
    return mailer.send_email(email, subject, html, text, con=con)
