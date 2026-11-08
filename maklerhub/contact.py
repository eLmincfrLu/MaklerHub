"""Admin əlaqə məlumatı (settings-dən, kodda sabit deyil): ad, telefon, e-poçt.

Şablonlara `admin_contact` kimi ötürülür — tənbəl obyekt: bazadan YALNIZ şablon ona müraciət edəndə və sorğu başına
bir dəfə oxunur (g-də saxlanılır). İctimai səhifələrdə e-poçt/telefon açıq mətn kimi yazılmır (bot qoruması:
data-atributda tərsinə, static/js/contact-reveal.js yığır; JS yoxdursa "ad [at] domen [dot] com").
"""
from flask import g

from .db import get_db, get_settings
from .helpers import normalize_phone, phone_display

KEYS = ("admin_contact_name", "admin_contact_phone", "admin_contact_email")


def build(settings):
    phone = normalize_phone(settings.get("admin_contact_phone")) or ""
    email = (settings.get("admin_contact_email") or "").strip()
    digits = phone.lstrip("+")
    return {
        "name": (settings.get("admin_contact_name") or "").strip(),
        "phone": phone,                                   # +994XXXXXXXXX
        "phone_display": phone_display(phone) if phone else "",
        "tel": phone,
        "wa": f"https://wa.me/{digits}" if digits else "",
        "email": email,
        # bot qoruması üçün: tərsinə yazılmış dəyərlər və JS-siz oxunaqlı variantlar
        "email_rev": email[::-1],
        "phone_rev": phone[::-1],
        "email_fallback": email.replace("@", " [at] ").replace(".", " [dot] "),
        "phone_fallback": " · ".join(phone_display(phone).split()) if phone else "",
    }


def get():
    """Sorğu başına bir dəfə oxunur."""
    if "_admin_contact" not in g:
        g._admin_contact = build(get_settings(get_db()))
    return g._admin_contact


class LazyContact:
    """Jinja: {{ admin_contact.email }} — ilk müraciətdə get()."""

    def __getattr__(self, name):
        try:
            return get()[name]
        except KeyError:
            raise AttributeError(name) from None

    def __getitem__(self, name):
        return get()[name]


def init_app(app):
    lazy = LazyContact()

    @app.context_processor
    def inject_admin_contact():
        return {"admin_contact": lazy}
