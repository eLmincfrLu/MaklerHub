"""Makler kabineti: Kabinet, Ayarlar (profil, xidmət sahəsi, lisenziya) və bildiriş zəngi.
Tələblər/təkliflər sonrakı günlərdə."""
from flask import Blueprint, g, jsonify, render_template, request

from .auth import member_required
from .db import add_log, get_db
from .helpers import BAKU_DISTRICTS, date_short, full_name, long_date, normalize_phone, now, parse, phone_display, rel

bp = Blueprint("makler", __name__)

NAV = [("makler.kabinet", "Kabinet"), ("makler.ayarlar", "Ayarlar")]

DEALS = {"rent": "Kirayə", "sale": "Satış"}
PRICE_LIMIT = 100_000_000  # AZN, ağlabatan yuxarı həd


def _unread(db, user_id):
    return db.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0",
                      (user_id,)).fetchone()[0]


def render(template, active, **ctx):
    return render_template(template, nav=NAV, active=active, unread=_unread(get_db(), g.user["id"]), **ctx)


def err(message, code=400):
    return jsonify(ok=False, error=message), code


def load_areas(db, user_id):
    """{'rent': {'on', 'min', 'max', 'districts'}, 'sale': {...}} — agent_areas sətirlərindən."""
    out = {d: {"on": False, "min": None, "max": None, "districts": []} for d in DEALS}
    for r in db.execute("SELECT * FROM agent_areas WHERE user_id = ? ORDER BY id", (user_id,)):
        a = out.get(r["deal_type"])
        if a is None:
            continue
        a.update(on=True, min=r["price_min"], max=r["price_max"])
        a["districts"].append(r["district"])
    return out


def license_info(db, user):
    lic = db.execute("SELECT * FROM licenses WHERE id = ?", (user["license_id"],)).fetchone() \
        if user["license_id"] else None
    if not lic:
        return None
    ends = parse(lic["expires_at"])
    left = (ends - now()).total_seconds() / 86400 if ends else 0
    return {"code": lic["code"], "expires": date_short(lic["expires_at"]), "days_left": max(0, int(left + 0.999)),
            "activated": date_short(lic["activated_at"]), "days": lic["days"]}


# ---------------------------------------------------------------- səhifələr
@bp.route("/kabinet")
@member_required
def kabinet():
    db = get_db()
    has_area = db.execute("SELECT 1 FROM agent_areas WHERE user_id = ? LIMIT 1", (g.user["id"],)).fetchone()
    return render("makler/kabinet.html", "makler.kabinet", today_long=long_date(), has_area=bool(has_area),
                  lic=license_info(db, g.user))


@bp.route("/ayarlar")
@member_required
def ayarlar():
    db = get_db()
    return render("makler/ayarlar.html", "makler.ayarlar", areas=load_areas(db, g.user["id"]),
                  districts=BAKU_DISTRICTS, deals=DEALS, lic=license_info(db, g.user),
                  phone=phone_display(g.user["phone"]))


# Köhnə səhifələr: fayllar saxlanılır, içi "Tezliklə" (naviqasiyada görünmür, köhnə linklər qırılmasın)
@bp.route("/axtarislar")
@member_required
def axtarislar():
    return render("makler/axtarislar.html", "makler.kabinet")


@bp.route("/elanlar")
@member_required
def elanlar():
    return render("makler/elanlar.html", "makler.kabinet")


@bp.route("/musteriler")
@member_required
def musteriler():
    return render("makler/musteriler.html", "makler.kabinet")


@bp.route("/analitika")
@member_required
def analitika():
    return render("makler/analitika.html", "makler.kabinet")


# ---------------------------------------------------------------- ayarlar API
@bp.route("/api/me/profile", methods=["POST"])
@member_required
def api_me_profile():
    d = request.get_json(silent=True) or {}
    first, last = (d.get("first_name") or "").strip(), (d.get("last_name") or "").strip()
    if not first or not last:
        return err("Ad və soyad daxil edin.")
    if len(first) > 40 or len(last) > 40:
        return err("Ad və soyad 40 simvoldan uzun ola bilməz.")
    phone = normalize_phone(d.get("phone"))
    if not phone:
        return err("Telefon nömrəsi düzgün deyil (məsələn: +994 50 123 45 67).")
    db = get_db()
    db.execute("UPDATE users SET first_name = ?, last_name = ?, phone = ? WHERE id = ?",
               (first, last, phone, g.user["id"]))
    db.commit()
    return jsonify(ok=True, message="Profil yeniləndi", name=f"{first} {last}", phone=phone_display(phone))


def _int_or_none(value):
    s = str(value if value is not None else "").replace(" ", "").replace(",", "")
    if not s:
        return None
    if not s.isdigit():
        raise ValueError
    return int(s)


@bp.route("/api/me/areas", methods=["POST"])
@member_required
def api_me_areas():
    """Xidmət sahəsi: köhnə sətirlər silinir, seçilən hər (əməliyyat, rayon) üçün yeni sətir yazılır."""
    d = request.get_json(silent=True) or {}
    rows = []
    for deal, label in DEALS.items():
        a = d.get(deal) or {}
        if not a.get("on"):
            continue
        try:
            lo, hi = _int_or_none(a.get("min")), _int_or_none(a.get("max"))
        except ValueError:
            return err(f"{label}: qiymət yalnız rəqəm ola bilər.")
        if (lo is not None and lo > PRICE_LIMIT) or (hi is not None and hi > PRICE_LIMIT):
            return err(f"{label}: qiymət çox böyükdür.")
        if lo is not None and hi is not None and lo > hi:
            return err(f"{label}: minimum qiymət maksimumdan böyük ola bilməz.")
        districts = [x for x in dict.fromkeys(a.get("districts") or []) if x in BAKU_DISTRICTS]
        if not districts:
            return err(f"{label}: ən azı bir rayon seçin.")
        rows += [(deal, x, lo, hi) for x in districts]
    db = get_db()
    db.execute("DELETE FROM agent_areas WHERE user_id = ?", (g.user["id"],))
    for deal, district, lo, hi in rows:
        db.execute("INSERT INTO agent_areas(user_id, district, deal_type, price_min, price_max) VALUES (?,?,?,?,?)",
                   (g.user["id"], district, deal, lo, hi))
    add_log(db, "info", "Makler", f"Xidmət sahəsi yeniləndi: {full_name(g.user)}",
            ", ".join(f"{DEALS[k]}: {sum(1 for r in rows if r[0] == k)} rayon" for k in DEALS) or "boş")
    db.commit()
    return jsonify(ok=True, message="Xidmət sahəsi yadda saxlanıldı" if rows else "Xidmət sahəsi təmizləndi")


# ---------------------------------------------------------------- bildirişlər (zəng ikonu, app.js)
@bp.route("/api/notifications")
@member_required
def api_notifications():
    db = get_db()
    rows = db.execute("SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT 30",
                      (g.user["id"],)).fetchall()
    items = [{"id": r["id"], "title": r["text"], "body": "", "when": rel(r["created_at"]),
              "href": r["link"] or "#", "read": bool(r["is_read"])} for r in rows]
    return jsonify(ok=True, unread=_unread(db, g.user["id"]), items=items)


@bp.route("/api/notifications/<int:nid>/read", methods=["POST"])
@member_required
def api_notification_read(nid):
    db = get_db()
    db.execute("UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?", (nid, g.user["id"]))
    db.commit()
    return jsonify(ok=True, unread=_unread(db, g.user["id"]))


@bp.route("/api/notifications/read-all", methods=["POST"])
@member_required
def api_notifications_read_all():
    db = get_db()
    db.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0", (g.user["id"],))
    db.commit()
    return jsonify(ok=True, unread=0)
