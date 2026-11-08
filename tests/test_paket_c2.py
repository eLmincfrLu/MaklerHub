"""Paket C2: müştəri e-poçt bildirişləri (Brevo MOCK — conftest.mail), link bərpası, şifrə bərpası mailer ilə.
YALNIZ test bazasında."""
import json
import re
import secrets
from datetime import timedelta

import pytest

from conftest import csrf
from test_flow import RENT_AREA, send

EMAIL = "musteri.test@gmail.com"
PUBLIC_IP = "93.184.216.40"


def ago(**kw):
    from maklerhub.helpers import now, ts
    return ts(now() - timedelta(**kw))


def with_email(db, rid, email=EMAIL, notify=1):
    db.execute("UPDATE customer_requests SET email = ?, email_notify = ? WHERE id = ?", (email, notify, rid))
    db.commit()


def base_url():
    return "http://localhost"


def flush(app, db):
    """Maintenance hook-unun etdiyi iş (orada tətbiq konteksti var)."""
    from maklerhub.customer_mail import flush_pending
    with app.app_context():
        return flush_pending(db, base_url())


# ---------------------------------------------------------------- /teleb forması
def teleb_post(client, **over):
    data = {"csrf_token": csrf(client, "/teleb"), "name": "Test", "phone": "+99470" + str(secrets.randbelow(10 ** 7)).zfill(7),
            "deal": "rent", "rooms": "2", "budget_min": "", "budget_max": "900", "urgency": "month", "consent": "on",
            "website": "", "districts": ["Yasamal"], "email": ""}
    data.update(over)
    return client.post("/teleb", data=data)


def test_form_email_optional_validated_lowercased(app, db):
    c = app.test_client()
    html = c.get("/teleb").get_data(as_text=True)
    assert "E-poçt <span" in html and "(istəyə bağlı)" in html and 'name="email"' in html
    assert "Yeni təklif gələndə xəbər verək və linkini itirsən bərpa edək." in html
    assert 'href="/teleb/berpa"' in html
    r = teleb_post(c, email="yanlis@")
    assert r.status_code == 200 and "E-poçt ünvanı düzgün deyil" in r.get_data(as_text=True)
    r = teleb_post(c, email="  Ad.Soyad@Gmail.COM ")
    assert r.status_code == 302
    token = r.headers["Location"].rsplit("/", 1)[1]
    row = db.execute("SELECT email, email_notify FROM customer_requests WHERE token = ?", (token,)).fetchone()
    assert (row["email"], row["email_notify"]) == ("ad.soyad@gmail.com", 1)
    page = c.get(f"/teleb/{token}").get_data(as_text=True)
    assert "a***@gmail.com" in page and "ad.soyad@gmail.com" not in page and "Bildirişləri dayandır" in page
    r = teleb_post(app.test_client())                       # e-poçtsuz da olar
    token2 = r.headers["Location"].rsplit("/", 1)[1]
    row = db.execute("SELECT email, email_notify FROM customer_requests WHERE token = ?", (token2,)).fetchone()
    assert (row["email"], row["email_notify"]) == ("", 0)


def test_customer_page_add_stop_resume(app, db, make_request):
    rid, token = make_request()
    c = app.test_client()
    html = c.get(f"/teleb/{token}").get_data(as_text=True)
    assert "E-poçtla bildiriş al" in html
    tok = csrf(c, f"/teleb/{token}")
    c.post(f"/teleb/{token}/email", data={"csrf_token": tok, "action": "add", "email": "Yeni@Mail.az"})
    row = db.execute("SELECT email, email_notify FROM customer_requests WHERE id = ?", (rid,)).fetchone()
    assert (row["email"], row["email_notify"]) == ("yeni@mail.az", 1)
    c.post(f"/teleb/{token}/email", data={"csrf_token": tok, "action": "stop"})
    assert db.execute("SELECT email_notify FROM customer_requests WHERE id = ?", (rid,)).fetchone()[0] == 0
    assert "Bildirişləri yenidən aç" in c.get(f"/teleb/{token}").get_data(as_text=True)
    c.post(f"/teleb/{token}/email", data={"csrf_token": tok, "action": "resume"})
    assert db.execute("SELECT email_notify FROM customer_requests WHERE id = ?", (rid,)).fetchone()[0] == 1
    # artıq e-poçt varsa "add" onu dəyişmir
    c.post(f"/teleb/{token}/email", data={"csrf_token": tok, "action": "add", "email": "basqa@mail.az"})
    assert db.execute("SELECT email FROM customer_requests WHERE id = ?", (rid,)).fetchone()[0] == "yeni@mail.az"


# ---------------------------------------------------------------- yeni təklif bildirişi
def test_first_offer_sends_one_mail_without_personal_data(app, db, mail, make_makler, make_request):
    m, _ = make_makler(RENT_AREA, first="Rəşad", last="Məmmədov", phone="+994501234567")
    rid, token = make_request(phone="+994705550099", name="Aysel Gizli")
    with_email(db, rid)
    assert send(m, rid).status_code == 302
    got = mail.to(EMAIL)
    assert len(got) == 1
    msg = got[0]
    assert msg["subject"] == "Tələbinə yeni təklif gəldi" and msg["sender"]["name"] == "MaklerHub"
    assert msg["sender"]["email"] == "no-reply@maklerhub.test"
    body = msg["htmlContent"] + msg["textContent"]
    assert f"/teleb/{token}" in body and f"/teleb/{token}/email-dayandir" in body
    assert "Təkliflərə bax" in body and "Bu bildirişləri dayandır" in msg["htmlContent"]
    assert "Təmirli 2 otaqlı" in body and "800 AZN/ay" in body and "Yasamal" in body
    for secret in ("Rəşad", "Məmmədov", "501234567", "705550099", "Aysel", "+994"):
        assert secret not in body, secret
    assert msg["headers"]["List-Unsubscribe"].endswith(f"/teleb/{token}/email-dayandir>")
    assert "/static/img/logo-big-192.png" in msg["htmlContent"]


def test_second_offer_within_hour_waits_then_summary(app, db, mail, make_makler, make_request):
    rid, _ = make_request()
    with_email(db, rid)
    m1, _ = make_makler(RENT_AREA)
    m2, _ = make_makler(RENT_AREA)
    m3, _ = make_makler(RENT_AREA)
    send(m1, rid)
    send(m2, rid, title="İkinci təklif")
    send(m3, rid, title="Üçüncü təklif")
    assert len(mail.to(EMAIL)) == 1                       # saatda 1
    assert flush(app, db) == 0             # saat keçməyib
    db.execute("UPDATE customer_requests SET last_email_at = ? WHERE id = ?", (ago(minutes=61), rid))
    db.commit()
    assert flush(app, db) == 1
    got = mail.to(EMAIL)
    assert len(got) == 2 and got[1]["subject"] == "Tələbinə 2 yeni təklif gəldi"
    assert "2 yeni təklif" in got[1]["htmlContent"] and "İkinci təklif" in got[1]["textContent"]
    assert "Üçüncü təklif" in got[1]["textContent"] and "Təmirli 2 otaqlı" not in got[1]["textContent"]
    db.execute("UPDATE customer_requests SET last_email_at = ? WHERE id = ?", (ago(minutes=61), rid))
    db.commit()
    assert flush(app, db) == 0             # gözləyən yoxdur


def test_customer_seen_offers_not_mailed(app, db, mail, make_makler, make_request):
    rid, token = make_request()
    with_email(db, rid)
    m1, _ = make_makler(RENT_AREA)
    m2, _ = make_makler(RENT_AREA)
    send(m1, rid)
    send(m2, rid)
    app.test_client().get(f"/teleb/{token}")              # müştəri baxdı → gözləyən artıq "yeni" deyil
    db.execute("UPDATE customer_requests SET last_email_at = ? WHERE id = ?", (ago(minutes=61), rid))
    db.commit()
    assert flush(app, db) == 0 and len(mail.to(EMAIL)) == 1


def test_daily_limit_three(app, db, mail, make_makler, make_request):
    from maklerhub.helpers import ts
    rid, _ = make_request()
    with_email(db, rid)
    for i in range(4):
        m, _ = make_makler(RENT_AREA)
        send(m, rid, title=f"Təklif {i}")
        db.execute("UPDATE customer_requests SET last_email_at = ? WHERE id = ?", (ago(minutes=61), rid))
        db.commit()
        flush(app, db)
    assert len(mail.to(EMAIL)) == 3
    row = db.execute("SELECT emails_today, emails_day FROM customer_requests WHERE id = ?", (rid,)).fetchone()
    assert row["emails_today"] == 3 and row["emails_day"] == ts()[:10]
    # növbəti gün sayğac sıfırlanır
    db.execute("UPDATE customer_requests SET emails_day = '2000-01-01' WHERE id = ?", (rid,))
    db.commit()
    assert flush(app, db) == 1 and len(mail.to(EMAIL)) == 4


def test_unsubscribe_link_one_click(app, db, mail, make_makler, make_request):
    rid, token = make_request()
    with_email(db, rid)
    c = app.test_client()
    r = c.get(f"/teleb/{token}/email-dayandir")
    h = r.get_data(as_text=True)
    assert r.status_code == 200 and "Bildirişlər" in h and "dayandırıldı" in h and "Bildirişləri yenidən aç" in h
    assert r.headers["Referrer-Policy"] == "no-referrer" and "noindex" in r.headers["X-Robots-Tag"]
    assert db.execute("SELECT email_notify FROM customer_requests WHERE id = ?", (rid,)).fetchone()[0] == 0
    m, _ = make_makler(RENT_AREA)
    send(m, rid)
    assert mail.to(EMAIL) == []
    assert c.get(f"/teleb/{secrets.token_urlsafe(24)}/email-dayandir").status_code == 404


@pytest.mark.parametrize("state", ["closed", "expired", "old"])
def test_no_mail_for_closed_or_expired(app, db, mail, make_makler, make_request, state):
    rid, _ = make_request(age_days=40 if state == "old" else 0)
    with_email(db, rid)
    m, _ = make_makler(RENT_AREA)
    if state != "old":
        send(m, rid)                                       # aktivdə 1 məktub
        db.execute("UPDATE customer_requests SET status = ?, last_email_at = ? WHERE id = ?",
                   (state, ago(minutes=61), rid))
        db.commit()
        m2, _ = make_makler(RENT_AREA)
        db.execute("INSERT INTO offers(request_id, agent_id, title, price, district, rooms, status, created_at) "
                   "VALUES (?, (SELECT id FROM users WHERE email = ?), 'Gec', 700, 'Yasamal', 2, 'sent', ?)",
                   (rid, m2.email, ago(minutes=1)))
        db.commit()
        assert flush(app, db) == 0 and len(mail.to(EMAIL)) == 1
    else:
        from maklerhub.customer_mail import offer_sent
        db.execute("INSERT INTO offers(request_id, agent_id, title, price, district, rooms, status, created_at) "
                   "VALUES (?, (SELECT id FROM users WHERE email = ?), 'Gec', 700, 'Yasamal', 2, 'sent', ?)",
                   (rid, m.email, ago(minutes=1)))
        db.commit()
        with app.test_request_context("/"):
            assert offer_sent(db, rid, base_url()) is False
        assert flush(app, db) == 0 and mail.to(EMAIL) == []


def test_global_daily_limit(app, db, mail, make_makler, make_request):
    from maklerhub import mailer
    from maklerhub.helpers import ts
    db.execute("INSERT INTO settings(key, value) VALUES ('mail_quota', ?) ON CONFLICT(key) DO UPDATE SET "
               "value = excluded.value", (f"{ts()[:10]}|{mailer.DAILY_LIMIT - 1}",))
    db.commit()
    assert mailer.send_email("a@b.az", "s", "<p>h</p>", "t", con=db) is True
    assert mailer.limit_reached(db) and mailer.send_email("c@d.az", "s", "<p>h</p>", "t", con=db) is False
    assert [m["to"][0]["email"] for m in mail.sent] == ["a@b.az"]
    assert db.execute("SELECT COUNT(*) FROM logs WHERE message LIKE 'Gündəlik e-poçt limiti doldu%'").fetchone()[0] >= 1
    admin_html = None
    from conftest import login
    import os
    admin, _ = login(app, (os.environ.get("MAKLERHUB_ADMIN_EMAIL") or "admin@maklerhub.az").lower(),
                     os.environ.get("MAKLERHUB_ADMIN_PASSWORD"))
    admin_html = admin.get("/admin").get_data(as_text=True)
    assert "E-poçt limiti doldu" in admin_html and f"{mailer.DAILY_LIMIT}/{mailer.DAILY_LIMIT}" in admin_html
    # köhnə gün → sayğac sıfırdan
    db.execute("UPDATE settings SET value = '2000-01-01|250' WHERE key = 'mail_quota'")
    db.commit()
    assert not mailer.limit_reached(db) and mailer.send_email("e@f.az", "s", "<p>h</p>", "t", con=db) is True


def test_mailer_without_key_logs_and_returns_false(app, db, mail, monkeypatch, caplog):
    from maklerhub import mailer
    monkeypatch.delenv("BREVO_API_KEY")
    assert mailer.send_email("x@y.az", "Mövzu", "<p>h</p>", "mətn burada", con=db) is False
    assert mail.sent == [] and "mətn burada" in caplog.text


def test_mailer_retries_once_on_server_error(app, db, mail, monkeypatch):
    from maklerhub import mailer
    calls = []

    def flaky(payload):
        calls.append(1)
        return (503, {}) if len(calls) == 1 else (201, {"messageId": "<x>"})
    monkeypatch.setattr(mailer, "_post", flaky)
    monkeypatch.setattr(mailer.time, "sleep", lambda s: None)
    assert mailer.send_email("x@y.az", "s", "h", "t", con=db) is True and len(calls) == 2
    calls.clear()
    monkeypatch.setattr(mailer, "_post", lambda p: (calls.append(1), (400, {"message": "bad sender"}))[1])
    assert mailer.send_email("x@y.az", "s", "h", "t", con=db) is False and len(calls) == 1
    assert mailer.last_result["error"] == "bad sender"


def test_mail_failure_does_not_break_offer(app, db, mail, make_makler, make_request):
    mail.status = 500
    rid, _ = make_request()
    with_email(db, rid)
    m, _ = make_makler(RENT_AREA)
    r = send(m, rid)
    assert r.status_code == 302
    assert db.execute("SELECT COUNT(*) FROM offers WHERE request_id = ?", (rid,)).fetchone()[0] == 1


# ---------------------------------------------------------------- linkimi itirdim
def berpa(client, email, **over):
    data = {"csrf_token": csrf(client, "/teleb/berpa"), "email": email, "website": ""}
    data.update(over)
    return client.post("/teleb/berpa", data=data)


def test_recovery_same_answer_and_sends_links(app, db, mail, make_request):
    rid1, t1 = make_request()
    rid2, t2 = make_request(deal="sale", bmin=80000, bmax=150000)
    _, t_closed = make_request(status="closed")
    for rid in (rid1, rid2):
        with_email(db, rid, "itirdim@gmail.com")
    db.execute("UPDATE customer_requests SET email = 'itirdim@gmail.com' WHERE token = ?", (t_closed,))
    db.commit()
    c = app.test_client()
    page = c.get("/teleb/berpa").get_data(as_text=True)
    assert "Linkini itirmisən?" in page and 'name="website"' in page
    a = berpa(c, "  Itirdim@Gmail.com ")
    b = berpa(app.test_client(), "hec-kim@gmail.com")
    msg = "Əgər bu e-poçtla aktiv tələb varsa, linklər göndərildi."
    ta, tb = a.get_data(as_text=True), b.get_data(as_text=True)
    assert a.status_code == b.status_code == 200 and msg in ta and msg in tb
    strip = lambda h: re.sub(r'name="csrf_token" value="[^"]+"', "", h)  # noqa: E731
    assert strip(ta) == strip(tb)                       # cavab eynidir
    assert mail.to("hec-kim@gmail.com") == []
    got = mail.to("itirdim@gmail.com")
    assert len(got) == 1 and got[0]["subject"] == "MaklerHub — tələblərinin linkləri"
    body = got[0]["htmlContent"] + got[0]["textContent"]
    assert f"/teleb/{t1}" in body and f"/teleb/{t2}" in body and f"/teleb/{t_closed}" not in body
    assert "+994" not in body


def test_recovery_limits_and_honeypot(app, db, mail, make_request):
    rid, _ = make_request()
    with_email(db, rid, "limit@gmail.com")
    msg = "Əgər bu e-poçtla aktiv tələb varsa, linklər göndərildi."
    # honeypot: eyni cavab, məktub yox
    r = berpa(app.test_client(), "limit@gmail.com", website="http://spam")
    assert msg in r.get_data(as_text=True) and mail.to("limit@gmail.com") == []
    # e-poçt başına gündə 3 (fərqli IP-lərdən)
    for i in range(4):
        c = app.test_client()
        c.environ_base["REMOTE_ADDR"] = f"93.184.216.{50 + i}"
        assert msg in berpa(c, "limit@gmail.com").get_data(as_text=True)
    assert len(mail.to("limit@gmail.com")) == 3
    # IP başına saatda 5
    c = app.test_client()
    c.environ_base["REMOTE_ADDR"] = PUBLIC_IP
    for i in range(5):
        assert msg in berpa(c, f"ip{i}@gmail.com").get_data(as_text=True)
    h = berpa(c, "ip9@gmail.com").get_data(as_text=True)
    assert "Çox cəhd edildi" in h and msg not in h
    # format səhvi
    assert "E-poçt ünvanı düzgün deyil" in berpa(app.test_client(), "yox").get_data(as_text=True)
    # e-poçt login_attempts-də açıq saxlanılmır
    assert db.execute("SELECT COUNT(*) FROM login_attempts WHERE email LIKE '%limit@gmail.com%'").fetchone()[0] == 0


def test_lost_link_entry_points(app, make_request):
    c = app.test_client()
    r = c.get(f"/teleb/{secrets.token_urlsafe(24)}")
    assert r.status_code == 404 and "Linkini itirmisən?" in r.get_data(as_text=True)
    assert 'href="/teleb/berpa"' in r.get_data(as_text=True)
    landing = c.get("/").get_data(as_text=True)
    assert landing.count('href="/teleb/berpa"') >= 2      # hero + footer
    assert 'href="/teleb/berpa"' in c.get("/teleb").get_data(as_text=True)


# ---------------------------------------------------------------- şifrə bərpası və məxfilik
def test_password_reset_goes_through_mailer(app, db, mail, make_makler):
    _, uid = make_makler(RENT_AREA, login_now=False)
    email = db.execute("SELECT email FROM users WHERE id = ?", (uid,)).fetchone()[0]
    c = app.test_client()
    tok = csrf(c, "/sifre-berpa")
    c.post("/sifre-berpa", data={"csrf_token": tok, "email": email})
    got = mail.to(email)
    assert len(got) == 1 and got[0]["subject"] == "MaklerHub — şifrə bərpası"
    assert "/sifre-yenile/" in got[0]["textContent"] and "/sifre-yenile/" in got[0]["htmlContent"]
    from maklerhub import config
    for name in ("MAIL_SERVER", "MAIL_PORT", "MAIL_USERNAME", "MAIL_PASSWORD", "MAIL_TLS"):
        assert not hasattr(config.Config, name)


def test_email_hidden_from_makler_and_shown_to_admin(app, db, admin_client, make_makler, make_request):
    m, _ = make_makler(RENT_AREA)
    rid, token = make_request()
    with_email(db, rid, "gizli.musteri@gmail.com")
    pages = [m.get("/kabinet?view=all"), m.get(f"/kabinet/teleb/{rid}"), m.get("/kabinet/teklifler"),
             m.get("/api/notifications")]
    send(m, rid)
    pages += [m.get(f"/kabinet/teleb/{rid}"), m.get("/kabinet/teklifler")]
    for p in pages:
        h = p.get_data(as_text=True)
        assert "gizli.musteri" not in h and "g***@gmail.com" not in h
    st = app.test_client().post("/api/teleb/status", json={"tokens": [token]}, headers={"Origin": "http://localhost"})
    assert "gmail" not in st.get_data(as_text=True)
    assert "gizli.musteri@gmail.com" in admin_client.get(f"/admin/teleb/{rid}").get_data(as_text=True)


def test_anonymize_clears_email_and_privacy_text(app, db, make_request):
    from maklerhub.teleb import expire_requests
    rid, _ = make_request(status="closed")
    with_email(db, rid)
    db.execute("UPDATE customer_requests SET closed_at = ? WHERE id = ?", (ago(days=91), rid))
    db.commit()
    expire_requests(db)
    db.commit()
    row = db.execute("SELECT email, email_notify, phone FROM customer_requests WHERE id = ?", (rid,)).fetchone()
    assert (row["email"], row["email_notify"], row["phone"]) == ("", 0, "")
    html = app.test_client().get("/mexfilik").get_data(as_text=True)
    assert "E-poçt (istəyə bağlı)" in html and "Bu bildirişləri dayandır" in html


def test_sentry_scrubs_email_fields():
    from maklerhub.monitoring import before_send
    ev = before_send({"extra": {"email": "a@b.az", "payload": {"to": [{"email": "c@d.az"}], "api-key": "xkeysib-123"}},
                      "message": "göndərilmədi: e@f.az"})
    dumped = json.dumps(ev, ensure_ascii=False)
    for s in ("a@b.az", "c@d.az", "e@f.az", "xkeysib"):
        assert s not in dumped


# ---------------------------------------------------------------- baza ünvan (MAKLERHUB_BASE_URL)
RENDER = "https://maklerhub.onrender.com"


def links_in(msg):
    body = msg["htmlContent"] + "\n" + msg["textContent"]
    return set(re.findall(r'https?://[^\s"<>]+', body))


def test_mail_links_use_base_url_env(app, db, mail, monkeypatch, make_makler, make_request):
    """Bütün məktub linkləri və loqo MAKLERHUB_BASE_URL-dan (sorğunun host-undan YOX)."""
    monkeypatch.setenv("MAKLERHUB_BASE_URL", RENDER + "/")       # sondakı "/" atılır
    rid, token = make_request()
    with_email(db, rid, "baza@gmail.com")
    m, uid = make_makler(RENT_AREA)
    send(m, rid)                                                   # test klienti: http://localhost
    c = app.test_client()
    berpa(c, "baza@gmail.com")
    email = db.execute("SELECT email FROM users WHERE id = ?", (uid,)).fetchone()[0]
    c.post("/sifre-berpa", data={"csrf_token": csrf(c, "/sifre-berpa"), "email": email})
    msgs = mail.to("baza@gmail.com") + mail.to(email)
    assert [x["subject"] for x in msgs] == ["Tələbinə yeni təklif gəldi", "MaklerHub — tələbinin linki",
                                           "MaklerHub — şifrə bərpası"]
    for msg in msgs:
        links = links_in(msg)
        assert links and all(u.startswith(RENDER + "/") for u in links), links
        assert "localhost" not in msg["htmlContent"] + msg["textContent"]
        assert f'src="{RENDER}/static/img/logo-big-192.png"' in msg["htmlContent"]
    offer = msgs[0]
    assert f"{RENDER}/teleb/{token}" in offer["textContent"]
    assert offer["headers"]["List-Unsubscribe"] == f"<{RENDER}/teleb/{token}/email-dayandir>"
    assert f"{RENDER}/sifre-yenile/" in msgs[2]["textContent"]
    # hook (flush_pending) da eyni bazanı alır: maintenance after_request base_url() çağırır
    from maklerhub import mailer
    with app.test_request_context("/", base_url="http://basqa-host:9999"):
        assert mailer.base_url() == RENDER


def test_mail_base_url_falls_back_to_request_host(app, db, mail, make_makler, make_request):
    from maklerhub import mailer
    rid, token = make_request()
    with_email(db, rid, "ehtiyat@gmail.com")
    m, _ = make_makler(RENT_AREA)
    send(m, rid)
    msg = mail.to("ehtiyat@gmail.com")[0]
    assert all(u.startswith("http://localhost/") for u in links_in(msg))
    assert 'src="http://localhost/static/img/logo-big-192.png"' in msg["htmlContent"]
    with app.test_request_context("/", base_url="https://example.az"):
        assert mailer.base_url() == "https://example.az"
