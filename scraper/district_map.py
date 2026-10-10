"""Yer adı (metro / qəsəbə) → rayon xəritəsi (Tap.az elanlarında rayon sahəsi yoxdur).

Xəritə öz biliyimizdən YOX, bazadakı real Bina.az elanlarından qurulur (Bina.az-da rayon filtrdən bəllidir):
- hər metro adı üçün həmin adlı Bina.az elanlarının rayonlarına baxılır;
- yalnız hamısı EYNİ rayonu göstərirsə və ən azı MIN_SAMPLES elan varsa xəritəyə düşür;
- ziddiyyətli (bir neçə rayon) və az nümunəli adlar daxil edilmir → həmin elanlarda district = ''.
"q." və "qəs." eyni sayılır (açarlar şəkilçisiz addır).

Yeniləmək (layihə kökündən; bazadan oxuyur, yalnız bu faylı dəyişir):
    PYTHONIOENCODING=utf-8 python -m scraper.district_map
"""
import os
import re
import sys

MIN_SAMPLES = 2

# --- AVTOMATİK BAŞLANĞIC (python -m scraper.district_map ilə yaradılır, əllə dəyişmə) ---
BUILT_AT = '2026-10-11 01:28:49'
BUILT_FROM = 96  # neçə Bina.az elanından
METRO = {
    '20 Yanvar': 'Yasamal',
    '28 May': 'Nərimanov',
    'Elmlər Akademiyası': 'Yasamal',
    'Gənclik': 'Nərimanov',
    'Koroğlu': 'Nərimanov',
    'Nizami': 'Yasamal',
    'Nəriman Nərimanov': 'Nərimanov',
    'İnşaatçılar': 'Yasamal',
    'İçəri Şəhər': 'Yasamal',
}
SETTLEMENT = {}
# --- AVTOMATİK SON ---

_START = "# --- AVTOMATİK BAŞLANĞIC"
_END = "# --- AVTOMATİK SON ---"


def clean(name):
    """Açar üçün adı təmizləyir: boşluqlar və 'm.' / 'q.' / 'qəs.' şəkilçisi atılır."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    return re.sub(r"\s+(m\.|q\.|qəs\.)$", "", name)


def district_for(metro=None, settlement=None):
    """Xəritədən rayon; tapılmasa ''."""
    if metro and clean(metro) in METRO:
        return METRO[clean(metro)]
    if settlement and clean(settlement) in SETTLEMENT:
        return SETTLEMENT[clean(settlement)]
    return ""


def _group(rows):
    """{ad: {rayon: say}} → (xəritə, hesabat)."""
    result, report = {}, []
    for name in sorted(rows):
        counts = rows[name]
        total = sum(counts.values())
        if len(counts) > 1:
            report.append(("ziddiyyətli", name, counts))
        elif total < MIN_SAMPLES:
            report.append(("az nümunə", name, counts))
        else:
            result[name] = next(iter(counts))
            report.append(("daxil", name, counts))
    return result, report


def build(con):
    """Bazadakı Bina.az elanlarından (metro, qəsəbə) xəritələrini qurur."""
    cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'listings'")}
    base = ("FROM listings l JOIN sources s ON s.id = l.source_id "
            "WHERE s.key = 'bina' AND l.district <> '' AND l.{col} IS NOT NULL AND l.{col} <> ''")

    def collect(col):
        rows = {}
        for r in con.execute(f"SELECT l.{col} AS name, l.district, COUNT(*) AS n {base.format(col=col)} "
                             f"GROUP BY l.{col}, l.district"):
            rows.setdefault(clean(r["name"]), {})
            rows[clean(r["name"])][r["district"]] = rows[clean(r["name"])].get(r["district"], 0) + r["n"]
        return rows

    metro, metro_rep = _group(collect("metro_name"))
    if "settlement" in cols:
        settlement, sett_rep = _group(collect("settlement"))
    else:
        settlement, sett_rep = {}, [("sütun yoxdur", "listings.settlement", {})]
    used = con.execute("SELECT COUNT(*) FROM listings l JOIN sources s ON s.id = l.source_id "
                       "WHERE s.key = 'bina'").fetchone()[0]
    return metro, settlement, used, metro_rep, sett_rep


def _write(metro, settlement, used, stamp):
    path = os.path.abspath(__file__)
    src = open(path, encoding="utf-8").read()
    a = src.index(_START)
    b = src.index(_END)

    def fmt(d):
        if not d:
            return "{}"
        return "{\n" + "".join(f"    {k!r}: {v!r},\n" for k, v in sorted(d.items())) + "}"

    block = (f"{_START} (python -m scraper.district_map ilə yaradılır, əllə dəyişmə) ---\n"
             f"BUILT_AT = {stamp!r}\nBUILT_FROM = {used}  # neçə Bina.az elanından\n"
             f"METRO = {fmt(metro)}\nSETTLEMENT = {fmt(settlement)}\n")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(src[:a] + block + src[b:])


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    from dotenv import load_dotenv
    from maklerhub.db import connect
    from maklerhub.helpers import ts
    load_dotenv(os.path.join(root, ".env"))
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL tapılmadı (.env).")
    con = connect(url)
    try:
        metro, settlement, used, metro_rep, sett_rep = build(con)
    finally:
        con.close()
    _write(metro, settlement, used, ts())
    print(f"{used} Bina.az elanından quruldu. METRO: {len(metro)} ad, SETTLEMENT: {len(settlement)} ad.")
    for title, rep in (("Metro", metro_rep), ("Qəsəbə", sett_rep)):
        print(f"\n{title}:")
        for kind, name, counts in rep:
            print(f"  [{kind}] {name}: " + ", ".join(f"{d}={n}" for d, n in counts.items()))


if __name__ == "__main__":
    main()
