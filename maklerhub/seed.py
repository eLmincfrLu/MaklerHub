"""Nümunə (demo) məlumatlar — dizayn fayllarındakı məlumatların eynisi, cari tarixə uyğunlaşdırılmış.

Giriş hesabları mühit dəyişənlərindən gəlir (.env və ya Render Environment):
    MAKLERHUB_ADMIN_USERNAME, MAKLERHUB_ADMIN_PASSWORD,
    MAKLERHUB_DEMO_EMAIL, MAKLERHUB_DEMO_PASSWORD
"""
import json
import random
from datetime import timedelta

from werkzeug.security import generate_password_hash

from . import matching
from .db import add_log
from .helpers import day_start, now, ts
from .sources import SOURCE_DEFS, gen_for_profile, gen_listing, insert_listing

import os

ADMIN_EMAIL = os.environ.get("MAKLERHUB_ADMIN_USERNAME", "admin").strip().lower()
ADMIN_PASSWORD = os.environ.get("MAKLERHUB_ADMIN_PASSWORD", "")
DEMO_EMAIL = os.environ.get("MAKLERHUB_DEMO_EMAIL", "").strip().lower()
DEMO_PASSWORD = os.environ.get("MAKLERHUB_DEMO_PASSWORD", "")

MALE = ["Rəşad", "Kənan", "Tural", "Anar", "Elçin", "Vüqar", "Ramil", "Samir", "Nicat", "Ülvi", "Emil", "Cavid",
        "Fuad", "Rövşən", "Elnur", "Zaur", "İlkin", "Toğrul", "Namiq", "Orxan", "Seymur", "Mahir", "Hikmət"]
FEMALE = ["Aysel", "Günay", "Nərmin", "Səbinə", "Leyla", "Aynur", "Könül", "Lalə", "Fidan", "Nigar", "Ülkər",
          "Sevinc", "Zəhra", "Aytən", "Gülnar", "Nazlı", "Türkan", "Arzu"]
SURNAMES = ["Məmmədov", "Əliyev", "Həsənov", "Hüseynov", "Quliyev", "İsmayılov", "Abbasov", "Rzayev", "Nəbiyev",
            "Kərimov", "Cəfərov", "Səfərov", "Vəliyev", "Bağırov", "Orucov", "Babayev", "Qasımov", "Mustafayev",
            "Sadıqov", "Əhmədov", "Zeynalov", "Paşayev"]
TRANSLIT = str.maketrans({"ə": "e", "ı": "i", "ö": "o", "ü": "u", "ç": "c", "ş": "s", "ğ": "g",
                          "Ə": "e", "İ": "i", "Ö": "o", "Ü": "u", "Ç": "c", "Ş": "s", "Ğ": "g"})


def _surname(s, female):
    return s + "a" if female and s.endswith("v") else s


def _phone(rng):
    return f"+994 {rng.choice(['50', '51', '55', '70', '77', '99', '10'])} {rng.randint(100, 999)} " \
           f"{rng.randint(10, 99)} {rng.randint(10, 99)}"


def seed_minimal(con):
    """Boş baza: mənbələr, admin hesabı və bir makler hesabı."""
    for name, val in (("MAKLERHUB_ADMIN_PASSWORD", ADMIN_PASSWORD), ("MAKLERHUB_DEMO_EMAIL", DEMO_EMAIL),
                      ("MAKLERHUB_DEMO_PASSWORD", DEMO_PASSWORD)):
        if not val:
            raise RuntimeError(f"{name} mühit dəyişəni təyin olunmayıb.")
    n = now()
    for i, (key, name, logo, url) in enumerate(SOURCE_DEFS):
        con.execute("INSERT OR IGNORE INTO sources(key, name, logo, base_url, sort, last_sync) VALUES (?,?,?,?,?,?)",
                    (key, name, logo, url, i, None))
    con.execute(
        "INSERT OR IGNORE INTO users(first_name, last_name, email, password_hash, role, plan, status, "
        "password_changed_at, created_at) VALUES ('Admin', '', ?, ?, 'admin', 'none', 'active', ?, ?)",
        (ADMIN_EMAIL, generate_password_hash(ADMIN_PASSWORD), ts(n), ts(n)),
    )
    con.execute(
        "INSERT OR IGNORE INTO users(first_name, last_name, email, password_hash, role, plan, status, "
        "password_changed_at, created_at) VALUES ('Elmin', '', ?, ?, 'makler', 'pro', 'active', ?, ?)",
        (DEMO_EMAIL, generate_password_hash(DEMO_PASSWORD), ts(n), ts(n)),
    )


def seed(con):
    rng = random.Random(2026)
    N = now()
    today = day_start(N)
    pw = generate_password_hash(DEMO_PASSWORD)

    def at(dt):
        """Bu günə aid vaxt gələcəkdədirsə, indiyə sıxışdırır."""
        return min(dt, N - timedelta(minutes=1))

    # --- mənbələr
    src = {}
    for i, (key, name, logo, url) in enumerate(SOURCE_DEFS):
        cur = con.execute("INSERT INTO sources(key, name, logo, base_url, sort) VALUES (?,?,?,?,?)",
                          (key, name, logo, url, i))
        src[key] = cur.lastrowid

    # --- admin
    con.execute(
        "INSERT INTO users(first_name, last_name, email, password_hash, role, plan, status, password_changed_at, "
        "created_at) VALUES ('Admin', '', ?, ?, 'admin', 'none', 'active', ?, ?)",
        (ADMIN_EMAIL, generate_password_hash(ADMIN_PASSWORD), ts(N - timedelta(days=12)),
         ts(N - timedelta(days=400))),
    )

    # --- maklerlər (dizayndakılar)
    def add_user(first, last, email, phone, plan, status, created, trial_ends=None, license_no=""):
        cur = con.execute(
            "INSERT INTO users(first_name, last_name, email, phone, password_hash, role, license_no, plan, status, "
            "trial_ends, password_changed_at, created_at) VALUES (?,?,?,?,?,'makler',?,?,?,?,?,?)",
            (first, last, email, phone, pw, license_no, plan, status, trial_ends and ts(trial_ends), ts(created),
             ts(created)),
        )
        return cur.lastrowid

    elvin = add_user("Elvin", "Məmmədov", "elvin@nümunə.az", "+994 50 123 45 67", "pro", "active",
                     today - timedelta(days=4) + timedelta(hours=10, minutes=12), license_no="MH-204518")
    sabina = add_user("Səbinə", "Rzayeva", "sabina@nümunə.az", "+994 55 311 22 41", "pro", "active",
                      today - timedelta(days=6) + timedelta(hours=11, minutes=3))
    orxan = add_user("Orxan", "Nəbiyev", "orxan@nümunə.az", "+994 70 402 18 90", "start", "active",
                     today - timedelta(days=9) + timedelta(hours=15, minutes=40))
    ferid = add_user("Fərid", "Quliyev", "ferid@nümunə.az", "+994 51 777 30 12", "start", "trial",
                     today - timedelta(days=12) + timedelta(hours=13, minutes=20),
                     trial_ends=N + timedelta(days=4))
    leyla = add_user("Leyla", "Kərimova", "leyla@nümunə.az", "+994 99 245 61 08", "none", "suspended",
                     today - timedelta(days=15) + timedelta(hours=9, minutes=5))

    # --- digər maklerlər (ümumi say 52)
    others = []
    used = set()
    while len(others) < 47:
        female = rng.random() < 0.45
        first = rng.choice(FEMALE if female else MALE)
        last = _surname(rng.choice(SURNAMES), female)
        email = f"{first.translate(TRANSLIT).lower()}.{last.translate(TRANSLIT).lower()}@nümunə.az"
        if email in used:
            continue
        used.add(email)
        created = N - timedelta(days=rng.randint(20, 320), hours=rng.randint(0, 12))
        others.append(add_user(first, last, email, _phone(rng), rng.choice(["pro", "pro", "start"]), "active",
                               created))

    # --- Elvinin müştəriləri və axtarış profilləri (dizayndakı kimi)
    def add_client(uid, number, name, phone, status, last_contact, created, closed_at=None):
        cur = con.execute(
            "INSERT INTO clients(user_id, number, name, phone, status, last_contact, closed_at, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (uid, number, name, phone, status, ts(last_contact), closed_at and ts(closed_at), ts(created)),
        )
        return cur.lastrowid

    def add_profile(uid, cid, district, rooms, plus, lo, hi, deal, ptype, status, last_checked, created,
                    paused_at=None):
        cur = con.execute(
            "INSERT INTO profiles(user_id, client_id, district, rooms, rooms_plus, price_min, price_max, deal_type, "
            "property_type, status, last_checked, paused_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, cid, district, rooms, plus, lo, hi, deal, ptype, status, ts(last_checked),
             paused_at and ts(paused_at), ts(created)),
        )
        return cur.lastrowid

    ec = today - timedelta(days=4) + timedelta(hours=10, minutes=30)
    c_ahmed = add_client(elvin, 1, "Ahməd Quliyev", "+994 50 123 45 67", "active",
                         at(today + timedelta(hours=9, minutes=14)), ec)
    c_rauf = add_client(elvin, 4, "Rauf Səfərov", "+994 55 234 56 78", "active",
                        today - timedelta(days=1) + timedelta(hours=18, minutes=40), ec + timedelta(minutes=9))
    c_murad = add_client(elvin, 2, "Murad Əliyev", "+994 70 345 67 89", "active", N - timedelta(days=3, hours=2),
                         ec + timedelta(minutes=3))
    c_nigar = add_client(elvin, 5, "Nigar Hüseynova", "+994 51 456 78 90", "active", N - timedelta(hours=5),
                         ec + timedelta(minutes=12))
    c_kamran = add_client(elvin, 3, "Kamran Vəliyev", "+994 77 567 89 01", "deal", N - timedelta(days=14),
                          ec + timedelta(minutes=6), closed_at=N - timedelta(days=14))
    c_tural = add_client(elvin, 6, "Tural Cəfərov", "+994 99 678 90 12", "archived", N - timedelta(days=3, hours=4),
                         ec + timedelta(minutes=15))

    p_ahmed = add_profile(elvin, c_ahmed, "Nərimanov", 3, 0, 200000, 250000, "sale", "Yeni tikili", "active",
                          N - timedelta(minutes=12), ec)
    p_rauf = add_profile(elvin, c_rauf, "Yasamal", 2, 0, 900, 1200, "rent", "", "active",
                         N - timedelta(minutes=12), ec + timedelta(minutes=9))
    p_murad = add_profile(elvin, c_murad, "Xətai", 4, 1, 280000, 320000, "sale", "", "active",
                          N - timedelta(minutes=27), ec + timedelta(minutes=3))
    p_nigar = add_profile(elvin, c_nigar, "Səbail", 1, 0, 120000, 150000, "sale", "Studiya", "active",
                          N - timedelta(minutes=40), ec + timedelta(minutes=12))
    add_profile(elvin, c_tural, "Bakı, mərkəz", None, 0, 350000, 500000, "sale", "Ofis", "paused",
                N - timedelta(days=3), ec + timedelta(minutes=15), paused_at=N - timedelta(days=3))
    p_kamran = add_profile(elvin, c_kamran, "Nəsimi", 2, 0, 180000, 210000, "sale", "", "closed",
                           N - timedelta(days=14), ec + timedelta(minutes=6))

    # --- digər maklerlərin müştəriləri və profilləri
    counts = {sabina: 11, orxan: 3, ferid: 1, leyla: 0}
    rest = 316 - 6 - sum(counts.values())
    per = [2] * len(others)
    left = rest - sum(per)
    while left > 0:
        i = rng.randrange(len(per))
        if per[i] < 11:
            per[i] += 1
            left -= 1
    counts.update(dict(zip(others, per)))
    for uid, cnt in counts.items():
        for num in range(1, cnt + 1):
            female = rng.random() < 0.45
            name = f"{rng.choice(FEMALE if female else MALE)} {_surname(rng.choice(SURNAMES), female)}"
            status = rng.choices(["active", "deal", "archived"], [74, 13, 13])[0]
            created = N - timedelta(days=rng.randint(1, 90), hours=rng.randint(0, 20))
            cid = add_client(uid, num, name, _phone(rng), status, N - timedelta(hours=rng.randint(1, 400)), created,
                             closed_at=created + timedelta(days=rng.randint(1, 20)) if status == "deal" else None)
            if status == "active" and rng.random() > 0.93:
                continue
            district = rng.choice(list(matching.CENTER) + ["Nərimanov", "Xətai", "Nizami", "Binəqədi", "Xəzər",
                                                           "Sabunçu", "Suraxanı", "Qaradağ", "Bakı, mərkəz"])
            deal = "rent" if rng.random() < 0.25 else "sale"
            ptype = rng.choice(["", "", "", "Yeni tikili", "Köhnə tikili"])
            rooms = rng.choice([1, 2, 2, 3, 3, 4])
            sample = gen_listing(rng, district=district if district != "Bakı, mərkəz" else "Nəsimi", deal=deal,
                                 rooms=rooms, building="Yeni tikili")
            step = 50 if deal == "rent" else 10000
            lo = max(step, int(sample["price"] * 0.85 / step) * step)
            hi = int(sample["price"] * 1.15 / step + 1) * step
            pstatus = {"active": "active", "deal": "closed", "archived": "paused"}[status]
            add_profile(uid, cid, district, rooms, 1 if rooms == 4 else 0, lo, hi, deal, ptype, pstatus,
                        N - timedelta(minutes=rng.randint(1, 40)), created,
                        paused_at=created + timedelta(days=5) if pstatus == "paused" else None)

    # --- Elvinin profillərinə uyğun bugünkü elanlar (dizayndakı kartlar)
    def L(district, rooms, area, building, renovation, floor, floors, metro, price, deal="sale", suffix=None):
        title = f"{rooms} otaqlı, {district} r."
        if suffix is None:
            suffix = "kirayə" if deal == "rent" else ("studiya" if rooms == 1 and area <= 45 else "")
        return dict(district=district, rooms=rooms, area=area, building=building, renovation=renovation,
                    floor=floor, floors=floors, metro_m=metro, price=price, deal_type=deal, title=title, suffix=suffix)

    crafted = [
        # (mənbə, elan, neçə dəqiqə əvvəl)
        ("bina", L("Nərimanov", 3, 92, "Yeni tikili", "", 7, 12, 400, 235000), 14),
        ("lalafo", L("Yasamal", 2, 61, "Köhnə tikili", "Təmirli", 3, 9, 900, 1150, "rent"), 31),
        ("tap", L("Xətai", 4, 128, "Köhnə tikili", "Orta təmir", 2, 5, None, 310000), 47),
        ("bina", L("Nərimanov", 3, 88, "Yeni tikili", "", 9, 14, 650, 230000), 75),
        ("lalafo", L("Səbail", 1, 42, "Köhnə tikili", "Təmirli", 5, 9, 700, 138000), 96),
        ("tap", L("Nərimanov", 3, 95, "Yeni tikili", "", 4, 16, 700, 245000), 128),
        ("lalafo", L("Nərimanov", 3, 101, "Yeni tikili", "Orta təmir", 12, 17, 450, 248000), 150),
        ("bina", L("Nərimanov", 3, 84, "Köhnə tikili", "Təmirli", 3, 9, 300, 210000), 185),
        ("bina", L("Yasamal", 2, 58, "Köhnə tikili", "Təmirli", 5, 9, 300, 1000, "rent"), 210),
        ("tap", L("Yasamal", 2, 70, "Köhnə tikili", "Orta təmir", 6, 9, 1100, 1100, "rent"), 240),
        ("bina", L("Yasamal", 2, 64, "Yeni tikili", "Təmirli", 8, 16, 450, 1200, "rent"), 262),
        ("bina", L("Xətai", 5, 150, "Yeni tikili", "Təmirli", 8, 16, 600, 318000), 290),
        ("bina", L("Səbail", 1, 38, "Yeni tikili", "Təmirli", 11, 20, 350, 145000), 320),
        ("lalafo", L("Səbail", 1, 47, "Köhnə tikili", "Orta təmir", 2, 5, 800, 150000), 355),
        ("bina", L("Nərimanov", 3, 90, "Yeni tikili", "", 2, 12, 1100, 255000), 60 * 24 + 180),  # dünən
    ]
    elvin_profiles = [con.execute("SELECT * FROM profiles WHERE id = ?", (i,)).fetchone()
                      for i in (p_ahmed, p_rauf, p_murad, p_nigar)]
    crafted_rows = []
    for key, data, mins_ago in crafted:
        match_time = N - timedelta(minutes=mins_ago)
        if mins_ago < 60 * 24:
            match_time = max(match_time, today + timedelta(minutes=1 + len(crafted_rows)))
            match_time = min(match_time, N - timedelta(seconds=30))
        posted = match_time - timedelta(minutes=rng.randint(4, 17))
        row = insert_listing(con, src[key], data, created_at=ts(match_time - timedelta(minutes=1)),
                             posted_at=ts(posted))
        crafted_rows.append(row)
        matching.match_listing(con, row, elvin_profiles, when=ts(match_time))

    # 2-ci Nərimanov elanı: 22 gündə 2 dəfə ucuzlaşıb (250,000 → 240,000 → 230,000)
    drop = crafted_rows[3]
    con.execute("DELETE FROM price_history WHERE listing_id = ?", (drop["id"],))
    for days, price in ((22, 250000), (12, 240000), (2, 230000)):
        con.execute("INSERT INTO price_history(listing_id, price, recorded_at) VALUES (?,?,?)",
                    (drop["id"], price, ts(N - timedelta(days=days))))

    # --- fon elanları (cəmi aktiv elan 142: Bina 81 · Lalafo 43 · Tap 18)
    bg_districts = ["Binəqədi", "Nizami", "Sabunçu", "Suraxanı", "Xəzər", "Qaradağ", "Nəsimi"]
    targets = {"bina": 81, "lalafo": 43, "tap": 18}
    have = {"bina": 0, "lalafo": 0, "tap": 0}
    for key, _, _ in crafted[:-1]:
        have[key] += 1
    have["bina"] += 1
    all_profiles = con.execute("SELECT * FROM profiles WHERE status = 'active' AND user_id != ?", (elvin,)).fetchall()
    bg_rows = []
    for key, target in targets.items():
        for _ in range(target - have[key]):
            data = gen_listing(rng, district=rng.choice(bg_districts))
            created = N - timedelta(days=rng.randint(1, 20), minutes=rng.randint(0, 1400))
            row = insert_listing(con, src[key], data, created_at=ts(created),
                                 posted_at=ts(created - timedelta(minutes=rng.randint(2, 25))))
            bg_rows.append(row)
            matching.match_listing(con, row, all_profiles, when=ts(created + timedelta(minutes=2)))
    # 5 fon elanında qiymət düşüb
    for row in rng.sample(bg_rows, 5):
        old = int(row["price"] * rng.uniform(1.04, 1.12) / 1000 + 1) * 1000 if row["deal_type"] == "sale" \
            else int(row["price"] * 1.08 / 50 + 1) * 50
        con.execute("UPDATE price_history SET recorded_at = ? WHERE listing_id = ?",
                    (ts(N - timedelta(days=rng.randint(1, 5))), row["id"]))
        con.execute("INSERT INTO price_history(listing_id, price, recorded_at) VALUES (?,?,?)",
                    (row["id"], old, ts(N - timedelta(days=rng.randint(8, 20)))))

    # --- tarixi uyğunluqlar (analitika üçün): son 14 gün dizayndakı kimi, 30 gündə 248, əvvəlki 30 gündə 210
    bars = [5, 7, 4, 9, 6, 11, 8, 7, 10, 13, 10, 14, 12, 14]
    per_day = {}
    for d in range(1, 14):
        per_day[d] = bars[13 - d] - (1 if d == 1 else 0)
    mid = [7] * 16
    extra = 118 - sum(mid)
    for i in range(extra):
        mid[i % 16] += 1
    for i, d in enumerate(range(14, 30)):
        per_day[d] = mid[i] + rng.choice([-1, 0, 1]) * (1 if i % 2 else -1)
    diff = 118 - sum(per_day[d] for d in range(14, 30))
    per_day[20] += diff
    prev = [7] * 30
    for d, v in zip(range(30, 60), prev):
        per_day[d] = v
    kamran_p = con.execute("SELECT * FROM profiles WHERE id = ?", (p_kamran,)).fetchone()
    hist_profiles = elvin_profiles
    for d, cnt in per_day.items():
        day = today - timedelta(days=d)
        for _ in range(cnt):
            pool = hist_profiles + ([kamran_p] if d > 14 else [])
            p = rng.choices(pool, [6, 4, 2, 3] + ([2] if d > 14 else []))[0]
            for _try in range(20):
                data = gen_for_profile(rng, p, strict=True)
                res = matching.evaluate(p, data | {"id": 0})
                if res:
                    break
            match_time = day + timedelta(hours=rng.randint(8, 20), minutes=rng.randint(0, 59))
            posted = match_time - timedelta(minutes=rng.randint(3, 19) if d < 30 else rng.randint(4, 20))
            key = rng.choices(["bina", "lalafo", "tap"], [57, 30, 13])[0]
            row = insert_listing(con, src[key], data, created_at=ts(match_time - timedelta(minutes=1)),
                                 posted_at=ts(posted), active=0)
            if row is None or not res:
                continue
            con.execute("INSERT OR IGNORE INTO matches(profile_id, listing_id, score, reasons, created_at) "
                        "VALUES (?,?,?,?,?)",
                        (p["id"], row["id"], res[0], json.dumps(res[1], ensure_ascii=False), ts(match_time)))

    # --- sinxronizasiya tarixçəsi (30 gün, saatlıq)
    spark_design = {
        "bina": [30, 45, 38, 52, 48, 60, 55, 58, 65, 70, 62, 68, 72, 66],
        "tap": [20, 25, 22, 30, 28, 35, 32, 38, 40, 36, 42, 45, 40, 44],
        "lalafo": [18, 15, 20, 12, 16, 10, 14, 8, 12, 9, 11, 7, 10, 8],
    }
    meta = {
        "bina": dict(base=812, lat=1.4, fails30=3, fails24=0, last=2),
        "tap": dict(base=640, lat=1.8, fails30=8, fails24=1, last=4),
        "lalafo": dict(base=390, lat=4.2, fails30=42, fails24=6, last=19),
    }
    run_rows = []
    for key, m in meta.items():
        last_run = N - timedelta(minutes=m["last"])
        times = [last_run - timedelta(hours=h) for h in range(0, 30 * 24)]
        times.reverse()
        n_runs = len(times)
        recent = [i for i, t in enumerate(times) if t >= N - timedelta(hours=24) and i != n_runs - 1]
        older = [i for i, t in enumerate(times) if t < N - timedelta(hours=24)]
        fail_idx = set(rng.sample(recent, m["fails24"])) | set(rng.sample(older, m["fails30"] - m["fails24"]))
        by_day = {}
        for i, t in enumerate(times):
            by_day.setdefault(t.strftime("%Y-%m-%d"), []).append(i)
        new_counts = [0] * n_runs
        for day_str, idxs in by_day.items():
            days_ago = (today - day_start(times[idxs[0]])).days
            total = spark_design[key][13 - days_ago] if days_ago < 14 else rng.randint(
                min(spark_design[key]), max(spark_design[key]))
            ok_idxs = [i for i in idxs if i not in fail_idx] or idxs
            for _ in range(total):
                new_counts[rng.choice(ok_idxs)] += 1
        for i, t in enumerate(times):
            ok = 0 if i in fail_idx else 1
            lat = round(max(0.4, rng.gauss(m["lat"], m["lat"] * 0.12)), 1) if ok else 10.0
            checked = (m["base"] - (n_runs - i) // 40 + rng.randint(-3, 3)) if ok else 0
            if i == n_runs - 1:
                checked, lat = m["base"], m["lat"]
            run_rows.append((src[key], ts(t), ok, lat, int(checked * lat / 30) if ok else 0, checked,
                             new_counts[i] if ok else 0, rng.randint(10, 45) if ok else 0,
                             rng.randint(0, 3) if ok else 0))
        con.execute("UPDATE sources SET last_sync = ?, status = ? WHERE id = ?",
                    (ts(last_run), "warn" if m["lat"] > 3 else "up", src[key]))
    run_rows.sort(key=lambda r: r[1])
    con.executemany(
        "INSERT INTO sync_runs(source_id, started_at, ok, latency, duration, checked, new_count, updated, removed) "
        "VALUES (?,?,?,?,?,?,?,?,?)", run_rows)
    con.execute("UPDATE sources SET rate_limit = 18 WHERE key = 'lalafo'")

    # --- sistem loqları (dizayndakı 20 qeyd)
    base = N - timedelta(minutes=2)
    first = 17 * 3600 + 42 * 60 + 3
    D = [
        ("17:42:03", "ok", "Bina.az", "Sinxronizasiya tamamlandı — 812 elan yoxlandı, 6 yeni tapıldı",
         "job=sync-bina run=#4821 müddət=38s\nyeni=6 yenilənən=41 silinən=2"),
        ("17:38:11", "ok", "Tap.az", "Sinxronizasiya tamamlandı — 640 elan yoxlandı, 2 yeni tapıldı",
         "job=sync-tap run=#4820 müddət=44s\nyeni=2 yenilənən=27 silinən=1"),
        ("17:23:44", "warn", "Lalafo", "Cavab vaxtı normadan yüksək (4.2s) — sorğu sürəti azaldıldı",
         "gecikmə=4.2s hədd=3.0s\nrate_limit: 30 → 18 sorğu/dəq"),
        ("17:10:02", "info", "Makler", "Yeni makler qeydiyyatı: Elvin Məmmədov",
         "plan=Pro sınaq=14 gün\nemail təsdiqi göndərildi"),
        ("16:55:37", "ok", "Uyğunlaşdırma", "Mühərrik 1,842 elanı yenidən emal etdi",
         "profil=211 uyğunluq=37 müddət=12.4s"),
        ("16:41:09", "err", "Lalafo", "Sinxronizasiya xətası: bağlantı vaxtı bitdi (timeout 10s)",
         "HTTP 504 Gateway Timeout\nyenidən cəhd 2/3 uğurlu oldu"),
        ("16:20:31", "ok", "Bina.az", "Sinxronizasiya tamamlandı — 806 elan yoxlandı, 4 yeni tapıldı",
         "job=sync-bina run=#4817 müddət=36s"),
        ("16:03:55", "info", "Sistem", "Gecə ehtiyat nüsxəsi uğurla tamamlandı", "ölçü=1.2 GB müddət=4dəq 12s"),
        ("15:47:18", "warn", "Makler", "3 uğursuz giriş cəhdi: leyla@nümunə.az",
         "IP=178.xxx.xx.24 cəhd=3\nhesab 15 dəq müddətinə bloklandı"),
        ("15:30:02", "ok", "Tap.az", "Sinxronizasiya tamamlandı — 638 elan yoxlandı, 5 yeni tapıldı",
         "job=sync-tap run=#4812 müddət=41s"),
        ("15:12:44", "err", "Uyğunlaşdırma", "Profil #148 üçün filtr analizi alınmadı",
         "səbəb: yanlış qiymət aralığı (min>max)\nprofil deaktiv edildi"),
        ("14:58:20", "ok", "Lalafo", "Sinxronizasiya tamamlandı — 391 elan yoxlandı, 3 yeni tapıldı",
         "job=sync-lalafo run=#4808 müddət=71s"),
        ("14:31:07", "info", "Makler", "Səbinə Rzayeva yeni axtarış profili yaratdı",
         "profil=#212 rayon=Nəsimi otaq=3"),
        ("14:05:49", "ok", "Bina.az", "Sinxronizasiya tamamlandı — 801 elan yoxlandı, 7 yeni tapıldı",
         "job=sync-bina run=#4802 müddət=39s"),
        ("13:44:12", "warn", "Tap.az", "Səhifə strukturu dəyişib — 2 elanda qiymət sahəsi boşdur",
         "selector .price-val tapılmadı\nehtiyat parser istifadə olundu"),
        ("13:20:00", "info", "Sistem", "Plan yeniləməsi: Fərid Quliyev sınaq müddəti başladı",
         "plan=Başlanğıc sınaq=4 gün qalıb"),
        ("12:52:33", "ok", "Uyğunlaşdırma", "Mühərrik 1,790 elanı yenidən emal etdi",
         "profil=208 uyğunluq=29 müddət=11.8s"),
        ("12:30:15", "ok", "Tap.az", "Sinxronizasiya tamamlandı — 635 elan yoxlandı, 1 yeni tapıldı",
         "job=sync-tap run=#4794 müddət=40s"),
        ("12:04:58", "err", "Sistem", "Bildiriş göndərilməsi uğursuz oldu (SMTP 421)",
         "müştəri=#302 yenidən cəhd 15 dəq sonra"),
        ("11:40:27", "ok", "Bina.az", "Sinxronizasiya tamamlandı — 798 elan yoxlandı, 9 yeni tapıldı",
         "job=sync-bina run=#4788 müddət=37s"),
    ]
    for clock_str, level, source, msg, det in reversed(D):
        h, mi, s = map(int, clock_str.split(":"))
        when = base - timedelta(seconds=first - (h * 3600 + mi * 60 + s))
        add_log(con, level, source, msg, det, when=ts(when))

    con.commit()
