"""Demo rejimi (portfolio): real məlumatla qarışmayan demo maklerlər, tələblər və təkliflər.

    flask --app run seed-demo    → köhnə demo silinir, yenisi yaradılır (dublikat olmur)
    flask --app run clear-demo   → bütün demo məlumatı silinir (real məlumata toxunulmur)

İzolyasiya users.is_demo və customer_requests.is_demo ilə: demo makler yalnız demo tələbləri görür və onlara təklif
göndərir, real makler demo tələbləri görmür, demo tələb real maklerə bildiriş yaratmır, admin göstəriciləri demo-suzdur.
Demo telefonları aşkar saxtadır (+994 50 000 00 0X), şəkillər boşdur (placeholder görünür).
"""
import json
import secrets
from datetime import timedelta

import click
from werkzeug.security import generate_password_hash

from .auth import demo_password
from .db import add_log
from .helpers import now, ts
from .licenses import gen_code
from .offers import notify_new_request
from .teleb import DEALS, ROOMS

LICENSE_DAYS = 3650

# (e-poçt, ad, soyad, telefon, [(əməliyyat, [rayonlar], min, max), ...])
AGENTS = [
    ("demo1@maklerhub.az", "Rəşad", "Məmmədov", "+994500000011",
     [("rent", ["Yasamal", "Nəsimi", "Nərimanov", "28 May", "Gənclik"], 400, 1500),
      ("sale", ["Yasamal", "Nəsimi"], 80_000, 250_000)]),
    ("demo2@maklerhub.az", "Leyla", "Hüseynova", "+994500000012",
     [("sale", ["Xətai", "Nərimanov", "Nizami", "Həzi Aslanov", "Əhmədli", "Yeni Yasamal"], 60_000, 300_000),
      ("rent", ["Xətai", "Nizami"], 300, 900)]),
    ("demo3@maklerhub.az", "Elvin", "Qasımov", "+994500000013",
     [("rent", ["Səbail", "Badamdar", "Bayıl", "İçərişəhər", "Nəsimi"], 800, 3000),
      ("sale", ["Xəzər", "Binəqədi", "Masazır", "Xırdalan"], 40_000, 150_000)]),
]

# (ad, əməliyyat, rayonlar, otaq, büdcə min, max, təcililik, qeyd, neçə saat əvvəl)
REQUESTS = [
    ("Aysel Məmmədova", "rent", ["Yasamal", "Nəsimi"], 2, 600, 900, "urgent",
     "Metroya yaxın olsun, əşyalı mənzil axtarıram.", 3),
    ("Tural Əliyev", "sale", ["Nərimanov", "Xətai"], 3, 120_000, 160_000, "month",
     "Təmirli, çıxarışlı olsun. İpoteka da mümkündür.", 20),
    ("Nigar Həsənova", "rent", ["Səbail", "Badamdar"], 3, 1500, 2500, "flexible",
     "Dəniz mənzərəli olsa yaxşı olar, uzunmüddətli kirayə.", 30),
    ("Kamran Quliyev", "rent", ["Xətai", "Nizami"], 1, 300, 450, "urgent",
     "Tələbəyəm, tək yaşayacağam.", 44),
    ("Günel Abbasova", "sale", ["Yasamal", "Nəsimi"], 2, 90_000, 130_000, "month",
     "Yeni tikili, orta mərtəbə.", 60),
    ("Orxan Babayev", "sale", ["Masazır", "Xırdalan"], 2, 50_000, 75_000, "flexible", "", 80),
    ("Səbinə Rzayeva", "rent", ["Nərimanov", "Gənclik", "28 May"], 2, 700, 1000, "month",
     "Ailə üçün, pişiyimiz var.", 100),
    ("Fərid İsmayılov", "sale", ["Həzi Aslanov", "Əhmədli"], 4, 150_000, 220_000, "flexible",
     "Böyük ailə üçün, məktəbə yaxın.", 130),
]

# (makler indeksi, tələb indeksi, başlıq, qiymət, rayon, otaq, sahə, təsvir, status, neçə saat əvvəl)
OFFERS = [
    (0, 0, "Nəsimi m. yaxınlığında 2 otaqlı, əşyalı", 800, "Nəsimi", 2, 65,
     "Metroya 5 dəqiqə. Kombi, kondisioner, bütün əşyalar var.", "accepted", 2),
    (2, 0, "Nəsimidə təmirli 2 otaqlı", 880, "Nəsimi", 2, 70, "Yeni təmir, ev heyvanı olmadan.", "sent", 1),
    (1, 1, "Nərimanovda 3 otaqlı, çıxarışlı", 145_000, "Nərimanov", 3, 98,
     "Kupçalı, təmirli, 9/12 mərtəbə. İpotekaya uyğundur.", "accepted", 15),
    (2, 2, "Badamdarda dəniz mənzərəli 3 otaqlı", 2200, "Badamdar", 3, 120,
     "Həyətyanı parkinq, mühafizə, panoram pəncərələr.", "sent", 25),
    (1, 3, "Xətaidə 1 otaqlı mənzil", 420, "Xətai", 1, 38, "Əşyalı, metroya yaxın.", "rejected", 40),
    (2, 5, "Masazırda yeni tikili, 2 otaqlı", 68_000, "Masazır", 2, 62, "Təmirli, çıxarış hazırdır.", "sent", 70),
    (0, 6, "Gənclikdə 2 otaqlı, ailə üçün", 900, "Gənclik", 2, 60, "Ev heyvanına icazə var.", "accepted", 90),
]

PHONE_FMT = "+9945000000{:02d}"  # +994 50 000 00 01..08 — aşkar saxta


def clear_demo(con):
    """Bütün demo məlumatı silir (commit çağıran tərəfdə). Qaytarır: {cədvəl: say}."""
    users = "SELECT id FROM users WHERE is_demo = 1"
    reqs = "SELECT id FROM customer_requests WHERE is_demo = 1"
    out = {}
    out["notifications"] = con.execute(
        f"DELETE FROM notifications WHERE user_id IN ({users}) OR request_id IN ({reqs})").rowcount
    out["complaints"] = con.execute(
        f"DELETE FROM complaints WHERE request_id IN ({reqs}) OR agent_id IN ({users})").rowcount
    out["contacts"] = con.execute(
        f"DELETE FROM contacts WHERE request_id IN ({reqs}) OR agent_id IN ({users})").rowcount
    out["offers"] = con.execute(f"DELETE FROM offers WHERE request_id IN ({reqs}) OR agent_id IN ({users})").rowcount
    out["requests"] = con.execute("DELETE FROM customer_requests WHERE is_demo = 1").rowcount
    out["login_attempts"] = con.execute(
        "DELETE FROM login_attempts WHERE email IN (SELECT email FROM users WHERE is_demo = 1)").rowcount
    lic_ids = [r[0] for r in con.execute("SELECT license_id FROM users WHERE is_demo = 1 AND license_id IS NOT NULL")]
    # sessiyalar, cihazlar, xidmət sahələri — users FK CASCADE
    out["users"] = con.execute("DELETE FROM users WHERE is_demo = 1").rowcount
    out["licenses"] = con.execute("DELETE FROM licenses WHERE id = ANY(?)", (lic_ids,)).rowcount if lic_ids else 0
    return out


def seed_demo(con):
    """Köhnə demo silinir və yenidən yaradılır (commit çağıran tərəfdə). Qaytarır: (counts, [müştəri tokenləri])."""
    clash = con.execute("SELECT email FROM users WHERE email = ANY(?) AND is_demo = 0",
                        ([a[0] for a in AGENTS],)).fetchone()
    if clash:
        raise click.ClickException(f"{clash[0]} real hesabdır — demo yaradılmadı (real məlumata toxunulmur).")
    clear_demo(con)
    stamp = now()
    pw_hash = generate_password_hash(demo_password())
    agent_ids = []
    for email, first, last, phone, areas in AGENTS:
        lid = con.execute(
            "INSERT INTO licenses(code, days, status, note, created_at, activated_at, expires_at) "
            "VALUES (?, ?, 'used', 'demo', ?, ?, ?)",
            (gen_code(), LICENSE_DAYS, ts(stamp), ts(stamp), ts(stamp + timedelta(days=LICENSE_DAYS)))).lastrowid
        uid = con.execute(
            "INSERT INTO users(first_name, last_name, email, phone, password_hash, role, status, license_id, created_at, "
            "password_changed_at, is_demo) VALUES (?,?,?,?,?,'makler','active',?,?,?,1)",
            (first, last, email, phone, pw_hash, lid, ts(stamp - timedelta(days=10)), ts(stamp))).lastrowid
        con.execute("UPDATE licenses SET user_id = ? WHERE id = ?", (uid, lid))
        for deal, districts, lo, hi in areas:
            for d in districts:
                con.execute("INSERT INTO agent_areas(user_id, district, deal_type, price_min, price_max) VALUES (?,?,?,?,?)",
                            (uid, d, deal, lo, hi))
        agent_ids.append(uid)

    req_ids, tokens = [], []
    for i, (name, deal, districts, rooms, lo, hi, urgency, note, hours) in enumerate(REQUESTS, start=1):
        created = ts(stamp - timedelta(hours=hours))
        token = secrets.token_urlsafe(24)
        rid = con.execute(
            "INSERT INTO customer_requests(token, name, phone, deal_type, districts, rooms, budget_min, budget_max, "
            "urgency, note, consent, consent_at, status, ip, created_at, is_demo) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,1,?,'active','',?,1)",
            (token, name, PHONE_FMT.format(i), deal, json.dumps(districts, ensure_ascii=False), rooms, lo, hi, urgency,
             note, created, created)).lastrowid
        notify_new_request(con, {"id": rid, "is_demo": 1, "deal_type": deal, "districts": districts,
                                 "budget_min": lo, "budget_max": hi,
                                 "summary": f"{DEALS[deal]}, {', '.join(districts)}, {ROOMS[rooms]} otaq"})
        con.execute("UPDATE notifications SET created_at = ? WHERE request_id = ? AND kind = 'new_request'", (created, rid))
        req_ids.append(rid)
        tokens.append(token)

    for ai, ri, title, price, district, rooms, area, desc, status, hours in OFFERS:
        sent_at = ts(stamp - timedelta(hours=hours))
        decided = ts(stamp - timedelta(hours=hours) + timedelta(minutes=40)) if status != "sent" else None
        uid, rid = agent_ids[ai], req_ids[ri]
        oid = con.execute(
            "INSERT INTO offers(request_id, agent_id, title, price, district, rooms, area, image_url, description, status, "
            "created_at, decided_at) VALUES (?,?,?,?,?,?,?,NULL,?,?,?,?)",
            (rid, uid, title, price, district, rooms, area, desc, status, sent_at, decided)).lastrowid
        con.execute("INSERT INTO notifications(user_id, request_id, kind, text, link, is_read, created_at) "
                    "VALUES (NULL, ?, 'new_offer', ?, '', 0, ?)", (rid, f"Yeni təklif: {title}", sent_at))
        if status == "accepted":
            con.execute("INSERT INTO contacts(offer_id, request_id, agent_id, created_at) VALUES (?,?,?,?)",
                        (oid, rid, uid, decided))
            con.execute("INSERT INTO notifications(user_id, request_id, kind, text, link, is_read, created_at) "
                        "VALUES (?, NULL, 'offer_accepted', ?, ?, 0, ?)",
                        (uid, f"Təklifiniz qəbul edildi: {title}. Müştərinin nömrəsi açıldı.", f"/kabinet/teleb/{rid}",
                         decided))
        elif status == "rejected":
            con.execute("INSERT INTO notifications(user_id, request_id, kind, text, link, is_read, created_at) "
                        "VALUES (?, NULL, 'offer_rejected', ?, ?, 0, ?)",
                        (uid, f"Təklifiniz rədd edildi: {title}", f"/kabinet/teleb/{rid}", decided))

    add_log(con, "info", "Sistem", "Demo məlumat yaradıldı",
            f"makler={len(agent_ids)} tələb={len(req_ids)} təklif={len(OFFERS)}")
    return {"users": len(agent_ids), "requests": len(req_ids), "offers": len(OFFERS)}, tokens


def init_app(app):
    from .db import connect

    def _run(fn):
        con = connect(app.config["DATABASE"])
        try:
            result = fn(con)
            con.commit()
            return result
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    @app.cli.command("seed-demo")
    def seed_demo_command():
        """Demo maklerlər, tələblər və təkliflər (köhnə demo əvvəlcə silinir)."""
        counts, tokens = _run(seed_demo)
        click.echo(f"Demo yaradıldı: {counts['users']} makler, {counts['requests']} tələb, {counts['offers']} təklif.")
        click.echo(f"Makler girişi: {AGENTS[0][0]} (həmçinin demo2@, demo3@) / şifrə: "
                   f"{'.env-dəki MAKLERHUB_DEMO_PASSWORD' if demo_password() != 'Demo12345' else 'Demo12345'}")
        click.echo("Demo müştəri linkləri (sayt ünvanının arxasına əlavə edin):")
        for t in tokens[:2]:
            click.echo(f"  /teleb/{t}")

    @app.cli.command("clear-demo")
    def clear_demo_command():
        """Bütün demo məlumatı silir (real məlumata toxunmur)."""
        counts = _run(clear_demo)
        click.echo("Demo silindi: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
