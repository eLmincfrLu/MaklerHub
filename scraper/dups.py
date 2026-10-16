"""Təkrar elanları bütün baza üzrə yenidən qruplaşdırır və hesabat verir (qaydalar: maklerhub/dups.py).

    PYTHONIOENCODING=utf-8 python -m scraper.dups            (dry-run: yalnız hesabat, bazaya yazmır)
    PYTHONIOENCODING=utf-8 python -m scraper.dups --save     (dup_group + rayon yazılır, matching yenidən)
"""
import argparse
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))


def _line(r):
    area = f"{r['area']:g} m²" if r["area"] is not None else "—"
    return (f"    {r['skey']:4} {r['district'] or '(rayon boş)':12} {r['rooms']} otaq  {area:9} "
            f"{r['price']:>8} AZN  {r['property_type'] or ''}  {r['url']}")


def _risks(items, inherit):
    """Yanlış birləşmə əlamətləri (hesabat üçün); çox əlamət = daha riskli."""
    out = []
    if len(items) > 2:
        out.append(f"{len(items)} elan bir qrupda")
    prices = [r["price"] for r in items]
    if (max(prices) - min(prices)) > 0.03 * max(prices):
        out.append(f"qiymət fərqi {(max(prices) - min(prices)) * 100 / max(prices):.1f}%")
    areas = [r["area"] for r in items]
    if max(areas) - min(areas) > 2:
        out.append(f"sahə fərqi {max(areas) - min(areas):g} m²")
    taken = [r for r in items if r["id"] in inherit]
    if taken:
        out.append("Tap rayonu Bina-dan götürülür")
        if all(not r["metro_name"] and not r["settlement"] for r in taken):
            out.append("Tap-da metro/qəsəbə yoxdur (rayonu yoxlamaq olmur)")
    if any(r["price_period"] == "day" for r in items):
        out.append("günlük kirayə (oxşar elanlar çox olur)")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="bazaya yaz və matching-i yenidən işlət")
    ap.add_argument("--samples", type=int, default=10)
    args = ap.parse_args()

    sys.path.insert(0, os.path.dirname(HERE))
    from dotenv import load_dotenv
    from maklerhub import dups, matching
    from maklerhub.db import connect
    load_dotenv(os.path.join(os.path.dirname(HERE), ".env"))
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL tapılmadı (.env).")
    con = connect(url)
    matches_before = con.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    rows, groups, inherit, ambiguous = dups.rebuild(con, save=args.save)

    by_id = {r["id"]: r for r in rows}
    members = defaultdict(list)
    for lid, gid in groups.items():
        members[gid].append(by_id[lid])
    kinds = Counter()
    for items in members.values():
        c = Counter(r["skey"] for r in items)
        kinds["bina ↔ tap" if len(c) > 1 else f"yalnız {next(iter(c))} (təkrar yerləşdirmə)"] += 1
    print(f"{len(rows)} aktiv elan → {len(members)} qrup ({sum(len(v) for v in members.values())} elan):")
    for k, n in kinds.most_common():
        print(f"  {k}: {n}")
    print(f"Tap elanı Bina.az-dan rayon alır: {len(inherit)}")
    print(f"Birləşdirilmədi (çox namizəd, qarşılıqlı tək deyil): {len(ambiguous)} Tap elanı/qrupu")

    ordered = sorted(members.values(), key=lambda v: (len({r["skey"] for r in v}) == 1, v[0]["id"]))
    risky = [(v, _risks(v, inherit)) for v in ordered]
    clean = [v for v, rk in risky if not rk]
    print(f"\n--- {args.samples} nümunə (riskli əlaməti olmayanlar) ---")
    for v in clean[:args.samples]:
        print(f"  qrup #{min(r['id'] for r in v)}")
        for r in v:
            print(_line(r))
    flagged = sorted([(v, rk) for v, rk in risky if rk], key=lambda x: -len(x[1]))
    print(f"\n--- Şübhəli görünən qruplar: {len(flagged)} ---")
    for v, rk in flagged:
        print(f"  qrup #{min(r['id'] for r in v)}: " + "; ".join(rk))
        for r in v:
            print(_line(r))
    if inherit:
        print("\n--- Rayon alan Tap elanları ---")
        for lid, d in sorted(inherit.items()):
            r = by_id[lid]
            print(f"    {r['external_id']} → {d}  (metro: {r['metro_name']}, qəsəbə: {r['settlement']})")

    if args.save:
        res = matching.rematch_all(con)  # bildirişlər də əsas nüsxəyə yönəlir
        con.commit()
        after = con.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        print(f"\nBAZA: yazıldı. rematch (elan, profil, uyğunluq, san) = {res}; matches {matches_before} → {after}")
    else:
        print("\n(dry-run: bazaya yazılmadı; --save ilə yaz)")
    con.close()


if __name__ == "__main__":
    main()
