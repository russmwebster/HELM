"""s157c PART 2 (runs): the board's three chain buttons as production starts the board, on a fresh DB copy.
IBKR is out of reach from the VM, so the real CLI records runs that read nothing -- exactly what a run with the gateway
down records. The test engine copy holds `helm pmcc` for 8 s at its start (stand-in for a long search) so the
"running" states can be seen. A second engine copy has the snapshot window forced on.
Usage: TMPDIR=/tmp PYTHONPATH=/tmp/pylib python3 -B _s157b/verify_buttons_runs.py [ENG_DIR PG_DIR]"""
import hashlib, json, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"; PGROOT = HOME + "/mnt/helm-pg"
W = "/tmp/s157r"; os.makedirs(W + "/home", exist_ok=True)
IGN_E = ("data", "archive", "_s1*", "_to_delete", "*.bak-*", "__pycache__", ".git", "docs", "_records")
IGN_P = ("*.bak-*", "__pycache__", ".git", "_to_delete")
def tree(src, dst, ign):
    if os.path.exists(dst): shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*ign))
    c = open(dst + "/helm/config.py").read() if os.path.exists(dst + "/helm/config.py") else None
    if c is not None:
        open(dst + "/helm/config.py", "w").write(re.sub(r"^HELM_PYTHON = .*$", "HELM_PYTHON = %r" % sys.executable, c, flags=re.M))
    return dst
SRC_E, SRC_P = (sys.argv[1], sys.argv[2]) if len(sys.argv) > 2 else (ROOT, PGROOT)
ENG = tree(SRC_E, W + "/eng", IGN_E); PG = tree(SRC_P, W + "/pg", IGN_P)
pc = ENG + "/helm/cli/pmcc_cmd.py"; s = open(pc).read()
s = s.replace("def run():\n", "def run():\n    import time as _t; _t.sleep(8)   # TEST COPY ONLY (s157c harness)\n", 1); open(pc, "w").write(s)
ENGW = tree(ENG, W + "/eng-win", ())
pu = ENGW + "/helm/cli/puts_cmd.py"; s = open(pu).read()
s = s.replace("def _in_window(now=None):\n", "def _in_window(now=None):\n    return '15:15'   # TEST COPY ONLY\n", 1); open(pu, "w").write(s)
print("engine:", SRC_E, "| board:", SRC_P)
for f in ("helm_engine.py", "app.py", "engine_store.py", "templates/today.html"):
    print("   ", f, hashlib.sha256(open(PG + "/" + f, "rb").read()).hexdigest()[:12])
fails, n = [], [0]
def ok(c, m):
    n[0] += 1; print(("  ok   " if c else "  FAIL ") + m)
    if not c: fails.append(m)
def fresh():
    db = W + "/copy.db"
    for x in (db, db + "-wal", db + "-shm"):
        if os.path.exists(x): os.remove(x)
    sqlite3.connect("file:%s/data/helm.db?mode=ro" % ROOT, uri=True).execute("VACUUM INTO ?", (db,)); return db
def dump(p):
    c = sqlite3.connect(p); out = {}
    for (t,) in c.execute("select name from sqlite_master where type='table' order by name"):
        out[t] = hashlib.sha256(repr(list(c.execute('select * from "%s" order by rowid' % t))).encode()).hexdigest()
    return out
def count(db, t):
    return sqlite3.connect(db).execute("select count(*) from %s" % t).fetchone()[0]
PORT = [9300]
class Board:
    def __init__(self, eng, db):
        PORT[0] += 1; self.port = str(PORT[0])
        cx = sqlite3.connect(db)
        open(W + "/home/.helm_profile", "w").write(cx.execute("select id from accounts").fetchone()[0])
        env = dict(os.environ, HOME=W + "/home", HELM_ROOT=eng, HELM_DB=db, PORT=self.port, HELM_PG_ENGINE="real",
                   PYTHONPATH="/tmp/pylib", HELM_ADVICE_DIR=W + "/advice-out", COLUMNS="200")
        os.makedirs(W + "/advice-out", exist_ok=True)
        self.p = subprocess.Popen([sys.executable, "-B", "app.py"], cwd=PG, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for _ in range(120):
            try: urllib.request.urlopen(self.u("/health"), timeout=2); break
            except urllib.error.HTTPError: break
            except Exception: time.sleep(0.5)
    def u(self, path): return "http://127.0.0.1:%s%s" % (self.port, path)
    def get(self, path):
        return urllib.request.urlopen(self.u(path), timeout=120).read().decode()
    def post(self, path):
        r = urllib.request.Request(self.u(path), data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        return json.loads(urllib.request.urlopen(r, timeout=170).read().decode())
    def close(self):
        self.p.terminate(); return self.p.communicate(timeout=10)[0].decode()

ALLOWED = {"call_runs", "call_candidates", "pmcc_runs", "pmcc_candidates"}
# ---- 1. the buttons, outside the windows
db = fresh(); t0 = dump(db); c0, p0 = count(db, "call_runs"), count(db, "pmcc_runs")
b = Board(ENG, db)
try:
    r = b.post("/api/calls/run")
    ok("GOOGL" in (r.get("output") or "") and "refused" not in r, "Price covered calls runs helm calls (output names GOOGL)")
    ok(count(db, "call_runs") == c0 + 1, "the calls run is recorded (+1 run)")
    r = b.post("/api/pmcc/start")
    ok(r.get("ok") and r.get("started"), "Run the PMCC search starts (%s)" % r.get("started"))
    r2 = b.post("/api/pmcc/start")
    ok(r2.get("refused") and "already running" in r2.get("output", ""), "a second start is refused: %r" % r2.get("output"))
    r3 = b.post("/api/puts/run")
    ok(r3.get("refused") and "PMCC search" in r3.get("output", ""), "the put list waits for it: %r" % r3.get("output"))
    r4 = b.post("/api/calls/run")
    ok(r4.get("refused") and "PMCC search" in r4.get("output", ""), "the calls check waits for it")
    st = json.loads(b.get("/api/pmcc/status"))
    ok(st.get("running") is True, "status: running")
    ok("Running since %s" % r["started"][11:16] in b.get("/today"), "Today shows Running since %s" % r["started"][11:16])
    for _ in range(150):
        st = json.loads(b.get("/api/pmcc/status"))
        if not st.get("running"): break
        time.sleep(1)
    ok(st.get("running") is False and st.get("finished") and "New PMCCs" in (st.get("output") or ""),
       "the search finishes on its own, no page open (finished %s)" % st.get("finished"))
    ok(count(db, "pmcc_runs") == p0 + 1, "the search is recorded (+1 run)")
    r5 = b.post("/api/calls/run")
    ok("refused" not in r5 and count(db, "call_runs") == c0 + 2, "after it, the calls button runs again (lock released)")
    t = b.get("/today")
    last = sqlite3.connect(db).execute("select run_at from pmcc_runs order by run_at desc limit 1").fetchone()[0]
    ok(("search %s" % last[5:16]) in t and "Run the PMCC search" in t, "Today shows the new search, button idle")
finally:
    log = b.close()
ok("Traceback" not in log, "no tracebacks in the board's log")
ch = {k for k in set(t0) | set(dump(db)) if t0.get(k) != dump(db).get(k)}
ok(ch <= ALLOWED and ch, "only the calls and PMCC run tables changed: %s" % sorted(ch))
# ---- 2. inside a snapshot window: all three refused, nothing written
db = fresh(); t0 = dump(db)
b = Board(ENGW, db)
try:
    rs = [b.post(p) for p in ("/api/puts/run", "/api/calls/run", "/api/pmcc/start")]
    ok(all(x.get("refused") and "snapshot window" in x.get("output", "") for x in rs), "inside a window all three are refused")
    st = json.loads(b.get("/api/pmcc/status"))
    ok(not st.get("running"), "no search started")
    r = b.post("/api/calls/run")
    ok(r.get("refused") and "snapshot window" in r.get("output", ""), "a refusal leaves no lock behind (still the window's reason)")
finally:
    log = b.close()
ok(dump(db) == t0, "inside a window: no table changed")
print("\n%d checks, %d failed" % (n[0], len(fails)) if fails else "\n%d checks, all passed" % n[0])
sys.exit(1 if fails else 0)
