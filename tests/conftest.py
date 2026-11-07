"""Ümumi fixture-lər. Testlər YALNIZ .env-dəki TEST_DATABASE_URL bazasında işləyir:
- TEST_DATABASE_URL yoxdursa və ya host-u DATABASE_URL ilə eynidirsə pytest dərhal dayanır;
- sessiyanın əvvəlində test bazasında sxem sıfırdan yaradılır (init_db);
- prosesdə DATABASE_URL test bazası ilə əvəz olunur ki, heç bir kod real bazaya qoşula bilməsin."""
import os
import re
import secrets
import sys
from datetime import timedelta

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv  # noqa: E402

import dbguard  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))  # mövcud mühit dəyişənlərini əvəz etmir


def pytest_configure(config):
    try:
        test_url = dbguard.check(os.environ)
    except dbguard.UnsafeTestDatabase as exc:
        raise pytest.UsageError(f"TƏHLÜKƏSİZLİK: {exc}")
    os.environ["MAKLERHUB_REAL_DATABASE_HOST"] = dbguard.host_of(os.environ.get("DATABASE_URL"))
    os.environ["DATABASE_URL"] = test_url  # maklerhub import olunmamışdan əvvəl
    os.environ.pop("MAKLERHUB_BEHIND_PROXY", None)


@pytest.fixture(scope="session")
def app():
    from maklerhub import create_app, db as dbmod
    url = os.environ["DATABASE_URL"]
    # pytest_configure işləməyibsə (məs. fixture skriptdən çağırılıb) DATABASE_URL real baza ola bilər → init_db YOX
    real_host = os.environ.get("MAKLERHUB_REAL_DATABASE_HOST")
    if real_host is None or url != (os.environ.get("TEST_DATABASE_URL") or "").strip() or dbguard.host_of(url) == real_host:
        raise RuntimeError("TƏHLÜKƏSİZLİK: app fixture yalnız qoruyucudan keçmiş test bazası ilə işləyir.")
    dbmod.init_db(url)  # test bazası sıfırdan
    application = create_app({"TESTING": True, "DATABASE": url})
    yield application
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


def login(app, email, password, ip=None):
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = ip or f"10.9.{secrets.randbelow(250)}.{secrets.randbelow(250)}"
    tok = csrf(client, "/giris")
    r = client.post("/giris", data={"csrf_token": tok, "email": email, "password": password})
    return client, r


@pytest.fixture
def admin_client(app):
    client, r = login(app, (os.environ.get("MAKLERHUB_ADMIN_EMAIL") or "admin@maklerhub.az").lower(),
                      os.environ["MAKLERHUB_ADMIN_PASSWORD"])
    assert r.status_code == 302
    return client


@pytest.fixture
def make_makler(app, db):
    """Lisenziyalı makler (+ xidmət sahəsi) yaradır və daxil edir. Qaytarır: (client, user_id)."""
    from werkzeug.security import generate_password_hash
    from maklerhub.helpers import now, ts

    def _make(areas=(), first="Test", last="Sınaqov", phone="+994500000000", login_now=True):
        email = f"pytest-{secrets.token_hex(4)}@maklerhub.local"
        stamp = now()
        lid = db.execute("INSERT INTO licenses(code, days, status, note, created_at, activated_at, expires_at) "
                         "VALUES (?, 30, 'used', 'pytest', ?, ?, ?)",
                         (f"MH-TEST-{secrets.token_hex(3).upper()}", ts(stamp), ts(stamp),
                          ts(stamp + timedelta(days=30)))).lastrowid
        uid = db.execute("INSERT INTO users(first_name, last_name, email, phone, password_hash, role, status, license_id, "
                         "created_at) VALUES (?, ?, ?, ?, ?, 'makler', 'active', ?, ?)",
                         (first, last, email, phone, generate_password_hash("TestPass123"), lid, ts(stamp))).lastrowid
        db.execute("UPDATE licenses SET user_id = ? WHERE id = ?", (uid, lid))
        for deal, districts, lo, hi in areas:
            for d in districts:
                db.execute("INSERT INTO agent_areas(user_id, district, deal_type, price_min, price_max) VALUES (?,?,?,?,?)",
                           (uid, d, deal, lo, hi))
        db.commit()
        if not login_now:
            return email, uid
        client, r = login(app, email, "TestPass123")
        assert r.status_code == 302, r.get_data(as_text=True)[:500]
        client.email = email
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
