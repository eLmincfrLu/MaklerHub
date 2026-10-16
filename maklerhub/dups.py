"""Təkrar elanlar (dup_group): eyni mənzilin bir neçə elanı bir kartda birləşir. Elanlar silinmir.

Qaydalar (Gün 13):
- Bina ↔ Tap: əməliyyat növü və kirayə müddəti eyni, otaq sayı eyni, tikili tipi eyni, sahə fərqi ≤ 3 m²,
  qiymət fərqi ≤ 5%, rayonlar eyni və ya ikisindən biri boş.
  Cüt yalnız QARŞILIQLI TƏK namizəd olanda birləşir: Tap elanının Bina.az-da bir namizəd qrupu var VƏ həmin
  Bina qrupunun da Tap.az-da bir namizədi var. Çox namizəd → heç nə.
  Yer adı ziddiyyəti də birləşməyə mane olur: hər ikisində metro (qəsəbə) adı var və fərqlidir, və ya Tap-ın
  metrosu (qəsəbəsi) Bina.az elanlarında yalnız BAŞQA rayonlarda görünüb, və ya Tap-ın rayonu boşdur və
  yer adı Bina.az-da heç görünməyib (yoxlamaq olmur).
- Bina ↔ Bina (eyni elanın təkrar yerləşdirilməsi): rayon, otaq, sahə, qiymət, mərtəbə, mərtəbə sayı və yer
  adı (metro/qəsəbə) TAM eyni.
- Tap ↔ Tap birləşmir (mərtəbə yoxdur, oxşar mənzilləri ayırd etmək olmur).
- 3+ elanlı qrupda HƏR cüt yuxarıdakı şərtləri ödəməlidir; ödəmirsə Bina ↔ Tap əlaqəsi qırılır (qrup parçalanır).
- Tap elanının rayonu boşdursa və birləşdiyi Bina.az qrupunun rayonu varsa, rayon oradan götürülür.
- dup_group = qrupdakı ən kiçik listings.id; qrupda olmayan elanda NULL.
- Əsas nüsxə (uyğunluq və kart onun üzərindədir) hər profil üçün ayrıca seçilir: makler üçün açıq olan
  mənbələrdən, Bina.az birinci, sonra ən kiçik id (maklerhub/matching.py). Açıq mənbə yoxdursa kart görünmür.

Hesablama (əməliyyat, kirayə müddəti, otaq) hissəsi üzrə aparılır: təkrar yalnız bu hissənin daxilində ola bilər.
"""
from collections import defaultdict

AREA_TOL = 3        # m²
PRICE_TOL = 0.05    # 5%

COLS = ("l.id, l.source_id, s.key AS skey, l.external_id, l.url, l.district, l.metro_name, l.settlement, l.rooms, "
        "l.area, l.price, l.price_period, l.deal_type, l.property_type, l.floor, l.floors, l.dup_group")


def _cross(t, b, places):
    """Tap (t) və Bina (b) elanı təkrar ola bilərmi (qarşılıqlı təklik yoxlanmadan).

    places: {("metro_name"|"settlement", ad): {Bina.az-da həmin adla görülən rayonlar}}.
    """
    if t["property_type"] != b["property_type"]:
        return False
    if t["area"] is None or b["area"] is None or abs(t["area"] - b["area"]) > AREA_TOL:
        return False
    if abs(t["price"] - b["price"]) > PRICE_TOL * max(t["price"], b["price"]):
        return False
    if not (t["district"] == b["district"] or not t["district"] or not b["district"]):
        return False
    for col in ("metro_name", "settlement"):
        if t[col] and b[col] and t[col] != b[col]:
            return False
        if not t[col]:
            continue
        seen = places.get((col, t[col]))
        if seen and b["district"] not in seen:
            return False
        # rayonu boş Tap elanının yer adı Bina.az-da tanınmırsa yoxlamaq olmur → birləşmir
        if not seen and not t["district"]:
            return False
    return True


_SAME = ("district", "area", "price", "floor", "floors", "metro_name", "settlement")


def _repost(a, b):
    """Bina.az daxilində eyni elanın təkrar yerləşdirilməsi: hər şey tam eyni (sahə və mərtəbə məlum olmalıdır)."""
    if a["skey"] != "bina" or b["skey"] != "bina":
        return False
    if not a["district"] or a["area"] is None or a["floor"] is None or a["floors"] is None:
        return False
    return all(a[k] == b[k] for k in _SAME)


def _pair_ok(a, b, places):
    if a["skey"] == b["skey"]:
        return _repost(a, b)
    t, b = (a, b) if a["skey"] == "tap" else (b, a)
    return _cross(t, b, places)


class _Union:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def join(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[max(a, b)] = min(a, b)


def group_rows(rows, places=None):
    """Bir hissənin aktiv elanları → (qruplar {id: dup_group}, rayon {id: rayon}, çox namizədli {id: say}).

    rows: deal_type, price_period və rooms-u eyni olan elanlar (dict kimi oxunan sətirlər).
    places: bina_places() nəticəsi (yer adı → Bina.az rayonları).
    """
    places = places or {}
    rows = [r for r in rows if r["rooms"] is not None]
    rep = _Union()  # yalnız Bina ↔ Bina təkrar yerləşdirmə (tam bərabərlik → keçişlidir)
    bina = [r for r in rows if r["skey"] == "bina"]
    tap = [r for r in rows if r["skey"] == "tap"]
    for r in rows:
        rep.find(r["id"])
    for i, a in enumerate(bina):
        for b in bina[i + 1:]:
            if _repost(a, b):
                rep.join(a["id"], b["id"])
    # Bina ↔ Tap: qarşılıqlı tək namizəd (Bina qrupları səviyyəsində)
    t2b, b2t = defaultdict(set), defaultdict(set)
    for t in tap:
        for b in bina:
            if _cross(t, b, places):
                t2b[t["id"]].add(rep.find(b["id"]))
                b2t[rep.find(b["id"])].add(t["id"])
    ambiguous = {}
    u = _Union()
    for r in rows:
        u.join(r["id"], rep.find(r["id"]))
    for tid, bgs in t2b.items():
        if len(bgs) == 1 and len(b2t[next(iter(bgs))]) == 1:
            u.join(tid, next(iter(bgs)))
        else:
            ambiguous[tid] = len(bgs)

    members = defaultdict(list)
    for r in rows:
        members[u.find(r["id"])].append(r)
    # 3+ elanlı qrup: hər cüt şərtləri ödəməlidir, əks halda Bina ↔ Tap əlaqəsi qırılır
    final = defaultdict(list)
    for gid, items in members.items():
        clique = all(_pair_ok(a, b, places) for i, a in enumerate(items) for b in items[i + 1:])
        if clique:
            final[gid] = items
        else:
            for r in items:
                final[rep.find(r["id"])].append(r)

    groups, inherit = {}, {}
    for items in final.values():
        if len(items) < 2:
            continue
        gid = min(r["id"] for r in items)
        for r in items:
            groups[r["id"]] = gid
        districts = {r["district"] for r in items if r["skey"] == "bina" and r["district"]}
        if len(districts) == 1:
            d = next(iter(districts))
            for r in items:
                if r["skey"] == "tap" and not r["district"]:
                    inherit[r["id"]] = d
    return groups, inherit, ambiguous


def bina_places(con):
    """{("metro_name"|"settlement", ad): {rayonlar}} — bazadakı bütün Bina.az elanlarından."""
    places = defaultdict(set)
    for col in ("metro_name", "settlement"):
        for r in con.execute(f"SELECT DISTINCT l.{col} AS name, l.district FROM listings l "
                             f"JOIN sources s ON s.id = l.source_id WHERE s.key = 'bina' AND l.{col} IS NOT NULL"):
            places[(col, r["name"])].add(r["district"])
    return places


def _partition_rows(con, deal_type, price_period, rooms):
    return con.execute(
        f"SELECT {COLS} FROM listings l JOIN sources s ON s.id = l.source_id WHERE l.active = 1 "
        "AND l.deal_type = ? AND l.price_period IS NOT DISTINCT FROM ? AND l.rooms = ? ORDER BY l.id",
        (deal_type, price_period, rooms)).fetchall()


def _apply(con, rows, groups, inherit):
    """Bazaya yazır. dup_group və ya rayonu dəyişən elanların id-lərini qaytarır."""
    changed = set()
    for r in rows:
        gid = groups.get(r["id"])
        if r["dup_group"] != gid:
            con.execute("UPDATE listings SET dup_group = ? WHERE id = ?", (gid, r["id"]))
            changed.add(r["id"])
        if r["id"] in inherit and r["district"] != inherit[r["id"]]:
            con.execute("UPDATE listings SET district = ? WHERE id = ?", (inherit[r["id"]], r["id"]))
            changed.add(r["id"])
    return changed


def refresh(con, listing):
    """Bir elanın hissəsini yenidən qruplaşdırır (yeni / dəyişmiş elan üçün). Dəyişən id-lər."""
    if listing["rooms"] is None:
        return set()
    rows = _partition_rows(con, listing["deal_type"], listing["price_period"], listing["rooms"])
    groups, inherit, _ = group_rows(rows, bina_places(con))
    return _apply(con, rows, groups, inherit)


def rebuild(con, save=True):
    """Bütün aktiv elanları sıfırdan qruplaşdırır. (sətirlər, qruplar, rayon alanlar, çox namizədlilər)."""
    if save:
        con.execute("UPDATE listings SET dup_group = NULL WHERE dup_group IS NOT NULL")
    rows = con.execute(f"SELECT {COLS} FROM listings l JOIN sources s ON s.id = l.source_id "
                       "WHERE l.active = 1 AND l.rooms IS NOT NULL ORDER BY l.id").fetchall()
    parts = defaultdict(list)
    for r in rows:
        parts[(r["deal_type"], r["price_period"], r["rooms"])].append(r)
    groups, inherit, ambiguous = {}, {}, {}
    places = bina_places(con)
    for items in parts.values():
        g, i, a = group_rows(items, places)
        groups.update(g)
        inherit.update(i)
        ambiguous.update(a)
    if save:
        _apply(con, rows, groups, inherit)
    return rows, groups, inherit, ambiguous


# ---------------------------------------------------------------- əsas nüsxə
PRIMARY_ORDER = "CASE s.key WHEN 'bina' THEN 0 ELSE 1 END, l.id"


def members(con, gid):
    """Qrupun aktiv elanları, əsas nüsxə birinci."""
    return con.execute(
        f"SELECT l.* FROM listings l JOIN sources s ON s.id = l.source_id WHERE l.dup_group = ? AND l.active = 1 "
        f"ORDER BY {PRIMARY_ORDER}", (gid,)).fetchall()


def ordered_groups(con):
    """{dup_group: [(id, source_id), ...]} — qrupların aktiv elanları, əsas nüsxə sırası ilə."""
    result = defaultdict(list)
    for r in con.execute(
            f"SELECT l.id, l.source_id, l.dup_group FROM listings l JOIN sources s ON s.id = l.source_id "
            f"WHERE l.dup_group IS NOT NULL AND l.active = 1 ORDER BY l.dup_group, {PRIMARY_ORDER}"):
        result[r["dup_group"]].append((r["id"], r["source_id"]))
    return result


def pick(items, visible):
    """Əsas nüsxə: makler üçün açıq mənbədən ilk elan (items: [(id, source_id)]). Yoxdursa None."""
    return next((lid for lid, sid in items if sid in visible), None)
