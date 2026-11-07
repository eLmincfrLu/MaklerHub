"""Admin panel. Gün 1: placeholder səhifələr və sistem loqları (lisenziya/makler idarəsi sonrakı günlərdə)."""
from datetime import timedelta

from flask import Blueprint, jsonify, render_template

from .auth import admin_required
from .db import get_db
from .helpers import log_time, now, ts

bp = Blueprint("admin", __name__, url_prefix="/admin")

NAV = [("admin.panel", "Ümumi baxış"), ("admin.maklerler", "Maklerlər"), ("admin.loglar", "Sistem loqları"),
       ("admin.ayarlar", "Ayarlar")]


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
