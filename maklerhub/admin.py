"""Admin panel: lisenziya kodları (sadə bölmə), sistem loqları; qalan səhifələr placeholder (tam panel Gün 4-də)."""
import os
from datetime import timedelta

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, url_for

from . import licenses
from .auth import admin_required
from .db import add_log, get_db
from .helpers import date_short, full_name, log_time, now, ts

bp = Blueprint("admin", __name__, url_prefix="/admin")

NAV = [("admin.panel", "Ümumi baxış"), ("admin.maklerler", "Maklerlər"), ("admin.lisenziyalar", "Lisenziyalar"),
       ("admin.loglar", "Sistem loqları"), ("admin.ayarlar", "Ayarlar")]


def render(template, active, **ctx):
    return render_template(template, nav=NAV, active=active, **ctx)


# ---------------------------------------------------------------- səhifələr
@bp.route("")
@bp.route("/")
@admin_required
def panel():
    return render("admin/panel.html", "admin.panel")


@bp.route("/maklerler")
@admin_required
def maklerler():
    return render("admin/maklerler.html", "admin.maklerler")


@bp.route("/loglar")
@admin_required
def loglar():
    return render("admin/loglar.html", "admin.loglar")


@bp.route("/ayarlar")
@admin_required
def ayarlar():
    return render("admin/ayarlar.html", "admin.ayarlar")


# Köhnə "Mənbə statusu": fayl saxlanılır, içi "Tezliklə" (naviqasiyada görünmür)
@bp.route("/menbe")
@admin_required
def menbe():
    return render("admin/menbe.html", "admin.panel")


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


# ---------------------------------------------------------------- MÜVƏQQƏTİ: IP diaqnostikası
# Render-də real müştəri IP-si təsdiqlənəndən sonra silinəcək.
DEBUG_IP_HEADERS = ("X-Forwarded-For", "X-Forwarded-Proto", "X-Real-IP", "True-Client-IP", "CF-Connecting-IP")


@bp.route("/debug/ip")
@admin_required
def debug_ip():
    from .auth import client_ip, ip_limit_applies
    env = request.environ
    rows = [("request.remote_addr (ProxyFix-dən sonra)", request.remote_addr),
            ("client_ip()", client_ip()),
            ("IP limiti tətbiq olunur?", "bəli" if ip_limit_applies(client_ip()) else "XEYR (fail-open)"),
            ("ProxyFix-dən əvvəlki REMOTE_ADDR", (env.get("werkzeug.proxy_fix.orig") or {}).get("REMOTE_ADDR")),
            ("MAKLERHUB_BEHIND_PROXY", os.environ.get("MAKLERHUB_BEHIND_PROXY", "(yoxdur)"))]
    rows += [(h, request.headers.get(h)) for h in DEBUG_IP_HEADERS]
    body = "\n".join(f"{k}:{v if v is not None else '(yoxdur)'}" for k, v in rows)
    return body, 200, {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"}


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
