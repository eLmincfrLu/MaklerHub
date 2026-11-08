"""Sentry (xəta monitorinqi). Yalnız SENTRY_DSN mühit dəyişəni varsa işə düşür; yoxdursa heç nə etmir.

Şəxsi məlumat Sentry-yə getmir: send_default_pii=False, lokal dəyişənlər göndərilmir, sorğunun gövdəsi (forma —
ad, telefon, qeyd), cookie, query string və başlıqların çoxu silinir; mətnlərdəki telefon nömrələri, e-poçtlar və
müştəri linkinin tokeni (/teleb/<token>) maskalanır.
"""
import logging
import os
import re

log = logging.getLogger(__name__)

PHONE_RE = re.compile(r"(?:\+?994|\b0)[\s\-()]*\d{2}[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}\b|\b\d{9}\b")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
TOKEN_RE = re.compile(r"(/teleb/)[A-Za-z0-9_\-]{16,}")
TOKEN_KEYS = {"token", "tokens", "mh_requests"}
EMAIL_KEYS = {"email", "to", "sender", "api-key", "api_key"}  # Brevo payload / forma sahələri
SAFE_HEADERS = {"user-agent", "accept", "accept-language", "content-type", "content-length", "host", "referer"}

_enabled = False


def _secrets():
    """Mühitdəki açar dəyərləri (Cloudinary və s.) — mətndə rast gəlinsə maskalanır."""
    out = []
    for key in ("CLOUDINARY_API_SECRET", "CLOUDINARY_API_KEY", "MAKLERHUB_SECRET", "MAKLERHUB_ADMIN_PASSWORD",
                "BREVO_API_KEY"):
        v = (os.environ.get(key) or "").strip()
        if len(v) >= 6:
            out.append(v)
    url = os.environ.get("CLOUDINARY_URL") or ""
    if url:
        out.append(url)
    return out


def scrub_text(value):
    for secret in _secrets():
        if secret in value:
            value = value.replace(secret, "[gizli]")
    value = TOKEN_RE.sub(r"\1[token]", value)
    value = EMAIL_RE.sub("[email]", value)
    return PHONE_RE.sub("[telefon]", value)


def _scrub(obj, depth=0):
    if depth > 12:
        return obj
    if isinstance(obj, str):
        return scrub_text(obj)
    if isinstance(obj, dict):
        # /api/teleb/status body-si və s.: token açarları tam maskalanır (URL-dəki /teleb/<token> TOKEN_RE ilə)
        return {k: "[token]" if isinstance(k, str) and k.lower() in TOKEN_KEYS
                else "[email]" if isinstance(k, str) and k.lower() in EMAIL_KEYS
                else _scrub(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(v, depth + 1) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_scrub(v, depth + 1) for v in obj)
    return obj


def before_send(event, _hint=None):
    req = event.get("request")
    if isinstance(req, dict):
        for key in ("data", "cookies", "query_string", "env"):
            req.pop(key, None)
        headers = req.get("headers")
        if isinstance(headers, dict):
            req["headers"] = {k: v for k, v in headers.items() if k.lower() in SAFE_HEADERS}
    event.pop("user", None)
    return _scrub(event)


def before_breadcrumb(crumb, _hint=None):
    return _scrub(crumb)


def init_sentry():
    """SENTRY_DSN varsa Sentry-ni bir dəfə işə salır. Qaytarır: aktivdirmi."""
    global _enabled
    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    if not dsn or _enabled:
        return _enabled
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
    except ImportError:
        log.warning("SENTRY_DSN təyin olunub, amma sentry-sdk quraşdırılmayıb — monitorinq söndürülüb.")
        return False
    sentry_sdk.init(dsn=dsn, integrations=[FlaskIntegration()], traces_sample_rate=0, send_default_pii=False,
                    include_local_variables=False, max_request_body_size="never",
                    before_send=before_send, before_breadcrumb=before_breadcrumb,
                    environment=os.environ.get("MAKLERHUB_ENV", "production"))
    _enabled = True
    return True
