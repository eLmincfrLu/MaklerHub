"""Avtomatik işin hesabatı (24 saatlıq test üçün). Yalnız oxuyur, bazaya heç nə yazmır.

    PYTHONIOENCODING=utf-8 python -m scraper.report              (son 24 saat)
    PYTHONIOENCODING=utf-8 python -m scraper.report --hours 6

Bölmələr:
1) Dövrlər: hər mənbə üçün sync_runs sayı, uğurlu/uğursuz, müddət (min/median/max), yeni/yenilənən/silinən.
2) Yeni uyğunluqlar və bildirişlər; bloklanmalar (logs) və cooldown vəziyyəti.
3) Bildiriş qapısı (notify_min_id − N): baseline-dan sonra ilk dəfə görülən UYĞUN elanlar — bildiriş getdi /
   nömrəyə görə bildirişsiz qaldı; bildirişsiz qalanların nömrəsi max-dan (notify_min_id) nə qədər aşağıdır.
4) Ziddiyyət yoxlaması: baseline-dan sonra ilk dəfə görülən BÜTÜN elanlarda nömrə ilə ilk görülmə vaxtı —
   özündən əvvəl görülənlərin ən böyük nömrəsindən kiçik nömrəli elanlar (fərqin bölgüsü).
"""
import argparse
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_MARGIN = {"bina": 800, "tap": 2000}


def _buckets(gaps, margin):
    """Fərqlərin bölgüsü: ≤N, ≤5N, ≤50N, >50N (N = ehtiyat aralığı)."""
    edges = [(margin, f"≤{margin}"), (5 * margin, f"≤{5 * margin}"), (50 * margin, f"≤{50 * margin}")]
    out = defaultdict(int)
    for g in gaps:
        for edge, name in edges:
            if g <= edge:
                out[name] += 1
                break
        else:
            out[f">{50 * margin}"] += 1
    return dict(out)


def _stats(values):
    if not values:
        return "—"
    return f"min {min(values)} / median {int(statistics.median(values))} / max {max(values)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24)
    args = ap.parse_args()

    sys.path.insert(0, ROOT)
    from dotenv import load_dotenv
    from maklerhub.db import connect
    from maklerhub.helpers import now, parse, ts
    load_dotenv(os.path.join(ROOT, ".env"))
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL tapılmadı (.env).")
    con = connect(url)
    since = ts(now() - timedelta(hours=args.hours))
    setting = {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM settings")}
    print(f"=== Hesabat: son {args.hours} saat ({since} – {ts()}) ===")

    # 1) dövrlər
    print("\n1) Dövrlər (sync_runs)")
    for s in con.execute("SELECT id, key FROM sources WHERE key IN ('bina', 'tap') ORDER BY sort"):
        runs = con.execute("SELECT * FROM sync_runs WHERE source_id = ? AND started_at >= ? ORDER BY started_at",
                           (s["id"], since)).fetchall()
        ok = [r for r in runs if r["ok"]]
        print(f"  {s['key']}: {len(runs)} dövr ({len(ok)} uğurlu, {len(runs) - len(ok)} uğursuz) | "
              f"müddət san: {_stats([r['duration'] for r in runs])} | yeni={sum(r['new_count'] for r in runs)} "
              f"yenilənən={sum(r['updated'] for r in runs)} silinən={sum(r['removed'] for r in runs)}")
        if runs:
            gaps = [(parse(b["started_at"]) - parse(a["started_at"])).total_seconds() / 60
                    for a, b in zip(runs, runs[1:])]
            if gaps:
                print(f"      dövrlər arası fasilə dəq: min {min(gaps):.0f} / median {statistics.median(gaps):.0f} / "
                      f"max {max(gaps):.0f}")
    labels = defaultdict(list)
    for r in con.execute("SELECT details FROM logs WHERE created_at >= ? AND details LIKE 'job=scraper-%'", (since,)):
        head = r["details"].split("\n")[0].split()
        mode = head[1] if len(head) > 1 else "?"
        coll = next((x.split("=")[1].rstrip("s") for x in head if x.startswith("collect=")), None)
        if coll is not None and coll.isdigit():
            labels[mode].append(int(coll))
    for mode, vals in labels.items():
        print(f"  {mode}: toplama müddəti san: {_stats(vals)} ({len(vals)} mənbə-dövr)")

    # 2) uyğunluq, bildiriş, bloklanma
    print("\n2) Uyğunluq / bildiriş / bloklanma")
    print("  yeni elan:", {r["key"]: r["n"] for r in con.execute(
        "SELECT s.key, COUNT(*) n FROM listings l JOIN sources s ON s.id = l.source_id WHERE l.created_at >= ? "
        "GROUP BY s.key", (since,))})
    print("  yeni uyğunluq:", con.execute("SELECT COUNT(*) FROM matches WHERE created_at >= ?", (since,)).fetchone()[0],
          "| yeni bildiriş:", con.execute("SELECT COUNT(*) FROM notifications WHERE created_at >= ?",
                                         (since,)).fetchone()[0])
    blocks = con.execute("SELECT created_at, source, message FROM logs WHERE created_at >= ? AND level = 'err' "
                         "AND message LIKE 'Scraper dayandı%' ORDER BY created_at", (since,)).fetchall()
    print(f"  uğursuz dövr / bloklanma: {len(blocks)}")
    for b in blocks[:20]:
        print(f"    {b['created_at']} {b['source']}: {b['message'][:150]}")
    state = json.loads(setting.get("scraper_state") or "{}")
    print(f"  scraper_state: cooldown={state.get('cooldown_until') or 'yoxdur'} ardıcıl={state.get('blocks', 0)} "
          f"son bloklanma={state.get('last_block') or '—'} son uğurlu={state.get('last_ok') or '—'}")

    # 3) bildiriş qapısı və 4) ziddiyyət
    base_at = setting.get("notify_baseline_at")
    if not base_at:
        print("\n3-4) Baseline yoxdur (settings.notify_baseline_at) — qapı və ziddiyyət yoxlaması atlanır.")
        con.close()
        return
    start = max(base_at, since)
    print(f"\n3) Bildiriş qapısı (baseline {base_at}; yoxlanan dövr {start}-dən)")
    for key in ("bina", "tap"):
        min_id = int(setting.get(f"notify_min_id_{key}") or 0)
        margin = int(setting.get(f"notify_margin_{key}") or DEFAULT_MARGIN[key])
        floor = min_id - margin
        rows = con.execute(
            "SELECT l.id, l.external_id, l.created_at, "
            "(SELECT COUNT(*) FROM matches m WHERE m.listing_id = l.id) AS nm, "
            "(SELECT COUNT(*) FROM notifications n WHERE n.listing_id = l.id) AS nn "
            "FROM listings l JOIN sources s ON s.id = l.source_id WHERE s.key = ? AND l.created_at > ? "
            "ORDER BY l.created_at, l.id", (key, start)).fetchall()
        rows = [r for r in rows if r["external_id"].isdigit()]
        matched = [r for r in rows if r["nm"]]
        notified = [r for r in matched if r["nn"]]
        gated = [r for r in matched if int(r["external_id"]) <= floor]
        print(f"  {key}: notify_min_id={min_id} N={margin} (hədd {floor}) | ilk dəfə görülən {len(rows)}, "
              f"uyğun {len(matched)}, bildiriş getdi {len(notified)}, nömrəyə görə bildirişsiz {len(gated)}")
        if gated:
            gaps = [min_id - int(r["external_id"]) for r in gated]
            print(f"      bildirişsiz qalanların max-dan aşağı olması: {_stats(gaps)} | bölgü {_buckets(gaps, margin)}")
            for r in gated[:10]:
                print(f"        {r['external_id']} ({r['created_at']}): max-dan {min_id - int(r['external_id'])} aşağı")

        # 4) ziddiyyət: özündən əvvəl görülənlərin ən böyük nömrəsindən kiçik
        running, lower = 0, []
        for r in rows:
            n = int(r["external_id"])
            if running and n < running:
                lower.append(running - n)
            running = max(running, n)
        print(f"  4) {key} ziddiyyət: {len(rows)} ilk dəfə görülən elanın {len(lower)}-i özündən əvvəlkilərin max "
              f"nömrəsindən kiçikdir | fərq: {_stats(lower)} | bölgü {_buckets(lower, margin)}")
        if lower:
            near = sum(1 for g in lower if g <= margin)
            print(f"      ≤N ({margin}) fərqlə: {near} — bunlar 'gec dərc olunmuş yeni elan' ola bilər; "
                  f"böyük fərqlilər çox güman ki irəli çəkilmiş köhnə elanlardır")
    con.close()


if __name__ == "__main__":
    main()
