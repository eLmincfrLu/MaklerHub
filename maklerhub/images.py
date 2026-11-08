"""Təklif şəkilləri: yoxlama və emal (Pillow), Cloudinary-ə imzalı yükləmə, silmə, çatdırma URL-ləri.

- Fayl adı və brauzerin göndərdiyi tip heç yerdə istifadə olunmur: format Pillow ilə MƏZMUNA görə yoxlanır
  (yalnız JPEG/PNG/WEBP), ölçü 200..10000 px, decompression bomb qorunur (Image.MAX_IMAGE_PIXELS).
- Emal: EXIF orientasiyası tətbiq olunur, sonra metadata (EXIF, GPS, XMP, şərhlər) atılır — yalnız ICC rəng profili
  saxlanılır (RGB şəkillərdə); uzun tərəf ≤ 1600 px; JPEG q=82.
- Cloudinary: server tərəfindən imzalı yükləmə (SDK), folder maklerhub/offers/<offer_id>, public_id — təsadüfi.
  Açarlar yalnız mühitdən (CLOUDINARY_*); heç yerə yazılmır.
- Şəbəkə çağırışları _upload/_delete/_list funksiyalarındadır — testlər onları mock edir.
"""
import io
import logging
import os
import secrets
import warnings
from dataclasses import dataclass
from datetime import timedelta

from PIL import Image, ImageOps

log = logging.getLogger(__name__)

MAX_FILES = 5
DEMO_MAX_FILES = 2
MAX_BYTES = 5 * 1024 * 1024
MIN_SIDE = 200
MAX_SIDE = 10_000
LONG_SIDE = 1600
JPEG_QUALITY = 82
HOURLY_LIMIT = 60          # makler başına saatda yüklənən şəkil
FOLDER = "maklerhub/offers"
FORMATS = {"JPEG", "PNG", "WEBP"}
FIELD = "images"

# 10000×10000-dən böyük piksel sayı açılmır (Pillow: limitin 2 qatından çox → DecompressionBombError,
# arada → DecompressionBombWarning; aşağıda xəbərdarlıq da xətaya çevrilir)
Image.MAX_IMAGE_PIXELS = MAX_SIDE * MAX_SIDE

UPLOAD_FAILED = "Şəkil yüklənmədi, yenidən cəhd edin."


class ImageError(ValueError):
    """İstifadəçiyə göstərilən yoxlama xətası."""


class UploadError(RuntimeError):
    pass


@dataclass
class Processed:
    data: bytes
    width: int
    height: int


# ---------------------------------------------------------------- yoxlama və emal
def _read_limited(stream):
    data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ImageError(f"Şəkil {MAX_BYTES // (1024 * 1024)} MB-dan böyük ola bilməz.")
    if not data:
        raise ImageError("Şəkil faylı boşdur.")
    return data


def _open_checked(data):
    """Başlığı oxuyur, formatı və ölçünü yoxlayır, verify edir. Qaytarır: format."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(data)) as im:
                fmt, (w, h) = im.format, im.size
                if fmt not in FORMATS:
                    raise ImageError("Yalnız JPEG, PNG və ya WEBP şəkil yükləmək olar.")
                if w > MAX_SIDE or h > MAX_SIDE:
                    raise ImageError(f"Şəkil çox böyükdür (maks. {MAX_SIDE}×{MAX_SIDE} piksel).")
                if w < MIN_SIDE or h < MIN_SIDE:
                    raise ImageError(f"Şəkil çox kiçikdir (ən azı {MIN_SIDE}×{MIN_SIDE} piksel).")
                im.verify()
        except ImageError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise ImageError(f"Şəkil çox böyükdür (maks. {MAX_SIDE}×{MAX_SIDE} piksel).")
        except Exception:
            raise ImageError("Fayl şəkil deyil və ya zədələnib. Yalnız JPEG, PNG, WEBP.")
    return fmt


def process(stream):
    """Bir faylı yoxlayır və emal edir. Qaytarır: Processed. Xətada ImageError."""
    data = _read_limited(stream)
    _open_checked(data)
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(data)) as src:  # verify-dən sonra yenidən açmaq lazımdır
                src.load()
                icc = src.info.get("icc_profile") if src.mode in ("RGB", "RGBA", "P", "L", "LA") else None
                im = ImageOps.exif_transpose(src)
                if im.mode in ("RGBA", "LA", "P"):
                    im = im.convert("RGBA")
                    bg = Image.new("RGB", im.size, (255, 255, 255))  # şəffaflıq → ağ fon (JPEG)
                    bg.paste(im, mask=im.getchannel("A"))
                    im = bg
                elif im.mode != "RGB":
                    im = im.convert("RGB")
                im.thumbnail((LONG_SIDE, LONG_SIDE), Image.LANCZOS)
                # yeni şəkil: köhnə info/EXIF/GPS/XMP ötürülmür; yalnız ICC
                clean = Image.new("RGB", im.size)
                clean.paste(im)
                out = io.BytesIO()
                kw = {"icc_profile": icc} if icc else {}
                clean.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True, **kw)
        except ImageError:
            raise
        except Exception:
            raise ImageError("Şəkil emal olunmadı. Başqa şəkil seçin.")
    return Processed(out.getvalue(), clean.width, clean.height)


def process_all(files, max_files):
    """Bütün faylları yoxlayır/emal edir (biri səhvdirsə heç biri yüklənmir). Qaytarır: ([Processed], [xəta])."""
    if len(files) > max_files:
        return [], [f"Ən çox {max_files} şəkil yükləmək olar."]
    out, errors = [], []
    for i, f in enumerate(files, start=1):
        try:
            out.append(process(f.stream))
        except ImageError as exc:
            errors.append(f"{i}-ci şəkil: {exc}" if len(files) > 1 else str(exc))
    return (out, []) if not errors else ([], errors)


def uploaded_files(request):
    """Formadan gələn boş olmayan fayllar (ad yoxlanmır — yalnız boş seçim atılır)."""
    return [f for f in request.files.getlist(FIELD) if f and (f.filename or f.content_length)]


# ---------------------------------------------------------------- Cloudinary
def configured():
    return all((os.environ.get(k) or "").strip()
               for k in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET"))


_sdk_ready = False


def _sdk():
    global _sdk_ready
    import cloudinary
    if not _sdk_ready:
        cloudinary.config(cloud_name=os.environ["CLOUDINARY_CLOUD_NAME"].strip(),
                          api_key=os.environ["CLOUDINARY_API_KEY"].strip(),
                          api_secret=os.environ["CLOUDINARY_API_SECRET"].strip(), secure=True)
        _sdk_ready = True
    return cloudinary


def _upload(data, folder, public_id):
    import cloudinary.uploader  # noqa: F401
    res = _sdk().uploader.upload(io.BytesIO(data), folder=folder, public_id=public_id, overwrite=False,
                                 unique_filename=False, use_filename=False, resource_type="image")
    return {"public_id": res["public_id"], "url": res["secure_url"], "width": res.get("width"),
            "height": res.get("height"), "bytes": res.get("bytes")}


def _delete(public_ids):
    import cloudinary.api  # noqa: F401
    return _sdk().api.delete_resources(list(public_ids), resource_type="image", type="upload")


def _list(prefix, cursor=None):
    """Qaytarır: ([{'public_id', 'created_at'}], next_cursor)."""
    import cloudinary.api  # noqa: F401
    kw = {"type": "upload", "prefix": prefix, "max_results": 500, "resource_type": "image"}
    if cursor:
        kw["next_cursor"] = cursor
    res = _sdk().api.resources(**kw)
    return [{"public_id": r["public_id"], "created_at": r.get("created_at", "")} for r in res.get("resources", [])], \
        res.get("next_cursor")


def upload_all(offer_id, processed):
    """Hamısını yükləyir; biri uğursuz olsa artıq yüklənənlər silinir və UploadError. Qaytarır: [dict]."""
    if not configured():
        raise UploadError("Cloudinary konfiqurasiya olunmayıb")
    done = []
    try:
        for p in processed:
            done.append(_upload(p.data, f"{FOLDER}/{offer_id}", secrets.token_hex(10)))
    except Exception as exc:
        log.warning("Cloudinary yükləmə xətası (%s), %d şəkil geri silinir", exc.__class__.__name__, len(done))
        destroy([d["public_id"] for d in done])
        raise UploadError(exc.__class__.__name__) from None
    return done


def destroy(public_ids, con=None):
    """Cloudinary-dən toplu silmə. Xəta axını sındırmır: loqa yazılır. Qaytarır: uğurludurmu."""
    ids = [p for p in dict.fromkeys(public_ids or []) if p]
    if not ids:
        return True
    if not configured():
        log.warning("Cloudinary konfiqurasiya olunmayıb — %d şəkil silinmədi", len(ids))
        return False
    ok = True
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        try:
            _delete(chunk)
        except Exception as exc:
            ok = False
            log.warning("Cloudinary silmə xətası (%s): %d şəkil", exc.__class__.__name__, len(chunk))
            if con is not None:
                try:
                    from .db import add_log
                    add_log(con, "err", "Sistem", f"Cloudinary-dən şəkil silinmədi ({exc.__class__.__name__})",
                            "\n".join(chunk[:20]))
                except Exception:
                    pass
    return ok


def public_ids_for(con, where_sql, params):
    """offer_images-dən public_id-lər: where_sql offers (o) üzrə şərtdir."""
    return [r[0] for r in con.execute(
        f"SELECT i.public_id FROM offer_images i JOIN offers o ON o.id = i.offer_id WHERE {where_sql}", params)]


def delete_orphans(con, now_dt, min_age=timedelta(hours=24)):
    """Bazada olmayan və 24 saatdan köhnə Cloudinary şəkillərini silir. Qaytarır: silinən say."""
    from .helpers import ts
    known = {r[0] for r in con.execute("SELECT public_id FROM offer_images")}
    limit = (now_dt - min_age).strftime("%Y-%m-%dT%H:%M:%S")  # Cloudinary created_at: ISO, UTC
    orphans, cursor = [], None
    while True:
        items, cursor = _list(FOLDER + "/", cursor)
        for it in items:
            if it["public_id"] not in known and it["created_at"][:19] < limit:
                orphans.append(it["public_id"])
        if not cursor:
            break
    if orphans and not destroy(orphans, con):
        return 0
    if orphans:
        from .db import add_log
        add_log(con, "info", "Sistem", f"Yetim Cloudinary şəkilləri silindi: {len(orphans)}", f"vaxt={ts()}")
    return len(orphans)


# ---------------------------------------------------------------- çatdırma URL-ləri
CARD, THUMB, FULL = "f_auto,q_auto,w_800", "f_auto,q_auto,w_200,h_200,c_fill", "f_auto,q_auto,w_1600"


def transformed(url, transformation):
    marker = "/image/upload/"
    if not url or marker not in url:
        return url
    head, tail = url.split(marker, 1)
    return f"{head}{marker}{transformation}/{tail}"


def _scaled(w, h, target):
    if not w or not h:
        return None, None
    if w <= target:
        return w, h
    return target, round(h * target / w)


def view(row):
    """offer_images sətri → şablon üçün URL-lər və ölçülər."""
    cw, ch = _scaled(row["width"], row["height"], 800)
    return {"card": transformed(row["url"], CARD), "thumb": transformed(row["url"], THUMB),
            "full": transformed(row["url"], FULL), "w": cw, "h": ch}


def images_by_offer(con, offer_ids):
    """{offer_id: [view, ...]} sort sırası ilə (bir sorğu)."""
    if not offer_ids:
        return {}
    out = {}
    for r in con.execute("SELECT * FROM offer_images WHERE offer_id = ANY(?) ORDER BY offer_id, sort, id",
                         (list(offer_ids),)):
        out.setdefault(r["offer_id"], []).append(view(r))
    return out


def recent_count(con, agent_id, now_dt):
    """Son 1 saatda maklerin yüklədiyi şəkil sayı (limit üçün)."""
    from .helpers import ts
    return con.execute("SELECT COUNT(*) FROM offer_images i JOIN offers o ON o.id = i.offer_id "
                       "WHERE o.agent_id = ? AND i.created_at >= ?",
                       (agent_id, ts(now_dt - timedelta(hours=1)))).fetchone()[0]
