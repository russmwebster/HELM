"""W135 verified both ways. The load-bearing check is that moving committed()
into helm/exposure.py did NOT change tools/exposure_report.py's output."""
import subprocess, sys, os, pathlib, shutil, tempfile
ROOT = pathlib.Path.home()/"mnt/helm"
# s110's trap: helm/config.py defaults PROJECT_ROOT to ~/Projects/helm, which in
# this VM is NOT the mount. Without HELM_ROOT the module opens an empty scratch DB.
os.environ.setdefault('HELM_ROOT', str(ROOT))
P=F=0
def ck(n, ok):
    global P, F
    print(("  PASS  " if ok else "  FAIL  ") + n)
    if ok: P += 1
    else:  F += 1

env = dict(os.environ, HELM_ROOT=str(ROOT))
def run(args, cwd=ROOT):
    r = subprocess.run([sys.executable]+args, cwd=str(cwd), capture_output=True, text=True, env=env)
    return r.stdout, r.stderr, r.returncode

# --- 1. the refactor is output-neutral: run the tool from the BACKUP and from HEAD
bak = sorted(ROOT.glob("tools/exposure_report.py.bak-s117-*"))[-1]
tmp = ROOT/"_s117/_before_exposure_report.py"
shutil.copy2(bak, tmp)
for book in ("REAL","PAPER","ALL"):
    a,_,_ = run(["_s117/_before_exposure_report.py","--book",book,"--json"])
    b,_,_ = run(["tools/exposure_report.py","--book",book,"--json"])
    ck(f"exposure_report --book {book} byte-identical before/after refactor", a == b and len(a) > 50)
a,_,_ = run(["_s117/_before_exposure_report.py"]); b,_,_ = run(["tools/exposure_report.py"])
ck("exposure_report human output byte-identical", a == b and len(a) > 50)
tmp.unlink()

# --- 2. one definition, not two
src_tool = (ROOT/"tools/exposure_report.py").read_text()
ck("tools/ no longer defines committed()", "def committed(legs):" not in src_tool)
ck("tools/ imports the single definition", "from helm.exposure import committed" in src_tool)
ck("helm/exposure.py defines it once",
   (ROOT/"helm/exposure.py").read_text().count("def committed(legs):") == 1)

# --- 3. candidate_read, four cases including the two that must not look like errors
sys.path.insert(0, str(ROOT))
from helm import exposure as X
r = X.candidate_read("NVDA")
ck("held name: names the group and the share", r["group"]=="AI_SEMI" and r["share_pct"] > 0)
ck("held name: says you already hold it", any("already hold NVDA" in l for l in r["lines"]))
r2 = X.candidate_read("AMAT")
ck("watchlist name with nothing open: reads as a real answer, not an error",
   r2["group"]=="AI_SEMI" and "lines" in r2 and not r2["gates"])
r3 = X.candidate_read("ZZZZ")
ck("unknown name: answers, does not raise", r3["group"] is None and "not on the watchlist" in r3["lines"][0])
ck("nothing gates", all(X.candidate_read(t).get("gates") is False for t in ("NVDA","AMAT","ZZZZ")))

# --- 4. the CLI runs and agrees with the module
out,_,rc = run(["helm.py","exposure"])
ck("helm exposure exits 0", rc == 0)
b = X.book_exposure("REAL")
top = max(b["groups"].items(), key=lambda kv: -(-kv[1]["committed"]))
ck("CLI headline agrees with the module", ("%.1f%%" % max(v["share_pct"] for v in b["groups"].values())) in out)
out2,_,rc2 = run(["helm.py","exposure","NVDA"])
ck("helm exposure TICKER exits 0 and names the group", rc2 == 0 and "AI_SEMI" in out2)

# --- 5. control: the live DB was not written
import sqlite3
c = sqlite3.connect(f"file:{ROOT}/data/helm.db?mode=ro", uri=True)
ck("live DB untouched (positions still 463)", c.execute("select count(*) from positions").fetchone()[0] == 463)
c.close()
print(f"\nPASS {P} · FAIL {F}")
sys.exit(1 if F else 0)
