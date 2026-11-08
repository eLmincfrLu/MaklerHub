"""Müştəri tələbi (qeydiyyatsız): /teleb forması və /teleb/<token> "Mənim tələbim" səhifəsi."""
import json
import secrets
from datetime import timedelta

from flask import Blueprint, abort, flash, g, make_response, redirect, render_template, request, url_for

from .auth import client_ip, ip_limit_applies
from .db import add_log, get_db, get_setting
from .helpers import (BAKU_DISTRICTS, DISTRICTS_AZ, date_short, districts_text, money, normalize_phone, now,
                      phone_display, phone_masked, rel, ts)
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
    db.execute("UPDATE customer_requests SET name = '', phone = '', note = '', ip = '' "
               "WHERE status IN ('closed', 'expired') AND closed_at < ? AND phone != ''", (old,))
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
    clean = {"name": name, "phone": phone, "deal": deal, "districts": districts, "rooms": rooms,
             "budget_min": lo, "budget_max": hi, "urgency": urgency, "note": f["note"]}
    return clean, errors


@bp.route("/teleb", methods=["GET", "POST"])
def forma():
    form = {"name": "", "phone": "", "deal": "", "districts": [], "rooms": "", "budget_min": "", "budget_max": "",
            "urgency": "", "note": "", "consent": False}
    errors = []
    if request.method == "POST":
        form = {k: (request.form.get(k) or "").strip() for k in
                ("name", "phone", "deal", "rooms", "budget_min", "budget_max", "urgency", "note")}
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
                    "budget_max, urgency, note, consent, consent_at, status, ip, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,1,?,'active',?,?)",
                    (token, clean["name"], clean["phone"], clean["deal"],
                     json.dumps(clean["districts"], ensure_ascii=False), clean["rooms"], clean["budget_min"],
                     clean["budget_max"], clean["urgency"], clean["note"], stamp, ip, stamp)).lastrowid
                from .offers import notify_new_request
                notify_new_request(db, {"id": rid, "is_demo": 0, "deal_type": clean["deal"], "districts": clean["districts"],
                                        "budget_min": clean["budget_min"], "budget_max": clean["budget_max"],
                                        "summary": f"{DEALS[clean['deal']]}, {districts_text(clean['districts'])}, "
                                                   f"{ROOMS[clean['rooms']]} otaq"})
                add_log(db, "info", "Müştəri", f"Yeni tələb: {DEALS[clean['deal']]}, {districts_text(clean['districts'])}",
                        f"otaq={ROOMS[clean['rooms']]} büdcə≤{clean['budget_max']}")
                db.commit()
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
        abort(404)
    db = get_db()
    lazy_expire(db)
    r = db.execute("SELECT * FROM customer_requests WHERE token = ?", (token,)).fetchone()
    if r is None:
        abort(404)
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
    return _page_response(render_template(
        "teleb/menim.html", r=view, token=token, alive=alive, link=url_for("teleb.menim", token=token, _external=True),
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
