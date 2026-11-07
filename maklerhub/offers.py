"""Makler təklifləri: forma yoxlaması, göndərmə (limit FOR UPDATE ilə), geri çəkmə; kabinet üçün tələb siyahısı."""
from urllib.parse import urlsplit

from . import matching
from .helpers import BAKU_DISTRICTS, ts
from .teleb import BUDGET, OFFER_LIMIT, ROOMS, URGENCY_ORDER, request_alive

TITLE_MAX = 80
DESC_MAX = 500
URL_MAX = 500
AREA_RANGE = (5, 10_000)
OFFER_STATUS = {"sent": "Göndərilib", "accepted": "Qəbul edildi", "rejected": "Rədd edildi", "withdrawn": "Geri çəkildi"}

# Makler tərəfinə tələbdən gedən sütunlar — telefon və IP burada YOXDUR (heç vaxt seçilmir)
REQ_COLS = ("r.id, r.name, r.deal_type, r.districts, r.rooms, r.budget_min, r.budget_max, r.urgency, r.note, "
            "r.status, r.created_at")


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
    image = form["image_url"] or None
    if image and not valid_https_url(image):
        errors.append(f"Şəkil linki https:// ilə başlamalı və {URL_MAX} simvoldan qısa olmalıdır.")
    if len(form["description"]) > DESC_MAX:
        errors.append(f"Təsvir {DESC_MAX} simvoldan uzun ola bilməz.")
    return {"title": title, "price": price, "district": district, "rooms": rooms, "area": area,
            "image_url": image, "description": form["description"]}, errors


def send_offer(db, request_id, agent_id, clean):
    """Bir tranzaksiyada: tələb sətri FOR UPDATE ilə kilidlənir, şərtlər yoxlanır, təklif və müştəri bildirişi yazılır.
    Qaytarır: (offer_id, None) və ya (None, xəta). Uğurda commit edir, xətada rollback."""
    req = db.execute("SELECT id, status, created_at FROM customer_requests WHERE id = ? FOR UPDATE",
                     (request_id,)).fetchone()
    error = None
    if req is None or not request_alive(db, req):
        error = "Bu tələb bağlanıb — təklif göndərmək olmur."
    elif db.execute("SELECT 1 FROM offers WHERE request_id = ? AND agent_id = ?", (request_id, agent_id)).fetchone():
        error = "Bu tələbə artıq təklif göndərmisiniz (geri çəkilmiş təklif də sayılır)."
    elif db.execute("SELECT COUNT(*) FROM offers WHERE request_id = ? AND status != 'withdrawn'",
                    (request_id,)).fetchone()[0] >= OFFER_LIMIT:
        error = f"Bu tələbə artıq {OFFER_LIMIT} təklif göndərilib — limit dolub."
    if error:
        db.rollback()
        return None, error
    stamp = ts()
    oid = db.execute(
        "INSERT INTO offers(request_id, agent_id, title, price, district, rooms, area, image_url, description, status, "
        "created_at) VALUES (?,?,?,?,?,?,?,?,?,'sent',?)",
        (request_id, agent_id, clean["title"], clean["price"], clean["district"], clean["rooms"], clean["area"],
         clean["image_url"], clean["description"], stamp)).lastrowid
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
        f"FROM customer_requests r WHERE r.status = 'active' AND r.deal_type IN ({marks})",
        (agent_id, *deals)).fetchall()
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
