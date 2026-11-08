"""s154: the new board on a copy of the LIVE database, which holds the first (version 1) `helm recovery` run:
every put card must render and say the run was made by an earlier version, with the rerun command.
Usage (after verify_recovery.py has built /tmp/s154c/eng-new and pg-new): python3 -B _s154/verify_v1_records.py"""
import html as H, os, re, sqlite3, subprocess, sys, time, urllib.request
HOME = os.path.expanduser("~"); W = "/tmp/s154v1"; ENG, PG = "/tmp/s154c/eng-new", "/tmp/s154c/pg-new"
os.makedirs(W + "/home", exist_ok=True); DB = W + "/copy.db"
if os.path.exists(DB): os.remove(DB)
sqlite3.connect("file:%s/mnt/helm/data/helm.db?mode=ro" % HOME, uri=True).execute("VACUUM INTO ?", (DB,))
c = sqlite3.connect(DB)
v1 = c.execute("select count(*) from recovery_results r where r.result not like '%p_assign%'").fetchone()[0]
ids = [r[0] for r in c.execute("select p.id from positions p where book='REAL' and status='OPEN' and strategy='CSP'")]
acct = c.execute("select id from accounts").fetchone()[0]; c.close()
open(W + "/home/.helm_profile", "w").write(acct)
env = dict(os.environ, HOME=W + "/home", HELM_ROOT=ENG, HELM_DB=DB, PORT="8977", HELM_PG_ENGINE="real", PYTHONPATH="/tmp/pylib", HELM_ADVICE_DIR=W, TMPDIR="/tmp")
p = subprocess.Popen([sys.executable, "-B", "app.py"], cwd=PG, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
res = {}
try:
    for _ in range(120):
        try: urllib.request.urlopen("http://127.0.0.1:8977/health", timeout=2); break
        except urllib.error.HTTPError: break
        except Exception: time.sleep(0.5)
    for i in ids:
        try: r = urllib.request.urlopen("http://127.0.0.1:8977/thesis/" + i, timeout=120); res[i] = (r.status, r.read().decode())
        except Exception as e: res[i] = (getattr(e, "code", "ERR"), str(e))
finally:
    p.terminate(); log = p.communicate(timeout=10)[0].decode()
txt = lambda b: re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", b)))
ok1 = v1 >= len(ids)
ok2 = all(res[i][0] == 200 for i in ids) and "Traceback" not in log
ok3 = all("made by an earlier version of the model -- rerun" in txt(res[i][1]) and "helm recovery %s" % i.split("-")[0] in txt(res[i][1]) for i in ids)
print(("  ok   " if ok1 else "  FAIL ") + "the live copy holds %d version-1 results" % v1)
print(("  ok   " if ok2 else "  FAIL ") + "every put card renders (%s)" % [res[i][0] for i in ids])
print(("  ok   " if ok3 else "  FAIL ") + "each says the run was made by an earlier version, with its rerun command")
print("3 checks,", "ALL PASS" if ok1 and ok2 and ok3 else "FAIL"); sys.exit(0 if ok1 and ok2 and ok3 else 1)
