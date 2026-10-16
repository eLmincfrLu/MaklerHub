"""GitHub Actions fayllarının yoxlanması (şəbəkəsiz, bazasız): YAML sintaksisi və təhlükəsizlik qaydaları.

PyYAML lazımdır (`pip install pyyaml`). İşlətmək (layihə kökündən):
    PYTHONIOENCODING=utf-8 python tests/test_workflows.py
"""
import glob
import os
import re
import subprocess
import sys
import tempfile

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(ROOT, ".github", "workflows")
results = []


def check(name, ok, info=""):
    results.append(("✓" if ok else "✗") + " " + name + (f"  [{info}]" if info else ""))


def load(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    data = yaml.safe_load(text)
    if True in data:  # PyYAML "on" açarını True kimi oxuyur
        data["on"] = data.pop(True)
    return text, data


files = sorted(glob.glob(os.path.join(WF, "*.yml"))) + [os.path.join(ROOT, ".github", "actions", "setup-scraper",
                                                                      "action.yml")]
docs = {}
for f in files:
    try:
        docs[os.path.basename(f) if "workflows" in f else "setup-scraper"] = load(f)
        check(f"YAML sintaksisi: {os.path.relpath(f, ROOT)}", True)
    except Exception as e:
        check(f"YAML sintaksisi: {os.path.relpath(f, ROOT)}", False, str(e)[:200])


def steps(data):
    if "runs" in data:
        yield from data["runs"]["steps"]
        return
    for job in data.get("jobs", {}).values():
        yield from job.get("steps", [])


for name, (text, data) in docs.items():
    uses = [s["uses"] for s in steps(data) if "uses" in s]
    pinned = all(u.startswith("./") or re.fullmatch(r"[\w.-]+/[\w./-]+@v\d+", u) for u in uses)
    check(f"{name}: action versiyaları sabitdir (@vN)", pinned, ", ".join(uses))
    check(f"{name}: 'set -x' yoxdur", "set -x" not in text and "set -o xtrace" not in text)
    leaks = [s.get("name", s.get("run", "")[:30]) for s in steps(data) if "secrets." in str(s.get("run", ""))
             or "secrets." in str(s.get("with", ""))]
    check(f"{name}: secrets yalnız addımın env-ində (run/with mətnində yox)", not leaks, f"{leaks}")
    if name == "setup-scraper":
        check("setup-scraper: secret istifadə etmir", "secrets." not in text)
        continue
    on = data["on"]
    for jname, job in data.get("jobs", {}).items():
        for s in job.get("steps", []):
            if "secrets." in str(s.get("env", {})):
                check(f"{name}/{jname}: secret yalnız DATABASE_URL", set(k for k, v in s["env"].items()
                                                                     if "secrets." in str(v)) == {"DATABASE_URL"})
    if "schedule" in on:
        first = [j for j in data["jobs"].values() if "needs" not in j]
        ok = all("vars.SCRAPER_ENABLED == 'true'" in str(j.get("if", "")) for j in first)
        check(f"{name}: cədvəl yalnız SCRAPER_ENABLED=true olanda (ilk işlərin if-i)", ok)
        check(f"{name}: workflow_dispatch də var", "workflow_dispatch" in on)
    if name in ("fast.yml", "nightly.yml", "baseline.yml"):
        c = data.get("concurrency", {})
        check(f"{name}: concurrency qrupu 'scraper', cancel-in-progress: false",
              c.get("group") == "scraper" and c.get("cancel-in-progress") is False)
        env_all = dict(data.get("env", {}))
        runs = [(jn, s) for jn, j in data["jobs"].items() for s in j.get("steps", [])
                if "run-scraper.sh" in str(s.get("run", ""))]
        check(f"{name}: hər scraper addımında MAKLERHUB_NO_LOCK=1",
              runs and all(str({**env_all, **s.get("env", {})}.get("MAKLERHUB_NO_LOCK")) == "1" for _, s in runs))
        saves = [(jn, s) for jn, j in data["jobs"].items() for s in j.get("steps", [])
                 if str(s.get("uses", "")).startswith("actions/cache/save")]
        check(f"{name}: hər scraper işi sonda cache-i yadda saxlayır (if: always())",
              {jn for jn, _ in runs} == {jn for jn, s in saves if s.get("if") == "always()"})

_, fast = docs["fast.yml"]
check("fast.yml: hər 15 dəq, 23:xx UTC boş (nightly)", fast["on"]["schedule"][0]["cron"] == "*/15 0-22 * * *")
check("fast.yml: timeout-minutes 15", fast["jobs"]["fast"]["timeout-minutes"] == 15)
_, night = docs["nightly.yml"]
check("nightly.yml: 23:00 UTC (Bakı 03:00)", night["on"]["schedule"][0]["cron"] == "0 23 * * *")
check("nightly.yml: hər işin timeout-u < 330 dəq",
      all(j.get("timeout-minutes", 360) < 330 for j in night["jobs"].values()),
      str({k: j.get("timeout-minutes") for k, j in night["jobs"].items()}))
order = [night["jobs"]["sale-1"]["needs"], night["jobs"]["sale-2"]["needs"], night["jobs"]["sale-3"]["needs"]]
check("nightly.yml: satış hissələri ardıcıl (needs) və --chunk 1/3, 2/3, 3/3",
      order == [["plan", "rent"], "sale-1", "sale-2"]
      and all(f"--chunk {i}/3" in str(night["jobs"][f"sale-{i}"]["steps"]) for i in (1, 2, 3)))
_, base = docs["baseline.yml"]
check("baseline.yml: yalnız əl ilə", list(base["on"]) == ["workflow_dispatch"])
check("baseline.yml: bloklanma uğursuz sayılır (BLOCKED_OK yoxdur)", "BLOCKED_OK" not in docs["baseline.yml"][0])

# run-scraper.sh: çıxış kodlarının çevrilməsi (saxta python ilə)
script = os.path.join(ROOT, ".github", "scripts", "run-scraper.sh")
tmp = tempfile.mkdtemp()
stub = os.path.join(tmp, "fakepy")
with open(stub, "w", newline="\n") as fh:
    fh.write('#!/usr/bin/env bash\nexit "${FAKE_CODE:-0}"\n')
os.chmod(stub, 0o755)


def run(code, blocked_ok):
    env = dict(os.environ, PYTHON=stub, FAKE_CODE=str(code), BLOCKED_OK="1" if blocked_ok else "0")
    p = subprocess.run(["bash", script.replace("\\", "/"), "fast"], env=env, capture_output=True, text=True)
    return p.returncode, p.stdout


try:
    cases = [(0, True, 0), (3, True, 0), (3, False, 3), (2, True, 2), (1, True, 1)]
    got = [(c, b, run(c, b)[0]) for c, b, _ in cases]
    check("run-scraper.sh: 0→0, bloklanma(3)→0 (BLOCKED_OK=1) / 3, baseline yox(2)→2, xəta(1)→1",
          [g[2] for g in got] == [e for *_, e in cases], f"{got}")
    check("run-scraper.sh: bloklanmada xəbərdarlıq çap olunur", "::warning" in run(3, True)[1])
except FileNotFoundError:
    check("run-scraper.sh: bash tapılmadı", False)

print("\n".join(results))
bad = sum(r.startswith("✗") for r in results)
print(f"\n{len(results) - bad}/{len(results)} keçdi")
sys.exit(1 if bad else 0)
