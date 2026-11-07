"""Giriş, çıxış, şifrə bərpası, qeydiyyat (placeholder), CSRF qoruması və icazə dekoratorları."""
import functools
import re
import secrets
import smtplib
import time
from datetime import timedelta
from email.message import EmailMessage

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   session, url_for)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from .db import add_log, get_db, get_setting
from .helpers import full_name, now, ts

bp = Blueprint("auth", __name__)

ROLES = {"admin", "makler"}


# ---------------------------------------------------------------- köməkçilər
def normalize_email(value):
    e = (value or "").strip().lower()
    if "@" in e:
        local, domain = e.rsplit("@", 1)
        if "xn--" in domain:  # brauzer IDN domeni punycode-a çevirə bilər (nümunə.az)
            try:
                domain = domain.encode("ascii").decode("idna")
            except (UnicodeError, ValueError):
                pass
        e = f"{local}@{domain}"
    return e


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_email(e):
    return bool(EMAIL_RE.match(e or ""))


def password_problem(pw):
    if len(pw or "") < 8:
        return "Şifrə minimum 8 simvol olmalıdır."
    if not any(ch.isdigit() for ch in pw):
        return "Şifrədə ən azı bir rəqəm olmalıdır."
    return None


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def is_api_request():
    return request.path.startswith("/api/") or request.path.startswith("/admin/api/") \
        or request.accept_mimetypes.best == "application/json"


def home_url(user):
    return url_for("admin.panel") if user["role"] == "admin" else url_for("makler.kabinet")


def _safe_next(target):
    return target if target and target.startswith("/") and not target.startswith("//") else None


def login_user(user, remember=False):
    session.clear()
    session["uid"] = user["id"]
    session["seen"] = time.time()
    session.permanent = bool(remember)
    csrf_token()


# ---------------------------------------------------------------- dekoratorlar
def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            if is_api_request():
                return jsonify(ok=False, error="Sessiya bitib. Yenidən daxil olun."), 401
            return redirect(url_for("auth.giris", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def member_required(view):
    """Makler kabineti (admin öz panelinə yönləndirilir)."""
    @functools.wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["role"] == "admin":
            if is_api_request():
                return jsonify(ok=False, error="Bu bölmə maklerlər üçündür."), 403
            return redirect(url_for("admin.panel"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @functools.wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------- sorğu öncəsi
def init_app(app):
    app.permanent_session_lifetime = timedelta(days=30)
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def load_user_and_check_csrf():
        g.user = None
        uid = session.get("uid")
        if uid:
            db = get_db()
            user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
            if user is None:
                session.clear()
            elif user["status"] == "suspended":
                session.clear()
                flash("Hesabınız dayandırılıb. Administratorla əlaqə saxlayın.", "err")
            else:
                if user["role"] == "admin":
                    limit = int(get_setting(db, "session_timeout") or 120) * 60
                    last = session.get("seen")
                    if last and time.time() - last > limit:
                        session.clear()
                        flash("Sessiya vaxtı bitdi. Yenidən daxil olun.", "err")
                        if is_api_request():
                            return jsonify(ok=False, error="Sessiya vaxtı bitdi."), 401
                        return redirect(url_for("auth.giris"))
                session["seen"] = time.time()
                g.user = user

        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
            if not sent or sent != session.get("_csrf"):
                if is_api_request():
                    return jsonify(ok=False, error="Təhlükəsizlik tokeni etibarsızdır. Səhifəni yeniləyin."), 400
                flash("Sessiya yeniləndi, zəhmət olmasa yenidən cəhd edin.", "err")
                return redirect(request.path)


# ---------------------------------------------------------------- giriş
@bp.route("/giris", methods=["GET", "POST"])
def giris():
    if g.user:
        return redirect(home_url(g.user))
    email = ""
    error = None
    if request.method == "POST":
        db = get_db()
        email = normalize_email(request.form.get("email"))
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "on"
        limit = int(get_setting(db, "login_fail_limit") or 5)
        since = ts(now() - timedelta(minutes=15))
        fails = db.execute("SELECT COUNT(*) FROM login_attempts WHERE email = ? AND success = 0 AND created_at >= ?",
                           (email, since)).fetchone()[0]
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
        if fails >= limit:
            error = "Çox sayda uğursuz cəhd edildi. 15 dəqiqə sonra yenidən yoxlayın."
        else:
            user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if user and check_password_hash(user["password_hash"], password):
                if user["status"] == "suspended":
                    error = "Hesabınız dayandırılıb. Administratorla əlaqə saxlayın."
                else:
                    stamp = ts()
                    db.execute("INSERT INTO login_attempts(email, ip, success, created_at) VALUES (?, ?, 1, ?)",
                               (email, ip, stamp))
                    db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (stamp, user["id"]))
                    db.commit()
                    login_user(user, remember)
                    return redirect(_safe_next(request.args.get("next")) or home_url(user))
            else:
                db.execute("INSERT INTO login_attempts(email, ip, success, created_at) VALUES (?, ?, 0, ?)",
                           (email, ip, ts()))
                if fails + 1 >= limit:
                    masked = ".".join(ip.split(".")[:1] + ["xxx", "xx"] + ip.split(".")[3:]) if ip.count(".") == 3 else ip
                    add_log(db, "warn", "Makler", f"{limit} uğursuz giriş cəhdi: {email}",
                            f"IP={masked} cəhd={limit}\nhesab 15 dəq müddətinə bloklandı")
                db.commit()
                error = "E-poçt və ya şifrə yanlışdır."
    return render_template("auth/giris.html", email=email, error=error)


# ---------------------------------------------------------------- qeydiyyat
@bp.route("/qeydiyyat")
def qeydiyyat():
    """Makler qeydiyyatı lisenziya kodu ilə olacaq (Gün 2). Hələlik məlumat səhifəsi."""
    if g.user:
        return redirect(home_url(g.user))
    return render_template("auth/qeydiyyat.html")


@bp.route("/cixis")
def cixis():
    session.clear()
    return redirect(url_for("public.landing"))


# ---------------------------------------------------------------- şifrə bərpası
def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="maklerhub-reset")


def _send_mail(to, subject, body):
    cfg = current_app.config
    if not cfg.get("MAIL_SERVER"):
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, cfg["MAIL_FROM"], to
    msg.set_content(body)
    with smtplib.SMTP(cfg["MAIL_SERVER"], cfg["MAIL_PORT"], timeout=15) as smtp:
        if cfg.get("MAIL_TLS"):
            smtp.starttls()
        if cfg.get("MAIL_USERNAME"):
            smtp.login(cfg["MAIL_USERNAME"], cfg["MAIL_PASSWORD"])
        smtp.send_message(msg)
    return True


@bp.route("/sifre-berpa", methods=["GET", "POST"])
def sifre_berpa():
    sent = False
    email = ""
    if request.method == "POST":
        db = get_db()
        email = normalize_email(request.form.get("email"))
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user:
            token = _serializer().dumps({"id": user["id"], "h": user["password_hash"][-12:]})
            link = url_for("auth.sifre_yenile", token=token, _external=True)
            try:
                delivered = _send_mail(email, "MaklerHub — şifrə bərpası",
                                       f"Salam, {user['first_name']}!\n\nYeni şifrə təyin etmək üçün linkə keçin "
                                       f"(1 saat etibarlıdır):\n{link}\n\nMaklerHub")
            except Exception as exc:  # SMTP xətası
                delivered = False
                add_log(db, "err", "Sistem", f"Bildiriş göndərilməsi uğursuz oldu ({exc.__class__.__name__})",
                        f"şifrə bərpası email={email}")
            if not delivered:
                current_app.logger.warning("ŞİFRƏ BƏRPA LİNKİ (%s): %s", email, link)
            add_log(db, "info", "Sistem", f"Şifrə bərpası tələb olundu: {email}",
                    "link e-poçta göndərildi" if delivered else "SMTP qurulmayıb — link server konsoluna yazıldı")
            db.commit()
        sent = True
    return render_template("auth/sifre_berpa.html", sent=sent, email=email)


@bp.route("/sifre-yenile/<token>", methods=["GET", "POST"])
def sifre_yenile(token):
    db = get_db()
    try:
        data = _serializer().loads(token, max_age=3600)
    except SignatureExpired:
        flash("Bərpa linkinin vaxtı bitib. Yenidən tələb edin.", "err")
        return redirect(url_for("auth.sifre_berpa"))
    except BadSignature:
        abort(404)
    user = db.execute("SELECT * FROM users WHERE id = ?", (data.get("id"),)).fetchone()
    if not user or user["password_hash"][-12:] != data.get("h"):
        flash("Bu link artıq istifadə olunub.", "err")
        return redirect(url_for("auth.sifre_berpa"))
    error = None
    if request.method == "POST":
        pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
        error = password_problem(pw) or (None if pw == pw2 else "Şifrələr üst-üstə düşmür.")
        if not error:
            db.execute("UPDATE users SET password_hash = ?, password_changed_at = ? WHERE id = ?",
                       (generate_password_hash(pw), ts(), user["id"]))
            db.commit()
            flash("Şifrəniz yeniləndi. İndi daxil ola bilərsiniz.", "ok")
            return redirect(url_for("auth.giris"))
    return render_template("auth/sifre_yenile.html", error=error, name=user["first_name"])


# ---------------------------------------------------------------- şifrə dəyişmə (kabinet və admin)
@bp.route("/api/password", methods=["POST"])
@login_required
def change_password():
    data = request.get_json(silent=True) or {}
    current, new, new2 = data.get("current", ""), data.get("new", ""), data.get("new2", "")
    if not check_password_hash(g.user["password_hash"], current):
        return jsonify(ok=False, error="Cari şifrə yanlışdır."), 400
    problem = password_problem(new)
    if problem:
        return jsonify(ok=False, error=problem), 400
    if new != new2:
        return jsonify(ok=False, error="Yeni şifrələr üst-üstə düşmür."), 400
    db = get_db()
    db.execute("UPDATE users SET password_hash = ?, password_changed_at = ? WHERE id = ?",
               (generate_password_hash(new), ts(), g.user["id"]))
    add_log(db, "info", "Sistem", f"Şifrə dəyişdirildi: {full_name(g.user)}", f"email={g.user['email']}")
    db.commit()
    return jsonify(ok=True, message="Şifrə yeniləndi")
