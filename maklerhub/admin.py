"""Admin panel: ümumi baxış, tələblər, maklerlər, lisenziyalar, şikayətlər, loqlar, ayarlar (şifrə).
Bütün admin hərəkətləri loqa yazılır."""
import json
from datetime import timedelta

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, session, url_for

from . import contact as contact_mod, images as images_mod, licenses, maintenance
from .auth import admin_required
from .db import add_log, get_db, get_settings, set_setting
from .helpers import date_short, districts_text, full_name, log_time, normalize_phone, now, parse, phone_display, rel, ts
from .offers import AGENT_COMPLAINTS, CLOSE_REASONS, CUSTOMER_COMPLAINTS, OFFER_STATUS
from .teleb import DEALS, OFFER_LIMIT, ROOMS, STATUS as REQ_STATUS, URGENCY, budget_str

bp = Blueprint("admin", __name__, url_prefix="/admin")

NAV = [("admin.panel", "Ümumi baxış"), ("admin.telebler", "Tələblər"), ("admin.maklerler", "Maklerlər"),
       ("admin.lisenziyalar", "Lisenziyalar"), ("admin.sikayetler", "Şikayətlər"), ("admin.loglar", "Sistem loqları"),
       ("admin.ayarlar", "Ayarlar")]
PAGE = 50
COMPLAINT_REASONS = {**CUSTOMER_COMPLAINTS, **AGENT_COMPLAINTS}
COMPLAINT_STATUS = {"new": "Yeni", "reviewed": "Baxılıb"}
CLOSE_REASONS_ALL = {**CLOSE_REASONS, "admin": "Admin bağladı"}


def render(template, active, **ctx):
    new_complaints = get_db().execute(
        "SELECT COUNT(*) FROM complaints c JOIN customer_requests r ON r.id = c.request_id "
        "WHERE c.status = 'new' AND r.is_demo = 0").fetchone()[0]
    return render_template(template, nav=NAV, active=active, new_complaints=new_complaints, **ctx)


def _back(default):
    nxt = request.form.get("next") or request.args.get("next") or ""
    return redirect(nxt if nxt.startswith("/admin") and not nxt.startswith("//") else default)


def _page():
    try:
        return max(1, int(request.args.get("page", 1)))
    except ValueError:
        return 1


def _req_row(r):
    districts = json.loads(r["districts"] or "[]")
    return {"r": r, "deal": DEALS.get(r["deal_type"], r["deal_type"]), "districts": districts_text(districts),
            "rooms": ROOMS.get(r["rooms"], "—") if r["rooms"] else "—",
            "budget": budget_str(r["deal_type"], r["budget_min"], r["budget_max"]),
            "status": REQ_STATUS.get(r["status"], r["status"]), "when": rel(r["created_at"]),
            "pill": "active" if r["status"] == "active" else "susp" if r["status"] == "closed" else "trial",
            "demo": bool(r.get("is_demo")),
            # müştəri məlumatı — YALNIZ admin səhifələri (makler/kabinet/API-də yoxdur)
            "anon": not r["phone"], "phone": phone_display(r["phone"]) if r["phone"] else "",
            "email": (r.get("email") or "").strip(), "created": date_short(r["created_at"]),
            "n_offers": r.get("n_offers"), "offer_limit": OFFER_LIMIT}


def _phone_digits(q):
    """Axtarış üçün telefon rəqəmləri: +994 50 ..., 994..., 050 ..., 50 ... → operator kodundan başlayan hissə."""
    d = "".join(ch for ch in q if ch.isdigit())
    if d.startswith("994"):
        d = d[3:]
    elif d.startswith("0"):
        d = d[1:]
    return d


# ---------------------------------------------------------------- ümumi baxış
@bp.route("")
@bp.route("/")
@admin_required
def panel():
    """Göstəricilər YALNIZ real məlumatdan; ?demo=1 — eyni göstəricilər yalnız demo məlumatdan (ayrıca)."""
    db = get_db()
    maintenance.lazy_expire(db)
    demo = 1 if request.args.get("demo") == "1" else 0
    week = ts(now() - timedelta(days=7))
    offers_of = "FROM offers o JOIN customer_requests r ON r.id = o.request_id WHERE r.is_demo = ? AND "
    k = db.execute(
        "SELECT (SELECT COUNT(*) FROM customer_requests WHERE status = 'active' AND is_demo = ?) AS active_req, "
        "(SELECT COUNT(*) FROM customer_requests WHERE created_at >= ? AND is_demo = ?) AS new_req, "
        f"(SELECT COUNT(*) {offers_of} o.status != 'withdrawn') AS offers, "
        f"(SELECT COUNT(*) {offers_of} o.status = 'accepted') AS accepted, "
        f"(SELECT COUNT(*) {offers_of} o.status IN ('accepted', 'rejected')) AS decided, "
        "(SELECT COUNT(*) FROM users u JOIN licenses l ON l.id = u.license_id WHERE u.role = 'makler' AND u.is_demo = ? "
        " AND u.status = 'active' AND l.status = 'used' AND l.expires_at > ?) AS maklers, "
        "(SELECT COUNT(*) FROM complaints c JOIN customer_requests r ON r.id = c.request_id "
        " WHERE c.status = 'new' AND r.is_demo = ?) AS complaints",
        (demo, week, demo, demo, demo, demo, demo, ts(), demo)).fetchone()
    kpi = dict(k)
    # qəbul faizi: cavab verilmiş (qəbul + rədd) təkliflər arasında qəbul olunanlar
    kpi["accept_rate"] = round(100 * k["accepted"] / k["decided"]) if k["decided"] else None
    reqs = [_req_row(r) for r in db.execute(
        "SELECT r.*, (SELECT COUNT(*) FROM offers o WHERE o.request_id = r.id AND o.status != 'withdrawn') AS n_offers "
        "FROM customer_requests r WHERE r.is_demo = ? ORDER BY r.id DESC LIMIT 10", (demo,))]
    offers = db.execute(
        "SELECT o.id, o.title, o.status, o.created_at, o.request_id, u.first_name, u.last_name FROM offers o "
        "JOIN users u ON u.id = o.agent_id JOIN customer_requests r ON r.id = o.request_id WHERE r.is_demo = ? "
        "ORDER BY o.id DESC LIMIT 10", (demo,)).fetchall()
    demo_total = db.execute("SELECT COUNT(*) FROM customer_requests WHERE is_demo = 1").fetchone()[0]
    from . import mailer
    mail_day, mail_sent = mailer.quota(db)
    return render("admin/panel.html", "admin.panel", kpi=kpi, reqs=reqs, offers=offers, offer_status=OFFER_STATUS,
                  rel=rel, full_name=full_name, demo=demo, demo_total=demo_total,
                  mail_sent=mail_sent, mail_limit=mailer.DAILY_LIMIT, mail_full=mail_sent >= mailer.DAILY_LIMIT)


# ---------------------------------------------------------------- tələblər
@bp.route("/telebler")
@admin_required
def telebler():
    db = get_db()
    maintenance.lazy_expire(db)
    status = request.args.get("status") if request.args.get("status") in REQ_STATUS else ""
    q = (request.args.get("q") or "").strip()[:60]
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if q:
        digits = _phone_digits(q)
        cond = ["name ILIKE ?", "districts ILIKE ?", "email ILIKE ?"]
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
        if len(digits) >= 2:
            cond.append("phone LIKE ?")
            params.append(f"%{digits[-9:]}%")
        if q.lstrip("#").isdigit() and len(q.lstrip("#")) <= 9:  # #id (integer həddini aşmasın)
            cond.append("id = ?")
            params.append(int(q.lstrip("#")))
        where.append("(" + " OR ".join(cond) + ")")
    sql_where = (" WHERE " + " AND ".join(where)) if where else ""
    total = db.execute("SELECT COUNT(*) FROM customer_requests" + sql_where, params).fetchone()[0]
    page = _page()
    rows = db.execute(
        "SELECT r.*, (SELECT COUNT(*) FROM offers o WHERE o.request_id = r.id AND o.status != 'withdrawn') AS n_offers "
        "FROM customer_requests r" + sql_where + f" ORDER BY r.id DESC LIMIT {PAGE} OFFSET {(page - 1) * PAGE}",
        params).fetchall()
    counts = {r["status"]: r["c"] for r in db.execute("SELECT status, COUNT(*) c FROM customer_requests GROUP BY status")}
    return render("admin/telebler.html", "admin.telebler", items=[_req_row(r) for r in rows], status=status, q=q,
                  counts=counts, labels=REQ_STATUS, page=page, pages=max(1, -(-total // PAGE)), total=total)


@bp.route("/teleb/<int:rid>")
@admin_required
def teleb(rid):
    db = get_db()
    r = db.execute("SELECT * FROM customer_requests WHERE id = ?", (rid,)).fetchone()
    if r is None:
        abort(404)
    offers = db.execute(
        "SELECT o.*, u.first_name, u.last_name, u.email, u.phone AS agent_phone, "
        "(SELECT 1 FROM contacts c WHERE c.offer_id = o.id) AS has_contact FROM offers o "
        "JOIN users u ON u.id = o.agent_id WHERE o.request_id = ? ORDER BY o.id", (rid,)).fetchall()
    complaints = db.execute("SELECT * FROM complaints WHERE request_id = ? ORDER BY id", (rid,)).fetchall()
    pics = images_mod.images_by_offer(db, [o["id"] for o in offers])
    return render("admin/teleb.html", "admin.telebler", x=_req_row(r), phone=phone_display(r["phone"]), offers=offers,
                  pics=pics,
                  complaints=complaints, offer_status=OFFER_STATUS, reasons=COMPLAINT_REASONS, urgency=URGENCY,
                  close_reason=CLOSE_REASONS_ALL.get(r["close_reason"]), full_name=full_name, rel=rel,
                  date_short=date_short, phone_display=phone_display)


@bp.route("/teleb/<int:rid>/bagla", methods=["POST"])
@admin_required
def teleb_bagla(rid):
    db = get_db()
    n = db.execute("UPDATE customer_requests SET status = 'closed', closed_at = ?, close_reason = 'admin' "
                   "WHERE id = ? AND status = 'active'", (ts(), rid)).rowcount
    if n:
        add_log(db, "warn", "Sistem", f"Admin tələbi bağladı: #{rid}", f"icra edən={g.user['email']}")
        flash(f"Tələb #{rid} bağlandı.", "ok")
    else:
        flash("Tələb aktiv deyil.", "err")
    db.commit()
    return redirect(url_for("admin.teleb", rid=rid))


@bp.route("/teleb/<int:rid>/sil", methods=["POST"])
@admin_required
def teleb_sil(rid):
    db = get_db()
    r = db.execute("SELECT id, deal_type FROM customer_requests WHERE id = ?", (rid,)).fetchone()
    if r is None:
        abort(404)
    counts = db.execute("SELECT (SELECT COUNT(*) FROM offers WHERE request_id = ?) AS o, "
                        "(SELECT COUNT(*) FROM contacts WHERE request_id = ?) AS c, "
                        "(SELECT COUNT(*) FROM complaints WHERE request_id = ?) AS k", (rid, rid, rid)).fetchone()
    public_ids = images_mod.public_ids_for(db, "o.request_id = ?", (rid,))
    # FK-lar CASCADE: təkliflər, şəkil sətirləri, kontaktlar, şikayətlər, bu tələbə bağlı bildirişlər birlikdə silinir
    db.execute("DELETE FROM notifications WHERE link = ?", (f"/kabinet/teleb/{rid}",))
    db.execute("DELETE FROM customer_requests WHERE id = ?", (rid,))
    add_log(db, "warn", "Sistem", f"Admin tələbi sildi: #{rid}",
            f"təklif={counts['o']} kontakt={counts['c']} şikayət={counts['k']} şəkil={len(public_ids)}"
            f"\nicra edən={g.user['email']}")
    db.commit()
    # Cloudinary bazadan SONRA; xəta axını sındırmır, loqa yazılır (qalan yetimləri həftəlik təmizlik silir)
    if public_ids and not images_mod.destroy(public_ids, db):
        db.commit()
    flash(f"Tələb #{rid} və əlaqəli məlumatlar silindi.", "ok")
    return redirect(url_for("admin.telebler"))


# ---------------------------------------------------------------- maklerlər
@bp.route("/maklerler")
@admin_required
def maklerler():
    db = get_db()
    rows = db.execute(
        "SELECT u.*, l.code AS lic_code, l.status AS lic_status, l.expires_at, "
        "(SELECT COUNT(*) FROM devices d WHERE d.user_id = u.id) AS n_devices, "
        "(SELECT COUNT(*) FROM offers o WHERE o.agent_id = u.id AND o.status != 'withdrawn') AS n_offers, "
        "(SELECT COUNT(*) FROM offers o WHERE o.agent_id = u.id AND o.status = 'accepted') AS n_accepted "
        "FROM users u LEFT JOIN licenses l ON l.id = u.license_id WHERE u.role = 'makler' ORDER BY u.id DESC").fetchall()
    stamp = ts()
    items = []
    for u in rows:
        lic_ok = u["lic_status"] == "used" and u["expires_at"] and u["expires_at"] > stamp
        items.append({"u": u, "name": full_name(u) + (" (demo)" if u["is_demo"] else ""),
                      "phone": phone_display(u["phone"]), "lic_ok": lic_ok,
                      "expires": date_short(u["expires_at"]) if u["expires_at"] else "—",
                      "lic_label": "Ləğv edilib" if u["lic_status"] == "revoked" else ("Aktiv" if lic_ok else "Bitib")})
    real = [x for x in items if not x["u"]["is_demo"]]  # göstəricilər demo-suz
    kpi = {"all": len(real), "active": sum(1 for x in real if x["u"]["status"] == "active" and x["lic_ok"]),
           "susp": sum(1 for x in real if x["u"]["status"] == "suspended"),
           "expired": sum(1 for x in real if not x["lic_ok"])}
    return render("admin/maklerler.html", "admin.maklerler", items=items, kpi=kpi)


def _makler(db, uid):
    u = db.execute("SELECT * FROM users WHERE id = ? AND role = 'makler'", (uid,)).fetchone()
    if u is None:
        abort(404)
    return u


@bp.route("/makler/<int:uid>/status", methods=["POST"])
@admin_required
def makler_status(uid):
    db = get_db()
    u = _makler(db, uid)
    status = request.form.get("status")
    if status not in ("active", "suspended"):
        abort(400)
    db.execute("UPDATE users SET status = ? WHERE id = ?", (status, uid))
    if status == "suspended":
        db.execute("UPDATE sessions SET active = 0 WHERE user_id = ? AND active = 1", (uid,))
    add_log(db, "warn" if status == "suspended" else "info", "Sistem",
            f"Makler {'dayandırıldı' if status == 'suspended' else 'aktivləşdirildi'}: {full_name(u)}",
            f"email={u['email']}\nicra edən={g.user['email']}")
    db.commit()
    flash(f"{full_name(u)} {'dayandırıldı' if status == 'suspended' else 'aktivləşdirildi'}.", "ok")
    return _back(url_for("admin.maklerler"))


@bp.route("/makler/<int:uid>/cihazlar", methods=["POST"])
@admin_required
def makler_cihazlar(uid):
    db = get_db()
    u = _makler(db, uid)
    n = licenses.reset_devices(db, uid)
    add_log(db, "info", "Sistem", f"Cihazlar sıfırlandı: {full_name(u)}", f"silinən={n}\nicra edən={g.user['email']}")
    db.commit()
    flash(f"{full_name(u)}: {n} cihaz silindi, aktiv sessiyalar bağlandı.", "ok")
    return _back(url_for("admin.maklerler"))


@bp.route("/makler/<int:uid>/uzat", methods=["POST"])
@admin_required
def makler_uzat(uid):
    db = get_db()
    u = _makler(db, uid)
    try:
        days = int(request.form.get("days") or 0)
    except ValueError:
        days = 0
    if not 1 <= days <= 3650:
        flash("Gün sayı 1–3650 aralığında olmalıdır.", "err")
        return _back(url_for("admin.maklerler"))
    lic = db.execute("SELECT * FROM licenses WHERE id = ?", (u["license_id"],)).fetchone() if u["license_id"] else None
    if lic is None or lic["status"] == "revoked":
        flash("Lisenziya yoxdur və ya ləğv edilib — yeni kod verin.", "err")
        return _back(url_for("admin.maklerler"))
    base = max(parse(lic["expires_at"]) or now(), now())
    new_exp = ts(base + timedelta(days=days))
    db.execute("UPDATE licenses SET status = 'used', expires_at = ? WHERE id = ?", (new_exp, lic["id"]))
    add_log(db, "info", "Sistem", f"Lisenziya uzadıldı: {full_name(u)} (+{days} gün)",
            f"kod={lic['code']}\nyeni bitmə={new_exp}\nicra edən={g.user['email']}")
    db.commit()
    flash(f"{full_name(u)}: lisenziya {date_short(new_exp)} tarixinə qədər uzadıldı.", "ok")
    return _back(url_for("admin.maklerler"))


# ---------------------------------------------------------------- şikayətlər
@bp.route("/sikayetler")
@admin_required
def sikayetler():
    db = get_db()
    status = request.args.get("status", "new")
    status = status if status in COMPLAINT_STATUS or status == "all" else "new"
    sql = ("SELECT c.*, o.title AS offer_title, u.first_name, u.last_name, u.status AS agent_status "
           "FROM complaints c LEFT JOIN offers o ON o.id = c.offer_id LEFT JOIN users u ON u.id = c.agent_id")
    params = ()
    if status != "all":
        sql += " WHERE c.status = ?"
        params = (status,)
    rows = db.execute(sql + " ORDER BY c.id DESC LIMIT 300", params).fetchall()
    counts = {r["status"]: r["c"] for r in db.execute("SELECT status, COUNT(*) c FROM complaints GROUP BY status")}
    return render("admin/sikayetler.html", "admin.sikayetler", items=rows, status=status, counts=counts,
                  labels=COMPLAINT_STATUS, reasons=COMPLAINT_REASONS, rel=rel, full_name=full_name)


@bp.route("/sikayet/<int:cid>/baxildi", methods=["POST"])
@admin_required
def sikayet_baxildi(cid):
    db = get_db()
    if db.execute("UPDATE complaints SET status = 'reviewed' WHERE id = ? AND status = 'new'", (cid,)).rowcount:
        add_log(db, "info", "Sistem", f"Şikayətə baxıldı: #{cid}", f"icra edən={g.user['email']}")
        db.commit()
        flash(f"Şikayət #{cid} baxılmış kimi qeyd edildi.", "ok")
    return _back(url_for("admin.sikayetler"))


# ---------------------------------------------------------------- loqlar, ayarlar
@bp.route("/loglar")
@admin_required
def loglar():
    return render("admin/loglar.html", "admin.loglar")


@bp.route("/ayarlar")
@admin_required
def ayarlar():
    changed = g.user["password_changed_at"]
    last_at, last_stats = maintenance.last_cleanup(get_db())
    c = contact_mod.get()
    form = session.pop("contact_form", None) or {"name": c["name"], "phone": c["phone_display"], "email": c["email"]}
    return render("admin/ayarlar.html", "admin.ayarlar", pw_changed=rel(changed) if changed else "məlum deyil",
                  contact_form=form,
                  cleanup_when=rel(last_at) if last_at else None, cleanup_at=last_at, cleanup_stats=last_stats)


@bp.route("/ayarlar/elaqe", methods=["POST"])
@admin_required
def elaqe():
    """Admin əlaqəsi (settings): ad, telefon (+994), e-poçt. notify_email da eyni e-poçta bağlanır."""
    from .auth import normalize_email, valid_email
    name = (request.form.get("name") or "").strip()
    raw_phone = (request.form.get("phone") or "").strip()
    email = normalize_email(request.form.get("email"))
    phone = normalize_phone(raw_phone)
    errors = []
    if not name or len(name) > 60:
        errors.append("Ad boş və ya 60 simvoldan uzun ola bilməz.")
    if not phone:
        errors.append("Telefon düzgün deyil (məsələn: +994 50 123 45 67).")
    if not valid_email(email) or len(email) > 120:
        errors.append("E-poçt ünvanı düzgün deyil.")
    if errors:
        session["contact_form"] = {"name": name, "phone": raw_phone, "email": request.form.get("email") or ""}
        flash(" ".join(errors), "err")
        return redirect(url_for("admin.ayarlar"))
    db = get_db()
    old = contact_mod.build(get_settings(db))
    for key, value in (("admin_contact_name", name), ("admin_contact_phone", phone), ("admin_contact_email", email),
                       ("notify_email", email)):
        set_setting(db, key, value)
    changes = [f"{label}: {old[k]} → {new}" for label, k, new in
               (("ad", "name", name), ("telefon", "phone", phone), ("e-poçt", "email", email)) if old[k] != new]
    add_log(db, "info", "Sistem", "Admin əlaqəsi dəyişdirildi" if changes else "Admin əlaqəsi yadda saxlanıldı (dəyişiklik yox)",
            "\n".join(changes + [f"icra edən={g.user['email']}"]))
    db.commit()
    g.pop("_admin_contact", None)
    flash("Əlaqə məlumatı yeniləndi — bütün səhifələrdə göstərilir.", "ok")
    return redirect(url_for("admin.ayarlar"))


@bp.route("/ayarlar/temizlik", methods=["POST"])
@admin_required
def temizlik():
    """Təmizliyi indi işlət (saatlıq məhdudiyyətə baxmadan)."""
    db = get_db()
    counts = maintenance.run_cleanup(db)
    total = sum(counts.values())
    details = "\n".join(f"{maintenance.LABELS[k]}: {v}" for k, v in counts.items())
    add_log(db, "info", "Sistem", f"Təmizlik əl ilə işlədildi: {total} sətir silindi",
            f"{details}\nicra edən={g.user['email']}")
    db.commit()
    flash(f"Təmizlik tamamlandı: {total} sətir silindi.", "ok")
    return redirect(url_for("admin.ayarlar"))


# ---------------------------------------------------------------- lisenziya kodları
@bp.route("/lisenziyalar")
@admin_required
def lisenziyalar():
    db = get_db()
    status = request.args.get("status", "all")
    sql = ("SELECT l.*, u.first_name, u.last_name, u.email FROM licenses l LEFT JOIN users u ON u.id = l.user_id")
    params = ()
    if status in licenses.STATUS_LABELS:
        sql += " WHERE l.status = ?"
        params = (status,)
    stamp = ts()
    rows = []
    for r in db.execute(sql + " ORDER BY l.id DESC LIMIT 500", params).fetchall():
        expired = r["status"] == "used" and r["expires_at"] and r["expires_at"] <= stamp
        rows.append({"l": r, "owner": full_name(r) if r["email"] else None,
                     "label": "Bitib" if expired else licenses.STATUS_LABELS.get(r["status"], r["status"]),
                     "pill": "susp" if expired or r["status"] == "revoked" else
                             "active" if r["status"] == "used" else "trial",
                     "created": date_short(r["created_at"]), "activated": date_short(r["activated_at"]),
                     "expires": date_short(r["expires_at"])})
    counts = {r["status"]: r["c"] for r in db.execute("SELECT status, COUNT(*) c FROM licenses GROUP BY status")}
    return render("admin/lisenziyalar.html", "admin.lisenziyalar", rows=rows, counts=counts, status=status,
                  labels=licenses.STATUS_LABELS, default_days=licenses.DEFAULT_DAYS, max_batch=licenses.MAX_BATCH)


@bp.route("/lisenziyalar/yarat", methods=["POST"])
@admin_required
def lisenziya_yarat():
    try:
        count = int(request.form.get("count") or 1)
        days = int(request.form.get("days") or licenses.DEFAULT_DAYS)
    except ValueError:
        flash("Say və müddət rəqəm olmalıdır.", "err")
        return redirect(url_for("admin.lisenziyalar"))
    note = (request.form.get("note") or "").strip()[:200]
    if not 1 <= count <= licenses.MAX_BATCH:
        flash(f"Say 1–{licenses.MAX_BATCH} aralığında olmalıdır.", "err")
    elif not 1 <= days <= 3650:
        flash("Müddət 1–3650 gün aralığında olmalıdır.", "err")
    else:
        db = get_db()
        codes = licenses.create_licenses(db, count, days, note)
        add_log(db, "info", "Sistem", f"{count} lisenziya kodu yaradıldı ({days} gün)",
                f"icra edən={g.user['email']}" + (f"\nqeyd={note}" if note else ""))
        db.commit()
        flash(f"{count} kod yaradıldı ({days} gün): " + ", ".join(codes), "ok")
    return redirect(url_for("admin.lisenziyalar"))


@bp.route("/lisenziyalar/<int:lid>/legv", methods=["POST"])
@admin_required
def lisenziya_legv(lid):
    db = get_db()
    lic = db.execute("SELECT code FROM licenses WHERE id = ?", (lid,)).fetchone()
    if lic and licenses.revoke(db, lid):
        add_log(db, "warn", "Sistem", f"Lisenziya ləğv edildi: {lic['code']}", f"icra edən={g.user['email']}")
        db.commit()
        flash(f"{lic['code']} ləğv edildi.", "ok")
    else:
        flash("Kod tapılmadı və ya artıq ləğv edilib.", "err")
    return redirect(url_for("admin.lisenziyalar", status=request.args.get("status", "all")))


# ---------------------------------------------------------------- loqlar API
@bp.route("/api/logs")
@admin_required
def api_logs():
    db = get_db()
    rows = [[log_time(r["created_at"]), r["level"], r["source"], r["message"], r["details"]]
            for r in db.execute("SELECT * FROM logs ORDER BY created_at DESC, id DESC LIMIT 2000")]
    since = ts(now() - timedelta(hours=24))
    counts = {r["level"]: r["c"] for r in db.execute(
        "SELECT level, COUNT(*) c FROM logs WHERE created_at >= ? GROUP BY level", (since,))}
    kpi = {"all": sum(counts.values()), "ok": counts.get("ok", 0), "warn": counts.get("warn", 0),
           "err": counts.get("err", 0)}
    return jsonify(ok=True, rows=rows, kpi=kpi)
