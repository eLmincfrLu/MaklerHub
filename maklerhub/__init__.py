"""MaklerHub — daşınmaz əmlak maklerləri üçün elan izləmə və uyğunlaşdırma platforması."""
import logging
import os

from flask import Blueprint, Flask, g, render_template, send_from_directory

from . import auth, db as dbmod, helpers
from .config import Config

public = Blueprint("public", __name__)


@public.route("/")
def landing():
    con = dbmod.get_db()
    sources = con.execute("SELECT COUNT(*) FROM sources WHERE enabled = 1").fetchone()[0]
    return render_template("landing.html", source_count=sources)

@public.route("/saglamliq")
def health():
    """UptimeRobot üçün: bazaya toxunmur, ona görə Neon-u oyatmır."""
    return "ok", 200, {"Cache-Control": "no-store"}


SCRAPER_STALE_MIN = 45      # son uğurlu sinxronizasiya bundan köhnədirsə → 503
SCRAPER_HEALTH_TTL = 600    # nəticə 10 dəq yaddaşda (Neon-u hər yoxlamada oyatmamaq üçün)
_scraper_health = {"at": 0.0, "result": None}


def scraper_health_check(con):
    """(JSON, HTTP kodu): aktiv Bina.az / Tap.az üçün son uğurlu sync_runs və cooldown vəziyyəti."""
    import json
    stamp = helpers.now()
    body = {"ok": True, "checked_at": helpers.ts(stamp), "sources": {}, "cooldown_until": None}
    for s in con.execute("SELECT id, key FROM sources WHERE enabled = 1 AND key IN ('bina', 'tap') ORDER BY sort"):
        last = con.execute("SELECT MAX(started_at) FROM sync_runs WHERE source_id = ? AND ok = 1",
                           (s["id"],)).fetchone()[0]
        age = round((stamp - helpers.parse(last)).total_seconds() / 60) if last else None
        fresh = age is not None and age <= SCRAPER_STALE_MIN
        body["sources"][s["key"]] = {"last_ok": last, "age_min": age, "fresh": fresh}
        body["ok"] = body["ok"] and fresh
    row = con.execute("SELECT value FROM settings WHERE key = 'scraper_state'").fetchone()
    state = json.loads(row["value"]) if row else {}
    until = helpers.parse(state.get("cooldown_until"))
    if until and until > stamp:
        body.update(ok=False, cooldown_until=state["cooldown_until"], reason=state.get("reason"))
    return body, 200 if body["ok"] else 503


@public.route("/saglamliq/scraper")
def scraper_health():
    """UptimeRobot üçün: scraper işləyirmi. Bazaya yalnız bu link açılanda və 10 dəqiqədə bir dəfə toxunur."""
    import time
    from flask import jsonify
    cache = _scraper_health
    if cache["result"] is None or time.time() - cache["at"] > SCRAPER_HEALTH_TTL:
        cache["result"] = scraper_health_check(dbmod.get_db())
        cache["at"] = time.time()
    body, code = cache["result"]
    return jsonify(body), code, {"Cache-Control": "no-store"}


@public.route("/media/avatars/<path:filename>")
def avatar(filename):
    from flask import current_app
    return send_from_directory(current_app.config["UPLOAD_DIR"], filename, max_age=3600)


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

    helpers.set_timezone(app.config["TIMEZONE"])
    helpers.init_app(app)
    dbmod.init_app(app)
    auth.init_app(app)

    from .admin import bp as admin_bp
    from .makler import bp as makler_bp

    app.register_blueprint(public)
    app.register_blueprint(auth.bp)
    app.register_blueprint(makler_bp)
    app.register_blueprint(admin_bp)

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("error.html", code=403, title="Giriş qadağandır",
                               text="Bu səhifəyə baxmaq üçün icazəniz yoxdur."), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("error.html", code=404, title="Səhifə tapılmadı",
                               text="Axtardığınız səhifə mövcud deyil və ya köçürülüb."), 404

    @app.errorhandler(500)
    def server_error(_e):
        return render_template("error.html", code=500, title="Xəta baş verdi",
                               text="Gözlənilməz xəta baş verdi. Bir az sonra yenidən cəhd edin."), 500

    @app.context_processor
    def inject_user():
        return {"current_user": g.get("user")}

    # Fon sinxronizasiyası (Flask debug reloader-də yalnız bir dəfə işə düşür)
    if app.config["SCHEDULER"] and not app.testing:
        if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
            from .sources import start_scheduler
            start_scheduler(app)

    return app
