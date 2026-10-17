"""Uyğunlaşdırma mühərriki.

Hər elan hər aktiv axtarış profili ilə müqayisə olunur və 0–100 arası uyğunluq
balı hesablanır. Bal 60-dan aşağıdırsa uyğunluq saxlanılmır.

Çəkilər:  rayon 25 · büdcə 25 · otaq sayı 20 · əmlak tipi 10 · təmir 15 · metro 5
Sərt şərtlər: əməliyyat növü (satış/kirayə) və rayon mütləq uyğun olmalıdır.
"""
import json
import time
from collections import Counter, defaultdict

from . import dups
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


def _notified(con, profile_id, listing_ids):
    marks = ",".join("?" * len(listing_ids))
    return con.execute(f"SELECT 1 FROM notifications WHERE profile_id = ? AND listing_id IN ({marks}) LIMIT 1",
                       [profile_id] + list(listing_ids)).fetchone() is not None


def visible_source_ids(con, user, enabled=None):
    """Makler üçün açıq mənbələr: admin aktiv edib VƏ makler Ayarlar-da söndürməyib (users.src_<key>).

    enabled: əvvəlcədən oxunmuş aktiv mənbələr (id, key) — toplu emalda bir dəfə oxunur."""
    ids = set()
    if enabled is None:
        enabled = con.execute("SELECT id, key FROM sources WHERE enabled = 1").fetchall()
    for src in enabled:
        col = f"src_{src['key']}"
        if col not in user.keys() or user[col]:
            ids.add(src["id"])
    return ids


def _visible_for(con, cache, user_id):
    if user_id not in cache:
        user = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        cache[user_id] = visible_source_ids(con, user) if user else set()
    return cache[user_id]


def _retarget_notifications(con, profile_id, main_id, ids):
    """Qrupun köhnə bildirişləri əsas nüsxənin kartını açsın."""
    others = [i for i in ids if i != main_id]
    if others:
        con.execute(f"UPDATE notifications SET listing_id = ? WHERE profile_id = ? "
                    f"AND listing_id IN ({','.join('?' * len(others))})", [main_id, profile_id] + others)


def _match_group(con, gid, profiles, when, notify):
    """Təkrar qrupu: uyğunluq yalnız əsas nüsxədədir. Əsas nüsxə hər profil üçün makler üçün açıq mənbələrdən
    seçilir (Bina.az birinci); açıq mənbə yoxdursa uyğunluq yoxdur. Digər nüsxələrin uyğunluğu oraya köçür
    (ilk tapılma vaxtı saxlanır), bildiriş qrup üçün bir dəfə yaranır."""
    group = dups.members(con, gid)
    if not group:
        return 0
    ids = [r["id"] for r in group]
    marks = ",".join("?" * len(ids))
    cache, created = {}, 0
    for p in profiles:
        visible = _visible_for(con, cache, p["user_id"])
        main = next((r for r in group if r["source_id"] in visible), None)
        old = con.execute(f"SELECT id, listing_id, created_at FROM matches WHERE profile_id = ? "
                          f"AND listing_id IN ({marks})", [p["id"]] + ids).fetchall()
        res = evaluate(p, main) if main else None
        for m in old:
            if not main or m["listing_id"] != main["id"]:
                con.execute("DELETE FROM matches WHERE id = ?", (m["id"],))
        if not main:
            continue
        mine = next((m for m in old if m["listing_id"] == main["id"]), None)
        if not res:
            if mine:
                con.execute("DELETE FROM matches WHERE id = ?", (mine["id"],))
            continue
        score, reasons = res
        reasons = json.dumps(reasons, ensure_ascii=False)
        if mine:
            con.execute("UPDATE matches SET score = ?, reasons = ? WHERE id = ?", (score, reasons, mine["id"]))
        elif old:
            # başqa nüsxədə artıq var idi → köçür, yeni uyğunluq sayılmır
            con.execute("INSERT INTO matches(profile_id, listing_id, score, reasons, created_at) "
                        "VALUES (?, ?, ?, ?, ?)", (p["id"], main["id"], score, reasons, min(m["created_at"] for m in old)))
        else:
            con.execute("INSERT INTO matches(profile_id, listing_id, score, reasons, created_at) "
                        "VALUES (?, ?, ?, ?, ?)", (p["id"], main["id"], score, reasons, when or ts()))
            created += 1
            if notify and main["created_at"] > p["created_at"] and not _notified(con, p["id"], ids):
                notify_new_match(con, p, main, score, when)
        _retarget_notifications(con, p["id"], main["id"], ids)
    return created


def match_listing(con, listing, profiles=None, when=None, notify=False):
    """Yeni və ya dəyişmiş elanı aktiv profillərlə yoxlayır. Yeni uyğunluq sayını qaytarır.

    notify=True: YENİ uyğunluq yarananda və elan profildən SONRA bazaya düşübsə bildiriş yazılır.
    Təkrar qrupundakı elan (dup_group) qrupun əsas nüsxəsi üzərindən yoxlanır.
    """
    profiles = _active_profiles(con) if profiles is None else profiles
    if listing.get("dup_group") is not None and listing.get("active", 1):
        return _match_group(con, listing["dup_group"], profiles, when, notify)
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
            if notify and listing["created_at"] > p["created_at"] and not _notified(con, p["id"], [listing["id"]]):
                notify_new_match(con, p, listing, score, when)
    return created


def match_many(con, targets, profiles, notify=None, when=None, timing=None):
    """match_listing()-in toplu variantı (scraper/store.py save_many): nəticə eynidir, amma lazım olan məlumat
    (mövcud uyğunluqlar, bildirişlər, qrup üzvləri, istifadəçilər, müştəri adları) bir dəfə oxunur və
    dəyişikliklər qrup halında yazılır.

    targets: elan sətirləri emal sırası ilə; eyni təkrar qrupundan yalnız birincisi emal olunur (qrup bir dəfə).
    notify: {listing_id: True} — bildiriş yalnız bu elanın vahidi (elan və ya onun qrupu) üçün yaranır.
    timing: Counter — "match" və "notify" fazalarının vaxtı (san) əlavə olunur.
    → {listing_id: (yeni uyğunluq sayı, bildiriş sayı)} hər vahidin birinci elanı üzrə.
    """
    notify = notify or {}
    timing = Counter() if timing is None else timing
    if not targets or not profiles:
        return {}
    t0, t_notify = time.time(), 0.0
    units, keys = [], set()
    for l in targets:
        gid = l["dup_group"] if l.get("dup_group") is not None and l.get("active", 1) else None
        key = ("g", gid) if gid is not None else ("l", l["id"])
        if key not in keys:
            keys.add(key)
            units.append((gid, l))
    gids = sorted({gid for gid, _ in units if gid is not None})
    members = defaultdict(list)
    if gids:
        for r in con.execute(
                f"SELECT l.* FROM listings l JOIN sources s ON s.id = l.source_id WHERE l.dup_group = ANY(?) "
                f"AND l.active = 1 ORDER BY l.dup_group, {dups.PRIMARY_ORDER}", (gids,)):
            members[r["dup_group"]].append(r)
    ids = [l["id"] for gid, l in units if gid is None] + [r["id"] for gid in gids for r in members[gid]]
    pids = [p["id"] for p in profiles]
    existing = {(m["profile_id"], m["listing_id"]): m for m in con.execute(
        "SELECT id, profile_id, listing_id, created_at FROM matches WHERE listing_id = ANY(?) AND profile_id = ANY(?)",
        (ids, pids))}
    visible = {}
    if gids:
        enabled = con.execute("SELECT id, key FROM sources WHERE enabled = 1").fetchall()
        users = con.execute("SELECT * FROM users WHERE id = ANY(?)", (sorted({p["user_id"] for p in profiles}),))
        visible = {u["id"]: visible_source_ids(con, u, enabled) for u in users}
    tn = time.time()
    notified = {(r["profile_id"], r["listing_id"]) for r in con.execute(
        "SELECT DISTINCT profile_id, listing_id FROM notifications WHERE listing_id = ANY(?) AND profile_id = ANY(?)",
        (ids, pids))}
    names = {}
    if any(notify.get(l["id"]) for _, l in units):
        names = {c["id"]: c["name"] for c in con.execute("SELECT id, name FROM clients WHERE id = ANY(?)",
                                                          (sorted({p["client_id"] for p in profiles}),))}
    t_notify += time.time() - tn

    stamp = when or ts()
    dels, upds, ins, notes, moves, out = set(), [], [], [], [], {}

    def note(p, listing, score):
        notes.append((p["user_id"], f"Yeni uyğun elan — {names.get(p['client_id']) or 'müştəri'}",
                      f"{listing['title']} · {score}% uyğunluq", listing["id"], p["id"], stamp))
        notified.add((p["id"], listing["id"]))

    for gid, l in units:
        created = sent = 0
        want = bool(notify.get(l["id"]))
        if gid is None:
            for p in profiles:
                res = evaluate(p, l)
                cur = existing.get((p["id"], l["id"]))
                if not res:
                    if cur:
                        dels.add(cur["id"])
                    continue
                score, reasons = res
                reasons = json.dumps(reasons, ensure_ascii=False)
                if cur:
                    upds.append((score, reasons, cur["id"]))
                    continue
                ins.append((p["id"], l["id"], score, reasons, stamp))
                created += 1
                if want and l["created_at"] > p["created_at"] and (p["id"], l["id"]) not in notified:
                    note(p, l, score)
                    sent += 1
            out[l["id"]] = (created, sent)
            continue
        # təkrar qrupu: _match_group() ilə eyni qaydalar
        group = members[gid]
        ids_ = [r["id"] for r in group]
        for p in profiles:
            main = next((r for r in group if r["source_id"] in visible.get(p["user_id"], set())), None)
            old = [existing[(p["id"], i)] for i in ids_ if (p["id"], i) in existing]
            res = evaluate(p, main) if main else None
            for m in old:
                if not main or m["listing_id"] != main["id"]:
                    dels.add(m["id"])
            if not main:
                continue
            mine = next((m for m in old if m["listing_id"] == main["id"]), None)
            if not res:
                if mine:
                    dels.add(mine["id"])
                continue
            score, reasons = res
            reasons = json.dumps(reasons, ensure_ascii=False)
            if mine:
                upds.append((score, reasons, mine["id"]))
            elif old:
                # başqa nüsxədə artıq var idi → köçür, yeni uyğunluq sayılmır
                ins.append((p["id"], main["id"], score, reasons, min(m["created_at"] for m in old)))
            else:
                ins.append((p["id"], main["id"], score, reasons, stamp))
                created += 1
                if (want and main["created_at"] > p["created_at"]
                        and not any((p["id"], i) in notified for i in ids_)):
                    note(p, main, score)
                    sent += 1
            others = [i for i in ids_ if i != main["id"] and (p["id"], i) in notified]
            if others:  # qrupun köhnə bildirişləri əsas nüsxənin kartını açsın
                moves.append((main["id"], p["id"], others))
                notified.difference_update((p["id"], i) for i in others)
                notified.add((p["id"], main["id"]))
        out[l["id"]] = (created, sent)

    # əvvəl silinənlər: UNIQUE(profile_id, listing_id), köçən uyğunluq əsas nüsxəyə yazılır
    if dels:
        con.execute("DELETE FROM matches WHERE id = ANY(?)", (sorted(dels),))
    if upds:
        con.executemany("UPDATE matches SET score = ?, reasons = ? WHERE id = ?", upds)
    if ins:
        con.executemany("INSERT INTO matches(profile_id, listing_id, score, reasons, created_at) VALUES (?, ?, ?, ?, ?)",
                        ins)
    tn = time.time()
    if moves:
        con.executemany("UPDATE notifications SET listing_id = ? WHERE profile_id = ? AND listing_id = ANY(?)", moves)
    if notes:
        con.executemany("INSERT INTO notifications(user_id, kind, title, body, listing_id, profile_id, created_at) "
                        "VALUES (?, 'new_match', ?, ?, ?, ?, ?)", notes)
    t_notify += time.time() - tn
    timing["notify"] += t_notify
    timing["match"] += time.time() - t0 - t_notify
    return out


def rematch_profile(con, profile_id):
    """Profil yaradıldıqda / dəyişdikdə bütün aktiv elanlara qarşı yenidən hesablayır."""
    p = con.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
    if not p:
        return 0
    # təkrar qruplarında uyğunluq yalnız əsas nüsxəyə yazılır (makler üçün açıq mənbədən);
    # ilk tapılma vaxtı qrup üzrə ən erkən
    groups = dups.ordered_groups(con)
    group_of = {lid: gid for gid, items in groups.items() for lid, _ in items}
    user = con.execute("SELECT * FROM users WHERE id = ?", (p["user_id"],)).fetchone()
    visible = visible_source_ids(con, user) if user else set()
    main_of = {gid: dups.pick(items, visible) for gid, items in groups.items()}
    existing = {}
    for r in con.execute(
            "SELECT m.listing_id, m.created_at FROM matches m JOIN listings l ON l.id = m.listing_id "
            "WHERE m.profile_id = ? AND l.active = 1", (profile_id,)):
        key = ("g", group_of[r["listing_id"]]) if r["listing_id"] in group_of else r["listing_id"]
        existing[key] = min(existing.get(key, r["created_at"]), r["created_at"])
    con.execute(
        "DELETE FROM matches WHERE profile_id = ? AND listing_id IN (SELECT id FROM listings WHERE active = 1)",
        (profile_id,),
    )
    if p["status"] != "active":
        return 0
    stamp = ts()
    count = 0
    for l in con.execute("SELECT * FROM listings WHERE active = 1 AND deal_type = ?", (p["deal_type"],)):
        key = l["id"]
        if l["id"] in group_of:
            key = ("g", group_of[l["id"]])
            if main_of[group_of[l["id"]]] != l["id"]:
                continue
        res = evaluate(p, l)
        if not res:
            continue
        score, reasons = res
        con.execute(
            "INSERT INTO matches(profile_id, listing_id, score, reasons, created_at) VALUES (?, ?, ?, ?, ?)",
            (p["id"], l["id"], score, json.dumps(reasons, ensure_ascii=False), existing.get(key, stamp)),
        )
        count += 1
    notified = {r["listing_id"] for r in con.execute(
        "SELECT DISTINCT listing_id FROM notifications WHERE profile_id = ? AND listing_id IS NOT NULL", (profile_id,))}
    for gid, items in groups.items():
        ids = [lid for lid, _ in items]
        if main_of[gid] and notified & set(ids):
            _retarget_notifications(con, profile_id, main_of[gid], ids)
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
