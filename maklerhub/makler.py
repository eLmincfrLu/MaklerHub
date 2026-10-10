"""Makler kabineti: Kabinet, Axtarışlar, Elanlar, Müştərilər, Analitika, Ayarlar və onların API-ləri."""
import os
import re
import secrets
from datetime import timedelta
from urllib.parse import quote

from flask import Blueprint, current_app, g, jsonify, render_template, request, url_for

from . import matching
from .auth import member_required, normalize_email, valid_email
from .db import add_log, get_db, get_settings
from .helpers import (SOURCE_COLORS, contact_rel, date_short, day_start, full_name, initials, listing_meta,
                      listing_title, listing_title_long, long_date, now, parse, price_drop_info, price_str,
                      profile_crit, profile_tags, reasons_list, rel, ring_offset, ts, why)

bp = Blueprint("makler", __name__)

NAV = [("makler.kabinet", "Kabinet"), ("makler.axtarislar", "Axtarışlar"), ("makler.elanlar", "Elanlar"),
       ("makler.musteriler", "Müştərilər"), ("makler.analitika", "Analitika"), ("makler.ayarlar", "Ayarlar")]

DISTRICTS = ["Nərimanov", "Yasamal", "Nəsimi", "Səbail", "Xətai", "Nizami", "Binəqədi", "Xəzər", "Sabunçu",
             "Suraxanı", "Qaradağ", "Bakı, mərkəz"]
PROPERTY_TYPES = ["", "Yeni tikili", "Köhnə tikili", "Studiya", "Ofis", "Həyət evi"]
PAGE_SIZE = 10


def render(template, active, **ctx):
    unread = get_db().execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read_at IS NULL",
                              (g.user["id"],)).fetchone()[0]
    return render_template(template, nav=NAV, active=active, unread=unread, **ctx)


def err(message, code=400):
    return jsonify(ok=False, error=message), code


# ---------------------------------------------------------------- ümumi sorğular
def visible_source_ids(db, user):
    ids = []
    for s in db.execute("SELECT id, key FROM sources WHERE enabled = 1"):
        col = f"src_{s['key']}"
        if col not in user.keys() or user[col]:
            ids.append(s["id"])
    return ids


def _in(ids):
    return ",".join(str(int(i)) for i in ids) if ids else "NULL"


MATCH_SQL = """
SELECT m.id AS mid, m.score, m.reasons, m.created_at AS matched_at,
       l.id AS lid, l.title, l.suffix, l.district, l.rooms, l.area, l.floor, l.floors, l.price, l.deal_type,
       l.building, l.renovation, l.metro_m, l.url, l.posted_at, l.created_at AS listing_created,
       s.key AS skey, s.name AS sname, s.logo AS slogo,
       c.id AS cid, c.name AS client_name, c.number AS client_no, c.phone AS client_phone, p.id AS pid,
       p.created_at AS profile_created
FROM matches m
JOIN profiles p ON p.id = m.profile_id
JOIN clients c ON c.id = p.client_id
JOIN listings l ON l.id = m.listing_id
JOIN sources s ON s.id = l.source_id
WHERE p.user_id = ? AND l.active = 1 AND p.status = 'active' AND l.source_id IN ({ids})
"""
# B: profil yaradılandan SONRA bazaya düşən elanlar ("Yeni gələnlər"); A: o vaxt artıq mövcud olanlar
NEW_ONLY = " AND l.created_at > p.created_at"
OLD_ONLY = " AND l.created_at <= p.created_at"
SECTIONS = {"new": NEW_ONLY, "old": OLD_ONLY}


def match_view(db, r):
    reasons = reasons_list(r["reasons"])
    drop = price_drop_info(db, {"id": r["lid"], "price": r["price"], "deal_type": r["deal_type"]})
    short = why(reasons)
    if drop:
        short = f"{short} · {drop['text']}" if short else drop["text"]
    return {
        "id": r["mid"], "score": r["score"], "mid": r["score"] < 80,
        "ring56": ring_offset(r["score"], 151), "ring52": ring_offset(r["score"], 138),
        "title": listing_title(r), "title_long": listing_title_long(r), "meta": listing_meta(r),
        "why_short": short, "why_long": why(reasons, long=True), "reasons": reasons,
        "price": price_str(r["price"], r["deal_type"]),
        "old": drop["old"] if drop else None, "drop": drop["pct"] if drop else None,
        "drop_text": drop["text"] if drop else None,
        "client": r["client_name"], "client_no": f"{r['client_no']:03d}", "client_phone": r["client_phone"],
        "logo": url_for("static", filename="img/" + r["slogo"]), "source": r["sname"],
        "url": r["url"], "matched": r["matched_at"], "posted": r["posted_at"], "lid": r["lid"],
        "new": r["listing_created"] > r["profile_created"], "arrived": rel(r["listing_created"]),
    }


def plan_info(db, user):
    s = get_settings(db)
    key = "pro" if user["plan"] == "pro" else "start"
    info = {
        "key": user["plan"], "name": "Pro" if key == "pro" else "Başlanğıc",
        "price": int(s[f"plan_{key}_price"]), "clients": int(s[f"plan_{key}_clients"]),
        "profiles": int(s[f"plan_{key}_profiles"]),
        "start_price": int(s["plan_start_price"]), "pro_price": int(s["plan_pro_price"]),
        "start_clients": int(s["plan_start_clients"]), "start_profiles": int(s["plan_start_profiles"]),
        "pro_clients": int(s["plan_pro_clients"]), "pro_profiles": int(s["plan_pro_profiles"]),
        "trial_days_left": None, "expired": False,
    }
    if user["status"] == "trial" and user["trial_ends"]:
        left = (parse(user["trial_ends"]) - now()).total_seconds() / 86400
        info["trial_days_left"] = max(0, int(left + 0.999))
        info["expired"] = left <= 0
    return info


def limit_error(db, user, kind):
    info = plan_info(db, user)
    if info["expired"]:
        return "Sınaq müddəti bitib. Ayarlar → Abunəlik bölməsindən plan seçin."
    if kind == "clients":
        n = db.execute("SELECT COUNT(*) FROM clients WHERE user_id = ?", (user["id"],)).fetchone()[0]
        if n >= info["clients"]:
            return f"{info['name']} planında maksimum {info['clients']} müştəri ola bilər."
    else:
        n = db.execute("SELECT COUNT(*) FROM profiles WHERE user_id = ? AND status != 'closed'",
                       (user["id"],)).fetchone()[0]
        if n >= info["profiles"]:
            return f"{info['name']} planında maksimum {info['profiles']} axtarış profili ola bilər."
    return None


# ---------------------------------------------------------------- KABİNET
@bp.route("/kabinet")
@member_required
def kabinet():
    db, u = get_db(), g.user
    ids = visible_source_ids(db, u)
    # yalnız B: profil yaradılandan sonra gələn elanlar (profil yaradılanda tapılan köhnələr bura düşmür)
    today = db.execute(MATCH_SQL.format(ids=_in(ids)) + NEW_ONLY +
                       " AND m.created_at >= ? ORDER BY m.score DESC, m.created_at DESC",
                       (u["id"], ts(day_start()))).fetchall()
    top = [match_view(db, r) for r in today[:3]]
    active_profiles = db.execute("SELECT COUNT(*) FROM profiles WHERE user_id = ? AND status = 'active'",
                                 (u["id"],)).fetchone()[0]
    maklers = db.execute("SELECT COUNT(*) FROM users WHERE role = 'makler' AND status != 'suspended'").fetchone()[0]
    chips = []
    for p in db.execute(
            "SELECT p.*, c.name AS client_name FROM profiles p JOIN clients c ON c.id = p.client_id "
            "WHERE p.user_id = ? AND p.status = 'active' ORDER BY p.id", (u["id"],)).fetchall():
        cnt = db.execute(
            f"SELECT COUNT(*) FROM matches m JOIN listings l ON l.id = m.listing_id WHERE m.profile_id = ? "
            f"AND l.active = 1 AND l.source_id IN ({_in(ids)})", (p["id"],)).fetchone()[0]
        chips.append({"id": p["id"], "name": p["client_name"], "crit": profile_crit(p, "-"), "count": cnt})
    stats = {"new": len(today), "hi": sum(1 for r in today if r["score"] >= 90),
             "profiles": active_profiles, "maklers": maklers}
    return render("makler/kabinet.html", "makler.kabinet", stats=stats, top=top, chips=chips,
                  today_long=long_date())


# ---------------------------------------------------------------- ELANLAR
SORTS = {
    "match": "m.score DESC, m.created_at DESC",
    "new": "m.created_at DESC",
    "price_asc": "l.price ASC",
    "price_desc": "l.price DESC",
}


def query_matches(db, user, src="all", sort="match", offset=0, profile_id=None, section=None, limit=PAGE_SIZE):
    ids = visible_source_ids(db, user)
    if src != "all":
        row = db.execute("SELECT id FROM sources WHERE key = ?", (src,)).fetchone()
        ids = [i for i in ids if row and i == row["id"]]
    sql = MATCH_SQL.format(ids=_in(ids)) + SECTIONS.get(section, "")
    args = [user["id"]]
    if profile_id:
        sql += " AND p.id = ?"
        args.append(profile_id)
    total = db.execute(f"SELECT COUNT(*) FROM ({sql})", args).fetchone()[0]
    rows = db.execute(sql + f" ORDER BY {SORTS.get(sort, SORTS['match'])} LIMIT ? OFFSET ?",
                      args + [limit, offset]).fetchall()
    return [match_view(db, r) for r in rows], total


@bp.route("/elanlar")
@member_required
def elanlar():
    db, u = get_db(), g.user
    src = request.args.get("src", "all")
    sort = request.args.get("sort", "match")
    profile_id = request.args.get("profile", type=int)
    # Profil səhifəsi: B ("Yeni gələnlər", ən təzə yuxarıda) + A ("mövcud olanlar", səhifələnir)
    section = "old" if profile_id else None
    items, total = query_matches(db, u, src, sort, 0, profile_id, section)
    new_items, new_total = ([], 0)
    if profile_id:
        new_items, new_total = query_matches(db, u, src, "new", 0, profile_id, "new", limit=100)
    ids = visible_source_ids(db, u)
    since = ts(now() - timedelta(days=30))
    visible = MATCH_SQL.format(ids=_in(ids))
    stats = {
        "tracked": db.execute(f"SELECT COUNT(*) FROM listings WHERE active = 1 AND source_id IN ({_in(ids)})").fetchone()[0],
        "today": db.execute(f"SELECT COUNT(*) FROM ({visible} AND m.created_at >= ?)",
                            (u["id"], ts(day_start()))).fetchone()[0],
        "hi": db.execute(f"SELECT COUNT(*) FROM ({visible} AND m.score >= 90)", (u["id"],)).fetchone()[0],
        "dropped": db.execute(
            f"SELECT COUNT(*) FROM listings l WHERE l.active = 1 AND l.source_id IN ({_in(ids)}) AND EXISTS "
            f"(SELECT 1 FROM price_history h WHERE h.listing_id = l.id AND h.price > l.price AND h.recorded_at >= ?)",
            (since,)).fetchone()[0],
    }
    sources = db.execute(f"SELECT * FROM sources WHERE id IN ({_in(ids)}) ORDER BY sort").fetchall()
    profile = None
    if profile_id:
        profile = db.execute("SELECT p.id, c.name FROM profiles p JOIN clients c ON c.id = p.client_id "
                             "WHERE p.id = ? AND p.user_id = ?", (profile_id, u["id"])).fetchone()
    return render("makler/elanlar.html", "makler.elanlar", items=items, total=total, stats=stats,
                  sources=sources, src=src, sort=sort, profile=profile, page_size=PAGE_SIZE,
                  new_items=new_items, new_total=new_total)


@bp.route("/api/elanlar")
@member_required
def api_elanlar():
    db = get_db()
    offset = max(0, request.args.get("offset", 0, type=int))
    profile_id = request.args.get("profile", type=int)
    section = request.args.get("section") if profile_id else None
    items, total = query_matches(db, g.user, request.args.get("src", "all"), request.args.get("sort", "match"),
                                 offset, profile_id, section if section in SECTIONS else None)
    html = render_template("makler/_elan_cards.html", items=items, base_delay=0.05 if offset else 0.28)
    return jsonify(ok=True, html=html, total=total, has_more=offset + len(items) < total)


@bp.route("/api/matches/<int:mid>")
@member_required
def api_match(mid):
    db, u = get_db(), g.user
    row = db.execute(MATCH_SQL.format(ids=_in(visible_source_ids(db, u))) + " AND m.id = ?", (u["id"], mid)).fetchone()
    if not row:
        return err("Elan tapılmadı və ya artıq aktiv deyil.", 404)
    m = match_view(db, row)
    # elan açıldı → bu profil+elan üzrə bildirişlər oxunmuş sayılır
    db.execute("UPDATE notifications SET read_at = ? WHERE user_id = ? AND profile_id = ? AND listing_id = ? "
               "AND read_at IS NULL", (ts(), u["id"], row["pid"], row["lid"]))
    db.commit()
    history = db.execute("SELECT price, recorded_at FROM price_history WHERE listing_id = ? ORDER BY recorded_at",
                         (row["lid"],)).fetchall()
    history = [{"date": date_short(h["recorded_at"]), "price": price_str(h["price"], row["deal_type"])} for h in history]
    first = row["client_name"].split()[0]
    text = (f"Salam, {first}! Sizin üçün uyğun elan tapdım:\n{m['title_long']}\n{m['meta']}\n"
            f"Uyğunluq: {m['score']}%\n\n— {full_name(u)}, MaklerHub")
    digits = re.sub(r"\D", "", row["client_phone"] or "")
    wa = f"https://wa.me/{digits}?text={quote(text)}" if digits else None
    html = render_template("makler/_detail.html", m=m, history=history, share_text=text, wa=wa,
                           posted=rel(row["posted_at"]), matched=rel(row["matched_at"]))
    return jsonify(ok=True, html=html)


# ---------------------------------------------------------------- BİLDİRİŞLƏR
def _unread(db, user_id):
    return db.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read_at IS NULL",
                      (user_id,)).fetchone()[0]


@bp.route("/api/notifications")
@member_required
def api_notifications():
    db, u = get_db(), g.user
    rows = db.execute(
        "SELECT n.*, m.id AS mid FROM notifications n "
        "LEFT JOIN matches m ON m.profile_id = n.profile_id AND m.listing_id = n.listing_id "
        "WHERE n.user_id = ? ORDER BY n.created_at DESC, n.id DESC LIMIT 20", (u["id"],)).fetchall()
    items = []
    for n in rows:
        href = url_for("makler.elanlar", profile=n["profile_id"]) if n["profile_id"] else url_for("makler.elanlar")
        if n["mid"]:
            href += f"&open={n['mid']}"
        items.append({"id": n["id"], "title": n["title"], "body": n["body"], "read": bool(n["read_at"]),
                      "when": rel(n["created_at"]), "href": href})
    return jsonify(ok=True, unread=_unread(db, u["id"]), items=items)


@bp.route("/api/notifications/<int:nid>/read", methods=["POST"])
@member_required
def api_notification_read(nid):
    db, u = get_db(), g.user
    db.execute("UPDATE notifications SET read_at = ? WHERE id = ? AND user_id = ? AND read_at IS NULL",
               (ts(), nid, u["id"]))
    db.commit()
    return jsonify(ok=True, unread=_unread(db, u["id"]))


@bp.route("/api/notifications/read-all", methods=["POST"])
@member_required
def api_notifications_read_all():
    db, u = get_db(), g.user
    db.execute("UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL", (ts(), u["id"]))
    db.commit()
    return jsonify(ok=True, unread=0)


# ---------------------------------------------------------------- MÜŞTƏRİLƏR
def _client_rows(db, user):
    ids = visible_source_ids(db, user)
    rows = []
    for c in db.execute(
            "SELECT * FROM clients WHERE user_id = ? ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'deal' THEN 1 "
            "ELSE 2 END, id", (user["id"],)).fetchall():
        p = db.execute(
            "SELECT * FROM profiles WHERE client_id = ? ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' "
            "THEN 1 ELSE 2 END, id DESC LIMIT 1", (c["id"],)).fetchone()
        count = None
        if c["status"] != "deal":
            count = 0
            if p and p["status"] == "active":
                count = db.execute(
                    f"SELECT COUNT(*) FROM matches m JOIN listings l ON l.id = m.listing_id WHERE m.profile_id = ? "
                    f"AND l.active = 1 AND l.source_id IN ({_in(ids)})", (p["id"],)).fetchone()[0]
        rows.append({
            "c": c, "p": p, "crit": profile_crit(p) if p else "—", "count": count,
            "contact": contact_rel(c["last_contact"]), "initials": initials(c["name"]),
            "json": {"id": c["id"], "name": c["name"], "phone": c["phone"], "note": c["note"],
                     "status": c["status"], "profile_id": p["id"] if p else None},
        })
    return rows


@bp.route("/musteriler")
@member_required
def musteriler():
    db = get_db()
    rows = _client_rows(db, g.user)
    counts = {"all": len(rows)}
    for key in ("active", "deal", "archived"):
        counts[key] = sum(1 for r in rows if r["c"]["status"] == key)
    return render("makler/musteriler.html", "makler.musteriler", rows=rows, counts=counts)


def _client_or_404(db, cid):
    return db.execute("SELECT * FROM clients WHERE id = ? AND user_id = ?", (cid, g.user["id"])).fetchone()


def _clean_phone(v):
    return re.sub(r"[^\d+ ()-]", "", (v or "").strip())[:25]


@bp.route("/api/clients", methods=["POST"])
@member_required
def api_client_create():
    db, u = get_db(), g.user
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if len(name) < 2:
        return err("Müştərinin adını daxil edin.")
    problem = limit_error(db, u, "clients")
    if problem:
        return err(problem)
    number = (db.execute("SELECT MAX(number) FROM clients WHERE user_id = ?", (u["id"],)).fetchone()[0] or 0) + 1
    cur = db.execute(
        "INSERT INTO clients(user_id, number, name, phone, note, status, last_contact, created_at) "
        "VALUES (?,?,?,?,?,'active',?,?)",
        (u["id"], number, name[:80], _clean_phone(data.get("phone")), (data.get("note") or "")[:500], ts(), ts()))
    db.commit()
    return jsonify(ok=True, id=cur.lastrowid, message="Müştəri əlavə edildi")


@bp.route("/api/clients/<int:cid>", methods=["POST"])
@member_required
def api_client_update(cid):
    db = get_db()
    c = _client_or_404(db, cid)
    if not c:
        return err("Müştəri tapılmadı.", 404)
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if len(name) < 2:
        return err("Müştərinin adını daxil edin.")
    db.execute("UPDATE clients SET name = ?, phone = ?, note = ? WHERE id = ?",
               (name[:80], _clean_phone(data.get("phone")), (data.get("note") or "")[:500], cid))
    db.commit()
    return jsonify(ok=True, message="Dəyişikliklər yadda saxlanıldı")


@bp.route("/api/clients/<int:cid>/status", methods=["POST"])
@member_required
def api_client_status(cid):
    db = get_db()
    c = _client_or_404(db, cid)
    if not c:
        return err("Müştəri tapılmadı.", 404)
    status = (request.get_json(silent=True) or {}).get("status")
    if status not in ("active", "deal", "archived"):
        return err("Yanlış status.")
    stamp = ts()
    db.execute("UPDATE clients SET status = ?, closed_at = ? WHERE id = ?",
               (status, stamp if status == "deal" else None, cid))
    profiles = db.execute("SELECT * FROM profiles WHERE client_id = ?", (cid,)).fetchall()
    if status == "deal":
        db.execute("UPDATE profiles SET status = 'closed' WHERE client_id = ?", (cid,))
    elif status == "archived":
        db.execute("UPDATE profiles SET status = 'paused', paused_at = ? WHERE client_id = ? AND status = 'active'",
                   (stamp, cid))
    elif status == "active" and profiles:
        latest = max(profiles, key=lambda p: p["id"])
        db.execute("UPDATE profiles SET status = 'active', paused_at = NULL WHERE id = ?", (latest["id"],))
    for p in profiles:
        matching.rematch_profile(db, p["id"])
    db.commit()
    labels = {"active": "Müştəri aktivləşdirildi", "deal": "Sövdə bağlandı — təbriklər!", "archived": "Müştəri arxivləşdirildi"}
    return jsonify(ok=True, message=labels[status])


@bp.route("/api/clients/<int:cid>/touch", methods=["POST"])
@member_required
def api_client_touch(cid):
    db = get_db()
    if not _client_or_404(db, cid):
        return err("Müştəri tapılmadı.", 404)
    db.execute("UPDATE clients SET last_contact = ? WHERE id = ?", (ts(), cid))
    db.commit()
    return jsonify(ok=True, message="Əlaqə qeydə alındı")


@bp.route("/api/clients/<int:cid>/delete", methods=["POST"])
@member_required
def api_client_delete(cid):
    db = get_db()
    if not _client_or_404(db, cid):
        return err("Müştəri tapılmadı.", 404)
    db.execute("DELETE FROM clients WHERE id = ?", (cid,))
    db.commit()
    return jsonify(ok=True, message="Müştəri silindi")


# ---------------------------------------------------------------- AXTARIŞLAR
@bp.route("/axtarislar")
@member_required
def axtarislar():
    db, u = get_db(), g.user
    ids = visible_source_ids(db, u)
    cards = []
    for p in db.execute(
            "SELECT p.*, c.name AS client_name FROM profiles p JOIN clients c ON c.id = p.client_id "
            "WHERE p.user_id = ? AND p.status IN ('active', 'paused') "
            "ORDER BY CASE p.status WHEN 'active' THEN 0 ELSE 1 END, p.id", (u["id"],)).fetchall():
        cnt = db.execute(
            f"SELECT COUNT(*) FROM matches m JOIN listings l ON l.id = m.listing_id WHERE m.profile_id = ? "
            f"AND l.active = 1 AND l.source_id IN ({_in(ids)})", (p["id"],)).fetchone()[0]
        rooms = ""
        if p["rooms"]:
            rooms = f"{p['rooms']}{'+' if p['rooms_plus'] else ''}"
        cards.append({
            "p": p, "count": cnt, "tags": profile_tags(p), "initials": initials(p["client_name"]),
            "last": (f"Son yoxlanış: {rel(p['last_checked'])}" if p["status"] == "active"
                     else f"Dayandırılıb: {rel(p['paused_at'])}"),
            "json": {"id": p["id"], "client_id": p["client_id"], "district": p["district"], "rooms": rooms,
                     "price_min": p["price_min"], "price_max": p["price_max"], "deal_type": p["deal_type"],
                     "property_type": p["property_type"], "status": p["status"], "client": p["client_name"]},
        })
    counts = {"all": len(cards), "active": sum(1 for c in cards if c["p"]["status"] == "active")}
    counts["paused"] = counts["all"] - counts["active"]
    clients = db.execute("SELECT id, name, number FROM clients WHERE user_id = ? AND status != 'deal' ORDER BY name",
                         (u["id"],)).fetchall()
    return render("makler/axtarislar.html", "makler.axtarislar", cards=cards, counts=counts, clients=clients,
                  districts=DISTRICTS, property_types=PROPERTY_TYPES)


def _profile_payload(data):
    district = data.get("district")
    if district not in DISTRICTS:
        return None, "Rayon seçin."
    deal = "rent" if data.get("deal_type") == "rent" else "sale"
    rooms_raw = str(data.get("rooms") or "").strip()
    rooms, plus = None, 0
    if rooms_raw:
        m = re.fullmatch(r"(\d)(\+?)", rooms_raw)
        if not m:
            return None, "Otaq sayı düzgün deyil."
        rooms, plus = int(m.group(1)), 1 if m.group(2) else 0
    try:
        lo = int(float(str(data.get("price_min")).replace(",", "").replace(" ", "")))
        hi = int(float(str(data.get("price_max")).replace(",", "").replace(" ", "")))
    except (TypeError, ValueError):
        return None, "Qiymət aralığını rəqəmlə daxil edin."
    if lo <= 0 or hi <= 0:
        return None, "Qiymətlər müsbət olmalıdır."
    if lo > hi:
        return None, "Minimum qiymət maksimumdan böyük ola bilməz."
    ptype = data.get("property_type") or ""
    if ptype not in PROPERTY_TYPES:
        ptype = ""
    if ptype == "Studiya":
        rooms, plus = 1, 0
    return {"district": district, "deal_type": deal, "rooms": rooms, "rooms_plus": plus, "price_min": lo,
            "price_max": hi, "property_type": ptype}, None


@bp.route("/api/profiles", methods=["POST"])
@member_required
def api_profile_create():
    db, u = get_db(), g.user
    data = request.get_json(silent=True) or {}
    payload, problem = _profile_payload(data)
    if problem:
        return err(problem)
    problem = limit_error(db, u, "profiles")
    if problem:
        return err(problem)
    if data.get("client_id") == "new":
        name = (data.get("new_name") or "").strip()
        if len(name) < 2:
            return err("Yeni müştərinin adını daxil edin.")
        problem = limit_error(db, u, "clients")
        if problem:
            return err(problem)
        number = (db.execute("SELECT MAX(number) FROM clients WHERE user_id = ?", (u["id"],)).fetchone()[0] or 0) + 1
        cid = db.execute(
            "INSERT INTO clients(user_id, number, name, phone, status, last_contact, created_at) "
            "VALUES (?,?,?,?,'active',?,?)",
            (u["id"], number, name[:80], _clean_phone(data.get("new_phone")), ts(), ts())).lastrowid
    else:
        try:
            cid = int(data.get("client_id"))
        except (TypeError, ValueError):
            return err("Müştəri seçin.")
        c = _client_or_404(db, cid)
        if not c:
            return err("Müştəri tapılmadı.")
        if c["status"] == "archived":
            db.execute("UPDATE clients SET status = 'active' WHERE id = ?", (cid,))
    cur = db.execute(
        "INSERT INTO profiles(user_id, client_id, district, rooms, rooms_plus, price_min, price_max, deal_type, "
        "property_type, status, last_checked, created_at) VALUES (?,?,?,?,?,?,?,?,?,'active',?,?)",
        (u["id"], cid, payload["district"], payload["rooms"], payload["rooms_plus"], payload["price_min"],
         payload["price_max"], payload["deal_type"], payload["property_type"], ts(), ts()))
    count = matching.rematch_profile(db, cur.lastrowid)
    add_log(db, "info", "Makler", f"{full_name(u)} yeni axtarış profili yaratdı",
            f"profil=#{cur.lastrowid} rayon={payload['district']} otaq={payload['rooms'] or '—'}")
    db.commit()
    return jsonify(ok=True, id=cur.lastrowid, matches=count,
                   message=f"Profil yaradıldı — {count} uyğun elan tapıldı")


def _profile_or_none(db, pid):
    return db.execute("SELECT * FROM profiles WHERE id = ? AND user_id = ?", (pid, g.user["id"])).fetchone()


@bp.route("/api/profiles/<int:pid>", methods=["POST"])
@member_required
def api_profile_update(pid):
    db = get_db()
    if not _profile_or_none(db, pid):
        return err("Profil tapılmadı.", 404)
    payload, problem = _profile_payload(request.get_json(silent=True) or {})
    if problem:
        return err(problem)
    db.execute("UPDATE profiles SET district = ?, rooms = ?, rooms_plus = ?, price_min = ?, price_max = ?, "
               "deal_type = ?, property_type = ? WHERE id = ?",
               (payload["district"], payload["rooms"], payload["rooms_plus"], payload["price_min"],
                payload["price_max"], payload["deal_type"], payload["property_type"], pid))
    count = matching.rematch_profile(db, pid)
    db.commit()
    return jsonify(ok=True, matches=count, message=f"Profil yeniləndi — {count} uyğun elan")


@bp.route("/api/profiles/<int:pid>/toggle", methods=["POST"])
@member_required
def api_profile_toggle(pid):
    db = get_db()
    p = _profile_or_none(db, pid)
    if not p:
        return err("Profil tapılmadı.", 404)
    if p["status"] == "active":
        db.execute("UPDATE profiles SET status = 'paused', paused_at = ? WHERE id = ?", (ts(), pid))
        msg = "Profil dayandırıldı"
    else:
        problem = None
        if p["status"] == "closed":
            problem = limit_error(db, g.user, "profiles")
        if problem:
            return err(problem)
        db.execute("UPDATE profiles SET status = 'active', paused_at = NULL WHERE id = ?", (pid,))
        db.execute("UPDATE clients SET status = 'active' WHERE id = ? AND status = 'archived'", (p["client_id"],))
        msg = "Profil aktivləşdirildi"
    count = matching.rematch_profile(db, pid)
    db.commit()
    return jsonify(ok=True, matches=count, message=msg)


@bp.route("/api/profiles/<int:pid>/delete", methods=["POST"])
@member_required
def api_profile_delete(pid):
    db = get_db()
    if not _profile_or_none(db, pid):
        return err("Profil tapılmadı.", 404)
    db.execute("DELETE FROM profiles WHERE id = ?", (pid,))
    db.commit()
    return jsonify(ok=True, message="Profil silindi")


# ---------------------------------------------------------------- ANALİTİKA
def _trend(cur, prev, lower_is_better=False, unit="%", absolute=False):
    if absolute:
        diff = cur - prev
        up = diff >= 0
        return {"cls": "up" if up else "down", "text": f"{'▲' if up else '▼'} {abs(diff)}{unit}"}
    if not prev:
        return {"cls": "up", "text": "▲ yeni"} if cur else {"cls": "up", "text": "—"}
    pct = round((cur - prev) / prev * 100)
    good = pct <= 0 if lower_is_better else pct >= 0
    return {"cls": "up" if good else "down", "text": f"{'▲' if good else '▼'} {abs(pct)}{unit}"}


@bp.route("/analitika")
@member_required
def analitika():
    db, u = get_db(), g.user
    days = request.args.get("range", 30, type=int)
    if days not in (7, 30, 90):
        days = 30
    n = now()
    start, prev_start = n - timedelta(days=days), n - timedelta(days=days * 2)

    def matched(a, b):
        return db.execute("SELECT COUNT(*) FROM matches m JOIN profiles p ON p.id = m.profile_id "
                          "WHERE p.user_id = ? AND m.created_at >= ? AND m.created_at < ?",
                          (u["id"], ts(a), ts(b))).fetchone()[0]

    def detect(a, b):
        v = db.execute("SELECT AVG(EXTRACT(EPOCH FROM (m.created_at::timestamp - l.posted_at::timestamp)) / 60) FROM matches m "
                       "JOIN profiles p ON p.id = m.profile_id JOIN listings l ON l.id = m.listing_id "
                       "WHERE p.user_id = ? AND m.created_at >= ? AND m.created_at < ?",
                       (u["id"], ts(a), ts(b))).fetchone()[0]
        return v or 0

    m_cur, m_prev = matched(start, n + timedelta(seconds=1)), matched(prev_start, start)
    d_cur, d_prev = detect(start, n + timedelta(seconds=1)), detect(prev_start, start)
    total_clients = db.execute("SELECT COUNT(*) FROM clients WHERE user_id = ?", (u["id"],)).fetchone()[0]
    deals = db.execute("SELECT COUNT(*) FROM clients WHERE user_id = ? AND status = 'deal'", (u["id"],)).fetchone()[0]
    clients_before = db.execute("SELECT COUNT(*) FROM clients WHERE user_id = ? AND created_at < ?",
                                (u["id"], ts(start))).fetchone()[0]
    deals_before = db.execute("SELECT COUNT(*) FROM clients WHERE user_id = ? AND status = 'deal' AND closed_at < ?",
                              (u["id"], ts(start))).fetchone()[0]
    rate = round(deals / total_clients * 100) if total_clients else 0
    rate_prev = round(deals_before / clients_before * 100) if clients_before else 0
    active_clients = db.execute("SELECT COUNT(*) FROM clients WHERE user_id = ? AND status = 'active'",
                                (u["id"],)).fetchone()[0]
    new_clients = db.execute("SELECT COUNT(*) FROM clients WHERE user_id = ? AND created_at >= ?",
                             (u["id"], ts(start))).fetchone()[0]
    kpis = [
        {"l": "Uyğunlaşdırılan elan", "n": f"{m_cur:,}", "t": _trend(m_cur, m_prev)},
        {"l": "Orta aşkar etmə vaxtı", "n": f"{round(d_cur)} dəq", "t": _trend(round(d_cur), round(d_prev), True)},
        {"l": "Sövdə bağlama nisbəti", "n": f"{rate}%",
         "t": _trend(rate, rate_prev, absolute=True) if clients_before else {"cls": "up", "text": "—"}},
        {"l": "Aktiv müştəri", "n": str(active_clients),
         "t": {"cls": "up", "text": f"▲ {new_clients}"} if new_clients else {"cls": "up", "text": "—"}},
    ]

    # Son 14 gün
    first_day = day_start(n) - timedelta(days=13)
    per_day = {r["d"]: r["c"] for r in db.execute(
        "SELECT substr(m.created_at, 1, 10) d, COUNT(*) c FROM matches m JOIN profiles p ON p.id = m.profile_id "
        "WHERE p.user_id = ? AND m.created_at >= ? GROUP BY d", (u["id"], ts(first_day)))}
    vals = [per_day.get((first_day + timedelta(days=i)).strftime("%Y-%m-%d"), 0) for i in range(14)]
    top = max(vals) or 1
    bars = [{"val": v, "h": round(v / top * 100)} for v in vals]
    labels = [str(13 - i + 1) for i in range(13)] + ["Bu gün"]

    # Mənbə üzrə bölgü (aktiv elanlar)
    ids = visible_source_ids(db, u)
    src_rows = db.execute(
        f"SELECT s.key, s.name, COUNT(l.id) c FROM sources s LEFT JOIN listings l ON l.source_id = s.id "
        f"AND l.active = 1 WHERE s.id IN ({_in(ids)}) GROUP BY s.id ORDER BY c DESC").fetchall()
    total = sum(r["c"] for r in src_rows)
    segs, cum = [], 0.0
    for i, r in enumerate(src_rows):
        f = r["c"] / total if total else 0
        segs.append({"name": r["name"], "color": SOURCE_COLORS.get(r["key"], "var(--gold-dim)"),
                     "final": round(427 * (1 - f)), "rot": round(cum * 360, 1) if i else None,
                     "pct": round(f * 100)})
        cum += f

    # Ən aktiv profillər
    rank = []
    for p in db.execute("SELECT p.id, c.name FROM profiles p JOIN clients c ON c.id = p.client_id "
                        "WHERE p.user_id = ? AND p.status = 'active'", (u["id"],)).fetchall():
        cnt = db.execute(
            f"SELECT COUNT(*) FROM matches m JOIN listings l ON l.id = m.listing_id WHERE m.profile_id = ? "
            f"AND l.active = 1 AND l.source_id IN ({_in(ids)})", (p["id"],)).fetchone()[0]
        rank.append({"name": p["name"], "count": cnt, "id": p["id"]})
    rank.sort(key=lambda r: -r["count"])
    rank = rank[:5]
    mx = max([r["count"] for r in rank] or [1]) or 1
    for r in rank:
        r["w"] = round(r["count"] / mx * 100)
    return render("makler/analitika.html", "makler.analitika", days=days, kpis=kpis, bars=bars, labels=labels,
                  segs=segs, total=total, rank=rank)


# ---------------------------------------------------------------- AYARLAR
@bp.route("/ayarlar")
@member_required
def ayarlar():
    db, u = get_db(), g.user
    integrations = []
    for s in db.execute("SELECT * FROM sources ORDER BY sort").fetchall():
        col = f"src_{s['key']}"
        on = bool(u[col]) if col in u.keys() else True
        if not s["enabled"]:
            status, cls = "Admin tərəfindən dayandırılıb", ""
        elif not on:
            status, cls = "Deaktiv · bu mənbədən elan göstərilmir", ""
        else:
            status, cls = f"● Aktiv · son sinxronizasiya {rel(s['last_sync'])}", "on"
        integrations.append({"s": s, "col": col, "on": on, "status": status, "cls": cls,
                             "logo": url_for("static", filename="img/" + s["logo"])})
    info = plan_info(db, u)
    used = db.execute("SELECT COUNT(*) FROM profiles WHERE user_id = ? AND status != 'closed'",
                      (u["id"],)).fetchone()[0]
    info["used"] = used
    info["pct"] = min(100, round(used / info["profiles"] * 100)) if info["profiles"] else 0
    return render("makler/ayarlar.html", "makler.ayarlar", integrations=integrations, plan=info,
                  user_initials=initials(u["first_name"], u["last_name"]))


@bp.route("/api/me/profile", methods=["POST"])
@member_required
def api_me_profile():
    db, u = get_db(), g.user
    data = request.get_json(silent=True) or {}
    first, last = (data.get("first_name") or "").strip(), (data.get("last_name") or "").strip()
    email = normalize_email(data.get("email"))
    if not first or not last:
        return err("Ad və soyad boş ola bilməz.")
    if not valid_email(email):
        return err("E-poçt ünvanı düzgün deyil.")
    if db.execute("SELECT 1 FROM users WHERE email = ? AND id != ?", (email, u["id"])).fetchone():
        return err("Bu e-poçt başqa hesabda istifadə olunur.")
    db.execute("UPDATE users SET first_name = ?, last_name = ?, phone = ?, email = ? WHERE id = ?",
               (first[:40], last[:40], _clean_phone(data.get("phone")), email, u["id"]))
    db.commit()
    return jsonify(ok=True, message="Dəyişikliklər yadda saxlanıldı",
                   initials=initials(first, last), name=f"{first} {last}")


ALLOWED_IMG = {"png", "jpg", "jpeg", "webp", "gif"}


@bp.route("/api/me/avatar", methods=["POST"])
@member_required
def api_me_avatar():
    db, u = get_db(), g.user
    f = request.files.get("avatar")
    if not f or not f.filename:
        return err("Şəkil seçin.")
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_IMG:
        return err("Yalnız PNG, JPG, WEBP və ya GIF formatı qəbul olunur.")
    head = f.stream.read(12)
    f.stream.seek(0)
    if not (head.startswith(b"\x89PNG") or head[:3] == b"\xff\xd8\xff" or head[:4] == b"RIFF" or head[:3] == b"GIF"):
        return err("Fayl şəkil deyil.")
    name = f"u{u['id']}_{secrets.token_hex(6)}.{ext}"
    folder = current_app.config["UPLOAD_DIR"]
    f.save(os.path.join(folder, name))
    if u["avatar"]:
        try:
            os.remove(os.path.join(folder, u["avatar"]))
        except OSError:
            pass
    db.execute("UPDATE users SET avatar = ? WHERE id = ?", (name, u["id"]))
    db.commit()
    return jsonify(ok=True, url=url_for("public.avatar", filename=name), message="Şəkil yeniləndi")


PREF_KEYS = {"notif_match", "notif_price", "notif_daily", "notif_sms", "src_bina", "src_tap", "src_lalafo", "twofa"}


@bp.route("/api/me/prefs", methods=["POST"])
@member_required
def api_me_prefs():
    db = get_db()
    data = request.get_json(silent=True) or {}
    changed = 0
    for k, v in data.items():
        if k in PREF_KEYS:
            db.execute(f"UPDATE users SET {k} = ? WHERE id = ?", (1 if v else 0, g.user["id"]))
            changed += 1
    db.commit()
    return jsonify(ok=True, message="Ayar yadda saxlanıldı" if changed else "Dəyişiklik yoxdur")


@bp.route("/api/me/plan", methods=["POST"])
@member_required
def api_me_plan():
    db, u = get_db(), g.user
    plan = (request.get_json(silent=True) or {}).get("plan")
    if plan not in ("start", "pro"):
        return err("Plan seçin.")
    status = u["status"]
    if status == "trial" and plan_info(db, u)["expired"]:
        status = "active"
    db.execute("UPDATE users SET plan = ?, status = ? WHERE id = ?", (plan, status, u["id"]))
    label = "Pro" if plan == "pro" else "Başlanğıc"
    add_log(db, "info", "Sistem", f"Plan yeniləməsi: {full_name(u)} — {label}", f"plan={label}")
    db.commit()
    return jsonify(ok=True, message=f"{label} planı aktivləşdirildi")
