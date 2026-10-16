"""Admin panel: Ümumi baxış, Maklerlər, Mənbə statusu, Sistem loqları, Ayarlar və API-lər."""
import secrets
import string
from datetime import timedelta

from flask import Blueprint, g, jsonify, render_template, request, url_for
from werkzeug.security import generate_password_hash

from . import matching
from .auth import admin_required, normalize_email, valid_email
from .db import DEFAULT_SETTINGS, add_log, get_db, get_settings, set_setting
from .helpers import PLAN_LABELS, date_short, full_name, initials, log_time, now, parse, rel, ts
from .sources import source_metrics, sync_source

bp = Blueprint("admin", __name__, url_prefix="/admin")

NAV = [("admin.panel", "Ümumi baxış"), ("admin.maklerler", "Maklerlər"), ("admin.menbe", "Mənbə statusu"),
       ("admin.loglar", "Sistem loqları"), ("admin.ayarlar", "Ayarlar")]


def render(template, active, **ctx):
    return render_template(template, nav=NAV, active=active, **ctx)


def err(message, code=400):
    return jsonify(ok=False, error=message), code


def user_pill(u):
    if u["status"] == "suspended":
        return "susp", "Dayandırılıb", "susp"
    if u["status"] == "trial":
        ends = parse(u["trial_ends"])
        left = (ends - now()).total_seconds() / 86400 if ends else 0
        if left <= 0:
            return "susp", "Sınaq bitib", "trial"
        return "trial", f"Sınaq · {int(left + 0.999)} gün qalıb", "trial"
    return "active", "Aktiv", "active"


def makler_rows(db, limit=None):
    sql = ("SELECT u.*, (SELECT COUNT(*) FROM clients c WHERE c.user_id = u.id) AS clients FROM users u "
           "WHERE u.role = 'makler' ORDER BY u.created_at DESC")
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = []
    for u in db.execute(sql).fetchall():
        cls, label, key = user_pill(u)
        rows.append({"u": u, "name": full_name(u), "initials": initials(u["first_name"], u["last_name"]),
                     "date": date_short(u["created_at"]), "plan": PLAN_LABELS.get(u["plan"], "—"),
                     "pill_cls": cls, "pill": label, "key": key})
    return rows


def sources_view(db):
    out = []
    for s in db.execute("SELECT * FROM sources ORDER BY sort").fetchall():
        m = source_metrics(db, s)
        out.append({"s": s, "m": m, "logo": url_for("static", filename="img/" + s["logo"]),
                    "last": rel(s["last_sync"]) if s["last_sync"] else "hələ yoxdur"})
    return out


LEVEL_LABEL = {"ok": "OK", "warn": "DİQQƏT", "err": "XƏTA", "info": "MƏLUMAT"}


# ---------------------------------------------------------------- səhifələr
@bp.route("")
@bp.route("/")
@admin_required
def panel():
    db = get_db()
    sources = sources_view(db)
    kpi = {
        "maklers": db.execute("SELECT COUNT(*) FROM users WHERE role = 'makler'").fetchone()[0],
        "clients": db.execute("SELECT COUNT(*) FROM clients").fetchone()[0],
        "tracked": sum(x["m"]["today"] for x in sources if x["s"]["enabled"]),
        "profiles": db.execute("SELECT COUNT(*) FROM profiles WHERE status = 'active'").fetchone()[0],
    }
    logs = [{"t": r["created_at"][11:19], "lv": r["level"], "label": LEVEL_LABEL.get(r["level"], r["level"]),
             "msg": r["message"]} for r in db.execute("SELECT * FROM logs ORDER BY created_at DESC, id DESC LIMIT 8")]
    return render("admin/panel.html", "admin.panel", kpi=kpi, sources=sources, maklers=makler_rows(db, 4), logs=logs)


@bp.route("/maklerler")
@admin_required
def maklerler():
    db = get_db()
    rows = makler_rows(db)
    kpi = {"all": len(rows), "active": sum(1 for r in rows if r["u"]["status"] == "active"),
           "trial": sum(1 for r in rows if r["u"]["status"] == "trial"),
           "susp": sum(1 for r in rows if r["u"]["status"] == "suspended")}
    return render("admin/maklerler.html", "admin.maklerler", rows=rows, kpi=kpi)


@bp.route("/menbe")
@admin_required
def menbe():
    db = get_db()
    return render("admin/menbe.html", "admin.menbe", sources=sources_view(db),
                  settings=get_settings(db))


@bp.route("/loglar")
@admin_required
def loglar():
    return render("admin/loglar.html", "admin.loglar")


@bp.route("/ayarlar")
@admin_required
def ayarlar():
    db = get_db()
    changed = g.user["password_changed_at"]
    return render("admin/ayarlar.html", "admin.ayarlar", s=get_settings(db),
                  pw_changed=rel(changed) if changed else "məlum deyil")


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


# ---------------------------------------------------------------- ayarlar API
NUMERIC = {"rate_limit": (5, 120), "timeout": (3, 60), "retries": (0, 10), "trial_days": (0, 60),
           "plan_start_price": (0, 10000), "plan_start_clients": (1, 100000), "plan_start_profiles": (1, 100000),
           "plan_pro_price": (0, 10000), "plan_pro_clients": (1, 100000), "plan_pro_profiles": (1, 100000),
           "login_fail_limit": (3, 10)}
CHOICES = {"sync_interval": {"2", "5", "10", "15"}, "session_timeout": {"30", "120", "480"}}
BOOLS = {"auto_slow", "notify_source_down", "notify_new_makler", "notify_daily", "admin_2fa"}


@bp.route("/api/settings", methods=["POST"])
@admin_required
def api_settings():
    db = get_db()
    data = request.get_json(silent=True) or {}
    current = get_settings(db)
    changed = []
    for key, value in data.items():
        if key == "admin_name":
            name = str(value).strip()[:40]
            if not name:
                return err("Admin adı boş ola bilməz.")
            if name != g.user["first_name"]:
                db.execute("UPDATE users SET first_name = ? WHERE id = ?", (name, g.user["id"]))
                changed.append("admin_name")
            continue
        if key not in DEFAULT_SETTINGS:
            continue
        if key in NUMERIC:
            try:
                v = int(float(value))
            except (TypeError, ValueError):
                return err(f"'{key}' üçün rəqəm daxil edin.")
            lo, hi = NUMERIC[key]
            if not lo <= v <= hi:
                return err(f"Dəyər {lo}–{hi} aralığında olmalıdır.")
            value = str(v)
        elif key in CHOICES:
            if str(value) not in CHOICES[key]:
                return err("Yanlış seçim.")
            value = str(value)
        elif key in BOOLS:
            value = "1" if value in (True, 1, "1", "true", "on") else "0"
        elif key == "notify_email":
            value = normalize_email(value)
            if not valid_email(value):
                return err("Bildiriş e-poçtu düzgün deyil.")
        if current.get(key) != value:
            set_setting(db, key, value)
            changed.append(key)
    if changed:
        add_log(db, "info", "Sistem", "Platforma ayarları yeniləndi", "dəyişən: " + ", ".join(changed))
    db.commit()
    return jsonify(ok=True, message="Ayarlar yadda saxlanıldı", changed=changed)


@bp.route("/api/cache/clear", methods=["POST"])
@admin_required
def api_cache_clear():
    db = get_db()
    n = db.execute("SELECT COUNT(*) FROM listings WHERE active = 1").fetchone()[0]
    db.execute("DELETE FROM listings WHERE active = 1")
    db.execute("UPDATE sources SET rate_limit = NULL")
    add_log(db, "warn", "Sistem", "Elan keşi təmizləndi", f"silinən aktiv elan={n}\nicra edən={g.user['email']}")
    db.commit()
    return jsonify(ok=True, message=f"Keş təmizləndi — {n} elan silindi")


# ---------------------------------------------------------------- mənbə API
def _source(db, key):
    return db.execute("SELECT * FROM sources WHERE key = ?", (key,)).fetchone()


@bp.route("/api/sources/<key>/sync", methods=["POST"])
@admin_required
def api_source_sync(key):
    db = get_db()
    s = _source(db, key)
    if not s:
        return err("Mənbə tapılmadı.", 404)
    if not s["enabled"]:
        return err(f"{s['name']} deaktivdir. Əvvəlcə Tənzimləmələrdən aktivləşdirin.")
    res = sync_source(db, s, manual=True)
    if not res["ok"]:
        return err(f"{s['name']}: {res['error']}")
    return jsonify(ok=True, message=f"{s['name']} sinxronlaşdırıldı — {res['checked']} elan yoxlandı, "
                                    f"{res['new']} yeni tapıldı")


@bp.route("/api/sources/<key>", methods=["POST"])
@admin_required
def api_source_update(key):
    db = get_db()
    s = _source(db, key)
    if not s:
        return err("Mənbə tapılmadı.", 404)
    data = request.get_json(silent=True) or {}
    enabled = 1 if data.get("enabled") in (True, 1, "1", "on") else 0
    base_url = (data.get("base_url") or s["base_url"]).strip()[:200]
    if base_url and not base_url.startswith(("http://", "https://")):
        return err("URL http:// və ya https:// ilə başlamalıdır.")
    db.execute("UPDATE sources SET enabled = ?, base_url = ? WHERE id = ?", (enabled, base_url, s["id"]))
    if enabled != s["enabled"]:
        add_log(db, "info", s["name"], f"{s['name']} {'aktivləşdirildi' if enabled else 'dayandırıldı'}",
                f"icra edən={g.user['email']}")
        # təkrar qruplarında əsas nüsxə yalnız aktiv mənbələrdən seçilir
        matching.rematch_all(db)
    db.commit()
    return jsonify(ok=True, message="Mənbə ayarları yadda saxlanıldı")


# ---------------------------------------------------------------- makler API
def _gen_password():
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(10))
        if any(c.isdigit() for c in pw):
            return pw


def _makler(db, uid):
    return db.execute("SELECT * FROM users WHERE id = ? AND role = 'makler'", (uid,)).fetchone()


@bp.route("/api/users", methods=["POST"])
@admin_required
def api_user_create():
    db = get_db()
    d = request.get_json(silent=True) or {}
    first, last = (d.get("first_name") or "").strip(), (d.get("last_name") or "").strip()
    email = normalize_email(d.get("email"))
    if not first or not last:
        return err("Ad və soyad daxil edin.")
    if not valid_email(email):
        return err("E-poçt ünvanı düzgün deyil.")
    if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        return err("Bu e-poçt ilə hesab artıq mövcuddur.")
    plan = d.get("plan") if d.get("plan") in ("start", "pro") else "pro"
    status = "trial" if d.get("status") == "trial" else "active"
    days = int(get_settings(db)["trial_days"])
    pw = (d.get("password") or "").strip() or _gen_password()
    if len(pw) < 8 or not any(c.isdigit() for c in pw):
        return err("Şifrə minimum 8 simvol və ən azı bir rəqəm olmalıdır.")
    stamp = now()
    db.execute(
        "INSERT INTO users(first_name, last_name, email, phone, password_hash, role, license_no, plan, status, "
        "trial_ends, password_changed_at, created_at) VALUES (?,?,?,?,?,'makler',?,?,?,?,?,?)",
        (first[:40], last[:40], email, (d.get("phone") or "").strip()[:25], generate_password_hash(pw),
         (d.get("license") or "").strip()[:30], plan, status,
         ts(stamp + timedelta(days=days)) if status == "trial" else None, ts(stamp), ts(stamp)))
    add_log(db, "info", "Makler", f"Yeni makler qeydiyyatı: {first} {last}",
            f"plan={PLAN_LABELS[plan]}{' sınaq=' + str(days) + ' gün' if status == 'trial' else ''}\n"
            f"admin tərəfindən əlavə edildi")
    db.commit()
    return jsonify(ok=True, message="Makler əlavə edildi", password=pw, email=email)


@bp.route("/api/users/<int:uid>/status", methods=["POST"])
@admin_required
def api_user_status(uid):
    db = get_db()
    u = _makler(db, uid)
    if not u:
        return err("Makler tapılmadı.", 404)
    status = (request.get_json(silent=True) or {}).get("status")
    if status not in ("active", "suspended"):
        return err("Yanlış status.")
    plan = u["plan"]
    if status == "active" and plan == "none":
        plan = "start"
    db.execute("UPDATE users SET status = ?, plan = ?, trial_ends = CASE WHEN ? = 'active' THEN NULL ELSE trial_ends END "
               "WHERE id = ?", (status, plan, status, uid))
    add_log(db, "info", "Makler", f"{full_name(u)} {'aktivləşdirildi' if status == 'active' else 'dayandırıldı'}",
            f"icra edən={g.user['email']}")
    db.commit()
    return jsonify(ok=True, message="Makler aktivləşdirildi" if status == "active" else "Makler dayandırıldı")


@bp.route("/api/users/<int:uid>/plan", methods=["POST"])
@admin_required
def api_user_plan(uid):
    db = get_db()
    u = _makler(db, uid)
    if not u:
        return err("Makler tapılmadı.", 404)
    plan = (request.get_json(silent=True) or {}).get("plan")
    if plan not in ("start", "pro"):
        return err("Yanlış plan.")
    db.execute("UPDATE users SET plan = ? WHERE id = ?", (plan, uid))
    add_log(db, "info", "Sistem", f"Plan yeniləməsi: {full_name(u)} — {PLAN_LABELS[plan]}",
            f"icra edən={g.user['email']}")
    db.commit()
    return jsonify(ok=True, message=f"Plan dəyişdirildi: {PLAN_LABELS[plan]}")


@bp.route("/api/users/<int:uid>/extend", methods=["POST"])
@admin_required
def api_user_extend(uid):
    db = get_db()
    u = _makler(db, uid)
    if not u:
        return err("Makler tapılmadı.", 404)
    base = max(parse(u["trial_ends"]) or now(), now())
    ends = base + timedelta(days=7)
    db.execute("UPDATE users SET status = 'trial', trial_ends = ? WHERE id = ?", (ts(ends), uid))
    add_log(db, "info", "Sistem", f"Sınaq müddəti uzadıldı: {full_name(u)}", f"yeni bitmə tarixi={date_short(ends)}")
    db.commit()
    return jsonify(ok=True, message="Sınaq müddəti 7 gün uzadıldı")


@bp.route("/api/users/<int:uid>/reset-password", methods=["POST"])
@admin_required
def api_user_reset(uid):
    db = get_db()
    u = _makler(db, uid)
    if not u:
        return err("Makler tapılmadı.", 404)
    pw = _gen_password()
    db.execute("UPDATE users SET password_hash = ?, password_changed_at = ? WHERE id = ?",
               (generate_password_hash(pw), ts(), uid))
    add_log(db, "info", "Sistem", f"Şifrə sıfırlandı: {full_name(u)}", f"icra edən={g.user['email']}")
    db.commit()
    return jsonify(ok=True, password=pw, email=u["email"], message="Yeni müvəqqəti şifrə yaradıldı")


@bp.route("/api/users/<int:uid>/delete", methods=["POST"])
@admin_required
def api_user_delete(uid):
    db = get_db()
    u = _makler(db, uid)
    if not u:
        return err("Makler tapılmadı.", 404)
    db.execute("DELETE FROM users WHERE id = ?", (uid,))
    add_log(db, "warn", "Sistem", f"Makler hesabı silindi: {full_name(u)}", f"email={u['email']}\nicra edən={g.user['email']}")
    db.commit()
    return jsonify(ok=True, message="Makler silindi")
