"""MaklerHub — müştəri tələbləri və makler təklifləri platforması (Bakı, daşınmaz əmlak)."""
import logging
import os

from flask import Blueprint, Flask, g, render_template, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

from . import auth, contact, db as dbmod, demo, helpers, maintenance, monitoring
from .config import Config

public = Blueprint("public", __name__)


@public.route("/")
def landing():
    return render_template("landing.html")


@public.route("/sertler")
def sertler():
    return render_template("public/sertler.html")  # əlaqə: admin_contact (contact.py, settings)


@public.route("/mexfilik")
def mexfilik():
    return render_template("public/mexfilik.html")


@public.route("/saglamliq")
def health():
    """UptimeRobot üçün: bazaya toxunmur, ona görə Neon-u oyatmır."""
    return "ok", 200, {"Cache-Control": "no-store"}


ROBOTS = "\n".join([
    "User-agent: *",
    "Allow: /",
    "Disallow: /teleb/",
    "Disallow: /kabinet",
    "Disallow: /admin",
    "Disallow: /giris",
    "Disallow: /qeydiyyat",
    "",
])


@public.route("/robots.txt")
def robots():
    return ROBOTS, 200, {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "public, max-age=86400"}


def security_headers(response):
    """Bütün cavablara: nosniff, iframe qadağası, referrer siyasəti (görünüş özü təyin edibsə — məs. /teleb/<token>
    no-referrer — toxunulmur), proxy (Render, https) arxasında HSTS."""
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if os.environ.get("MAKLERHUB_BEHIND_PROXY") == "1":
        h.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


@public.route("/media/avatars/<path:filename>")
def avatar(filename):
    from flask import current_app
    return send_from_directory(current_app.config["UPLOAD_DIR"], filename, max_age=3600)


def create_app(overrides=None):
    app = Flask(__name__)
    # ProxyFix yalnız sxem (https) üçün. Müştəri IP-si X-Forwarded-For-dan YOX, auth.client_ip()-dən gəlir
    # (Cloudflare → Render: XFF 3 hop-dur, x_for=1 Render-in daxili IP-sini verirdi).
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    monitoring.init_sentry()  # yalnız SENTRY_DSN varsa

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)

    helpers.set_timezone(app.config["TIMEZONE"])
    helpers.init_app(app)
    dbmod.init_app(app)
    auth.init_app(app)
    contact.init_app(app)
    maintenance.init_app(app)
    demo.init_app(app)
    app.after_request(security_headers)

    from .admin import bp as admin_bp
    from .makler import bp as makler_bp
    from .teleb import bp as teleb_bp

    app.register_blueprint(public)
    app.register_blueprint(auth.bp)
    app.register_blueprint(makler_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(teleb_bp)

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("error.html", code=403, title="Giriş qadağandır",
                               text="Bu səhifəyə baxmaq üçün icazəniz yoxdur."), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("error.html", code=404, title="Səhifə tapılmadı", teleb_link=True,
                               text="Bu səhifə mövcud deyil və ya köçürülüb. Ev axtarırsınızsa, tələbinizi yazın — "
                                    "maklerlər sizə uyğun təklif göndərsin."), 404

    @app.errorhandler(413)
    def too_large(_e):
        """Forma 26 MB-dan böyükdür. Gövdə oxunmadığı üçün dəyərləri qaytarmaq olmur — aydın mesaj."""
        from flask import flash, redirect, request
        msg = ("Şəkillər birlikdə çox böyükdür (forma 26 MB-dan çox). Daha az və ya kiçik şəkil seçib "
               "yenidən göndərin — forma sahələrini yenidən doldurmaq lazım ola bilər.")
        parts = request.path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["kabinet", "teleb"] and parts[2].isdigit() and parts[3] == "teklif":
            flash(msg, "err")
            return redirect(f"/kabinet/teleb/{parts[2]}"), 303
        return render_template("error.html", code=413, title="Fayl çox böyükdür", text=msg), 413

    @app.errorhandler(500)
    def server_error(_e):
        return render_template("error.html", code=500, title="Xəta baş verdi", teleb_link=True,
                               text="Gözlənilməz xəta baş verdi. Bir az sonra yenidən cəhd edin."), 500

    @app.context_processor
    def inject_user():
        return {"current_user": g.get("user")}

    return app
