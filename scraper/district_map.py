"""Yer adı (metro / qəsəbə) → rayon xəritəsi (Tap.az elanlarında rayon sahəsi yoxdur).

Xəritə öz biliyimizdən YOX, bazadakı real Bina.az elanlarından qurulur (Bina.az-da rayon filtrdən bəllidir):
- hər metro adı üçün həmin adlı Bina.az elanlarının rayonlarına baxılır;
- ən azı MAJORITY_SAMPLES (10) elan varsa və onların ≥90%-i eyni rayondadırsa → həmin rayon;
- 2-9 elan varsa yalnız hamısı EYNİ rayonu göstərirsə xəritəyə düşür;
- qalanları (ziddiyyətli və 1 nümunəli) daxil edilmir → həmin elanlarda district = ''.
"q." və "qəs." eyni sayılır (açarlar şəkilçisiz addır).

Tap.az adları yazılışla fərqlənə bilər ("İçərişəhər", "M.Əcəmi", "Elmlər", "28 may"). canonical() onları
YALNIZ Bina.az-da görülən real adlarla tutuşdurur (METRO_NAMES / SETTLEMENT_NAMES); tək bir uyğun ad
tapılmasa ad tanınmamış qalır.

Yeniləmək (layihə kökündən; bazadan oxuyur, yalnız bu faylı dəyişir):
    PYTHONIOENCODING=utf-8 python -m scraper.district_map
"""
import os
import re
import sys

MIN_SAMPLES = 2          # az nümunəli ad (2-9 elan): yalnız 100% eyni rayon
MAJORITY_SAMPLES = 10    # ən azı bu qədər elan VƏ
MAJORITY_SHARE = 0.9     # elanların ≥90%-i eyni rayondadırsa — həmin rayon

# --- AVTOMATİK BAŞLANĞIC (python -m scraper.district_map ilə yaradılır, əllə dəyişmə) ---
BUILT_AT = '2026-10-17 00:06:45'
BUILT_FROM = 832  # neçə Bina.az elanından
METRO = {
    '20 Yanvar': 'Yasamal',
    'Avtovağzal': 'Binəqədi',
    'Azadlıq Prospekti': 'Binəqədi',
    'Bakmil': 'Nərimanov',
    'Dərnəgül': 'Binəqədi',
    'Elmlər Akademiyası': 'Yasamal',
    'Gənclik': 'Nərimanov',
    'Neftçilər': 'Nizami',
    'Nəriman Nərimanov': 'Nərimanov',
    'Nəsimi': 'Binəqədi',
    'Qara Qarayev': 'Nizami',
    'İnşaatçılar': 'Yasamal',
    'Şah İsmayıl Xətai': 'Xətai',
    'Əhmədli': 'Xətai',
}
SETTLEMENT = {
    '7-ci mikrorayon': 'Binəqədi',
    '8-ci mikrorayon': 'Binəqədi',
    '9-cu mikrorayon': 'Binəqədi',
    'Ağ şəhər': 'Xətai',
    'Badamdar': 'Səbail',
    'Bakıxanov': 'Sabunçu',
    'Bayıl': 'Səbail',
    'Biləcəri': 'Binəqədi',
    'Binə': 'Xəzər',
    'Buzovna': 'Xəzər',
    'Günəşli': 'Suraxanı',
    'Hövsan': 'Suraxanı',
    'Həzi Aslanov': 'Xətai',
    'Lökbatan': 'Qaradağ',
    'Masazır': 'Abşeron',
    'Məmmədli': 'Abşeron',
    'Mərdəkan': 'Xəzər',
    'Nardaran': 'Sabunçu',
    'Qaraçuxur': 'Suraxanı',
    'Sabunçu': 'Sabunçu',
    'Sahil': 'Qaradağ',
    'Sea Breeze': 'Sabunçu',
    'Yeni Günəşli': 'Suraxanı',
    'Yeni Yasamal': 'Yasamal',
    'Zabrat': 'Sabunçu',
    'Zığ': 'Suraxanı',
    'Şıxov': 'Səbail',
    'Əhmədli': 'Xətai',
    'Əmircan': 'Suraxanı',
}
METRO_NAMES = [
    '20 Yanvar',
    '28 May',
    '8 Noyabr',
    'Avtovağzal',
    'Azadlıq Prospekti',
    'Bakmil',
    'Dərnəgül',
    'Elmlər Akademiyası',
    'Gənclik',
    'Həzi Aslanov',
    'Koroğlu',
    'Memar Əcəmi',
    'Neftçilər',
    'Nizami',
    'Nəriman Nərimanov',
    'Nəsimi',
    'Qara Qarayev',
    'Sahil',
    'Xalqlar Dostluğu',
    'İnşaatçılar',
    'İçəri Şəhər',
    'Şah İsmayıl Xətai',
    'Əhmədli',
]
SETTLEMENT_NAMES = [
    '20-ci sahə',
    '7-ci mikrorayon',
    '8-ci mikrorayon',
    '9-cu mikrorayon',
    'Ağ şəhər',
    'Badamdar',
    'Bakıxanov',
    'Bayıl',
    'Bibiheybət',
    'Biləcəri',
    'Binə',
    'Binəqədi',
    'Buzovna',
    'Böyükşor',
    'Günəşli',
    'Hövsan',
    'Həzi Aslanov',
    'Köhnə Günəşli',
    'Lökbatan',
    'Masazır',
    'Mehdiabad',
    'Müşfiqabad',
    'Məmmədli',
    'Mərdəkan',
    'NZS',
    'Nardaran',
    'Novxanı',
    'Qaraçuxur',
    'Sabunçu',
    'Sahil',
    'Sea Breeze',
    'Yasamal',
    'Yeni Günəşli',
    'Yeni Yasamal',
    'Zabrat',
    'Zirə',
    'Zığ',
    'Şimal DRES',
    'Şüvəlan',
    'Şıxov',
    'Əhmədli',
    'Ələt',
    'Əmircan',
]
# --- AVTOMATİK SON ---

_START = "# --- AVTOMATİK BAŞLANĞIC"
_END = "# --- AVTOMATİK SON ---"


def clean(name):
    """Açar üçün adı təmizləyir: boşluqlar və 'm.' / 'q.' / 'qəs.' şəkilçisi atılır."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    return re.sub(r"\s+(m\.|q\.|qəs\.)$", "", name)


_FOLD = str.maketrans("əışçğöü", "eiscgou")


def _tokens(name):
    """'M.Əcəmi' → ['m', 'ecemi']: kiçik hərf, Azərbaycan hərfləri latına, nöqtə/defis/boşluqla bölünür."""
    name = (name or "").replace("İ", "i").replace("I", "i").lower().translate(_FOLD)
    return [t for t in re.split(r"[\s.\-]+", name) if t]


def _initial(t):
    return len(t) == 1 and t.isalpha()


def _same(tap, bina):
    """Qısaltma / söz başlanğıcı ('h aslanov' ~ 'hezi aslanov', 'elmler' ~ 'elmler akademiyasi')."""
    if len(tap) <= len(bina) and all(b.startswith(t) if _initial(t) else b == t for t, b in zip(tap, bina)):
        return True
    # baş hərflər atılır: 'n nerimanov' ~ 'nerimanov'
    rest = [t for t in tap if not _initial(t)]
    return bool(rest) and rest != tap and rest == bina


def canonical(name, names):
    """Ad → Bina.az-dakı yazılışı (names siyahısından). Tək uyğunluq yoxdursa None."""
    tap = _tokens(clean(name))
    if not tap:
        return None
    exact = [n for n in names if "".join(_tokens(n)) == "".join(tap)]
    if exact:
        return exact[0] if len(exact) == 1 else None
    loose = [n for n in names if _same(tap, _tokens(n))]
    return loose[0] if len(loose) == 1 else None


def district_for(metro=None, settlement=None):
    """Xəritədən rayon; tapılmasa ''."""
    if metro:
        name = canonical(metro, METRO_NAMES)
        if name in METRO:
            return METRO[name]
    if settlement:
        name = canonical(settlement, SETTLEMENT_NAMES)
        if name in SETTLEMENT:
            return SETTLEMENT[name]
    return ""


def _group(rows):
    """{ad: {rayon: say}} → (xəritə, hesabat)."""
    result, report = {}, []
    for name in sorted(rows):
        counts = rows[name]
        total = sum(counts.values())
        top, n = max(counts.items(), key=lambda kv: kv[1])
        if total >= MAJORITY_SAMPLES and n / total >= MAJORITY_SHARE:
            result[name] = top
            report.append(("daxil" if n == total else f"daxil {n * 100 // total}%", name, counts))
        elif total < MIN_SAMPLES:
            report.append(("az nümunə", name, counts))
        elif len(counts) > 1:
            report.append(("ziddiyyətli", name, counts))
        else:
            result[name] = top
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

    metro_rows = collect("metro_name")
    metro, metro_rep = _group(metro_rows)
    if "settlement" in cols:
        sett_rows = collect("settlement")
        settlement, sett_rep = _group(sett_rows)
    else:
        sett_rows, settlement, sett_rep = {}, {}, [("sütun yoxdur", "listings.settlement", {})]
    used = con.execute("SELECT COUNT(*) FROM listings l JOIN sources s ON s.id = l.source_id "
                       "WHERE s.key = 'bina'").fetchone()[0]
    # bütün Bina.az adları (ziddiyyətlilər də) — Tap.az yazılışlarını tutuşdurmaq üçün
    names = (sorted(metro_rows), sorted(sett_rows))
    return metro, settlement, used, metro_rep, sett_rep, names


def _write(metro, settlement, used, stamp, names):
    path = os.path.abspath(__file__)
    src = open(path, encoding="utf-8").read()
    a = src.index(_START)
    b = src.index(_END)

    def fmt(d):
        if not d:
            return "{}"
        return "{\n" + "".join(f"    {k!r}: {v!r},\n" for k, v in sorted(d.items())) + "}"

    def fmt_list(items):
        if not items:
            return "[]"
        return "[\n" + "".join(f"    {k!r},\n" for k in items) + "]"

    block = (f"{_START} (python -m scraper.district_map ilə yaradılır, əllə dəyişmə) ---\n"
             f"BUILT_AT = {stamp!r}\nBUILT_FROM = {used}  # neçə Bina.az elanından\n"
             f"METRO = {fmt(metro)}\nSETTLEMENT = {fmt(settlement)}\n"
             f"METRO_NAMES = {fmt_list(names[0])}\nSETTLEMENT_NAMES = {fmt_list(names[1])}\n")
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
        metro, settlement, used, metro_rep, sett_rep, names = build(con)
    finally:
        con.close()
    _write(metro, settlement, used, ts(), names)
    print(f"{used} Bina.az elanından quruldu. METRO: {len(metro)} ad, SETTLEMENT: {len(settlement)} ad.")
    for title, rep in (("Metro", metro_rep), ("Qəsəbə", sett_rep)):
        print(f"\n{title}:")
        for kind, name, counts in rep:
            print(f"  [{kind}] {name}: " + ", ".join(f"{d}={n}" for d, n in counts.items()))


if __name__ == "__main__":
    main()
