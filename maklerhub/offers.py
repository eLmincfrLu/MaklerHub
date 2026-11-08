"""Makler təklifləri: yoxlama, göndərmə (limit FOR UPDATE ilə), geri çəkmə, kabinet siyahısı;
müştəri qərarları (qəbul/rədd, kontakt), tələbin bağlanması, şikayətlər, yeni tələb bildirişləri."""
from urllib.parse import urlsplit

import psycopg

from . import matching
from .helpers import BAKU_DISTRICTS, phone_display, ts
from .teleb import BUDGET, OFFER_LIMIT, ROOMS, URGENCY_ORDER, request_alive

TITLE_MAX = 80
DESC_MAX = 500
URL_MAX = 500
AREA_RANGE = (5, 10_000)
OFFER_STATUS = {"sent": "Göndərilib", "accepted": "Qəbul edildi", "rejected": "Rədd edildi", "withdrawn": "Geri çəkildi"}

# Makler tərəfinə tələbdən gedən sütunlar — telefon və IP burada YOXDUR (heç vaxt seçilmir)
REQ_COLS = ("r.id, r.name, r.deal_type, r.districts, r.rooms, r.budget_min, r.budget_max, r.urgency, r.note, "
            "r.status, r.created_at, r.is_demo")
# Demo izolyasiyası: makler yalnız öz növündən (demo ↔ demo, real ↔ real) olan tələbləri görür
SAME_KIND = "r.is_demo = (SELECT u.is_demo FROM users u WHERE u.id = ?)"


def _int(value):
    s = (value or "").replace(" ", "").replace(",", "")
    return int(s) if s.isdigit() else None


def valid_https_url(url):
    if not url or len(url) > URL_MAX or any(c.isspace() for c in url) or any(c in url for c in "\"'<>"):
        return False
    parts = urlsplit(url)
    return parts.scheme == "https" and bool(parts.netloc) and "." in parts.netloc


def validate_offer(form, deal_type):
    """(təmiz dəyərlər, xətalar)."""
    errors = []
    title = form["title"]
    if not title:
        errors.append("Başlıq yazın.")
    elif len(title) > TITLE_MAX:
        errors.append(f"Başlıq {TITLE_MAX} simvoldan uzun ola bilməz.")
    price = _int(form["price"])
    lo, hi = BUDGET[deal_type]
    unit = "AZN/ay" if deal_type == "rent" else "AZN"
    if price is None:
        errors.append(f"Qiyməti rəqəmlə yazın ({unit}).")
    elif not lo <= price <= hi:
        errors.append(f"Qiymət {lo:,}–{hi:,} {unit} aralığında olmalıdır.")
    district = form["district"] if form["district"] in BAKU_DISTRICTS else None
    if not district:
        errors.append("Rayonu seçin.")
    rooms = int(form["rooms"]) if form["rooms"].isdigit() and int(form["rooms"]) in ROOMS else None
    if not rooms:
        errors.append("Otaq sayını seçin.")
    area = None
    if form["area"]:
        try:
            area = round(float(form["area"].replace(",", ".")), 1)
        except ValueError:
            area = -1
        if not AREA_RANGE[0] <= area <= AREA_RANGE[1]:
            errors.append(f"Sahə {AREA_RANGE[0]}–{AREA_RANGE[1]:,} m² aralığında olmalıdır.")
    image = (form.get("image_url") or "").strip() or None  # köhnə link sahəsi: formada artıq yoxdur
    if image and not valid_https_url(image):
        errors.append(f"Şəkil linki https:// ilə başlamalı və {URL_MAX} simvoldan qısa olmalıdır.")
    if len(form["description"]) > DESC_MAX:
        errors.append(f"Təsvir {DESC_MAX} simvoldan uzun ola bilməz.")
    return {"title": title, "price": price, "district": district, "rooms": rooms, "area": area,
            "image_url": image, "description": form["description"]}, errors


def _offer_problem(db, request_id, agent_id, lock):
    """Təklif göndərmək olmursa xəta mətni, olursa None. lock=True → tələb sətri FOR UPDATE."""
    req = db.execute("SELECT id, status, created_at, is_demo FROM customer_requests WHERE id = ?"
                     + (" FOR UPDATE" if lock else ""), (request_id,)).fetchone()
    agent = db.execute("SELECT is_demo FROM users WHERE id = ?", (agent_id,)).fetchone()
    if req is None or agent is None or req["is_demo"] != agent["is_demo"]:
        return "Tələb tapılmadı."
    if not request_alive(db, req):
        return "Bu tələb bağlanıb — təklif göndərmək olmur."
    if db.execute("SELECT 1 FROM offers WHERE request_id = ? AND agent_id = ?", (request_id, agent_id)).fetchone():
        return "Bu tələbə artıq təklif göndərmisiniz (geri çəkilmiş təklif də sayılır)."
    if db.execute("SELECT COUNT(*) FROM offers WHERE request_id = ? AND status != 'withdrawn'",
                  (request_id,)).fetchone()[0] >= OFFER_LIMIT:
        return f"Bu tələbə artıq {OFFER_LIMIT} təklif göndərilib — limit dolub."
    return None


def precheck_offer(db, request_id, agent_id):
    """Şəkil yükləməzdən ƏVVƏL kilidsiz yoxlama (boşuna yükləmə olmasın). Oxu tranzaksiyası bağlanır —
    Cloudinary yüklənərkən bazada açıq tranzaksiya qalmır. Son söz send_offer-in FOR UPDATE yoxlamasındadır."""
    error = _offer_problem(db, request_id, agent_id, lock=False)
    db.rollback()
    return error


def reserve_offer_id(db):
    """Təklif id-si əvvəlcədən (şəkillərin Cloudinary qovluğu üçün). Sequence tranzaksiyadan asılı deyil."""
    oid = db.execute("SELECT nextval(pg_get_serial_sequence('offers', 'id'))").fetchone()[0]
    db.rollback()
    return oid


def send_offer(db, request_id, agent_id, clean, offer_id=None, images=()):
    """QISA tranzaksiya: tələb sətri FOR UPDATE ilə kilidlənir, şərtlər yoxlanır, təklif, şəkil sətirləri və müştəri
    bildirişi yazılır. images — artıq Cloudinary-ə yüklənmiş şəkillər (sıra = sort).
    Qaytarır: (offer_id, None) və ya (None, xəta). Uğurda commit edir, xətada rollback (şəkilləri çağıran silir)."""
    error = _offer_problem(db, request_id, agent_id, lock=True)
    if error:
        db.rollback()
        return None, error
    stamp = ts()
    cols = "request_id, agent_id, title, price, district, rooms, area, image_url, description, status, created_at"
    vals = [request_id, agent_id, clean["title"], clean["price"], clean["district"], clean["rooms"], clean["area"],
            clean["image_url"], clean["description"], "sent", stamp]
    if offer_id is not None:
        cols, vals = "id, " + cols, [offer_id] + vals
    oid = db.execute(f"INSERT INTO offers({cols}) VALUES ({','.join('?' for _ in vals)})", vals).lastrowid
    for i, im in enumerate(images):
        db.execute("INSERT INTO offer_images(offer_id, public_id, url, width, height, bytes, sort, created_at) "
                   "VALUES (?,?,?,?,?,?,?,?)", (oid, im["public_id"], im["url"], im.get("width"), im.get("height"),
                                                 im.get("bytes"), i, stamp))
    db.execute("INSERT INTO notifications(user_id, request_id, kind, text, link, is_read, created_at) "
               "VALUES (NULL, ?, 'new_offer', ?, '', 0, ?)", (request_id, f"Yeni təklif: {clean['title']}", stamp))
    db.commit()
    return oid, None


def withdraw_offer(db, offer_id, agent_id):
    n = db.execute("UPDATE offers SET status = 'withdrawn' WHERE id = ? AND agent_id = ? AND status = 'sent'",
                   (offer_id, agent_id)).rowcount
    db.commit()
    return n == 1


# ---------------------------------------------------------------- kabinet siyahısı
def load_ranked(db, agent_id, areas):
    """Aktiv tələblər (bir sorğuda, N+1 yox) + uyğunluq. 5 təklifi dolmuş tələblər çıxarılır
    (makler özü təklif göndəribsə qalır). Qaytarır: [{'r', 'score', 'reasons', 'n_offers', 'my_status'}, ...]."""
    deals = sorted({a["deal_type"] for a in areas})
    if not deals:
        return []
    marks = ",".join("?" for _ in deals)
    rows = db.execute(
        f"SELECT {REQ_COLS}, "
        "(SELECT COUNT(*) FROM offers o WHERE o.request_id = r.id AND o.status != 'withdrawn') AS n_offers, "
        "(SELECT o.status FROM offers o WHERE o.request_id = r.id AND o.agent_id = ?) AS my_status "
        f"FROM customer_requests r WHERE r.status = 'active' AND {SAME_KIND} AND r.deal_type IN ({marks})",
        (agent_id, agent_id, *deals)).fetchall()
    out = []
    for r, sc, reasons in matching.rank(rows, areas):
        if r["n_offers"] >= OFFER_LIMIT and not r["my_status"]:
            continue
        out.append({"r": r, "score": sc, "reasons": reasons, "n_offers": r["n_offers"], "my_status": r["my_status"]})
    return out


def apply_filters(items, f):
    """f: view (good|all), deal, district, rooms, bmin, bmax, sort (score|new|urgency)."""
    out = []
    for it in items:
        r = it["r"]
        if f["view"] == "good" and it["score"] < matching.GOOD_SCORE:
            continue
        if f["deal"] and r["deal_type"] != f["deal"]:
            continue
        if f["district"] and f["district"] not in matching._districts(r["districts"]):
            continue
        if f["rooms"] and r["rooms"] != f["rooms"]:
            continue
        lo, hi = matching.request_range(r["budget_min"], r["budget_max"])  # min boşdursa max × 0.7 (bal ilə eyni)
        if f["bmin"] is not None and hi < f["bmin"]:
            continue
        if f["bmax"] is not None and lo > f["bmax"]:
            continue
        out.append(it)
    if f["sort"] == "new":
        out.sort(key=lambda x: (x["r"]["created_at"], x["r"]["id"]), reverse=True)
    elif f["sort"] == "urgency":
        out.sort(key=lambda x: x["r"]["created_at"], reverse=True)  # bərabər olanlarda ən yeni yuxarıda (stabil)
        out.sort(key=lambda x: (URGENCY_ORDER.get(x["r"]["urgency"], 9), -x["score"]))
    else:
        out.sort(key=lambda x: (x["score"], x["r"]["created_at"]), reverse=True)
    return out


# ---------------------------------------------------------------- müştəri qərarları (Gün 4)
ACCEPT_LIMIT = 3  # bir tələbdə qəbul oluna bilən təklif
CLOSE_REASONS = {"found_here": "MaklerHub ilə tapdım", "found_elsewhere": "Başqa yolla tapdım",
                 "not_needed": "Artıq lazım deyil"}
CUSTOMER_COMPLAINTS = {"fake": "Saxta və ya mövcud olmayan ev", "spam": "Spam", "inappropriate": "Uyğunsuz məzmun",
                       "other": "Digər"}
AGENT_COMPLAINTS = {"fake_request": "Saxta tələb", "wrong_phone": "Nömrə yanlışdır", "other": "Digər"}
COMPLAINT_TEXT_MAX = 300


def _locked_request(db, token):
    """Tələb sətri token ilə FOR UPDATE (təklif qərarları bir-birinə qarışmasın)."""
    return db.execute("SELECT id, status, created_at, is_demo FROM customer_requests WHERE token = ? FOR UPDATE",
                      (token,)).fetchone()


def decide_offer(db, token, offer_id, accept):
    """Müştəri qərarı. Qaytarır: ("ok", None) | ("error", mətn) | ("notfound", None).
    Təklif mütləq HƏMİN tokenin tələbinə aid olmalıdır. Qəbulda contacts sətri yaranır (nömrə maklerə açılır)."""
    req = _locked_request(db, token)
    offer = db.execute("SELECT id, agent_id, status, title FROM offers WHERE id = ? AND request_id = ?",
                       (offer_id, req["id"])).fetchone() if req else None
    if offer is None:
        db.rollback()
        return "notfound", None
    error = None
    if not request_alive(db, req):
        error = "Bu tələb bağlanıb — təkliflərə cavab vermək olmur."
    elif offer["status"] != "sent":
        error = "Bu təklifə artıq cavab vermisiniz."
    elif accept and db.execute("SELECT COUNT(*) FROM offers WHERE request_id = ? AND status = 'accepted'",
                               (req["id"],)).fetchone()[0] >= ACCEPT_LIMIT:
        error = f"Bir tələbdə ən çox {ACCEPT_LIMIT} təklif qəbul etmək olar."
    if error:
        db.rollback()
        return "error", error
    stamp = ts()
    db.execute("UPDATE offers SET status = ?, decided_at = ? WHERE id = ?",
               ("accepted" if accept else "rejected", stamp, offer_id))
    if accept:
        db.execute("INSERT INTO contacts(offer_id, request_id, agent_id, created_at) VALUES (?,?,?,?)",
                   (offer_id, req["id"], offer["agent_id"], stamp))
        text = f"Təklifiniz qəbul edildi: {offer['title']}. Müştərinin nömrəsi açıldı."
    else:
        text = f"Təklifiniz rədd edildi: {offer['title']}"
    db.execute("INSERT INTO notifications(user_id, request_id, kind, text, link, is_read, created_at) "
               "VALUES (?, NULL, ?, ?, ?, 0, ?)",
               (offer["agent_id"], "offer_accepted" if accept else "offer_rejected", text,
                f"/kabinet/teleb/{req['id']}", stamp))
    db.commit()
    return "ok", None


def close_request(db, token, reason):
    if reason not in CLOSE_REASONS:
        return "error", "Bağlama səbəbini seçin."
    req = _locked_request(db, token)
    if req is None:
        db.rollback()
        return "notfound", None
    if req["status"] != "active":
        db.rollback()
        return "error", "Tələb artıq bağlanıb."
    db.execute("UPDATE customer_requests SET status = 'closed', closed_at = ?, close_reason = ? WHERE id = ?",
               (ts(), reason, req["id"]))
    db.commit()
    return "ok", None


def add_complaint(db, side, request_id, reason, text, offer_id=None, agent_id=None):
    """Şikayət yazır. Eyni obyekt üçün eyni tərəfdən təkrar — unikal indeks rədd edir."""
    reasons = CUSTOMER_COMPLAINTS if side == "customer" else AGENT_COMPLAINTS
    if reason not in reasons:
        return "error", "Şikayət səbəbini seçin."
    text = (text or "").strip()
    if len(text) > COMPLAINT_TEXT_MAX:
        return "error", f"Mətn {COMPLAINT_TEXT_MAX} simvoldan uzun ola bilməz."
    try:
        db.execute("INSERT INTO complaints(request_id, offer_id, agent_id, from_side, reason, text, status, created_at) "
                   "VALUES (?,?,?,?,?,?,'new',?)", (request_id, offer_id, agent_id, side, reason, text, ts()))
        db.commit()
    except psycopg.errors.UniqueViolation:
        db.rollback()
        return "error", "Bu barədə artıq şikayət göndərmisiniz."
    return "ok", None


def contacts_for(db, agent_id, offer_ids):
    """Qəbul olunmuş təkliflər üçün müştəri adı və telefonu: {offer_id: {...}}. YALNIZ contacts sətri varsa."""
    if not offer_ids:
        return {}
    rows = db.execute(
        "SELECT c.offer_id, r.name, r.phone FROM contacts c JOIN offers o ON o.id = c.offer_id "
        "JOIN customer_requests r ON r.id = c.request_id "
        "WHERE c.agent_id = ? AND o.status = 'accepted' AND c.offer_id = ANY(?)", (agent_id, list(offer_ids))).fetchall()
    out = {}
    for r in rows:
        digits = (r["phone"] or "").lstrip("+")
        out[r["offer_id"]] = {"name": r["name"] or "Müştəri", "phone": phone_display(r["phone"]),
                              "tel": r["phone"], "wa": f"https://wa.me/{digits}" if digits else None}
    return out


def notify_new_request(db, req):
    """Yeni tələb: aktiv lisenziyalı və aktiv maklerlərdən bal ≥ GOOD_SCORE olanlara bildiriş (bir sorğu + toplu yazma).
    Demo tələb yalnız demo maklerlərə, real tələb yalnız real maklerlərə."""
    rows = db.execute(
        "SELECT a.user_id, a.deal_type, a.district, a.price_min, a.price_max FROM agent_areas a "
        "JOIN users u ON u.id = a.user_id JOIN licenses l ON l.id = u.license_id "
        "WHERE u.role = 'makler' AND u.status = 'active' AND l.status = 'used' AND l.expires_at > ? "
        "AND a.deal_type = ? AND u.is_demo = ?", (ts(), req["deal_type"], int(bool(req.get("is_demo"))))).fetchall()
    by_user = {}
    for r in rows:
        by_user.setdefault(r["user_id"], []).append(r)
    stamp = ts()
    out = []
    for uid, areas in by_user.items():
        res = matching.score(req, areas)
        if res and res[0] >= matching.GOOD_SCORE:
            out.append((uid, req["id"], "new_request", f"Yeni uyğun tələb ({res[0]}%): {req['summary']}",
                        f"/kabinet/teleb/{req['id']}", stamp))
    if out:
        db.executemany("INSERT INTO notifications(user_id, request_id, kind, text, link, is_read, created_at) "
                       "VALUES (?,?,?,?,?,0,?)", out)
    return len(out)
