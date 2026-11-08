"""Təklif şəkilləri (Cloudinary MOCK — conftest.cloud; şəbəkəyə getmir). YALNIZ test bazasında."""
import io
import struct
import zlib

import pytest
from PIL import ExifTags, Image

from conftest import csrf
from test_flow import RENT_AREA, offer_form


# ---------------------------------------------------------------- test şəkilləri
def img_bytes(w=1200, h=800, fmt="JPEG", mode="RGB", gps=False, orientation=None, color=(180, 120, 60)):
    im = Image.new(mode, (w, h), color if mode == "RGB" else (180, 120, 60, 128))
    for x in range(0, w, 40):  # bir az detal (sıxılma real olsun)
        im.paste((20, 20, 20) if mode == "RGB" else (20, 20, 20, 255), (x, 0, min(w, x + 4), h))
    kw = {}
    if gps or orientation:
        exif = Image.Exif()
        if orientation:
            exif[ExifTags.Base.Orientation] = orientation
        if gps:
            exif[ExifTags.Base.Make] = "PytestCam"
            g = exif.get_ifd(ExifTags.IFD.GPSInfo)
            g[ExifTags.GPS.GPSLatitudeRef] = "N"
            g[ExifTags.GPS.GPSLatitude] = (40.0, 22.0, 30.0)
            g[ExifTags.GPS.GPSLongitudeRef] = "E"
            g[ExifTags.GPS.GPSLongitude] = (49.0, 50.0, 10.0)
        kw["exif"] = exif
    out = io.BytesIO()
    im.save(out, fmt, **kw)
    return out.getvalue()


def png_header_only(w, h):
    """Yalnız IHDR olan PNG (piksel yoxdur): decompression bomb sınağı üçün — yaddaş xərcləmir."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b""))


def process(data):
    from maklerhub import images
    return images.process(io.BytesIO(data))


# ---------------------------------------------------------------- H1: qəbul, kiçiltmə, metadata
@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_accepts_formats_and_resizes(fmt):
    p = process(img_bytes(3000, 2000, fmt=fmt))
    with Image.open(io.BytesIO(p.data)) as out:
        assert out.format == "JPEG" and max(out.size) == 1600 and out.size == (p.width, p.height) == (1600, 1067)
    assert len(p.data) < 600_000


def test_png_with_alpha_is_flattened():
    p = process(img_bytes(800, 600, fmt="PNG", mode="RGBA"))
    with Image.open(io.BytesIO(p.data)) as out:
        assert out.mode == "RGB" and out.size == (800, 600)


def test_exif_orientation_applied_and_gps_removed():
    src = img_bytes(2400, 1200, gps=True, orientation=6)
    with Image.open(io.BytesIO(src)) as im:  # sınaq şəklində GPS həqiqətən var
        assert im.getexif().get_ifd(ExifTags.IFD.GPSInfo)
    p = process(src)
    with Image.open(io.BytesIO(p.data)) as out:
        assert out.size == (800, 1600)                  # 90° döndərilib, sonra uzun tərəf 1600
        assert len(out.getexif()) == 0 and not out.getexif().get_ifd(ExifTags.IFD.GPSInfo)
        assert "exif" not in out.info and "xmp" not in out.info
    for marker in (b"Exif", b"PytestCam", b"GPS"):
        assert marker not in p.data


# ---------------------------------------------------------------- H2: rədd olunanlar
@pytest.mark.parametrize("data,needle", [
    ("bu mətn faylıdır, adı .jpg olsa da".encode(), "şəkil deyil"),
    (img_bytes(100, 100), "kiçikdir"),
    (img_bytes(300, 150), "kiçikdir"),
    (png_header_only(30_000, 30_000), "böyükdür"),     # bomb: Pillow DecompressionBombError
    (png_header_only(12_000, 9_000), "böyükdür"),      # limitin 1-2 qatı arası (xəbərdarlıq → xəta)
    (png_header_only(10_500, 300), "böyükdür"),        # tərəf > 10000
    (img_bytes(400, 400, fmt="GIF"), "Yalnız JPEG"),
    (b'<svg xmlns="http://www.w3.org/2000/svg" width="500" height="500"><rect width="500" height="500"/></svg>',
     "şəkil deyil"),
])
def test_rejects_bad_files(data, needle):
    from maklerhub.images import ImageError
    with pytest.raises(ImageError) as exc:
        process(data)
    assert needle in str(exc.value)


def test_rejects_over_5mb_and_too_many_files():
    from maklerhub import images
    big = img_bytes(800, 600) + b"\0" * (5 * 1024 * 1024)
    with pytest.raises(images.ImageError, match="5 MB"):
        process(big)

    class F:
        def __init__(self, d):
            self.stream = io.BytesIO(d)
    ok = img_bytes(800, 600)
    _, errors = images.process_all([F(ok) for _ in range(6)], 5)
    assert errors == ["Ən çox 5 şəkil yükləmək olar."]
    out, errors = images.process_all([F(ok), F(b"saxta")], 5)
    assert out == [] and errors and errors[0].startswith("2-ci şəkil")   # biri səhvdirsə heç biri


# ---------------------------------------------------------------- forma (inteqrasiya)
def post_offer(client, rid, files, **over):
    tok = csrf(client, f"/kabinet/teleb/{rid}")
    data = dict(offer_form(**over), csrf_token=tok)
    data["images"] = [(io.BytesIO(b), name, "image/jpeg") for b, name in files]
    return client.post(f"/kabinet/teleb/{rid}/teklif", data=data, content_type="multipart/form-data")


def jpgs(n, **kw):
    return [(img_bytes(**kw), f"foto{i}.jpg") for i in range(n)]


def test_upload_flow_and_display(app, db, cloud, make_makler, make_request, admin_client):
    rid, token = make_request()
    a, ua = make_makler(RENT_AREA)
    b, ub = make_makler(RENT_AREA)
    r = post_offer(a, rid, jpgs(2, w=2400, h=1600), image_url="https://saxta.example/x.jpg")
    assert r.status_code == 302, r.get_data(as_text=True)[:600]
    oid = db.execute("SELECT id, image_url FROM offers WHERE agent_id = ?", (ua,)).fetchone()
    assert oid["image_url"] is None   # link sahəsi artıq nəzərə alınmır
    oid = oid["id"]
    rows = db.execute("SELECT * FROM offer_images WHERE offer_id = ? ORDER BY sort", (oid,)).fetchall()
    assert [r_["sort"] for r_ in rows] == [0, 1] and len(cloud.uploaded) == 2
    assert all(r_["public_id"].startswith(f"maklerhub/offers/{oid}/") for r_ in rows)
    assert all("foto" not in r_["public_id"] for r_ in rows)          # fayl adı istifadə olunmur
    assert rows[0]["width"] == 1600 and rows[0]["height"] == 1067
    main_url = rows[0]["url"]

    # müştəri: transformasiyalı URL-lər, lazy, no-referrer, ölçülər
    html = app.test_client().get(f"/teleb/{token}").get_data(as_text=True)
    card = main_url.replace("/image/upload/", "/image/upload/f_auto,q_auto,w_800/")
    full = main_url.replace("/image/upload/", "/image/upload/f_auto,q_auto,w_1600/")
    assert f'src="{card}"' in html and f'data-full="{full}"' in html
    assert 'width="800" height="534"' in html and 'loading="lazy" referrerpolicy="no-referrer"' in html
    assert "data-gal" in html and "offer-gallery.js" in html and "1 / 2" in html
    thumb = main_url.replace("/image/upload/", "/image/upload/f_auto,q_auto,w_200,h_200,c_fill/")
    # makler: detal və Təkliflərim — miniatür; admin — miniatür
    assert thumb in a.get(f"/kabinet/teleb/{rid}").get_data(as_text=True)
    assert thumb in a.get("/kabinet/teklifler").get_data(as_text=True)
    assert thumb in admin_client.get(f"/admin/teleb/{rid}").get_data(as_text=True)
    # başqa makler: A-nın şəkillərini görmür, A-nın təklifini geri çəkə bilmir
    hb = b.get(f"/kabinet/teleb/{rid}").get_data(as_text=True)
    assert main_url.split("/upload/")[1] not in hb and 'type="file"' in hb
    r = b.post(f"/kabinet/teklif/{oid}/geri-cek", data={"csrf_token": csrf(b, "/kabinet/teklifler")})
    assert r.status_code == 404

    # geri çəkiləndə şəkillər qalır, müştəri görmür
    r = a.post(f"/kabinet/teklif/{oid}/geri-cek", data={"csrf_token": csrf(a, "/kabinet/teklifler")})
    assert r.status_code == 302 and cloud.deleted == []
    assert db.execute("SELECT COUNT(*) FROM offer_images WHERE offer_id = ?", (oid,)).fetchone()[0] == 2
    assert main_url.split("/upload/")[1] not in app.test_client().get(f"/teleb/{token}").get_data(as_text=True)


def test_offer_without_images_and_legacy_image_url(app, db, make_makler, make_request):
    rid, token = make_request()
    a, ua = make_makler(RENT_AREA)
    assert post_offer(a, rid, []).status_code == 302
    html = app.test_client().get(f"/teleb/{token}").get_data(as_text=True)
    assert "Şəkil yoxdur" in html
    db.execute("UPDATE offers SET image_url = 'https://kohne.example/ev.jpg' WHERE agent_id = ?", (ua,))
    db.commit()
    assert 'src="https://kohne.example/ev.jpg"' in app.test_client().get(f"/teleb/{token}").get_data(as_text=True)


def test_bad_file_in_form_uploads_nothing(app, db, cloud, make_makler, make_request):
    rid, _ = make_request()
    a, ua = make_makler(RENT_AREA)
    r = post_offer(a, rid, jpgs(1) + [(b"saxta", "x.jpg")])
    html = r.get_data(as_text=True)
    assert r.status_code == 400 and "2-ci şəkil" in html and "Şəkilləri yenidən seçin" in html
    assert 'value="Təmirli 2 otaqlı"' in html               # forma dəyərləri qalır
    assert cloud.uploaded == [] and not db.execute("SELECT 1 FROM offers WHERE agent_id = ?", (ua,)).fetchone()
    assert post_offer(a, rid, jpgs(6)).status_code == 400 and cloud.uploaded == []


# ---------------------------------------------------------------- H3: Cloudinary xətası
def test_cloudinary_failure_rolls_back(app, db, cloud, make_makler, make_request):
    rid, _ = make_request()
    a, ua = make_makler(RENT_AREA)
    cloud.fail_on = 3
    r = post_offer(a, rid, jpgs(3))
    assert r.status_code == 400 and "Şəkil yüklənmədi, yenidən cəhd edin." in r.get_data(as_text=True)
    assert len(cloud.uploaded) == 2 and sorted(cloud.deleted) == sorted(p for p, _ in cloud.uploaded)
    assert not db.execute("SELECT 1 FROM offers WHERE agent_id = ?", (ua,)).fetchone()
    assert db.execute("SELECT COUNT(*) FROM offer_images i JOIN offers o ON o.id = i.offer_id "
                      "WHERE o.agent_id = ?", (ua,)).fetchone()[0] == 0
    cloud.fail_on = None   # yenidən cəhd işləyir
    assert post_offer(a, rid, jpgs(1)).status_code == 302


# ---------------------------------------------------------------- H4: limit və FOR UPDATE
def test_offer_limit_with_images_and_race(app, db, cloud, make_makler, make_request):
    rid, _ = make_request()
    makers = [make_makler(RENT_AREA) for _ in range(5)]
    for c, _ in makers[:4]:
        assert post_offer(c, rid, jpgs(1)).status_code == 302
    late, late_uid = make_makler(RENT_AREA)
    racer, racer_uid = makers[4]

    # yarış: "late" yükləyərkən 5-ci təklif başqa maklerdən gəlir → son FOR UPDATE yoxlaması rədd edir
    def sneak(n):  # ayrıca bağlantı = paralel sorğu (iç-içə test client eyni g-ni paylaşardı)
        from maklerhub import offers
        cloud.on_upload = None
        oid, error = offers.send_offer(db, rid, racer_uid, {"title": "Rəqib", "price": 700, "district": "Yasamal",
                                                            "rooms": 2, "area": None, "image_url": None,
                                                            "description": ""})
        assert oid and not error
    before = len(cloud.uploaded)
    cloud.on_upload = sneak
    r = post_offer(late, rid, jpgs(2))
    assert r.status_code == 400 and "limit dolub" in r.get_data(as_text=True)
    mine = [p for p, _ in cloud.uploaded[before:]]
    assert len(mine) == 2 and sorted(cloud.deleted) == sorted(mine)
    assert not db.execute("SELECT 1 FROM offers WHERE agent_id = ?", (late_uid,)).fetchone()
    assert db.execute("SELECT COUNT(*) FROM offers WHERE request_id = ? AND status != 'withdrawn'",
                      (rid,)).fetchone()[0] == 5
    # limit artıq dolubsa şəkil heç yüklənmir
    other, _ = make_makler(RENT_AREA)
    n = len(cloud.uploaded)
    assert post_offer(other, rid, jpgs(1)).status_code == 400 and len(cloud.uploaded) == n


def test_hourly_rate_limit_and_demo_max_two(app, db, cloud, make_makler, make_request):
    from maklerhub.helpers import ts
    rid, _ = make_request()
    rid2, _ = make_request()
    a, ua = make_makler(RENT_AREA)
    assert post_offer(a, rid, jpgs(1)).status_code == 302
    oid = db.execute("SELECT id FROM offers WHERE agent_id = ?", (ua,)).fetchone()[0]
    db.executemany("INSERT INTO offer_images(offer_id, public_id, url, sort, created_at) VALUES (?,?,?,?,?)",
                   [(oid, f"pytest/limit/{ua}/{i}", "https://res.cloudinary.com/x/image/upload/a.jpg", i + 1, ts())
                    for i in range(58)])
    db.commit()   # 59 şəkil son 1 saatda
    r = post_offer(a, rid2, jpgs(2))
    assert r.status_code == 400 and "Saatda ən çox 60 şəkil" in r.get_data(as_text=True)

    email, du = make_makler(RENT_AREA, login_now=False)
    db.execute("UPDATE users SET is_demo = 1 WHERE id = ?", (du,))
    db.execute("UPDATE customer_requests SET is_demo = 1 WHERE id = ?", (rid2,))
    db.commit()
    from conftest import login
    demo, _ = login(app, email, "TestPass123")
    assert 'data-max="2"' in demo.get(f"/kabinet/teleb/{rid2}").get_data(as_text=True)
    assert post_offer(demo, rid2, jpgs(3)).status_code == 400
    assert post_offer(demo, rid2, jpgs(2)).status_code == 302
    db.execute("DELETE FROM customer_requests WHERE id = ?", (rid2,))
    db.commit()


# ---------------------------------------------------------------- H5: silmə (admin, anonimləşdirmə, clear-demo, yetimlər)
def test_admin_delete_and_anonymize_destroy_images(app, db, cloud, make_makler, make_request, admin_client):
    rid, _ = make_request()
    a, ua = make_makler(RENT_AREA)
    assert post_offer(a, rid, jpgs(2)).status_code == 302
    ids = [r[0] for r in db.execute("SELECT i.public_id FROM offer_images i JOIN offers o ON o.id = i.offer_id "
                                    "WHERE o.request_id = ?", (rid,))]
    tok = csrf(admin_client, f"/admin/teleb/{rid}")
    assert admin_client.post(f"/admin/teleb/{rid}/sil", data={"csrf_token": tok}).status_code == 302
    assert sorted(cloud.deleted) == sorted(ids)

    # 90 günlük anonimləşdirmə
    rid, _ = make_request()
    b, _ = make_makler(RENT_AREA)
    assert post_offer(b, rid, jpgs(1)).status_code == 302
    pid = db.execute("SELECT i.public_id FROM offer_images i JOIN offers o ON o.id = i.offer_id "
                     "WHERE o.request_id = ?", (rid,)).fetchone()[0]
    db.execute("UPDATE customer_requests SET status = 'closed', closed_at = '2020-01-01 00:00:00' WHERE id = ?", (rid,))
    db.commit()
    from maklerhub import maintenance
    assert maintenance.lazy_expire(db) is True
    assert pid in cloud.deleted
    assert not db.execute("SELECT 1 FROM offer_images WHERE public_id = ?", (pid,)).fetchone()


def test_admin_delete_survives_cloudinary_error(app, db, cloud, make_makler, make_request, admin_client, monkeypatch):
    from maklerhub import images
    rid, _ = make_request()
    a, _ = make_makler(RENT_AREA)
    assert post_offer(a, rid, jpgs(1)).status_code == 302

    def boom(_ids):
        raise RuntimeError("cloud down")
    monkeypatch.setattr(images, "_delete", boom)
    tok = csrf(admin_client, f"/admin/teleb/{rid}")
    assert admin_client.post(f"/admin/teleb/{rid}/sil", data={"csrf_token": tok}).status_code == 302
    assert not db.execute("SELECT 1 FROM customer_requests WHERE id = ?", (rid,)).fetchone()
    assert db.execute("SELECT 1 FROM logs WHERE message LIKE 'Cloudinary-dən şəkil silinmədi%'").fetchone()


def test_clear_demo_destroys_images_and_seed_uploads_none(app, db, cloud, make_makler, make_request):
    runner = app.test_cli_runner()
    assert runner.invoke(args=["seed-demo"]).exit_code == 0
    assert cloud.uploaded == []
    oid = db.execute("SELECT o.id FROM offers o JOIN customer_requests r ON r.id = o.request_id "
                     "WHERE r.is_demo = 1 LIMIT 1").fetchone()[0]
    db.execute("INSERT INTO offer_images(offer_id, public_id, url, sort, created_at) VALUES (?, ?, ?, 0, '2026-01-01')",
               (oid, f"maklerhub/offers/{oid}/demo-sinaq", "https://res.cloudinary.com/t/image/upload/x.jpg"))
    db.commit()
    res = runner.invoke(args=["clear-demo"])
    assert res.exit_code == 0 and f"maklerhub/offers/{oid}/demo-sinaq" in cloud.deleted


def test_weekly_orphan_cleanup(app, db, cloud, make_makler, make_request):
    from maklerhub import maintenance
    rid, _ = make_request()
    a, _ = make_makler(RENT_AREA)
    assert post_offer(a, rid, jpgs(1)).status_code == 302
    known = db.execute("SELECT public_id FROM offer_images ORDER BY id DESC LIMIT 1").fetchone()[0]
    cloud.listing = [{"public_id": known, "created_at": "2020-01-01T00:00:00Z"},
                     {"public_id": "maklerhub/offers/999999/kohne-yetim", "created_at": "2020-01-01T00:00:00Z"},
                     {"public_id": "maklerhub/offers/999999/yeni-yetim", "created_at": "2999-01-01T00:00:00Z"}]
    db.execute("DELETE FROM settings WHERE key = 'last_orphan_images_at'")
    db.commit()
    assert maintenance.orphans_if_due(db) == 1
    assert cloud.deleted == ["maklerhub/offers/999999/kohne-yetim"]
    assert maintenance.orphans_if_due(db) is None   # həftədə 1 dəfə


# ---------------------------------------------------------------- 413, Sentry
def test_too_large_form_gives_clear_message(app, make_makler, make_request):
    rid, _ = make_request()
    a, _ = make_makler(RENT_AREA)
    tok = csrf(a, f"/kabinet/teleb/{rid}")
    data = dict(offer_form(), csrf_token=tok, images=[(io.BytesIO(b"\0" * (27 * 1024 * 1024)), "a.jpg", "image/jpeg")])
    r = a.post(f"/kabinet/teleb/{rid}/teklif", data=data, content_type="multipart/form-data")
    assert r.status_code == 303 and r.headers["Location"].endswith(f"/kabinet/teleb/{rid}")
    assert "26 MB" in a.get(f"/kabinet/teleb/{rid}").get_data(as_text=True)


def test_secrets_never_reach_sentry_or_html(app, make_makler, make_request, monkeypatch):
    from maklerhub import monitoring
    monkeypatch.setenv("CLOUDINARY_API_SECRET", "sirr-acar-123456")
    out = monitoring.before_send({"exception": {"values": [{"value": "Error: api_secret=sirr-acar-123456"}]}})
    assert "sirr-acar-123456" not in str(out)
    rid, _ = make_request()
    a, _ = make_makler(RENT_AREA)
    html = a.get(f"/kabinet/teleb/{rid}").get_data(as_text=True)
    assert "pytest-saxta" not in html and 'accept="image/jpeg,image/png,image/webp"' in html
    assert 'enctype="multipart/form-data"' in html and "offer-upload.js" in html
