"""İstinad: maklerhub/dups.py group_rows()-un KÖHNƏ (cüt-cüt, O(n²)) variantı — dəyişdirilmədən saxlanılır.

Yeni (indeksli) group_rows bununla birə-bir müqayisə olunur: tests/test_group_rows.py.
"""
from collections import defaultdict

from maklerhub.dups import _Union, _can_inherit, _cross, _pair_ok, _repost


def group_rows_ref(rows, places=None):
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
        bina_items = [r for r in items if r["skey"] == "bina" and r["district"]]
        districts = {r["district"] for r in bina_items}
        if len(districts) == 1:
            d = next(iter(districts))
            for r in items:
                if r["skey"] == "tap" and not r["district"] and _can_inherit(r, bina_items):
                    inherit[r["id"]] = d
    return groups, inherit, ambiguous
