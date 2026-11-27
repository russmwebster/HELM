"""s165b: the board's put-list button waits 30 minutes (helm-pg/helm_engine.py PUTS_TIMEOUT). Old and new board trees;
the engine is the live tree's copy (/tmp/s165/new, which equals the installed engine). IBKR never called: subprocess.run
is replaced, so the button's own call is captured. The board started as production starts it for the page compare.
Usage: TMPDIR=/tmp python3 -B verify_s165b.py OLD_PG NEW_PG ENG"""
import hashlib, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
W = os.environ.get("S165BW", "/tmp/s165bw"); BASE = "/tmp/s165/base.db"
OLD_PG, NEW_PG, ENG = [os.path.abspath(x) for x in sys.argv[1:4]]
print("code loaded from:", NEW_PG, hashlib.sha256(open(NEW_PG + "/helm_engine.py", "rb").read()).hexdigest()[:12],
      "(old %s)" % hashlib.sha256(open(OLD_PG + "/helm_engine.py", "rb").read()).hexdigest()[:12])
os.makedirs(W + "/home", exist_ok=True)
fails, n = [], [0]
def ok(c, m):
    n[0] += 1; print(("  ok   " if c else "  FAIL ") + m)
    if not c: fails.append(m)
PROBE = r'''
import json, subprocess, sys
sys.path.insert(0, "PG")
import helm_engine as E
seen = []
class R: returncode = 0; stdout = "ok"; stderr = ""
def fake(cmd, **k):
    seen.append({"args": cmd[2:], "timeout": k.get("timeout")}); return R()
subprocess.run = fake
import helm.cli.puts_cmd as pc
pc._in_window = lambda now=None: None
out = {}
out["puts"] = E.run_puts(); out["puts_call"] = seen[-1]
out["lock_free_after"] = E._PUTS_LOCK.acquire(blocking=False)
if out["lock_free_after"]: E._PUTS_LOCK.release()
out["calls"] = E.run_calls(); out["calls_call"] = seen[-1]
pc._in_window = lambda now=None: "10:00-10:35"
out["refused"] = E.run_puts(); out["n_calls"] = len(seen)
print(json.dumps(out, default=str))
'''
import json
def probe(pg):
    env = dict(os.environ, HELM_ROOT=ENG, HELM_DB=BASE, PYTHONPATH=ENG + ":/tmp/pylib", HOME=W + "/home", TMPDIR="/tmp")
    r = subprocess.run([sys.executable, "-B", "-c", PROBE.replace("PG", pg)], cwd=pg, env=env, capture_output=True, text=True, timeout=120)
    if r.returncode: print(r.stdout[-800:], r.stderr[-1500:])
    return json.loads(r.stdout.strip().split("\n")[-1])
o, nw = probe(OLD_PG), probe(NEW_PG)
ok(o["puts_call"]["timeout"] == 900, "the old button waited 900 s (%s)" % o["puts_call"]["timeout"])
ok(nw["puts_call"]["timeout"] == 1800, "the new button waits 1,800 s -- 30 minutes (%s)" % nw["puts_call"]["timeout"])
ok(nw["puts_call"]["args"][-2:] == ["puts", "--why"] and o["puts_call"]["args"] == nw["puts_call"]["args"],
   "it runs the same command as before: %s" % " ".join(nw["puts_call"]["args"][-2:]))
ok(nw["lock_free_after"], "the one-chain-run-at-a-time lock is released after the run")
ok(nw["calls_call"]["timeout"] == o["calls_call"]["timeout"] == 900, "the covered-call button is unchanged (900 s)")
ok(nw["refused"].get("refused") and nw["n_calls"] == 2 and "snapshot window" in nw["refused"]["output"],
   "inside a snapshot window the button still refuses, without running anything")

def render(pg, db, paths, port):
    cx = sqlite3.connect(db); open(W + "/home/.helm_profile", "w").write(cx.execute("select id from accounts").fetchone()[0]); cx.close()
    e = dict(os.environ, HOME=W + "/home", HELM_ROOT=ENG, HELM_DB=db, PORT=str(port), HELM_PG_ENGINE="real",
             PYTHONPATH="/tmp/pylib", HELM_ADVICE_DIR=W + "/adv", PYTHONHASHSEED="0", TMPDIR="/tmp")
    os.makedirs(W + "/adv", exist_ok=True)
    p = subprocess.Popen([sys.executable, "-B", "app.py"], cwd=pg, env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = {}
    try:
        for _ in range(120):
            try: urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=2); break
            except urllib.error.HTTPError: break
            except Exception: time.sleep(0.5)
        for path in paths:
            try:
                r = urllib.request.urlopen("http://127.0.0.1:%d%s" % (port, path), timeout=120); out[path] = (r.status, r.read().decode())
            except Exception as ex: out[path] = (getattr(ex, "code", "ERR"), str(ex))
    finally:
        p.terminate(); p.communicate(timeout=10)
    return out
def norm(b, pg):
    b = b.replace(pg, "PG"); b = re.sub(r"\?v=\d+", "?v=X", b); b = re.sub(r'(clip|cp|g)[-_]?[0-9a-f]{6,}', 'ID', b)
    return re.sub(r'(["#])([a-z]{1,3})\d{3,}(["\)])', r'\1\2ID\3', b)
pages = ["/", "/today", "/book", "/reference", "/positions", "/watchlist"]
d1, d2 = W + "/r1.db", W + "/r2.db"; shutil.copy(BASE, d1); shutil.copy(BASE, d2)
p1, p2 = render(OLD_PG, d1, pages, 9701), render(NEW_PG, d2, pages, 9702)
for pth in pages:
    ok(p1[pth][0] == 200 and p2[pth][0] == 200 and norm(p1[pth][1], OLD_PG) == norm(p2[pth][1], NEW_PG),
       "board %s identical before and after (%s)" % (pth, p2[pth][0]))
print("verify_s165b: %d checks, %d failed" % (n[0], len(fails)))
shutil.rmtree(W, ignore_errors=True)
sys.exit(1 if fails else 0)
