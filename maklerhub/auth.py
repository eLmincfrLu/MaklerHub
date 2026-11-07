"""Giriş, lisenziya kodu ilə qeydiyyat, çıxış, şifrə bərpası, makler cihaz limiti və tək sessiya,
CSRF qoruması və icazə dekoratorları."""
import functools
import hashlib
import ipaddress
import os
import re
import secrets
import smtplib
import time
from datetime import timedelta
from email.message import EmailMessage

import psycopg
from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request,
                   session, url_for)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from . import licenses
from .db import add_log, get_db, get_setting
from .helpers import full_name, normalize_phone, now, ts

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


# CSRF uğursuz olanda yönləndirilməyən, g.csrf_failed ilə özü idarə edən görünüşlər
CSRF_SOFT_ENDPOINTS = {"teleb.forma"}


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


# Render-in qarşısında Cloudflare var (XFF: müştəri, Cloudflare, Render daxili — 3 hop). Cloudflare bu başlıqları
# özü yazır və müştərinin göndərdiyini əvəz edir, ona görə etibarlıdır. Yalnız MAKLERHUB_BEHIND_PROXY=1 olanda.
TRUSTED_IP_HEADERS = ("CF-Connecting-IP", "True-Client-IP")


def _valid_ip(value):
    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except ValueError:
        return None


def client_ip_source():
    """(IP, mənbə). Proxy rejimində: CF-Connecting-IP → True-Client-IP → remote_addr; etibarsız dəyər ötürülür.
    Lokalda bu başlıqlara baxılmır (saxtalaşdırıla bilər), yalnız remote_addr."""
    if os.environ.get("MAKLERHUB_BEHIND_PROXY") == "1":
        for header in TRUSTED_IP_HEADERS:
            ip = _valid_ip(request.headers.get(header))
            if ip:
                return ip, header
    return request.remote_addr or "", "remote_addr"


def client_ip():
    """Müştərinin IP-si. IP lazım olan hər yerdə yalnız bu funksiya istifadə olunur."""
    return client_ip_source()[0]


_ip_warned = False


def ip_limit_applies(ip):
    """IP əsaslı limitlər yalnız ictimai IP üçün. Loopback/private/boş IP — proxy başlıqları gəlməyib deməkdir;
    onda limit tətbiq olunmur (fail-open), yoxsa bütün müştərilər bir "IP"-də bloklanar. Bir dəfə warning yazılır."""
    global _ip_warned
    try:
        addr = ipaddress.ip_address(ip)
        public = not (addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_unspecified)
    except ValueError:
        public = False
    if not public and not _ip_warned:
        _ip_warned = True
        current_app.logger.warning("Müştəri IP-si ictimai deyil (%r): IP limitləri tətbiq olunmur. "
                                   "Proxy başlıqlarını yoxlayın (/admin/debug/ip).", ip)
    return public


def login_user(user, remember=False, session_token=None):
    session.clear()
    session["uid"] = user["id"]
    session["seen"] = time.time()
    if session_token:
        session["st"] = session_token
        session["st_seen"] = time.time()
    session.permanent = bool(remember)
    csrf_token()


# ---------------------------------------------------------------- makler: cihaz və tək sessiya
DEVICE_COOKIE = "mh_device"
DEVICE_COOKIE_AGE = 365 * 24 * 3600
TOUCH_EVERY = 300  # saniyə: sessions/devices.last_seen_at bundan tez-tez yazılmır
LICENSE_MSG = "Lisenziyanız bitib, admin ilə əlaqə saxlayın."
DEVICE_LIMIT_MSG = "Cihaz limiti dolub, admin ilə əlaqə saxlayın."


def _device_key(user_id, browser_id):
    """Bazada cookie-nin özü yox, istifadəçiyə bağlı heşi saxlanılır (bir brauzer — hər makler üçün ayrı cihaz)."""
    return hashlib.sha256(f"{user_id}:{browser_id}".encode()).hexdigest()


def _browser_id():
    value = request.cookies.get(DEVICE_COOKIE) or ""
    return value if 20 <= len(value) <= 100 else secrets.token_urlsafe(24)


def open_makler_session(db, user):
    """Cihazı yoxlayır/qeyd edir, köhnə sessiyaları bağlayıb yenisini açır.
    Qaytarır: (session_token, browser_id) və ya (None, xəta mətni)."""
    browser = _browser_id()
    key = _device_key(user["id"], browser)
    stamp, ip, ua = ts(), client_ip(), (request.user_agent.string or "")[:300]
    dev = db.execute("SELECT id FROM devices WHERE device_token = ?", (key,)).fetchone()
    if dev:
        device_id = dev["id"]
        db.execute("UPDATE devices SET last_seen_at = ?, ip = ?, user_agent = ? WHERE id = ?", (stamp, ip, ua, device_id))
    else:
        limit = int(get_setting(db, "max_devices") or 2)
        count = db.execute("SELECT COUNT(*) FROM devices WHERE user_id = ?", (user["id"],)).fetchone()[0]
        if count >= limit:
            add_log(db, "warn", "Makler", f"Cihaz limiti: {full_name(user)}",
                    f"email={user['email']}\ncihaz={count}/{limit}\nIP={ip}\n{ua[:120]}")
            db.commit()
            return None, DEVICE_LIMIT_MSG
        device_id = db.execute("INSERT INTO devices(user_id, device_token, user_agent, ip, created_at, last_seen_at) "
                               "VALUES (?,?,?,?,?,?)", (user["id"], key, ua, ip, stamp, stamp)).lastrowid
        if count:
            add_log(db, "info", "Makler", f"Yeni cihaz: {full_name(user)}", f"cihaz={count + 1}/{limit}\nIP={ip}")
    token = secrets.token_urlsafe(32)
    db.execute("UPDATE sessions SET active = 0 WHERE user_id = ? AND active = 1", (user["id"],))
    db.execute("INSERT INTO sessions(user_id, device_id, token, active, created_at, last_seen_at) VALUES (?,?,?,1,?,?)",
               (user["id"], device_id, token, stamp, stamp))
    return token, browser


def finish_login(db, user, remember=False):
    """Uğurlu yoxlamadan sonra: makler üçün cihaz + sessiya, sonra yönləndirmə. Alınmasa (None, xəta mətni)."""
    token = browser = None
    if user["role"] == "makler":
        token, browser = open_makler_session(db, user)
        if token is None:
            return None, browser
    db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (ts(), user["id"]))
    db.commit()
    login_user(user, remember, token)
    resp = redirect(_safe_next(request.args.get("next")) or home_url(user))
    if browser:
        resp.set_cookie(DEVICE_COOKIE, browser, max_age=DEVICE_COOKIE_AGE, httponly=True, samesite="Lax",
                        secure=current_app.config.get("SESSION_COOKIE_SECURE", False))
    return resp, None


def license_page(status=403):
    if is_api_request():
        return jsonify(ok=False, error=LICENSE_MSG), status
    return render_template("auth/lisenziya.html", message=LICENSE_MSG), status


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
def _logout(message):
    session.clear()
    flash(message, "err")
    if is_api_request():
        return jsonify(ok=False, error=message), 401
    return redirect(url_for("auth.giris"))


def _check_makler_session(db, user):
    """Makler sessiyası bazada aktiv olmalıdır (başqa cihazdan giriş köhnəni bağlayır)."""
    token = session.get("st")
    row = db.execute("SELECT id, device_id, active FROM sessions WHERE token = ? AND user_id = ?",
                     (token, user["id"])).fetchone() if token else None
    if row is None:
        return _logout("Sessiya bitib. Yenidən daxil olun.")
    if not row["active"]:
        return _logout("Hesabınıza başqa cihazdan daxil olundu.")
    if time.time() - session.get("st_seen", 0) > TOUCH_EVERY:
        stamp = ts()
        db.execute("UPDATE sessions SET last_seen_at = ? WHERE id = ?", (stamp, row["id"]))
        if row["device_id"]:
            db.execute("UPDATE devices SET last_seen_at = ? WHERE id = ?", (stamp, row["device_id"]))
        db.commit()
        session["st_seen"] = time.time()
    return None


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
                        return _logout("Sessiya vaxtı bitdi. Yenidən daxil olun.")
                else:
                    stop = _check_makler_session(db, user)
                    if stop:
                        return stop
                session["seen"] = time.time()
                g.user = user
                # lisenziyası bitmiş/ləğv olunmuş makler: kabinet və hesab API-ləri bağlıdır (çıxış açıqdır)
                if user["role"] == "makler" and (request.blueprint == "makler"
                                                 or request.endpoint == "auth.change_password"):
                    if licenses.license_problem(db, user):
                        return license_page()

        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
            if not sent or sent != session.get("_csrf"):
                if request.endpoint in CSRF_SOFT_ENDPOINTS:
                    # müştəri forması: yönləndirmə yox — görünüş özü formanı dəyərlərlə yenidən göstərir
                    session.pop("_csrf", None)
                    g.csrf_failed = True
                    return None
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
        ip = client_ip()
        if fails >= limit:
            error = "Çox sayda uğursuz cəhd edildi. 15 dəqiqə sonra yenidən yoxlayın."
        else:
            user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if user and check_password_hash(user["password_hash"], password):
                db.execute("INSERT INTO login_attempts(email, ip, success, created_at) VALUES (?, ?, 1, ?)",
                           (email, ip, ts()))
                db.commit()
                if user["status"] == "suspended":
                    error = "Hesabınız dayandırılıb. Administratorla əlaqə saxlayın."
                elif licenses.license_problem(db, user):
                    return license_page()
                else:
                    resp, error = finish_login(db, user, remember)
                    if resp:
                        return resp
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
CODE_FAIL_LIMIT = 10           # IP başına 15 dəqiqədə yanlış lisenziya kodu cəhdi
CODE_ATTEMPT_KEY = "#license"  # login_attempts.email sütununda kod cəhdlərinin işarəsi (e-poçt ola bilməz)


def _code_fails(db, ip):
    since = ts(now() - timedelta(minutes=15))
    return db.execute("SELECT COUNT(*) FROM login_attempts WHERE email = ? AND ip = ? AND success = 0 "
                      "AND created_at >= ?", (CODE_ATTEMPT_KEY, ip, since)).fetchone()[0]


def _code_attempt(db, ip, ok):
    db.execute("INSERT INTO login_attempts(email, ip, success, created_at) VALUES (?, ?, ?, ?)",
               (CODE_ATTEMPT_KEY, ip, 1 if ok else 0, ts()))


def _check_code(db, ip, raw):
    """(lisenziya sətri, None) və ya (None, xəta). Yanlış kod cəhdləri IP üzrə sayılır (yalnız ictimai IP)."""
    limited = ip_limit_applies(ip)
    if limited and _code_fails(db, ip) >= CODE_FAIL_LIMIT:
        return None, "Çox sayda yanlış kod cəhdi edildi. 15 dəqiqə sonra yenidən yoxlayın."
    code = licenses.normalize_code(raw)
    lic = db.execute("SELECT id, code, days, status FROM licenses WHERE code = ?", (code,)).fetchone() if code else None
    if lic is not None and lic["status"] == "new":
        return lic, None
    _code_attempt(db, ip, False)
    if limited and _code_fails(db, ip) >= CODE_FAIL_LIMIT:
        add_log(db, "warn", "Makler", "Lisenziya kodu cəhdləri bloklandı", f"IP={ip}\n{CODE_FAIL_LIMIT} yanlış kod / 15 dəq")
    db.commit()
    if lic is None:
        return None, "Lisenziya kodu yanlışdır."
    return None, ("Bu lisenziya kodu artıq istifadə olunub." if lic["status"] == "used"
                  else "Bu lisenziya kodu ləğv edilib.")


def _register(db, form, phone, pw, lic_id, ip):
    """Kod + istifadəçi + aktivləşdirmə bir tranzaksiyada. Qaytarır: (user_id, None) və ya (None, xəta)."""
    stamp = ts()
    try:
        # FOR UPDATE: eyni kodla paralel iki qeydiyyat ola bilməz
        lic = db.execute("SELECT id, code, days, status FROM licenses WHERE id = ? FOR UPDATE", (lic_id,)).fetchone()
        if lic["status"] != "new":
            db.rollback()
            return None, "Bu lisenziya kodu artıq istifadə olunub."
        uid = db.execute(
            "INSERT INTO users(first_name, last_name, email, phone, password_hash, role, status, license_id, "
            "created_at, password_changed_at) VALUES (?,?,?,?,?,'makler','active',?,?,?)",
            (form["fname"], form["lname"], form["email"], phone, generate_password_hash(pw), lic["id"],
             stamp, stamp)).lastrowid
        expires = licenses.activate(db, lic["id"], uid, lic["days"])
        _code_attempt(db, ip, True)
        add_log(db, "info", "Makler", f"Yeni makler qeydiyyatı: {form['fname']} {form['lname']}",
                f"email={form['email']}\nlisenziya={lic['code']} ({lic['days']} gün, bitir {expires[:10]})")
        db.commit()
        return uid, None
    except psycopg.errors.UniqueViolation:
        db.rollback()
        return None, "Bu e-poçt ilə artıq hesab mövcuddur."


@bp.route("/qeydiyyat", methods=["GET", "POST"])
def qeydiyyat():
    """Makler qeydiyyatı: yalnız admin verdiyi istifadə olunmamış lisenziya kodu ilə."""
    if g.user:
        return redirect(home_url(g.user))
    form = {}
    errors = []
    if request.method == "POST":
        db = get_db()
        ip = client_ip()
        form = {k: (request.form.get(k) or "").strip() for k in ("fname", "lname", "email", "phone", "license")}
        form["email"] = normalize_email(form["email"])
        pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
        if not form["fname"] or not form["lname"]:
            errors.append("Ad və soyad daxil edilməlidir.")
        elif len(form["fname"]) > 40 or len(form["lname"]) > 40:
            errors.append("Ad və soyad 40 simvoldan uzun ola bilməz.")
        if not valid_email(form["email"]):
            errors.append("E-poçt ünvanı düzgün deyil.")
        elif db.execute("SELECT 1 FROM users WHERE email = ?", (form["email"],)).fetchone():
            errors.append("Bu e-poçt ilə artıq hesab mövcuddur.")
        phone = normalize_phone(form["phone"])
        if not phone:
            errors.append("Telefon nömrəsi düzgün deyil (məsələn: +994 50 123 45 67).")
        problem = password_problem(pw)
        if problem:
            errors.append(problem)
        elif pw != pw2:
            errors.append("Şifrələr üst-üstə düşmür.")
        if request.form.get("terms") != "on":
            errors.append("İstifadə şərtləri ilə razılaşmalısınız.")
        lic, code_error = _check_code(db, ip, form["license"])
        if code_error:
            errors.append(code_error)

        if not errors:
            uid, error = _register(db, form, phone, pw, lic["id"], ip)
            if error:
                errors.append(error)
            else:
                user = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
                resp, error = finish_login(db, user)
                if resp:
                    return resp
                flash(error, "err")
                return redirect(url_for("auth.giris"))
    return render_template("auth/qeydiyyat.html", form=form, errors=errors)


@bp.route("/cixis")
def cixis():
    if session.get("st"):
        db = get_db()
        db.execute("UPDATE sessions SET active = 0 WHERE token = ?", (session["st"],))
        db.commit()
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
