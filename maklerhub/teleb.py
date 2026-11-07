"""Müştəri tələbi (qeydiyyatsız): /teleb forması və /teleb/<token> "Mənim tələbim" səhifəsi."""
import json
import secrets
from datetime import timedelta

from flask import Blueprint, abort, make_response, redirect, render_template, request, url_for

from .auth import client_ip
from .db import add_log, get_db
from .helpers import BAKU_DISTRICTS, date_short, money, normalize_phone, now, phone_display, ts

bp = Blueprint("teleb", __name__)

DEALS = {"rent": "Kirayə", "sale": "Alış"}
URGENCY = {"urgent": "Təcili", "month": "1 ay ərzində", "flexible": "Tələsmirəm"}
ROOMS = {1: "1", 2: "2", 3: "3", 4: "4", 5: "5+"}
STATUS = {"active": "Aktiv", "closed": "Bağlanıb", "expired": "Müddəti bitib"}
MAX_DISTRICTS = 3
NOTE_MAX = 300
NAME_MAX = 40
IP_DAILY_LIMIT = 3       # IP başına son 24 saatda
PHONE_ACTIVE_LIMIT = 2   # eyni nömrə ilə aktiv tələb
BUDGET = {"rent": (50, 100_000), "sale": (5_000, 50_000_000)}  # AZN/ay və AZN
CONSENT_TEXT = ("Nömrəm yalnız qəbul etdiyim təklifin maklerinə göstəriləcək. "
                "Məlumatlarımın bu məqsədlə emalına razıyam.")
HONEYPOT = "website"


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
    districts = [d for d in dict.fromkeys(f["districts"]) if d in BAKU_DISTRICTS]
    if not districts:
        errors.append("Ən azı bir rayon seçin.")
    elif len(f["districts"]) > MAX_DISTRICTS or len(districts) > MAX_DISTRICTS:
        errors.append(f"Ən çox {MAX_DISTRICTS} rayon seçmək olar.")
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
        form["consent"] = request.form.get("consent") == "on"
        db = get_db()
        ip = client_ip()
        if request.form.get(HONEYPOT):
            add_log(db, "warn", "Müştəri", "Tələb rədd edildi (honeypot)", f"IP={ip}")
            db.commit()
            errors = ["Tələb qəbul olunmadı. Səhifəni yeniləyib yenidən cəhd edin."]
        else:
            clean, errors = validate(form)
            if not errors:
                since = ts(now() - timedelta(hours=24))
                if db.execute("SELECT COUNT(*) FROM customer_requests WHERE ip = ? AND created_at >= ?",
                              (ip, since)).fetchone()[0] >= IP_DAILY_LIMIT:
                    errors.append(f"Bir gündə ən çox {IP_DAILY_LIMIT} tələb göndərmək olar. Sabah yenidən cəhd edin.")
                elif db.execute("SELECT COUNT(*) FROM customer_requests WHERE phone = ? AND status = 'active'",
                                (clean["phone"],)).fetchone()[0] >= PHONE_ACTIVE_LIMIT:
                    errors.append(f"Bu nömrə ilə artıq {PHONE_ACTIVE_LIMIT} aktiv tələb var. "
                                  "Yenisi üçün köhnə tələblərdən birinin bağlanmasını gözləyin.")
            if not errors:
                token = secrets.token_urlsafe(24)
                stamp = ts()
                db.execute(
                    "INSERT INTO customer_requests(token, name, phone, deal_type, districts, rooms, budget_min, "
                    "budget_max, urgency, note, consent, consent_at, status, ip, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,1,?,'active',?,?)",
                    (token, clean["name"], clean["phone"], clean["deal"],
                     json.dumps(clean["districts"], ensure_ascii=False), clean["rooms"], clean["budget_min"],
                     clean["budget_max"], clean["urgency"], clean["note"], stamp, ip, stamp))
                add_log(db, "info", "Müştəri", f"Yeni tələb: {DEALS[clean['deal']]}, {', '.join(clean['districts'])}",
                        f"otaq={ROOMS[clean['rooms']]} büdcə≤{clean['budget_max']}")
                db.commit()
                return redirect(url_for("teleb.menim", token=token))
    return render_template("teleb/forma.html", form=form, errors=errors, districts=BAKU_DISTRICTS, deals=DEALS,
                           urgency=URGENCY, rooms=ROOMS, max_districts=MAX_DISTRICTS, note_max=NOTE_MAX,
                           consent_text=CONSENT_TEXT, honeypot=HONEYPOT, budget=BUDGET)


def request_view(r):
    unit = " AZN/ay" if r["deal_type"] == "rent" else " AZN"
    lo, hi = r["budget_min"], r["budget_max"]
    budget = (f"{money(lo)} – {money(hi)}" if lo else f"{money(hi)}-dək") + unit if hi else "—"
    return {"deal": DEALS.get(r["deal_type"], r["deal_type"]), "districts": json.loads(r["districts"] or "[]"),
            "rooms": ROOMS.get(r["rooms"], "—") if r["rooms"] else "—", "budget": budget,
            "urgency": URGENCY.get(r["urgency"], r["urgency"]), "note": r["note"], "name": r["name"],
            "phone": phone_display(r["phone"]), "status": r["status"], "status_label": STATUS.get(r["status"]),
            "created": date_short(r["created_at"]), "created_time": r["created_at"][11:16]}


@bp.route("/teleb/<token>")
def menim(token):
    if len(token) > 64:
        abort(404)
    r = get_db().execute("SELECT * FROM customer_requests WHERE token = ?", (token,)).fetchone()
    if r is None:
        abort(404)
    resp = make_response(render_template("teleb/menim.html", r=request_view(r), offers=[],
                                         link=url_for("teleb.menim", token=token, _external=True)))
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Cache-Control"] = "no-store"
    return resp
