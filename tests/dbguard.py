"""Test bazası qoruyucusu: testlər YALNIZ TEST_DATABASE_URL ilə işləyir, real baza ilə eyni host olarsa dayanır."""
from urllib.parse import urlsplit


class UnsafeTestDatabase(Exception):
    pass


def host_of(url):
    try:
        return (urlsplit((url or "").strip().replace("postgres://", "postgresql://", 1)).hostname or "").lower()
    except ValueError:
        return ""


def check(env):
    """Təhlükəsiz test bazasının URL-ini qaytarır, yoxsa UnsafeTestDatabase."""
    test_url = (env.get("TEST_DATABASE_URL") or "").strip()
    if not test_url:
        raise UnsafeTestDatabase("TEST_DATABASE_URL təyin olunmayıb (.env). Testlər real bazada işləmir — dayandırıldı.")
    test_host = host_of(test_url)
    if not test_host:
        raise UnsafeTestDatabase("TEST_DATABASE_URL-dən host oxunmadı — dayandırıldı.")
    real_host = host_of(env.get("DATABASE_URL"))
    if real_host and test_host == real_host:
        raise UnsafeTestDatabase(f"TEST_DATABASE_URL real baza ilə eyni host-dadır ({test_host}) — dayandırıldı.")
    return test_url
