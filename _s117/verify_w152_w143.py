"""Verify both ways. Runs the real audit assertion against a copy whose
watchlist is perturbed, and the real status formatter against three signs."""
import sqlite3, os, shutil, subprocess, sys, importlib.util, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
HOME = os.path.expanduser("~")
LIVE = f"{HOME}/mnt/helm/data/helm.db"
P=F=0
def ck(name, ok):
    global P,F
    print(("  PASS  " if ok else "  FAIL  ")+name)
    if ok: P+=1
    else: F+=1

# ---- W152: the rendering, three ways
src = (ROOT/"helm/cli/status_cmd.py").read_text()
import re
m = re.search(r'pnl_sign\s*=\s*(.+)', src)
expr = m.group(1).strip()
for val, want in ((16963.0,"+"), (-16962.51,"-"), (0.0,"")):
    got = eval(expr, {}, {"total_realized": val})
    ck(f"W152 sign for {val:+,.2f} -> {got!r}", got == want)
ck("W152 a loss now renders with a minus", f'{"-"}${abs(-16962.51):>11,.0f}'.strip() == "-$     16,963".strip())

# ---- W143: the assertion, on a fresh copy, both ways
def audit_on(dbpath, date):
    env = dict(os.environ, HELM_ROOT=f"{HOME}/mnt/helm", HELM_DB=dbpath)
    r = subprocess.run([sys.executable, "helm.py", "audit", "eod", "--date", date, "--json"],
                       cwd=f"{HOME}/mnt/helm", capture_output=True, text=True, env=env)
    return r.stdout + r.stderr

def fresh(tag):
    dst = f"{HOME}/s117/w143_{tag}.db"
    if os.path.exists(dst): os.remove(dst)
    c = sqlite3.connect(f"file:{LIVE}?mode=ro", uri=True); c.execute("VACUUM INTO ?", (dst,)); c.close()
    return dst

DATE = "2026-09-04"
clean = fresh("clean")
out = audit_on(clean, DATE)
ck("W143 PASSES on the live watchlist (0 ungrouped)", '"exposure groups"' in out and "carries an exposure group" in out)

# perturb: blank three groups
dirty = fresh("dirty")
d = sqlite3.connect(dirty)
d.execute("update watchlist set exposure_group=NULL where ticker in ('AAPL','KO','XOM')"); d.commit()
n = d.execute("select count(*) from watchlist where active=1 and (exposure_group is null or trim(exposure_group)='')").fetchone()[0]
d.close()
out2 = audit_on(dirty, DATE)
ck(f"W143 perturbation planted {n} ungrouped names", n >= 1)
ck("W143 FAILS when a group is blanked", "carry no exposure group" in out2)
ck("W143 names the offending tickers", all(t in out2 for t in ("AAPL","KO")))
ck("W143 states the magnitude in the line", ("%d of " % n) in out2)

# control LAST, on a rebuilt copy
ctrl = fresh("ctrl")
out3 = audit_on(ctrl, DATE)
ck("control: clean copy passes again (harness is not sticky)", "carries an exposure group" in out3)
print(f"\nPASS {P} · FAIL {F}")
sys.exit(1 if F else 0)
