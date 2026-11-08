"""Makler kabineti: Tələblər (uyğunluqla), tələb detalı + təklif, Təkliflərim, Ayarlar, bildiriş zəngi."""
from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from . import images as images_mod, matching, offers as offers_mod
from .auth import member_required
from .db import add_log, get_db
from .helpers import BAKU_DISTRICTS, date_short, full_name, long_date, normalize_phone, now, parse, phone_display, rel
from .maintenance import lazy_expire
from .teleb import OFFER_LIMIT, ROOMS, request_alive, request_summary

bp = Blueprint("makler", __name__)

NAV = [("makler.kabinet", "Tələblər"), ("makler.teklifler", "Təkliflərim"), ("makler.bildirisler", "Bildirişlər"),
       ("makler.ayarlar", "Ayarlar")]
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
PAGE_SIZE = 20
SORTS = {"score": "Uyğunluğa görə", "new": "Ən yeni", "urgency": "Təcililiyə görə"}

DEALS = {"rent": "Kirayə", "sale": "Satış"}
PRICE_LIMIT = 100_000_000  # AZN, ağlabatan yuxarı həd
DEMO_READONLY = "Demo hesabda profil, xidmət sahəsi və şifrə dəyişdirilmir."


def _unread(db, user_id):
    return db.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0",
                      (user_id,)).fetchone()[0]


def render(template, active, **ctx):
    return render_template(template, nav=NAV, active=active, unread=_unread(get_db(), g.user["id"]), **ctx)


def err(message, code=400):
    return jsonify(ok=False, error=message), code


def demo_readonly():
    """Demo makler: profil/sahə/şifrə yalnız oxu rejimindədir (server 403)."""
    return err(DEMO_READONLY, 403) if g.user["is_demo"] else None


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


def agent_areas(db, user_id):
    return db.execute("SELECT deal_type, district, price_min, price_max FROM agent_areas WHERE user_id = ?",
                      (user_id,)).fetchall()


def _first_name(name):
    return (name or "").split(" ")[0] or "Müştəri"


def _ring(score, circ=138):
    return round(circ * (1 - score / 100.0), 1)


def request_card(it):
    """Kabinet kartı: telefon yoxdur, ad yalnız ilk ad."""
    r = it["r"]
    v = request_summary(r)
    v.update(id=r["id"], score=it["score"], ring=_ring(it["score"]), mid=it["score"] < 80,
             why=" · ".join(t for _, t in it["reasons"]), first_name=_first_name(r["name"]),
             n_offers=it["n_offers"], limit=OFFER_LIMIT, my_status=it["my_status"],
             my_label=offers_mod.OFFER_STATUS.get(it["my_status"]))
    return v


def _filters(args):
    def num(k):
        v = (args.get(k) or "").replace(" ", "")
        return int(v) if v.isdigit() else None
    rooms = num("rooms")
    return {"view": "all" if args.get("view") == "all" else "good",
            "deal": args.get("deal") if args.get("deal") in DEALS else "",
            "district": args.get("district") if args.get("district") in BAKU_DISTRICTS else "",
            "rooms": rooms if rooms in ROOMS else None, "bmin": num("bmin"), "bmax": num("bmax"),
            "sort": args.get("sort") if args.get("sort") in SORTS else "score"}


def offer_stats(db, user_id):
    row = db.execute("SELECT COUNT(*) FILTER (WHERE status != 'withdrawn') AS sent, "
                     "COUNT(*) FILTER (WHERE status = 'accepted') AS accepted FROM offers WHERE agent_id = ?",
                     (user_id,)).fetchone()
    return row["sent"], row["accepted"]


# ---------------------------------------------------------------- tələblər
@bp.route("/kabinet")
@member_required
def kabinet():
    db, u = get_db(), g.user
    lazy_expire(db)
    areas = agent_areas(db, u["id"])
    sent, accepted = offer_stats(db, u["id"])
    ctx = {"today_long": long_date(), "lic": license_info(db, u), "has_area": bool(areas), "sent": sent,
           "accepted": accepted, "good": 0, "items": [], "total": 0, "page": 1, "pages": 1}
    f = _filters(request.args)
    if areas:
        ranked = offers_mod.load_ranked(db, u["id"], areas)
        ctx["good"] = sum(1 for x in ranked if x["score"] >= matching.GOOD_SCORE)
        items = offers_mod.apply_filters(ranked, f)
        pages = max(1, -(-len(items) // PAGE_SIZE))
        try:
            page = min(max(1, int(request.args.get("page", 1))), pages)
        except ValueError:
            page = 1
        ctx.update(total=len(items), page=page, pages=pages,
                   items=[request_card(x) for x in items[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]])
    args = {k: v for k, v in request.args.items() if k != "page" and v}
    return render("makler/kabinet.html", "makler.kabinet", f=f, sorts=SORTS, deals=DEALS,
                  deals_served={a["deal_type"] for a in areas}, districts=BAKU_DISTRICTS, rooms=ROOMS, args=args,
                  good_score=matching.GOOD_SCORE, **ctx)


def _detail_ctx(db, rid):
    """Tələb + uyğunluq + öz təklifim. Makler həmin əməliyyatla işləmirsə və təklifi yoxdursa — 404."""
    r = db.execute(f"SELECT {offers_mod.REQ_COLS} FROM customer_requests r WHERE r.id = ?", (rid,)).fetchone()
    if r is None or r["is_demo"] != g.user["is_demo"]:  # demo ↔ real bir-birini görmür
        abort(404)
    mine = db.execute("SELECT * FROM offers WHERE request_id = ? AND agent_id = ?", (rid, g.user["id"])).fetchone()
    res = matching.score(r, agent_areas(db, g.user["id"]))
    if res is None and mine is None:
        abort(404)
    n_offers = db.execute("SELECT COUNT(*) FROM offers WHERE request_id = ? AND status != 'withdrawn'",
                          (rid,)).fetchone()[0]
    alive = request_alive(db, r)
    v = request_summary(r)
    v.update(id=r["id"], note=r["note"], first_name=_first_name(r["name"]))
    contact = offers_mod.contacts_for(db, g.user["id"], [mine["id"]]).get(mine["id"]) if mine else None
    complained = db.execute("SELECT 1 FROM complaints WHERE request_id = ? AND agent_id = ? AND from_side = 'agent'",
                            (rid, g.user["id"])).fetchone() is not None
    my_images = images_mod.images_by_offer(db, [mine["id"]]).get(mine["id"], []) if mine else []
    return {"req": v, "contact": contact, "my_images": my_images, "max_images": max_images(g.user),
            "max_mb": images_mod.MAX_BYTES // (1024 * 1024), "upload_ready": images_mod.configured(), "complained": complained, "complaint_reasons": offers_mod.AGENT_COMPLAINTS,
            "complaint_max": offers_mod.COMPLAINT_TEXT_MAX, "image_exts": IMAGE_EXTS, "score": res[0] if res else None, "reasons": res[1] if res else [],
            "ring": _ring(res[0], 151) if res else 151, "n_offers": n_offers, "limit": OFFER_LIMIT, "alive": alive,
            "mine": mine, "my_label": offers_mod.OFFER_STATUS.get(mine["status"]) if mine else None,
            "can_offer": alive and mine is None and n_offers < OFFER_LIMIT,
            "districts": BAKU_DISTRICTS, "rooms": ROOMS, "title_max": offers_mod.TITLE_MAX,
            "desc_max": offers_mod.DESC_MAX, "url_max": offers_mod.URL_MAX}


EMPTY_OFFER = {"title": "", "price": "", "district": "", "rooms": "", "area": "", "description": ""}


def max_images(user):
    return images_mod.DEMO_MAX_FILES if user["is_demo"] else images_mod.MAX_FILES


def _upload_offer_images(db, rid, files):
    """Şəkilləri yoxlayır, emal edir və Cloudinary-ə yükləyir — tranzaksiya AÇIQ DEYİL.
    Qaytarır: (offer_id və ya None, [yüklənmiş], [xəta])."""
    if not files:
        return None, [], []
    processed, errors = images_mod.process_all(files, max_images(g.user))
    if errors:
        return None, [], errors
    if not images_mod.configured():
        return None, [], ["Şəkil yükləmə hazırda işləmir. Şəkilsiz göndərin və ya sonra cəhd edin."]
    used = images_mod.recent_count(db, g.user["id"], now())
    if used + len(processed) > images_mod.HOURLY_LIMIT:
        db.rollback()
        return None, [], [f"Saatda ən çox {images_mod.HOURLY_LIMIT} şəkil yükləmək olar. Bir az sonra cəhd edin."]
    problem = offers_mod.precheck_offer(db, rid, g.user["id"])  # boşuna yükləmə olmasın
    if problem:
        return None, [], [problem]
    oid = offers_mod.reserve_offer_id(db)
    try:
        uploaded = images_mod.upload_all(oid, processed)
    except images_mod.UploadError as exc:
        add_log(db, "err", "Makler", f"Şəkil yüklənmədi: {full_name(g.user)}", f"tələb=#{rid} səbəb={exc}")
        db.commit()
        return None, [], [images_mod.UPLOAD_FAILED]
    return oid, uploaded, []


@bp.route("/kabinet/teleb/<int:rid>")
@member_required
def teleb_detal(rid):
    db = get_db()
    ctx = _detail_ctx(db, rid)
    # formanı tələbə görə əvvəlcədən doldur (ilk rayon, otaq sayı)
    form = dict(EMPTY_OFFER, district=ctx["req"]["districts"][0] if ctx["req"]["districts"] else "",
                rooms=str(next((k for k, v in ROOMS.items() if v == ctx["req"]["rooms"]), "")))
    return render("makler/teleb.html", "makler.kabinet", form=form, errors=[], **ctx)


@bp.route("/kabinet/teleb/<int:rid>/teklif", methods=["POST"])
@member_required
def teklif_gonder(rid):
    db = get_db()
    ctx = _detail_ctx(db, rid)
    form = {k: (request.form.get(k) or "").strip() for k in EMPTY_OFFER}
    clean, errors = offers_mod.validate_offer(form, ctx["req"]["deal_type"])
    files = images_mod.uploaded_files(request)
    if not errors:
        reserved, uploaded, errors = _upload_offer_images(db, rid, files)
    if not errors:
        oid, error = offers_mod.send_offer(db, rid, g.user["id"], clean, offer_id=reserved, images=uploaded)
        if oid:
            add_log(db, "info", "Makler", f"Təklif göndərildi: {full_name(g.user)}",
                    f"tələb=#{rid} təklif=#{oid} şəkil={len(uploaded)}")
            db.commit()
            from .customer_mail import offer_sent
            from .mailer import base_url
            offer_sent(db, rid, base_url())  # e-poçt varsa: saatda 1, gündə 3 (xəta təsir etmir)
            flash("Təklif göndərildi. Müştəri onu öz səhifəsində görəcək.", "ok")
            return redirect(url_for("makler.teleb_detal", rid=rid))
        images_mod.destroy([u["public_id"] for u in uploaded], db)  # son yoxlama keçmədi → Cloudinary-dən sil
        db.commit()
        errors.append(error)
    if files and errors:
        errors.append("Şəkilləri yenidən seçin (təhlükəsizlik üçün brauzer onları saxlamır).")
    ctx = _detail_ctx(db, rid)
    return render("makler/teleb.html", "makler.kabinet", form=form, errors=errors, **ctx), 400


@bp.route("/kabinet/teklif/<int:oid>/geri-cek", methods=["POST"])
@member_required
def teklif_geri_cek(oid):
    db = get_db()
    row = db.execute("SELECT request_id FROM offers WHERE id = ? AND agent_id = ?", (oid, g.user["id"])).fetchone()
    if row is None:
        abort(404)
    if offers_mod.withdraw_offer(db, oid, g.user["id"]):
        flash("Təklif geri çəkildi. Bu tələbə yenidən təklif göndərmək olmayacaq.", "ok")
    else:
        flash("Yalnız göndərilmiş (cavab verilməmiş) təklifi geri çəkmək olar.", "err")
    back = request.form.get("back", "")
    return redirect(back if back.startswith("/kabinet") else url_for("makler.teleb_detal", rid=row["request_id"]))


@bp.route("/kabinet/teklifler")
@member_required
def teklifler():
    db = get_db()
    status = request.args.get("status") if request.args.get("status") in offers_mod.OFFER_STATUS else ""
    sql = ("SELECT o.*, r.deal_type, r.districts, r.rooms AS rrooms, r.budget_min, r.budget_max, r.urgency, "
           "r.status AS rstatus, r.created_at AS rcreated FROM offers o "
           "JOIN customer_requests r ON r.id = o.request_id WHERE o.agent_id = ?")
    params = [g.user["id"]]
    if status:
        sql += " AND o.status = ?"
        params.append(status)
    rows = db.execute(sql + " ORDER BY o.created_at DESC, o.id DESC LIMIT 300", params).fetchall()
    counts = {r["status"]: r["c"] for r in db.execute(
        "SELECT status, COUNT(*) c FROM offers WHERE agent_id = ? GROUP BY status", (g.user["id"],))}
    items = []
    for o in rows:
        req = request_summary({"deal_type": o["deal_type"], "districts": o["districts"], "rooms": o["rrooms"],
                               "budget_min": o["budget_min"], "budget_max": o["budget_max"], "urgency": o["urgency"],
                               "status": o["rstatus"], "created_at": o["rcreated"]})
        unit = " AZN/ay" if o["deal_type"] == "rent" else " AZN"
        items.append({"o": o, "req": req, "label": offers_mod.OFFER_STATUS.get(o["status"]),
                      "price": f"{o['price']:,}{unit}" if o["price"] else "—", "when": rel(o["created_at"]),
                      "rooms": ROOMS.get(o["rooms"], "—") if o["rooms"] else "—"})
    contacts = offers_mod.contacts_for(db, g.user["id"], [it["o"]["id"] for it in items if it["o"]["status"] == "accepted"])
    pics = images_mod.images_by_offer(db, [it["o"]["id"] for it in items])
    for it in items:
        it["contact"] = contacts.get(it["o"]["id"])
        it["thumb"] = pics[it["o"]["id"]][0]["thumb"] if pics.get(it["o"]["id"]) else None
    return render("makler/teklifler.html", "makler.teklifler", items=items, status=status, counts=counts,
                  labels=offers_mod.OFFER_STATUS, total=sum(counts.values()))


@bp.route("/kabinet/teleb/<int:rid>/sikayet", methods=["POST"])
@member_required
def teleb_sikayet(rid):
    db = get_db()
    _detail_ctx(db, rid)  # yalnız görə bildiyi tələbə (yoxsa 404)
    res, error = offers_mod.add_complaint(db, "agent", rid, request.form.get("reason", ""), request.form.get("text", ""),
                                          agent_id=g.user["id"])
    if error:
        flash(error, "err")
    else:
        add_log(db, "warn", "Makler", f"Şikayət: tələb #{rid}", f"makler={g.user['email']} səbəb={request.form.get('reason')}")
        db.commit()
        flash("Şikayətiniz qəbul edildi. Administrator baxacaq.", "ok")
    return redirect(url_for("makler.teleb_detal", rid=rid))


# ---------------------------------------------------------------- bildirişlər
@bp.route("/kabinet/bildirisler")
@member_required
def bildirisler():
    db = get_db()
    rows = db.execute("SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT 100",
                      (g.user["id"],)).fetchall()
    items = [{"id": r["id"], "text": r["text"], "kind": r["kind"], "read": bool(r["is_read"]), "when": rel(r["created_at"]),
              "has_link": bool(r["link"])} for r in rows]
    return render("makler/bildirisler.html", "makler.bildirisler", items=items)


@bp.route("/kabinet/bildiris/<int:nid>")
@member_required
def bildiris_ac(nid):
    """Bildirişi oxunmuş edib linkinə aparır (yalnız daxili /kabinet linkləri)."""
    db = get_db()
    row = db.execute("SELECT link FROM notifications WHERE id = ? AND user_id = ?", (nid, g.user["id"])).fetchone()
    if row is None:
        abort(404)
    db.execute("UPDATE notifications SET is_read = 1 WHERE id = ?", (nid,))
    db.commit()
    link = row["link"] or ""
    return redirect(link if link.startswith("/kabinet") else url_for("makler.bildirisler"))


@bp.route("/kabinet/bildirisler/oxundu", methods=["POST"])
@member_required
def bildirisler_oxundu():
    db = get_db()
    db.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0", (g.user["id"],))
    db.commit()
    return redirect(url_for("makler.bildirisler"))


@bp.route("/ayarlar")
@member_required
def ayarlar():
    db = get_db()
    return render("makler/ayarlar.html", "makler.ayarlar", areas=load_areas(db, g.user["id"]),
                  districts=BAKU_DISTRICTS, deals=DEALS, lic=license_info(db, g.user),
                  phone=phone_display(g.user["phone"]))


# ---------------------------------------------------------------- ayarlar API
@bp.route("/api/me/profile", methods=["POST"])
@member_required
def api_me_profile():
    stop = demo_readonly()
    if stop:
        return stop
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
    stop = demo_readonly()
    if stop:
        return stop
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
              "href": url_for("makler.bildiris_ac", nid=r["id"]), "read": bool(r["is_read"])} for r in rows]
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
