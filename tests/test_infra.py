"""F1: test bazası qoruyucusu; F9: paralel startup (advisory lock) — YALNIZ test bazasında."""
import os
import subprocess
import sys
import time

import pytest

import dbguard

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_guard_unit():
    with pytest.raises(dbguard.UnsafeTestDatabase):
        dbguard.check({"DATABASE_URL": "postgresql://u:p@real.example/db"})
    with pytest.raises(dbguard.UnsafeTestDatabase):
        dbguard.check({"DATABASE_URL": "postgresql://u:p@Same.Example/db", "TEST_DATABASE_URL": "postgres://x:y@same.example/t"})
    assert dbguard.check({"DATABASE_URL": "postgresql://u:p@real.example/db",
                          "TEST_DATABASE_URL": "postgresql://u:p@test.example/db"}).endswith("test.example/db")


@pytest.mark.parametrize("env", [
    {"TEST_DATABASE_URL": ""},
    {"TEST_DATABASE_URL": "postgresql://u:p@eyni-host.example/db", "DATABASE_URL": "postgresql://u:p@eyni-host.example/x"},
])
def test_pytest_stops_without_safe_test_db(env):
    """Ayrı pytest prosesi: qoruyucu işə düşür, heç bir test (və baza bağlantısı) işləmir."""
    full = dict(os.environ, PYTHONIOENCODING="utf-8", **env)
    p = subprocess.run([sys.executable, "-X", "utf8", "-m", "pytest", "tests/test_matching.py", "-p", "no:cacheprovider",
                        "-q"], cwd=ROOT, env=full, capture_output=True, text=True, encoding="utf-8", timeout=120)
    out = p.stdout + p.stderr
    assert p.returncode != 0
    assert "TƏHLÜKƏSİZLİK" in out and "passed" not in out


SNIPPET = """
import os, sys, time
sys.path.insert(0, {root!r})
from maklerhub import db
time.sleep(max(0.0, float(sys.argv[1]) - time.time()))
db.bootstrap(os.environ["DATABASE_URL"])
print("ok")
"""


def test_parallel_startup_no_deadlock(app):
    url = app.config["DATABASE"]
    assert dbguard.host_of(url) != os.environ.get("MAKLERHUB_REAL_DATABASE_HOST")
    code = SNIPPET.format(root=ROOT)
    for _ in range(3):
        start = str(time.time() + 6)  # hər iki proses import-dan sonra eyni anda başlasın
        procs = [subprocess.Popen([sys.executable, "-c", code, start], cwd=ROOT, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                  env=dict(os.environ, DATABASE_URL=url, PYTHONIOENCODING="utf-8")) for _ in range(3)]
        results = [p.communicate(timeout=120) for p in procs]
        for p, (out, err) in zip(procs, results):
            assert p.returncode == 0 and "ok" in out, err[-800:]
            assert "deadlock" not in err.lower()
