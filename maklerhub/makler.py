"""Makler kabineti. Gün 1: yalnız placeholder səhifələr və bildiriş zəngi (tələblər/təkliflər sonrakı günlərdə)."""
from flask import Blueprint, g, jsonify, render_template

from .auth import member_required
from .db import get_db
from .helpers import long_date, rel

bp = Blueprint("makler", __name__)

NAV = [("makler.kabinet", "Kabinet"), ("makler.ayarlar", "Ayarlar")]


def _unread(db, user_id):
    return db.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0",
                      (user_id,)).fetchone()[0]


def render(template, active, **ctx):
    return render_template(template, nav=NAV, active=active, unread=_unread(get_db(), g.user["id"]), **ctx)


# ---------------------------------------------------------------- səhifələr
@bp.route("/kabinet")
@member_required
def kabinet():
    return render("makler/kabinet.html", "makler.kabinet", today_long=long_date())


@bp.route("/ayarlar")
@member_required
def ayarlar():
    return render("makler/ayarlar.html", "makler.ayarlar")


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
