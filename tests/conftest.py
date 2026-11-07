"""Ümumi fixture-lər. Axın testləri .env-dəki DATABASE_URL bazasında işləyir və sessiyanın sonunda
yaratdıqları bütün sətirləri (id-si başlanğıc nişanından böyük olanları) silir."""
import os
import re
import secrets
import sys
from datetime import timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

# silinmə ardıcıllığı FK-lara uyğundur
CLEAN_TABLES = ["contacts", "complaints", "notifications", "offers", "customer_requests", "agent_areas", "sessions",
                "devices", "logs", "login_attempts", "users", "licenses"]


@pytest.fixture(scope="session")
def app():
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL yoxdur")
    from maklerhub import create_app, db as dbmod
    application = create_app({"TESTING": True})
    con = dbmod.connect(application.config["DATABASE"])
    marks = {t: con.execute(f"SELECT COALESCE(MAX(id), 0) FROM {t}").fetchone()[0] for t in CLEAN_TABLES}
    con.close()
    application.config["_MARKS"] = marks
    yield application
    con = dbmod.connect(application.config["DATABASE"])
    try:
        new_users = [r[0] for r in con.execute("SELECT id FROM users WHERE id > ?", (marks["users"],))]
        if new_users:
            con.execute("UPDATE users SET license_id = NULL WHERE id = ANY(?)", (new_users,))
        for t in CLEAN_TABLES:
            con.execute(f"DELETE FROM {t} WHERE id > ?", (marks[t],))
        con.commit()
    finally:
        con.close()
        if dbmod._pool is not None:
            dbmod._pool.close()
            dbmod._pool = None


@pytest.fixture
def db(app):
    from maklerhub import db as dbmod
    con = dbmod.connect(app.config["DATABASE"])
    yield con
    con.close()


def csrf(client, path):
    html = client.get(path).get_data(as_text=True)
    m = re.search(r'name="csrf_token" value="([^"]+)"', html) or re.search(r'name="csrf-token" content="([^"]+)"', html)
    return m.group(1)


@pytest.fixture
def make_makler(app, db):
    """Lisenziyalı makler (+ xidmət sahəsi) yaradır və test client ilə daxil edir. Qaytarır: (client, user_id)."""
    from werkzeug.security import generate_password_hash
    from maklerhub.helpers import now, ts

    def _make(areas=(), ip=None):
        email = f"pytest-{secrets.token_hex(4)}@maklerhub.local"
        stamp = now()
        lid = db.execute("INSERT INTO licenses(code, days, status, note, created_at, activated_at, expires_at) "
                         "VALUES (?, 30, 'used', 'pytest', ?, ?, ?)",
                         (f"MH-TEST-{secrets.token_hex(2).upper()}-{secrets.token_hex(2).upper()}", ts(stamp),
                          ts(stamp), ts(stamp + timedelta(days=30)))).lastrowid
        uid = db.execute("INSERT INTO users(first_name, last_name, email, phone, password_hash, role, status, license_id, "
                         "created_at) VALUES ('Test', 'Sınaqov', ?, '+994500000000', ?, 'makler', 'active', ?, ?)",
                         (email, generate_password_hash("TestPass123"), lid, ts(stamp))).lastrowid
        db.execute("UPDATE licenses SET user_id = ? WHERE id = ?", (uid, lid))
        for deal, districts, lo, hi in areas:
            for d in districts:
                db.execute("INSERT INTO agent_areas(user_id, district, deal_type, price_min, price_max) VALUES (?,?,?,?,?)",
                           (uid, d, deal, lo, hi))
        db.commit()
        client = app.test_client()
        client.environ_base["REMOTE_ADDR"] = ip or f"10.9.{secrets.randbelow(250)}.{secrets.randbelow(250)}"
        tok = csrf(client, "/giris")
        r = client.post("/giris", data={"csrf_token": tok, "email": email, "password": "TestPass123"})
        assert r.status_code == 302, r.get_data(as_text=True)[:500]
        return client, uid
    return _make


@pytest.fixture
def make_request(db):
    """Birbaşa bazada müştəri tələbi. Qaytarır: (id, token)."""
    from maklerhub.helpers import now, ts

    def _make(deal="rent", districts=("Yasamal",), rooms=2, bmin=500, bmax=900, urgency="month", status="active",
              age_days=0, phone=None, name="Aysel Məmmədova", note=""):
        import json
        token = secrets.token_urlsafe(24)
        phone = phone or "+99477" + str(secrets.randbelow(10 ** 7)).zfill(7)
        created = ts(now() - timedelta(days=age_days))
        rid = db.execute(
            "INSERT INTO customer_requests(token, name, phone, deal_type, districts, rooms, budget_min, budget_max, "
            "urgency, note, consent, consent_at, status, ip, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?,'127.0.0.1',?)",
            (token, name, phone, deal, json.dumps(list(districts), ensure_ascii=False), rooms, bmin, bmax, urgency, note,
             created, status, created)).lastrowid
        db.commit()
        return rid, token
    return _make
