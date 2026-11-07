"""MaklerHub — müştəri tələbləri və makler təklifləri platforması (Bakı, daşınmaz əmlak)."""
import logging
import os

from flask import Blueprint, Flask, g, render_template, send_from_directory

from . import auth, db as dbmod, helpers
from .config import Config

public = Blueprint("public", __name__)


@public.route("/")
def landing():
    return render_template("landing.html")


@public.route("/saglamliq")
def health():
    """UptimeRobot üçün: bazaya toxunmur, ona görə Neon-u oyatmır."""
    return "ok", 200, {"Cache-Control": "no-store"}


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

    return app
