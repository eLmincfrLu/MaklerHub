"""Bazaya yazan testlər üçün qoruyucu: YALNIZ TEST_DATABASE_URL (Neon `test` branch-i).

- TEST_DATABASE_URL yoxdursa və ya DATABASE_URL ilə eynidirsə (tam sətir və ya host) → test işləmir (çıxış kodu 2),
  heç vaxt DATABASE_URL-ə qayıtmır.
- Proses daxilində DATABASE_URL test bazasına yönəldilir: kod təsadüfən DATABASE_URL oxusa da real bazaya getmir.
- Bağlantı sətirləri heç yerdə çap olunmur.
"""
import os
import sys
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _host(url):
    return (urlparse(url.replace("postgres://", "postgresql://", 1)).hostname or "").lower()


def check(test_url, prod_url):
    """→ (icazə, səbəb). Bağlantı sətirlərini qaytarmır."""
    if not test_url:
        return False, "TEST_DATABASE_URL yoxdur"
    if prod_url and (test_url.strip() == prod_url.strip() or _host(test_url) == _host(prod_url)):
        return False, "TEST_DATABASE_URL real bazanın (DATABASE_URL) hostu ilə eynidir"
    if not _host(test_url):
        return False, "TEST_DATABASE_URL-də host yoxdur"
    return True, ""


def url():
    """Test bazasının bağlantı sətri; qoruyucu keçməsə proses dayanır (kod 2)."""
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
    test_url, prod_url = os.environ.get("TEST_DATABASE_URL"), os.environ.get("DATABASE_URL")
    ok, why = check(test_url, prod_url)
    if not ok:
        print(f"✗ TEST QORUYUCUSU: {why} — test işlədilmir (real bazaya qoşulmur).")
        sys.exit(2)
    os.environ["DATABASE_URL"] = test_url  # yalnız bu prosesdə
    return test_url
