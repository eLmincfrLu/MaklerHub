"""tests/testdb.py qoruyucusunun testi (bazaya qoşulmur, bağlantı sətirləri çap olunmur).

    PYTHONIOENCODING=utf-8 python tests/test_testdb.py
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import testdb

results = []
def check(name, ok, info=""):
    results.append(("✓" if ok else "✗") + " " + name + (f"  [{info}]" if info else ""))


PROD = "postgresql://u:p@ep-prod-123.eu-central-1.aws.neon.tech/neondb"
TEST = "postgresql://u:p@ep-test-456.eu-central-1.aws.neon.tech/neondb"
check("1 TEST_DATABASE_URL yoxdur → imtina", testdb.check(None, PROD)[0] is False)
check("2 TEST_DATABASE_URL = DATABASE_URL → imtina", testdb.check(PROD, PROD)[0] is False)
check("3 eyni host, başqa baza/istifadəçi → imtina",
      testdb.check("postgres://x:y@ep-prod-123.eu-central-1.aws.neon.tech/other", PROD)[0] is False)
check("4 fərqli host → icazə", testdb.check(TEST, PROD) == (True, ""))

probe = ("import os, sys; sys.path.insert(0, %r); import testdb; u = testdb.url(); "
         "print('OK', os.environ['DATABASE_URL'] == u)" % HERE)


def run(test_url, prod_url):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", TEST_DATABASE_URL=test_url, DATABASE_URL=prod_url)
    p = subprocess.run([sys.executable, "-c", probe], env=env, capture_output=True, text=True, encoding="utf-8")
    return p.returncode, p.stdout


code, out = run("", PROD)
check("5 url(): TEST_DATABASE_URL boşdur → proses kod 2 ilə dayanır, real bazaya qoşulmur",
      code == 2 and "TEST QORUYUCUSU" in out and "OK" not in out)
code, out = run(PROD, PROD)
check("6 url(): real baza ilə eyni → kod 2", code == 2 and "OK" not in out)
code, out = run(TEST, PROD)
check("7 url(): fərqli host → icazə, proses daxilində DATABASE_URL test bazasına yönəlir",
      code == 0 and "OK True" in out)
check("8 çıxışda bağlantı sətri yoxdur", "ep-prod" not in out and "ep-test" not in out and "p@" not in out)

writers = ["test_auto.py", "test_batch.py", "test_dups.py", "test_notifications.py", "test_removed.py"]
bad = []
for name in writers:
    text = open(os.path.join(HERE, name), encoding="utf-8").read()
    if "testdb.url()" not in text or 'os.environ["DATABASE_URL"]' in text or "load_dotenv(" in text:
        bad.append(name)
check("9 bazaya yazan bütün testlər testdb.url() işlədir, DATABASE_URL-i birbaşa oxumur", not bad, f"{bad}")

print("\n".join(results))
sys.exit(1 if any(r.startswith("✗") for r in results) else 0)
