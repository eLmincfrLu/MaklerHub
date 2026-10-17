"""GitHub Actions fayllarının yoxlanması (şəbəkəsiz, bazasız): YAML sintaksisi, təhlükəsizlik qaydaları,
cədvəlin yalnız loop.yml-də olması, zəncir (növbəti işin işə salınması) və söndürmə açarı.

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
    check(f"{name}: run mətnində ${{{{ inputs.* }}}} yoxdur (env ilə ötürülür)",
          not any("inputs." in str(s_.get("run", "")) for s_ in steps(data)))
    dispatches = [s_ for s_ in steps(data) if "gh workflow run loop.yml" in str(s_.get("run", ""))]
    if dispatches:
        check(f"{name}: loop-u işə salan addım yalnız SCRAPER_ENABLED=true olanda, permissions actions: write",
              all("vars.SCRAPER_ENABLED == 'true'" in str(s_.get("if", "")) for s_ in dispatches)
              and data.get("permissions", {}).get("actions") == "write")
    if name in ("fast.yml", "nightly.yml", "baseline.yml", "loop.yml"):
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

scheduled = sorted(n for n, (_, d) in docs.items() if n != "setup-scraper" and "schedule" in d["on"])
check("cədvəl YALNIZ loop.yml-də", scheduled == ["loop.yml"], f"{scheduled}")
for n in ("fast.yml", "nightly.yml"):
    check(f"{n}: yalnız əl ilə (workflow_dispatch)", list(docs[n][1]["on"]) == ["workflow_dispatch"])
_, lp = docs["loop.yml"]
job = lp["jobs"]["loop"]
check("loop.yml: ehtiyat cron hər 2 saatdan bir", lp["on"]["schedule"][0]["cron"] == "7 */2 * * *")
check("loop.yml: timeout-minutes 350", job["timeout-minutes"] == 350)
check("loop.yml: permissions actions: write", lp["permissions"].get("actions") == "write")
inputs = lp["on"]["workflow_dispatch"]["inputs"]
check("loop.yml: minutes parametri (default 340) və chain", inputs["minutes"]["default"] == 340 and "chain" in inputs)
run_step = next(s_ for s_ in job["steps"] if "run-scraper.sh loop" in str(s_.get("run", "")))
check("loop.yml: loop --minutes (inputs.minutes || 340), SCRAPER_ENABLED və GH_TOKEN env-də",
      '--minutes "$LOOP_MINUTES"' in run_step["run"] and run_step["env"]["LOOP_MINUTES"] == "${{ inputs.minutes || 340 }}"
      and run_step["env"]["SCRAPER_ENABLED"] == "${{ vars.SCRAPER_ENABLED }}"
      and run_step["env"]["GH_TOKEN"] == "${{ github.token }}" and run_step.get("id") == "loop")
check("loop.yml: söndürmə açarı — cədvəl və zəncirin işi yalnız SCRAPER_ENABLED=true, əl ilə (chain=false) həmişə",
      job["if"] == "vars.SCRAPER_ENABLED == 'true' || (github.event_name == 'workflow_dispatch' && !inputs.chain)")
fallback = [s_ for s_ in job["steps"] if "gh workflow run loop.yml" in str(s_.get("run", ""))]
check("loop.yml: ehtiyat dispatch yalnız uğurda və loop özü işə sala bilmədisə",
      len(fallback) == 1 and "success()" in fallback[0]["if"] and "steps.loop.outputs.dispatched != 'true'" in fallback[0]["if"]
      and "chain=true" in fallback[0]["run"])
_, night = docs["nightly.yml"]
check("nightly.yml: bir iş, --deadline-minutes 330 < timeout 350",
      list(night["jobs"]) == ["nightly"] and "--deadline-minutes 330" in str(night["jobs"]["nightly"]["steps"])
      and night["jobs"]["nightly"]["timeout-minutes"] == 350)
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
