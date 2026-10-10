"""Scraper CLI. Standart olaraq dry-run: yalnız JSON faylına yazır. --save / --full bazaya yazır.

Nümunələr (layihə kökündən):
    python -m scraper.run bina --selftest
    python -m scraper.run bina --district Yasamal --pages 1            (dry-run)
    python -m scraper.run bina --district all --deal both --pages 1 --save
    python -m scraper.run bina --full --deal rent                       (tam sayğı, ~1,5 saat)
"""
import argparse
import json
import os
import sys
from collections import Counter

from . import bina

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = "numuneler"


def selftest():
    with open(os.path.join(HERE, "sample_bina.json"), encoding="utf-8") as fh:
        sample = json.load(fh)
    rows = [bina.normalize(n, "Yasamal", "Köhnə tikili") for n in sample]
    for r in rows:
        print(r["external_id"], "|", r["title"], "|", r["deal_type"], r["price"], r["price_period"],
              "|", "metro:", r["metro_name"], "| qəsəbə:", r["settlement"],
              "| mərtəbə:", r["floor"], "/", r["floors"],
              "| şübhəli:", r["suspicious"], r["flags"])
    ok = True
    for r in rows:
        if "company" in r or "description" in r or "contactName" in r:
            ok = False
    by_id = {r["external_id"]: r for r in rows}
    checks = [
        ("satıcı adı yoxdur", ok),
        ("20 Yanvar 175 AZN şübhəli", by_id["6533856"]["suspicious"] == 1),
        ("normal kirayə şübhəli deyil", by_id["6501770"]["suspicious"] == 0),
        ("günlük kirayə period=day", by_id["6506163"]["price_period"] == "day"),
        ("metro adı çıxarıldı", by_id["6501770"]["metro_name"] == "Memar Əcəmi"),
        ("qəsəbə metro sayılmadı", by_id["6533870"]["metro_name"] is None),
        ("mərtəbə yoxdursa None", by_id["6310873"]["floor"] is None),
        ("otaq yoxdursa None", by_id["6310873"]["rooms"] is None),
        ("renovation uydurulmur", all(r["renovation"] is None for r in rows)),
    ]
    bad = 0
    for name, passed in checks:
        print(("OK   " if passed else "XƏTA ") + name)
        bad += 0 if passed else 1
    return 1 if bad else 0


def _open_store():
    """DATABASE_URL layihə kökündəki .env-dən oxunur (heç yerdə çap olunmur)."""
    from dotenv import load_dotenv
    from maklerhub.db import connect
    from .store import Store
    load_dotenv(os.path.join(os.path.dirname(HERE), ".env"))
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL tapılmadı (.env).")
    return Store(connect(url))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", choices=["bina"])
    ap.add_argument("--selftest", action="store_true", help="şəbəkəsiz yoxlama")
    ap.add_argument("--deal", choices=["rent", "sale", "both"], default="rent")
    ap.add_argument("--category", choices=["new", "old", "both"], default="both")
    ap.add_argument("--district", default=None, help="rayon adı və ya 'all' (standart: Yasamal, --full ilə all)")
    ap.add_argument("--pages", type=int, default=1, help="hər axın üçün maksimum səhifə (hər səhifə 16 elan)")
    ap.add_argument("--save", action="store_true", help="nəticəni Neon bazasına yaz (store.py)")
    ap.add_argument("--full", action="store_true",
                    help="TAM sayğı: hər axın sonuna qədər oxunur, bazaya yazılır və yoxa çıxan elanlar "
                         "(24 saatdan çox görünməyən) işarələnir. --deal ilə seç: kirayə ~1,5 saat, "
                         "satış ~4 saat, both ~5,5 saat çəkir")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())
    if args.full:
        args.save = True
        args.pages = 100000
    if args.district is None:
        args.district = "all" if args.full else "Yasamal"

    districts = list(bina.DISTRICTS) if args.district == "all" else [args.district]
    for d in districts:
        if d not in bina.DISTRICTS:
            sys.exit("Naməlum rayon: %s. Mümkün: %s" % (d, ", ".join(bina.DISTRICTS)))
    deals = [True, False] if args.deal == "both" else [args.deal == "rent"]
    cats = list(bina.CATEGORIES) if args.category == "both" else [args.category]

    streams = [(d, c, lz) for d in districts for c in cats for lz in deals]
    print(f"{len(streams)} axın, hər biri max {args.pages} səhifə. Sorğular arası 3-5 san.")
    client = bina.BinaClient()
    store = _open_store() if args.save else None
    rows, seen = [], set()
    error = None
    try:
        for i, (d, c, leased) in enumerate(streams, 1):
            cat_id, cat_name = bina.CATEGORIES[c]
            flt = {"cityId": bina.CITY_BAKU, "categoryId": cat_id, "leased": leased,
                   "locationIds": [bina.DISTRICTS[d]]}
            n_stream, total, complete = 0, 0, False
            for total, nodes, has_next in client.pages(flt, args.pages):
                for node in nodes:
                    row = bina.normalize(node, d, cat_name)
                    if row["external_id"] in seen:
                        continue
                    seen.add(row["external_id"])
                    rows.append(row)
                    n_stream += 1
                    if store:
                        store.save(row)
                if store:
                    store.commit()
                complete = not has_next
            removed = ""
            if store and args.full and complete:
                n = store.mark_removed(d, cat_name, "rent" if leased else "sale")
                store.commit()
                removed = f", yoxa çıxan {n}"
            print(f"[{i}/{len(streams)}] {d} | {cat_name} | {'kirayə' if leased else 'satış'}: "
                  f"{n_stream} elan (saytda cəmi {total}){removed}")
    except bina.BlockedError as e:
        error = e
        print("\nDAYANDI:", e)
    except bina.BinaError as e:
        error = e
        print("\nXƏTA:", e)

    if store:
        latency = client.req_time / client.requests if client.requests else 0.0
        label = ("full" if args.full else "save") + f" deal={args.deal} district={args.district}"
        store.finish(ok=error is None, error=error, latency=latency, label=label)
        st = store.stats
        print(f"\nBAZA: yoxlanan={st['checked']} yeni={st['new']} yenilənən={st['updated']} "
              f"(geri qayıdan={st['reactivated']}) yoxa çıxan={st['removed']} şübhəli={st['suspicious']} "
              f"atlanan={st['skipped']} yeni uyğunluq={st['matches']}")
        store.con.close()

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "bina_dry.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)
    print(f"\n{len(rows)} elan {path} faylına yazıldı" + ("." if store else " (bazaya yazılmadı)."))
    if rows:
        print("şübhəli:", sum(r["suspicious"] for r in rows),
              "| mərtəbə yoxdur:", sum(r["floor"] is None for r in rows),
              "| otaq yoxdur:", sum(r["rooms"] is None for r in rows),
              "| metro var:", sum(bool(r["metro_name"]) for r in rows),
              "| period:", dict(Counter(r["price_period"] for r in rows)))


if __name__ == "__main__":
    main()