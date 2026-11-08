"""Müştəri tələbi (qeydiyyatsız): /teleb forması və /teleb/<token> "Mənim tələbim" səhifəsi."""
import io
import json
import re
import secrets
import threading
import time
from collections import deque
from datetime import timedelta
from urllib.parse import urlsplit

from flask import (Blueprint, abort, current_app, flash, g, jsonify, make_response, redirect, render_template,
                   request, session, url_for)

from .auth import client_ip, ip_limit_applies
from .db import add_log, get_db, get_setting
from .helpers import (ALL_BAKU, BAKU_DISTRICTS, DISTRICTS_AZ, date_short, districts_text, is_all_baku, money,
                      normalize_phone, now, phone_display, phone_masked, rel, ts)
from .customer_mail import (RECOVERY_EMAIL_DAILY, RECOVERY_IP_HOURLY, email_key, mask_email, normalize_email,
                            send_recovery)
from .maintenance import lazy_expire

bp = Blueprint("teleb", __name__)

DEALS = {"rent": "Kirayə", "sale": "Alış"}
URGENCY = {"urgent": "Təcili", "month": "1 ay ərzində", "flexible": "Tələsmirəm"}
ROOMS = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5+"}
STATUS = {"active": "Aktiv", "closed": "Bağlanıb", "expired": "Müddəti bitib"}
NOTE_MAX = 300
NAME_MAX = 40
IP_DAILY_LIMIT = 3       # IP başına son 24 saatda
PHONE_ACTIVE_LIMIT = 2   # eyni nömrə ilə aktiv tələb
BUDGET = {"rent": (50, 100_000), "sale": (5_000, 50_000_000)}  # AZN/ay və AZN
CONSENT_TEXT = ("Nömrəm yalnız qəbul etdiyim təklifin maklerinə göstəriləcək. "
                "Məlumatlarımın bu məqsədlə emalına razıyam.")
HONEYPOT = "website"
ANON_DAYS = 90           # bağlanan/bitən tələbin şəxsi məlumatları bu qədər gündən sonra silinir
OFFER_LIMIT = 5          # bir tələbə aktiv (geri çəkilməmiş) təklif limiti
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")
STATUS_MAX_TOKENS = 5    # /api/teleb/status: bir sorğuda ən çox (brauzer yaddaşı da 5 saxlayır)
STATUS_RATE = 20         # /api/teleb/status: IP başına dəqiqədə
THEME_COLOR = "#101820"  # PWA: qeydiyyat/tələb səhifələrinin --navy rəngi
ICON_BG = "#141F35"      # ikonların fonu (loqonun öz tünd-göy fonu)


def expire_requests(db):
    """Lazy yeniləmə (cron yoxdur): müddəti keçən aktiv tələblər 'expired' olur; bağlanandan/bitəndən
    ANON_DAYS gün sonra ad, telefon, qeyd, IP və təklif şəkilləri silinir (Cloudinary-dən silməni çağıran edir —
    qaytarılan public_id-lər). Demo tələblərin müddəti bitmir (portfolio canlı qalsın).
    Çağıran tərəf commit etməlidir. Sorğularda birbaşa yox, maintenance.lazy_expire ilə (dəqiqədə ən çox 1 dəfə)."""
    ttl = int(get_setting(db, "request_ttl_days") or 30)
    stamp = now()
    from .images import public_ids_for
    old = ts(stamp - timedelta(days=ANON_DAYS))
    anon = ("o.request_id IN (SELECT id FROM customer_requests WHERE status IN ('closed', 'expired') "
            "AND closed_at < ? AND phone != '')")
    public_ids = public_ids_for(db, anon, (old,))
    if public_ids:
        db.execute("DELETE FROM offer_images WHERE public_id = ANY(?)", (public_ids,))
    db.execute("UPDATE customer_requests SET status = 'expired', closed_at = ? WHERE status = 'active' AND is_demo = 0 "
               "AND created_at < ?",
               (ts(stamp), ts(stamp - timedelta(days=ttl))))
    db.execute("UPDATE customer_requests SET name = '', phone = '', note = '', ip = '', email = '', email_notify = 0 "
               "WHERE status IN ('closed', 'expired') AND closed_at < ? AND (phone != '' OR email != '')", (old,))
    return public_ids


def request_alive(db, req):
    """Tələb aktivdir və müddəti keçməyib (lazy yeniləmə hələ işləməsə də). Demo tələbin müddəti bitmir."""
    if req.get("is_demo"):
        return req["status"] == "active"
    ttl = int(get_setting(db, "request_ttl_days") or 30)
    return req["status"] == "active" and req["created_at"] >= ts(now() - timedelta(days=ttl))


def _budget(value):
    s = (value or "").replace(" ", "").replace(",", "").replace(".", "")
    return int(s) if s.isdigit() else None


def validate(f):
    """Forma sahələrini yoxlayır. Qaytarır: (təmiz dəyərlər, xətalar)."""
    errors = []
    name = f["name"]
    if not name:
        errors.append("Adınızı yazın.")
    elif len(name) > NAME_MAX:
        errors.append(f"Ad {NAME_MAX} simvoldan uzun ola bilməz.")
    phone = normalize_phone(f["phone"])
    if not phone:
        errors.append("Telefon nömrəsi düzgün deyil. Nümunə: +994 50 123 45 67 "
                      "(operator kodu: 10, 50, 51, 55, 60, 70, 77, 99).")
    deal = f["deal"] if f["deal"] in DEALS else None
    if not deal:
        errors.append("Kirayə və ya alış seçin.")
    # rayon limiti yoxdur (minimum 1); "Fərq etmir (bütün Bakı)" → bütün rayonlar
    if f.get("all_districts"):
        districts = list(DISTRICTS_AZ)
    else:
        districts = [d for d in dict.fromkeys(f["districts"]) if d in BAKU_DISTRICTS]
    if not districts:
        errors.append("Ən azı bir rayon seçin.")
    rooms = int(f["rooms"]) if f["rooms"].isdigit() and int(f["rooms"]) in ROOMS else None
    if not rooms:
        errors.append("Otaq sayını seçin.")
    lo, hi = _budget(f["budget_min"]), _budget(f["budget_max"])
    if hi is None:
        errors.append("Maksimum büdcəni yazın.")
    elif deal:
        b_lo, b_hi = BUDGET[deal]
        unit = "AZN/ay" if deal == "rent" else "AZN"
        if not b_lo <= hi <= b_hi:
            errors.append(f"Büdcə {money(b_lo)}–{money(b_hi)} {unit} aralığında olmalıdır.")
        elif lo is not None and lo > hi:
            errors.append("Minimum büdcə maksimumdan böyük ola bilməz.")
    if f["budget_min"] and lo is None:
        errors.append("Minimum büdcə yalnız rəqəm ola bilər.")
    urgency = f["urgency"] if f["urgency"] in URGENCY else None
    if not urgency:
        errors.append("Nə qədər tez lazım olduğunu seçin.")
    if len(f["note"]) > NOTE_MAX:
        errors.append(f"Qeyd {NOTE_MAX} simvoldan uzun ola bilməz.")
    if not f["consent"]:
        errors.append("Razılıq qutusunu işarələyin — nömrəniz yalnız bu şərtlə saxlanılır.")
    email = normalize_email(f.get("email"))
    if email is None:
        errors.append("E-poçt ünvanı düzgün deyil (məs. ad@gmail.com) — və ya sahəni boş saxlayın.")
    clean = {"name": name, "phone": phone, "deal": deal, "districts": districts, "rooms": rooms,
             "budget_min": lo, "budget_max": hi, "urgency": urgency, "note": f["note"], "email": email or ""}
    return clean, errors


@bp.route("/teleb", methods=["GET", "POST"])
def forma():
    form = {"name": "", "phone": "", "deal": "", "districts": [], "rooms": "", "budget_min": "", "budget_max": "",
            "urgency": "", "note": "", "consent": False, "email": ""}
    errors = []
    if request.method == "POST":
        form = {k: (request.form.get(k) or "").strip() for k in
                ("name", "phone", "deal", "rooms", "budget_min", "budget_max", "urgency", "note", "email")}
        form["districts"] = request.form.getlist("districts")
        form["all_districts"] = request.form.get("all_districts") == "1"
        form["consent"] = request.form.get("consent") == "on"
        db = get_db()
        ip = client_ip()
        if g.get("csrf_failed"):
            # sessiya/token köhnəlib: yönləndirmə yox, dəyərlər qalır, yeni token formada gedir
            errors = ["Zəhmət olmasa yenidən göndərin — təhlükəsizlik tokeni yeniləndi."]
        elif request.form.get(HONEYPOT):
            add_log(db, "warn", "Müştəri", "Tələb rədd edildi (honeypot)", f"IP={ip}")
            db.commit()
            errors = ["Tələb qəbul olunmadı. Səhifəni yeniləyib yenidən cəhd edin."]
        else:
            clean, errors = validate(form)
            if not errors:
                lazy_expire(db)
                since = ts(now() - timedelta(hours=24))
                if ip_limit_applies(ip) and db.execute(
                        "SELECT COUNT(*) FROM customer_requests WHERE ip = ? AND created_at >= ?",
                        (ip, since)).fetchone()[0] >= IP_DAILY_LIMIT:
                    errors.append(f"Bir gündə ən çox {IP_DAILY_LIMIT} tələb göndərmək olar. Sabah yenidən cəhd edin.")
                elif db.execute("SELECT COUNT(*) FROM customer_requests WHERE phone = ? AND status = 'active' "
                                "AND is_demo = 0",
                                (clean["phone"],)).fetchone()[0] >= PHONE_ACTIVE_LIMIT:
                    errors.append(f"Bu nömrə ilə artıq {PHONE_ACTIVE_LIMIT} aktiv tələb var. "
                                  "Yenisi üçün köhnə tələblərdən birinin bağlanmasını gözləyin.")
            if not errors:
                token = secrets.token_urlsafe(24)
                stamp = ts()
                rid = db.execute(
                    "INSERT INTO customer_requests(token, name, phone, deal_type, districts, rooms, budget_min, "
                    "budget_max, urgency, note, consent, consent_at, status, ip, created_at, email, email_notify) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,1,?,'active',?,?,?,?)",
                    (token, clean["name"], clean["phone"], clean["deal"],
                     json.dumps(clean["districts"], ensure_ascii=False), clean["rooms"], clean["budget_min"],
                     clean["budget_max"], clean["urgency"], clean["note"], stamp, ip, stamp,
                     clean["email"], 1 if clean["email"] else 0)).lastrowid
                from .offers import notify_new_request
                notify_new_request(db, {"id": rid, "is_demo": 0, "deal_type": clean["deal"], "districts": clean["districts"],
                                        "budget_min": clean["budget_min"], "budget_max": clean["budget_max"],
                                        "summary": f"{DEALS[clean['deal']]}, {districts_text(clean['districts'])}, "
                                                   f"{ROOMS[clean['rooms']]} otaq"})
                add_log(db, "info", "Müştəri", f"Yeni tələb: {DEALS[clean['deal']]}, {districts_text(clean['districts'])}",
                        f"otaq={ROOMS[clean['rooms']]} büdcə≤{clean['budget_max']}")
                db.commit()
                session["teleb_new"] = rid  # "Linki saxla" bloku bir dəfə böyük göstərilsin (token cookie-yə yazılmır)
                return redirect(url_for("teleb.menim", token=token))
    return render_template("teleb/forma.html", form=form, errors=errors, districts=BAKU_DISTRICTS, deals=DEALS,
                           urgency=URGENCY, rooms=ROOMS, note_max=NOTE_MAX,
                           consent_text=CONSENT_TEXT, honeypot=HONEYPOT, budget=BUDGET)


URGENCY_ORDER = {"urgent": 0, "month": 1, "flexible": 2}


def budget_str(deal_type, lo, hi):
    unit = " AZN/ay" if deal_type == "rent" else " AZN"
    if not hi:
        return "—"
    return (f"{money(lo)} – {money(hi)}" if lo else f"{money(hi)}-dək") + unit


def request_summary(r):
    """Telefonsuz və adsız xülasə (makler tərəfi də bunu istifadə edir)."""
    return {"deal": DEALS.get(r["deal_type"], r["deal_type"]), "deal_type": r["deal_type"],
            "districts": json.loads(r["districts"] or "[]"),
            "rooms": ROOMS.get(r["rooms"], "—") if r["rooms"] else "—",
            "budget": budget_str(r["deal_type"], r["budget_min"], r["budget_max"]),
            "urgency": URGENCY.get(r["urgency"], r["urgency"]), "urgency_key": r["urgency"],
            "status": r["status"], "status_label": STATUS.get(r["status"]),
            "created": date_short(r["created_at"]), "created_time": r["created_at"][11:16], "ago": rel(r["created_at"])}


def request_view(r):
    """Müştərinin öz səhifəsi üçün: xülasə + qeyd, ad və telefon."""
    v = request_summary(r)
    v.update(note=r["note"], name=r["name"], phone=phone_display(r["phone"]))
    return v


def agent_short_name(first, last):
    """'Rəşad Məmmədov' → 'Rəşad M.' (müştəriyə maklerin tam soyadı göstərilmir)."""
    first, last = (first or "").strip(), (last or "").strip()
    return f"{first} {last[:1]}." if last else first


def offer_cards(db, request_id, deal_type, seen_at=None):
    from .images import images_by_offer
    """Müştəriyə görünən təkliflər (geri çəkilənlər yox). Makler adı: "Ad S."; qəbul olunanda tam ad və telefon.
    new: müştərinin son baxışından sonra gəlib."""
    rows = db.execute(
        "SELECT o.*, u.first_name, u.last_name, u.phone AS agent_phone FROM offers o JOIN users u ON u.id = o.agent_id "
        "WHERE o.request_id = ? AND o.status != 'withdrawn' ORDER BY o.created_at DESC, o.id DESC", (request_id,)).fetchall()
    unit = " AZN/ay" if deal_type == "rent" else " AZN"
    pics = images_by_offer(db, [o["id"] for o in rows])
    out = []
    for o in rows:
        accepted = o["status"] == "accepted"
        out.append({"id": o["id"], "title": o["title"], "price": f"{money(o['price'])}{unit}" if o["price"] else "—",
                    "district": o["district"], "rooms": ROOMS.get(o["rooms"], "—") if o["rooms"] else "—",
                    "area": (f"{o['area']:g} m²" if o["area"] else None), "image": o["image_url"],
                    "images": pics.get(o["id"], []),
                    "description": o["description"], "status": o["status"],
                    "agent": agent_short_name(o["first_name"], o["last_name"]),
                    "agent_full": f"{o['first_name']} {o['last_name']}".strip() if accepted else None,
                    "agent_phone": phone_display(o["agent_phone"]) if accepted else None,
                    "agent_tel": o["agent_phone"] if accepted else None,
                    "new": seen_at is None or o["created_at"] > seen_at,
                    "when": rel(o["created_at"])})
    return out


def _page_response(html):
    resp = make_response(html)
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/teleb/<token>")
def menim(token):
    from .offers import ACCEPT_LIMIT, CLOSE_REASONS, CUSTOMER_COMPLAINTS, COMPLAINT_TEXT_MAX
    if len(token) > 64:
        return lost_link_404()
    db = get_db()
    lazy_expire(db)
    r = db.execute("SELECT * FROM customer_requests WHERE token = ?", (token,)).fetchone()
    if r is None:
        return lost_link_404()
    offers = offer_cards(db, r["id"], r["deal_type"], r["customer_seen_at"])
    complained = {x[0] for x in db.execute(
        "SELECT offer_id FROM complaints WHERE request_id = ? AND from_side = 'customer'", (r["id"],))}
    # son baxış: növbəti dəfə yalnız bundan sonra gələnlər "Yeni" olacaq
    db.execute("UPDATE customer_requests SET customer_seen_at = ? WHERE id = ?", (ts(), r["id"]))
    db.commit()
    try:
        confirm_id = int(request.args.get("tesdiq", 0))
    except ValueError:
        confirm_id = 0
    alive = request_alive(db, r)
    n_accepted = sum(1 for o in offers if o["status"] == "accepted")
    view = request_view(r)
    view["phone"] = phone_masked(r["phone"])
    fresh = session.get("teleb_new") == r["id"]
    if fresh:
        session.pop("teleb_new", None)
    return _page_response(render_template(
        "teleb/menim.html", r=view, token=token, alive=alive, link=url_for("teleb.menim", token=token, _external=True),
        fresh=fresh, theme_color=THEME_COLOR, email_masked=mask_email(r["email"]), email_notify=bool(r["email_notify"]),
        offers=[o for o in offers if o["status"] != "rejected"], rejected=[o for o in offers if o["status"] == "rejected"],
        confirm_id=confirm_id, can_accept=alive and n_accepted < ACCEPT_LIMIT, accept_limit=ACCEPT_LIMIT,
        n_accepted=n_accepted, close_reasons=CLOSE_REASONS, close_reason=CLOSE_REASONS.get(r["close_reason"]),
        complaint_reasons=CUSTOMER_COMPLAINTS, complaint_max=COMPLAINT_TEXT_MAX, complained=complained))


def _back(token, anchor=""):
    return redirect(url_for("teleb.menim", token=token) + anchor)


def _offer_action(token, oid, accept):
    from .offers import decide_offer
    db = get_db()
    res, error = decide_offer(db, token, oid, accept)
    if res == "notfound":
        abort(404)
    if error:
        flash(error, "err")
    elif accept:
        add_log(db, "info", "Müştəri", f"Təklif qəbul edildi: #{oid}", "nömrə maklerə açıldı")
        db.commit()
        flash("Təklif qəbul edildi. Makler sizinlə əlaqə saxlayacaq.", "ok")
    else:
        flash("Təklif rədd edildi.", "ok")
    return _back(token, f"#o-{oid}")


@bp.route("/teleb/<token>/teklif/<int:oid>/qebul", methods=["POST"])
def qebul(token, oid):
    return _offer_action(token, oid, True)


@bp.route("/teleb/<token>/teklif/<int:oid>/redd", methods=["POST"])
def redd(token, oid):
    return _offer_action(token, oid, False)


@bp.route("/teleb/<token>/bagla", methods=["POST"])
def bagla(token):
    from .offers import close_request
    db = get_db()
    res, error = close_request(db, token, request.form.get("reason", ""))
    if res == "notfound":
        abort(404)
    if error:
        flash(error, "err")
    else:
        add_log(db, "info", "Müştəri", "Tələb müştəri tərəfindən bağlandı", f"səbəb={request.form.get('reason')}")
        db.commit()
        flash("Tələb bağlandı. Təşəkkür edirik!", "ok")
    return _back(token)


@bp.route("/teleb/<token>/teklif/<int:oid>/sikayet", methods=["POST"])
def sikayet(token, oid):
    from .offers import add_complaint
    db = get_db()
    row = db.execute("SELECT o.id, o.agent_id, r.id AS rid FROM offers o JOIN customer_requests r ON r.id = o.request_id "
                     "WHERE o.id = ? AND r.token = ?", (oid, token)).fetchone()
    if row is None:
        abort(404)
    res, error = add_complaint(db, "customer", row["rid"], request.form.get("reason", ""), request.form.get("text", ""),
                               offer_id=oid, agent_id=row["agent_id"])
    if error:
        flash(error, "err")
    else:
        add_log(db, "warn", "Müştəri", f"Şikayət: təklif #{oid}", f"səbəb={request.form.get('reason')}")
        db.commit()
        flash("Şikayətiniz qəbul edildi. Administrator baxacaq.", "ok")
    return _back(token, f"#o-{oid}")


# ---------------------------------------------------------------- link yaddaşı, QR, ana ekran (Paket C1)
def short_districts(districts):
    """Banner üçün qısa: "Bütün Bakı" | "Yasamal" | "Yasamal, Nəsimi" | "Yasamal, Nəsimi +2"."""
    if is_all_baku(districts):
        return ALL_BAKU
    if len(districts) > 2:
        return f"{districts[0]}, {districts[1]} +{len(districts) - 2}"
    return ", ".join(districts)


def _token_or_404(token):
    """Format səhvdirsə və ya tələb yoxdursa 404."""
    if not TOKEN_RE.fullmatch(token or ""):
        abort(404)
    row = get_db().execute("SELECT id FROM customer_requests WHERE token = ?", (token,)).fetchone()
    if row is None:
        abort(404)
    return row["id"]


def _private(resp, content_type):
    resp.headers["Content-Type"] = content_type
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@bp.route("/teleb/<token>/qr.svg")
def qr(token):
    """Tələb linkinin QR kodu (serverdə, segno ilə SVG). Keşlənmir, indekslənmir."""
    import segno
    _token_or_404(token)
    buf = io.BytesIO()
    segno.make(url_for("teleb.menim", token=token, _external=True), error="m").save(
        buf, kind="svg", scale=8, border=2, dark=THEME_COLOR, light="#ffffff", xmldecl=False)
    resp = _private(make_response(buf.getvalue()), "image/svg+xml")
    resp.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    return resp


@bp.route("/teleb/<token>/manifest.webmanifest")
def manifest(token):
    """"Ana ekrana əlavə et" üçün dinamik manifest: start_url/scope = həmin tələbin səhifəsi."""
    _token_or_404(token)
    start = url_for("teleb.menim", token=token)

    def icon(name):
        return url_for("static", filename=f"img/{name}")

    data = {"name": "MaklerHub — Tələbim", "short_name": "Tələbim", "id": start, "start_url": start, "scope": start,
            "display": "standalone", "orientation": "portrait", "lang": "az", "dir": "ltr",
            "theme_color": THEME_COLOR, "background_color": ICON_BG,
            "description": "Bakıda ev tələbin və maklerlərin təklifləri",
            "icons": [{"src": icon("icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
                      {"src": icon("icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
                      {"src": icon("icon-512-maskable.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"}]}
    return _private(make_response(json.dumps(data, ensure_ascii=False)), "application/manifest+json; charset=utf-8")


_status_hits = {}
_status_lock = threading.Lock()


def _status_rate_ok(ip):
    """IP başına dəqiqədə STATUS_RATE sorğu (proses yaddaşında — limit yoxlaması bazanı oyatmır)."""
    stamp = time.monotonic()
    with _status_lock:
        if len(_status_hits) > 10_000:  # köhnə IP-ləri at
            for key in [k for k, q in _status_hits.items() if not q or stamp - q[-1] > 60]:
                del _status_hits[key]
        q = _status_hits.setdefault(ip, deque())
        while q and stamp - q[0] > 60:
            q.popleft()
        if len(q) >= STATUS_RATE:
            return False
        q.append(stamp)
        return True


def _same_origin():
    """CSRF tokeni yoxdur (səhifə sessiyasız ola bilər) — əvəzində Origin, o yoxdursa Referer host-u bizimki olmalıdır."""
    src = request.headers.get("Origin") or request.headers.get("Referer") or ""
    if not src or src == "null":
        return False
    try:
        return urlsplit(src).netloc.lower() == request.host.lower()
    except ValueError:
        return False


@bp.route("/api/teleb/status", methods=["POST"])
def status_api():
    """Brauzer yaddaşındakı (localStorage) tələblərin vəziyyəti — ana səhifə və /teleb banneri üçün.
    Body: {"tokens": [...]} (≤5, URL-də yox). Cavab: {"ok": true, "results": [...]} eyni sıra ilə; tapılmayan →
    yalnız {"found": false}. Telefon/ad heç vaxt qaytarılmır. Tapılan/tapılmayan üçün iş yolu eynidir: həmişə eyni
    sayda sorğu (ayar + bir toplu sorğu), token sayından və mövcudluğundan asılı olmayaraq."""
    if not _same_origin():
        return jsonify(ok=False, error="Etibarsız mənbə."), 403
    ip = client_ip()
    if ip_limit_applies(ip) and not _status_rate_ok(ip):
        return jsonify(ok=False, error="Çox sorğu. Bir dəqiqədən sonra yenidən cəhd edin."), 429
    body = request.get_json(silent=True)
    tokens = body.get("tokens") if isinstance(body, dict) else None
    if (not isinstance(tokens, list) or not tokens or len(tokens) > STATUS_MAX_TOKENS
            or not all(isinstance(t, str) for t in tokens)):
        return jsonify(ok=False, error=f"1–{STATUS_MAX_TOKENS} token göndərin."), 400
    valid = [t for t in dict.fromkeys(tokens) if TOKEN_RE.fullmatch(t)]
    db = get_db()
    ttl = int(get_setting(db, "request_ttl_days") or 30)
    cutoff = ts(now() - timedelta(days=ttl))
    rows = db.execute(
        "SELECT r.token, r.deal_type, r.districts, r.rooms, r.status, r.is_demo, r.created_at, "
        "COUNT(o.id) FILTER (WHERE o.status != 'withdrawn') AS total, "
        "COUNT(o.id) FILTER (WHERE o.status = 'sent' AND (r.customer_seen_at IS NULL "
        "OR o.created_at > r.customer_seen_at)) AS fresh "
        "FROM customer_requests r LEFT JOIN offers o ON o.request_id = r.id "
        "WHERE r.token = ANY(?) GROUP BY r.id", (valid,)).fetchall()
    found = {r["token"]: r for r in rows}
    results = []
    for t in tokens:
        r = found.get(t)
        if r is None:
            results.append({"found": False})
            continue
        status = r["status"]
        if status == "active" and not r["is_demo"] and r["created_at"] < cutoff:
            status = "expired"  # lazy yeniləmə hələ işləməyib
        districts = json.loads(r["districts"] or "[]")
        summary = " · ".join([DEALS.get(r["deal_type"], r["deal_type"]),
                              f"{ROOMS.get(r['rooms'], '—')} otaq", short_districts(districts)])
        results.append({"found": True, "status": status, "new_offers": int(r["fresh"] or 0),
                        "total_offers": int(r["total"] or 0), "summary": summary})
    resp = jsonify(ok=True, results=results)
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------------------------------------------------------- e-poçt bildirişləri və link bərpası (Paket C2)
def lost_link_404():
    """Tapılmayan /teleb/<token>: adi 404 + "Linkini itirmisən?"."""
    resp = make_response(render_template(
        "error.html", code=404, title="Tələb tapılmadı", teleb_link=True, berpa_link=True,
        text="Bu link səhv yazılıb və ya tələb silinib. Tələb yazanda e-poçtunu qeyd etmisənsə, linki yenidən "
             "göndərə bilərik."), 404)
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


def _base_url():
    from .mailer import base_url
    return base_url()


@bp.route("/teleb/<token>/email", methods=["POST"])
def email_ayarla(token):
    """Müştəri səhifəsindən: e-poçt əlavə et / bildirişləri dayandır / yenidən aç."""
    _token_or_404(token)
    db = get_db()
    action = request.form.get("action")
    if action == "add":
        email = normalize_email(request.form.get("email"))
        if not email:
            flash("E-poçt ünvanı düzgün deyil (məs. ad@gmail.com).", "err")
        else:
            db.execute("UPDATE customer_requests SET email = ?, email_notify = 1 WHERE token = ? AND email = ''",
                       (email, token))
            db.commit()
            flash("Hazırdır — yeni təklif gələndə e-poçtla xəbər verəcəyik.", "ok")
    elif action in ("stop", "resume"):
        db.execute("UPDATE customer_requests SET email_notify = ? WHERE token = ? AND email != ''",
                   (1 if action == "resume" else 0, token))
        db.commit()
        flash("E-poçt bildirişləri dayandırıldı." if action == "stop" else "E-poçt bildirişləri yenidən açıldı.", "ok")
    return redirect(url_for("teleb.menim", token=token) + "#email")


@bp.route("/teleb/<token>/email-dayandir")
def email_dayandir(token):
    """Məktubdakı link: tək kliklə dayandırır və təsdiq səhifəsi göstərir (yenidən açmaq mümkündür)."""
    _token_or_404(token)
    db = get_db()
    db.execute("UPDATE customer_requests SET email_notify = 0 WHERE token = ?", (token,))
    db.commit()
    return _page_response(render_template("teleb/email_dayandir.html", token=token))


def _recovery_attempts(db, key, since):
    return db.execute("SELECT COUNT(*) FROM login_attempts WHERE email = ? AND created_at >= ?",
                      (key, since)).fetchone()[0]


RECOVERY_DONE = "Əgər bu e-poçtla aktiv tələb varsa, linklər göndərildi."
RECOVERY_IP_KEY = "#berpa"


@bp.route("/teleb/berpa", methods=["GET", "POST"])
def berpa():
    """"Linkimi itirdim": cavab HƏMİŞƏ eynidir; axtarış və göndərmə arxa fonda (cavab müddəti də oxşar qalsın)."""
    email, error, done = "", None, False
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        if g.get("csrf_failed"):
            error = "Zəhmət olmasa yenidən göndərin — təhlükəsizlik tokeni yeniləndi."
        elif request.form.get(HONEYPOT):
            done = True  # bot: eyni cavab, heç nə göndərilmir
        else:
            clean = normalize_email(email)
            if not clean:
                error = "E-poçt ünvanı düzgün deyil (məs. ad@gmail.com)."
            else:
                db = get_db()
                ip = client_ip()
                stamp = now()
                if ip_limit_applies(ip) and _recovery_attempts(
                        db, RECOVERY_IP_KEY + ":" + ip, ts(stamp - timedelta(hours=1))) >= RECOVERY_IP_HOURLY:
                    error = "Çox cəhd edildi. Bir saatdan sonra yenidən yoxlayın."
                else:
                    key = email_key(clean)
                    allowed = _recovery_attempts(db, key, ts(stamp - timedelta(days=1))) < RECOVERY_EMAIL_DAILY
                    db.execute("INSERT INTO login_attempts(email, ip, success, created_at) VALUES (?, ?, 0, ?), "
                               "(?, ?, 0, ?)", (RECOVERY_IP_KEY + ":" + ip, ip, ts(stamp), key, ip, ts(stamp)))
                    db.commit()
                    if allowed:
                        from . import mailer
                        base = _base_url()
                        mailer.run_background(current_app._get_current_object(),
                                              lambda con: send_recovery(con, clean, base))
                    done = True
        if done:
            email = ""
    return _page_response(render_template("teleb/berpa.html", email=email, error=error, done=done,
                                          message=RECOVERY_DONE, honeypot=HONEYPOT))
