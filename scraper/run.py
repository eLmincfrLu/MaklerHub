"""Scraper CLI. Standart olaraq dry-run: yalnız JSON faylına yazır. --save / --full bazaya yazır.

Nümunələr (layihə kökündən):
    python -m scraper.run bina --selftest
    python -m scraper.run bina --district Yasamal --pages 1            (dry-run)
    python -m scraper.run bina --district all --deal both --pages 1 --save
    python -m scraper.run bina --full --deal rent                       (tam sayğı, ~1,5 saat)
    python -m scraper.run tap --selftest
    python -m scraper.run tap --deal both --pages 1                     (dry-run)
    python -m scraper.run tap --deal both --pages 1 --save --quiet
"""
import argparse
import json
import os
import sys
from collections import Counter

from . import bina, district_map, tap
from .common import BlockedError, ScrapeError

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = "numuneler"


def _check(checks):
    bad = 0
    for name, passed in checks:
        print(("OK   " if passed else "XƏTA ") + name)
        bad += 0 if passed else 1
    return 1 if bad else 0


def selftest_bina():
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
    return _check([
        ("satıcı adı yoxdur", ok),
        ("20 Yanvar 175 AZN şübhəli", by_id["6533856"]["suspicious"] == 1),
        ("normal kirayə şübhəli deyil", by_id["6501770"]["suspicious"] == 0),
        ("günlük kirayə period=day", by_id["6506163"]["price_period"] == "day"),
        ("metro adı çıxarıldı", by_id["6501770"]["metro_name"] == "Memar Əcəmi"),
        ("qəsəbə metro sayılmadı", by_id["6533870"]["metro_name"] is None),
        ("mərtəbə yoxdursa None", by_id["6310873"]["floor"] is None),
        ("otaq yoxdursa None", by_id["6310873"]["rooms"] is None),
        ("renovation uydurulmur", all(r["renovation"] is None for r in rows)),
        ("district_src = 'bina'", all(r["district_src"] == "bina" for r in rows)),
    ])


def selftest_tap():
    with open(os.path.join(HERE, "sample_tap.json"), encoding="utf-8") as fh:
        sample = json.load(fh)
    # xəritə bazadan qurulur və dəyişə bilər — test sabit xəritə ilə işləyir
    dm = district_map
    saved = dm.METRO, dm.SETTLEMENT, dm.METRO_NAMES, dm.SETTLEMENT_NAMES
    dm.METRO, dm.SETTLEMENT = {"İnşaatçılar": "Yasamal"}, {"Masazır": "Abşeron"}
    dm.METRO_NAMES, dm.SETTLEMENT_NAMES = ["İnşaatçılar", "Memar Əcəmi"], ["Masazır"]
    try:
        rows = [tap.normalize(n, "rent", "month", "Yeni tikili") for n in sample]
        names = [dm.canonical(n, ["İçəri Şəhər", "Həzi Aslanov", "Elmlər Akademiyası", "28 May"])
                 for n in ("İçərişəhər", "H.Aslanov", "Elmlər", "28 may", "Yeni")]
    finally:
        dm.METRO, dm.SETTLEMENT, dm.METRO_NAMES, dm.SETTLEMENT_NAMES = saved
    for r in rows:
        print(r["external_id"], "|", r["title"], "|", r["price"], r["price_period"], "| yer:", r["location_text"],
              "| metro:", r["metro_name"], "| qəsəbə:", r["settlement"], "| rayon:", repr(r["district"]),
              "| şübhəli:", r["suspicious"], r["flags"])
    by_id = {r["external_id"]: r for r in rows}
    return _check([
        ("satıcı/mağaza saxlanmır", all("shop" not in r and "company" not in r for r in rows)),
        ("otaq və sahə başlıqdan", by_id["48902243"]["rooms"] == 3 and by_id["48902243"]["area"] == 137),
        ("'Nərimanov r.' → Nərimanov", by_id["48902243"]["district"] == "Nərimanov"),
        ("'N.Nərimanov r.' → Nərimanov", by_id["47054424"]["district"] == "Nərimanov"),
        ("district_src: başlıq → title, xəritə → map, boş → None",
         by_id["48902243"]["district_src"] == "title" and by_id["48829394"]["district_src"] == "map"
         and by_id["48777695"]["district_src"] == "map" and by_id["48910575"]["district_src"] is None),
        ("yazılış fərqləri Bina.az adına gətirilir",
         names == ["İçəri Şəhər", "Həzi Aslanov", "Elmlər Akademiyası", "28 May", None]),
        ("metro xəritədən: İnşaatçılar → Yasamal", by_id["48829394"]["district"] == "Yasamal"
         and by_id["48829394"]["metro_name"] == "İnşaatçılar"),
        ("qəsəbə xəritədən: Masazır qəs. → Abşeron", by_id["48777695"]["district"] == "Abşeron"
         and by_id["48777695"]["settlement"] == "Masazır" and by_id["48777695"]["metro_name"] is None),
        ("xəritədə olmayan metro → ''", by_id["48910575"]["district"] == ""),
        ("küçə / kompleks adı → ''", by_id["48866086"]["district"] == "" and by_id["48631024"]["district"] == ""),
        ("177 AZN / 60 m² kirayə şübhəli", by_id["44966523"]["suspicious"] == 1),
        ("normal kirayə şübhəli deyil", by_id["48829394"]["suspicious"] == 0),
        ("mərtəbə və təmir None", all(r["floor"] is None and r["floors"] is None and r["renovation"] is None
                                      for r in rows)),
        ("şəkil var", all(r["images"] for r in rows)),
    ])


def _open_store(notify=True, source_key="bina"):
    """DATABASE_URL layihə kökündəki .env-dən oxunur (heç yerdə çap olunmur)."""
    from dotenv import load_dotenv
    from maklerhub.db import connect
    from .store import Store
    load_dotenv(os.path.join(os.path.dirname(HERE), ".env"))
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL tapılmadı (.env).")
    return Store(connect(url), source_key=source_key, notify=notify)


# ---------------------------------------------------------------- axınlar
def bina_streams(args):
    district = args.district or ("all" if args.full else "Yasamal")
    districts = list(bina.DISTRICTS) if district == "all" else [district]
    for d in districts:
        if d not in bina.DISTRICTS:
            sys.exit("Naməlum rayon: %s. Mümkün: %s" % (d, ", ".join(bina.DISTRICTS)))
    deals = [True, False] if args.deal == "both" else [args.deal == "rent"]
    cats = list(bina.CATEGORIES) if args.category == "both" else [args.category]
    streams = []
    for d in districts:
        for c in cats:
            for leased in deals:
                cat_id, cat_name = bina.CATEGORIES[c]
                deal = "rent" if leased else "sale"
                streams.append({
                    "label": f"{d} | {cat_name} | {'kirayə' if leased else 'satış'}",
                    "flt": {"cityId": bina.CITY_BAKU, "categoryId": cat_id, "leased": leased,
                            "locationIds": [bina.DISTRICTS[d]]},
                    "normalize": lambda node, d=d, cat_name=cat_name: bina.normalize(node, d, cat_name),
                    "removal": {"deal_type": deal, "property_type": cat_name, "district": d},
                })
    return streams, f"district={district}"


def tap_streams(args):
    if args.district:
        sys.exit("Tap.az-da rayon filtri yoxdur: --district işlətməyin (rayon başlıqdan/xəritədən tapılır).")
    deals = ["rent", "sale"] if args.deal == "both" else [args.deal]
    cats = list(tap.CATEGORIES) if args.category == "both" else [args.category]
    streams = []
    for c in cats:
        cat_name = tap.CATEGORIES[c][1]
        for deal in deals:
            for period in (["month", "day"] if deal == "rent" else ["total"]):
                label = {"month": "kirayə aylıq", "day": "kirayə günlük", "total": "satış"}[period]
                streams.append({
                    "label": f"Bakı | {cat_name} | {label}",
                    "flt": tap.stream_filter(deal, period, c),
                    "normalize": lambda node, deal=deal, period=period, cat_name=cat_name:
                        tap.normalize(node, deal, period, cat_name),
                    "removal": {"deal_type": deal, "property_type": cat_name, "price_period": period},
                })
    return streams, "district=—"


SOURCES = {
    "bina": {"client": lambda: bina.BinaClient(), "streams": bina_streams, "selftest": selftest_bina, "page": 16},
    "tap": {"client": lambda: tap.TapClient(), "streams": tap_streams, "selftest": selftest_tap, "page": tap.PAGE_SIZE},
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", choices=list(SOURCES))
    ap.add_argument("--selftest", action="store_true", help="şəbəkəsiz yoxlama")
    ap.add_argument("--deal", choices=["rent", "sale", "both"], default="rent")
    ap.add_argument("--category", choices=["new", "old", "both"], default="both")
    ap.add_argument("--district", default=None,
                    help="yalnız bina: rayon adı və ya 'all' (standart: Yasamal, --full ilə all)")
    ap.add_argument("--pages", type=int, default=1,
                    help="hər axın üçün maksimum səhifə (bina: 16, tap: 24 elan/səhifə)")
    ap.add_argument("--save", action="store_true", help="nəticəni Neon bazasına yaz (store.py)")
    ap.add_argument("--full", action="store_true",
                    help="TAM sayğı: hər axın sonuna qədər oxunur, bazaya yazılır və yoxa çıxan elanlar "
                         "(24 saatdan çox görünməyən) işarələnir. --deal ilə seç. Bina.az: kirayə ~1,5 saat, "
                         "satış ~4 saat, both ~5,5 saat. Tap.az: kirayə ~15 dəq, satış ~25 dəq")
    ap.add_argument("--quiet", action="store_true",
                    help="elanlar yazılır və matching işləyir, amma makler bildirişi yaranmır "
                         "(ilk yükləmə / yeni rayon / yeni mənbə üçün)")
    args = ap.parse_args()

    src = SOURCES[args.source]
    if args.selftest:
        sys.exit(src["selftest"]())
    if args.full:
        args.save = True
        args.pages = 100000

    streams, scope = src["streams"](args)
    print(f"{args.source}: {len(streams)} axın, hər biri max {args.pages} səhifə. Sorğular arası 3-5 san.")
    client = src["client"]()
    store = _open_store(notify=not args.quiet, source_key=args.source) if args.save else None
    rows, seen = [], set()
    error = None
    try:
        for i, s in enumerate(streams, 1):
            n_stream, total, complete = 0, None, False
            for total, nodes, has_next in client.pages(s["flt"], args.pages):
                for node in nodes:
                    row = s["normalize"](node)
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
                n = store.mark_removed(**s["removal"])
                store.commit()
                removed = f", yoxa çıxan {n}"
            cem = f" (saytda cəmi {total})" if total is not None else ""
            print(f"[{i}/{len(streams)}] {s['label']}: {n_stream} elan{cem}{removed}")
    except BlockedError as e:
        error = e
        print("\nDAYANDI:", e)
    except ScrapeError as e:
        error = e
        print("\nXƏTA:", e)

    if store:
        latency = client.req_time / client.requests if client.requests else 0.0
        label = ("full" if args.full else "save") + f" deal={args.deal} {scope}"
        store.finish(ok=error is None, error=error, latency=latency, label=label)
        st = store.stats
        print(f"\nBAZA: yoxlanan={st['checked']} yeni={st['new']} yenilənən={st['updated']} "
              f"(geri qayıdan={st['reactivated']}, rayonu dəyişən={st['district_changed']}) yoxa çıxan={st['removed']} şübhəli={st['suspicious']} "
              f"atlanan={st['skipped']} yeni uyğunluq={st['matches']} "
              f"bildiriş={st['notifications']}{' (--quiet: bildiriş söndürülüb)' if args.quiet else ''}")
        store.con.close()

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{args.source}_dry.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)
    print(f"\n{len(rows)} elan {path} faylına yazıldı" + ("." if store else " (bazaya yazılmadı)."))
    if rows:
        n = len(rows)
        found = sum(bool(r["district"]) for r in rows)
        print(f"rayon tapıldı: {found}/{n} ({found * 100 // n}%) | rayon boş: {n - found}",
              "| şübhəli:", sum(r["suspicious"] for r in rows),
              "| günlük kirayə:", sum(r["price_period"] == "day" for r in rows),
              "| mərtəbə yoxdur:", sum(r["floor"] is None for r in rows),
              "| otaq yoxdur:", sum(r["rooms"] is None for r in rows),
              "| metro var:", sum(bool(r["metro_name"]) for r in rows),
              "| period:", dict(Counter(r["price_period"] for r in rows)))


if __name__ == "__main__":
    main()
