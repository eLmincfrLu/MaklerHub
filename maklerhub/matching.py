"""Uyğunlaşdırma mühərriki.

Hər elan hər aktiv axtarış profili ilə müqayisə olunur və 0–100 arası uyğunluq
balı hesablanır. Bal 60-dan aşağıdırsa uyğunluq saxlanılmır.

Çəkilər:  rayon 25 · büdcə 25 · otaq sayı 20 · əmlak tipi 10 · təmir 15 · metro 5
Sərt şərtlər: əməliyyat növü (satış/kirayə) və rayon mütləq uyğun olmalıdır.
"""
import json
import time

from .helpers import ts

CENTER = {"Səbail", "Nəsimi", "Yasamal"}   # "Bakı, mərkəz"
THRESHOLD = 60


def district_ok(profile_district, listing_district):
    if profile_district == listing_district:
        return True
    return profile_district == "Bakı, mərkəz" and listing_district in CENTER


def evaluate(p, l):
    """(bal, səbəblər) və ya None. Səbəb: [növ ('ok'|'warn'), qısa, uzun]."""
    if p["deal_type"] != l["deal_type"]:
        return None
    # Şübhəli qiymətli (o cümlədən AZN olmayan) elan uyğunluğa girmir
    if l.get("suspicious"):
        return None
    # Günlük kirayə aylıq büdcə ilə müqayisə oluna bilməz
    if l.get("price_period") == "day":
        return None
    if not district_ok(p["district"], l["district"]):
        return None

    pt = p["property_type"] or ""
    b = l["building"] or ""
    if pt == "Ofis" and b != "Ofis":
        return None
    if pt != "Ofis" and b == "Ofis":
        return None
    if pt == "Həyət evi" and b != "Həyət evi":
        return None
    if pt and pt != "Həyət evi" and b == "Həyət evi":
        return None

    rent = p["deal_type"] == "rent"
    score = 0
    ok, warn = [], []

    # Rayon — 25
    score += 25
    district_reason = ["ok", "Rayon", "Rayon uyğundur"]

    # Büdcə — 25
    price, lo, hi = l["price"], p["price_min"], p["price_max"]
    budget_reason = ["ok", "Qiymət", "Qiymət uyğundur"] if rent else ["ok", "Büdcə", "Büdcəyə uyğundur"]
    if lo <= price <= hi:
        score += 23 if price > hi - (hi - lo) * 0.1 else 25
        budget_ok = True
    elif price < lo:
        score += 21
        budget_ok = True
    elif price <= hi * 1.05:
        score += 16
        budget_ok = False
        warn.append(["warn", "Büdcədən bir az yuxarı", "Qiymət büdcədən bir az yuxarıdır"])
    elif price <= hi * 1.10:
        score += 9
        budget_ok = False
        warn.append(["warn", "Büdcədən yuxarı", "Qiymət büdcədən 10%-ə qədər yuxarıdır"])
    else:
        return None

    if rent:
        if budget_ok:
            ok.append(budget_reason)
        ok.append(district_reason)
    else:
        ok.append(district_reason)
        if budget_ok:
            ok.append(budget_reason)

    # Otaq sayı — 20
    if p["rooms"]:
        r = l["rooms"] or 0
        good = r >= p["rooms"] if p["rooms_plus"] else r == p["rooms"]
        if good:
            score += 20
            ok.append(["ok", "Otaq sayı", "Otaq sayı uyğundur"])
        elif abs(r - p["rooms"]) == 1:
            score += 7
            warn.append(["warn", "Otaq sayı fərqlidir", "Otaq sayı 1 fərqlənir"])
        else:
            return None
    else:
        score += 20

    # Əmlak tipi — 10
    if pt == "Yeni tikili":
        if b == "Yeni tikili":
            score += 10
        else:
            score += 3
            warn.append(["warn", "Köhnə tikili", "Tikili tipi arzuolunandan fərqlidir"])
    elif pt == "Köhnə tikili":
        if b == "Köhnə tikili":
            score += 10
        else:
            score += 5
            warn.append(["warn", "Yeni tikili", "Tikili tipi arzuolunandan fərqlidir"])
    elif pt == "Studiya":
        if (l["rooms"] or 0) == 1 and l["area"] is not None and l["area"] <= 55:
            score += 10
            ok.append(["ok", "Tip uyğundur", "Tip uyğundur"])
        else:
            score += 4
            warn.append(["warn", "Tip fərqlidir", "Studiya tipində deyil"])
    else:
        score += 10

    # Təmir — 15 ('' = məlum deyil: xəbərdarlıq yoxdur, orta bal verilir)
    ren = l["renovation"] or ""
    if ren == "Təmirli":
        score += 15 if b == "Yeni tikili" else 13
    elif not ren and b == "Yeni tikili":
        score += 11
    elif ren == "Orta təmir":
        score += 4
        warn.append(["warn", "Təmir vəziyyəti arzuolunandan aşağı", "Təmir vəziyyəti arzuolunandan aşağı"])
    elif ren == "Təmirsiz":
        warn.append(["warn", "Təmirsizdir", "Təmirsizdir"])
    else:
        score += 8

    # Metro — 5
    m = l["metro_m"]
    if m is None:
        score += 3
    elif m <= 300:
        score += 5
    elif m <= 600:
        score += 4
    elif m <= 1000:
        score += 2
        if m > 800:
            warn.append(["warn", "Metro məsafəsi bir az uzaq", "Metro məsafəsi bir az uzaq"])
    else:
        warn.append(["warn", "Metro uzaqdır", "Metroya məsafə uzaqdır"])

    # Mərtəbə: birinci və ya sonuncu mərtəbə — kiçik cərimə
    if l["floor"] and l["floors"] and l["floors"] > 3 and (l["floor"] == 1 or l["floor"] == l["floors"]):
        score -= 3
        warn.append(["warn", "Birinci/son mərtəbə", "Birinci və ya sonuncu mərtəbədir"])

    if score < THRESHOLD:
        return None
    reasons = (ok + warn)[:4]
    return min(score, 99), reasons


# ---------------------------------------------------------------- DB əməliyyatları
def _active_profiles(con):
    return con.execute("SELECT * FROM profiles WHERE status = 'active'").fetchall()


def notify_new_match(con, p, listing, score, when=None):
    """Profil yaradılandan sonra gələn elan üçün oxunmamış bildiriş yazır."""
    client = con.execute("SELECT name FROM clients WHERE id = ?", (p["client_id"],)).fetchone()
    con.execute(
        "INSERT INTO notifications(user_id, kind, title, body, listing_id, profile_id, created_at) "
        "VALUES (?, 'new_match', ?, ?, ?, ?, ?)",
        (p["user_id"], f"Yeni uyğun elan — {client['name'] if client else 'müştəri'}",
         f"{listing['title']} · {score}% uyğunluq", listing["id"], p["id"], when or ts()),
    )


def match_listing(con, listing, profiles=None, when=None, notify=False):
    """Yeni və ya dəyişmiş elanı aktiv profillərlə yoxlayır. Yeni uyğunluq sayını qaytarır.

    notify=True: YENİ uyğunluq yarananda və elan profildən SONRA bazaya düşübsə bildiriş yazılır.
    """
    profiles = _active_profiles(con) if profiles is None else profiles
    created = 0
    for p in profiles:
        res = evaluate(p, listing)
        cur = con.execute(
            "SELECT id FROM matches WHERE profile_id = ? AND listing_id = ?", (p["id"], listing["id"])
        ).fetchone()
        if not res:
            if cur:
                con.execute("DELETE FROM matches WHERE id = ?", (cur["id"],))
            continue
        score, reasons = res
        if cur:
            con.execute("UPDATE matches SET score = ?, reasons = ? WHERE id = ?",
                        (score, json.dumps(reasons, ensure_ascii=False), cur["id"]))
        else:
            con.execute(
                "INSERT INTO matches(profile_id, listing_id, score, reasons, created_at) VALUES (?, ?, ?, ?, ?)",
                (p["id"], listing["id"], score, json.dumps(reasons, ensure_ascii=False), when or ts()),
            )
            created += 1
            if notify and listing["created_at"] > p["created_at"]:
                notify_new_match(con, p, listing, score, when)
    return created


def rematch_profile(con, profile_id):
    """Profil yaradıldıqda / dəyişdikdə bütün aktiv elanlara qarşı yenidən hesablayır."""
    p = con.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
    if not p:
        return 0
    existing = {
        r["listing_id"]: r["created_at"]
        for r in con.execute(
            "SELECT m.listing_id, m.created_at FROM matches m JOIN listings l ON l.id = m.listing_id "
            "WHERE m.profile_id = ? AND l.active = 1", (profile_id,))
    }
    con.execute(
        "DELETE FROM matches WHERE profile_id = ? AND listing_id IN (SELECT id FROM listings WHERE active = 1)",
        (profile_id,),
    )
    if p["status"] != "active":
        return 0
    stamp = ts()
    count = 0
    for l in con.execute("SELECT * FROM listings WHERE active = 1 AND deal_type = ?", (p["deal_type"],)):
        res = evaluate(p, l)
        if not res:
            continue
        score, reasons = res
        con.execute(
            "INSERT INTO matches(profile_id, listing_id, score, reasons, created_at) VALUES (?, ?, ?, ?, ?)",
            (p["id"], l["id"], score, json.dumps(reasons, ensure_ascii=False), existing.get(l["id"], stamp)),
        )
        count += 1
    con.execute("UPDATE profiles SET last_checked = ? WHERE id = ?", (stamp, profile_id))
    return count


def rematch_all(con):
    """Bütün profilləri yenidən emal edir. (elan sayı, profil sayı, uyğunluq sayı, müddət) qaytarır."""
    t0 = time.time()
    total = 0
    profile_ids = [r["id"] for r in con.execute("SELECT id FROM profiles")]
    for pid in profile_ids:
        total += rematch_profile(con, pid)
    listings = con.execute("SELECT COUNT(*) FROM listings WHERE active = 1").fetchone()[0]
    active_profiles = con.execute("SELECT COUNT(*) FROM profiles WHERE status = 'active'").fetchone()[0]
    return listings, active_profiles, total, round(time.time() - t0, 1)
