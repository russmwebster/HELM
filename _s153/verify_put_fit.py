"""s153: "Best that fit your limits" under the put list's top 5 -- `helm puts` and Today.

Display only. Starts the board as production starts it (python3 app.py, real engine) on fresh DB
copies, old code (.bak-20261106-s153) and new side by side from /tmp copies; runs the CLI the same way.
Fixtures are written ONLY to the copies. The expected lists come from SQL on put_candidates, not from
the code under test.
Usage: TMPDIR=/tmp PYTHONPATH=/tmp/pylib python3 -B _s153/verify_put_fit.py [NEW_ENGINE_DIR NEW_PG_DIR]
"""
import hashlib, html as H, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"; PGROOT = HOME + "/mnt/helm-pg"
W = "/tmp/s153v"; BAK = ".bak-20261106-s153"
ENG_FILES = ["helm/puttool.py", "helm/cli/puts_cmd.py"]; PG_FILES = ["engine_store.py", "templates/today.html"]
ENG_IGN = ("data", "archive", "_s1*", "_to_delete", "*.bak-*", "__pycache__", ".git", "docs", "_records")
PG_IGN = ("*.bak-*", "__pycache__", ".git", "_to_delete")

def tree(src, dst, files, use_bak, ign):
    if os.path.exists(dst): shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*ign))
    if use_bak:
        for f in files: shutil.copy(src + "/" + f + BAK, dst + "/" + f)
    return dst
os.makedirs(W + "/home", exist_ok=True)
NEW_ENG = os.path.abspath(sys.argv[1]) if len(sys.argv) > 2 else tree(ROOT, W + "/eng-new", ENG_FILES, False, ENG_IGN)
NEW_PG = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else tree(PGROOT, W + "/pg-new", PG_FILES, False, PG_IGN)
OLD_ENG = tree(ROOT, W + "/eng-old", ENG_FILES, True, ENG_IGN)
OLD_PG = tree(PGROOT, W + "/pg-old", PG_FILES, True, PG_IGN)
print("new engine:", NEW_ENG, "| new board:", NEW_PG)
for d, fs in ((NEW_ENG, ENG_FILES), (NEW_PG, PG_FILES)):
    for f in fs: print("   ", f, hashlib.sha256(open(d + "/" + f, "rb").read()).hexdigest()[:12])

fails, n = [], [0]
def ok(c, m):
    n[0] += 1; print(("  ok   " if c else "  FAIL ") + m)
    if not c: fails.append(m)
def copy(name):
    p = W + "/" + name + ".db"
    if os.path.exists(p): os.remove(p)
    sqlite3.connect("file:%s/data/helm.db?mode=ro" % ROOT, uri=True).execute("VACUUM INTO ?", (p,))
    return p
def dump(p):
    c = sqlite3.connect(p); out = {}
    for (t,) in c.execute("select name from sqlite_master where type='table' order by name"):
        h = hashlib.sha256(); h.update(repr([r[1] for r in c.execute('pragma table_info("%s")' % t)]).encode())
        for r in c.execute('select * from "%s" order by rowid' % t): h.update(repr(r).encode())
        out[t] = h.hexdigest()
    return out
PORT = [8790]
def render(db, eng, pg):
    PORT[0] += 1; port = str(PORT[0])
    open(W + "/home/.helm_profile", "w").write(sqlite3.connect(db).execute("select id from accounts").fetchone()[0])
    env = dict(os.environ, HOME=W + "/home", HELM_ROOT=eng, HELM_DB=db, PORT=port, HELM_PG_ENGINE="real",
               COLUMNS="200", PYTHONPATH="/tmp/pylib", HELM_ADVICE_DIR=W + "/advice-out")
    os.makedirs(W + "/advice-out", exist_ok=True)
    p = subprocess.Popen([sys.executable, "-B", "app.py"], cwd=pg, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    body, st = "", None
    try:
        for _ in range(120):
            try: urllib.request.urlopen("http://127.0.0.1:%s/health" % port, timeout=2); break
            except urllib.error.HTTPError: break
            except Exception: time.sleep(0.5)
        r = urllib.request.urlopen("http://127.0.0.1:%s/today" % port, timeout=120)
        st, body = r.status, r.read().decode()
    except Exception as e:
        st, body = getattr(e, "code", "ERR"), str(e)
    finally:
        p.terminate(); log = p.communicate(timeout=10)[0].decode()
    return st, body, log
def cli(db, eng, *args):
    env = dict(os.environ, HOME=W + "/home", HELM_ROOT=eng, HELM_DB=db, COLUMNS="250", PYTHONPATH="/tmp/pylib")
    return subprocess.run([sys.executable, "-B", "helm.py", "puts", "--last"] + list(args), cwd=eng, env=env,
                          capture_output=True, text=True, timeout=120).stdout
FIT_RE = r'<div class="panel dim" style="margin:10px 0 8px">.*?(?:</table></div>|<div class="panel">No name below the top 5 fits the limits\.</div>)'
def norm(b):
    b = re.sub(r"\?v=\d+", "?v=N", b)
    return re.sub(r"\s+", " ", b)
def fit_block(b):
    m = re.search(FIT_RE, b, flags=re.S); return m.group(0) if m else ""
def fit_tickers(block):
    return re.findall(r'<span class="dim">#(\d+)</span> (\w+) ', block)
def text(h):
    return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", h))).strip()
def expected(db):
    c = sqlite3.connect(db)
    rid = c.execute("select id from put_runs order by run_at desc, rowid desc limit 1").fetchone()[0]
    rows = c.execute("select rank, ticker, size_n, size_binding from put_candidates where run_id=? order by rank", (rid,)).fetchall()
    top = rows[:5]
    fit = [] if all(r[2] for r in top) else [(str(r[0]), r[1]) for r in rows[5:] if r[2]][:5]
    nd = sum(1 for r in rows if not r[2])
    return rid, top, fit, nd

# ---- 1. live copy, old vs new
A = copy("live"); before = dump(A)
rid, top, fit, nd = expected(A)
print("latest run", rid, "| expected fit", fit, "| declined", nd)
st_o, old, log_o = render(A, OLD_ENG, OLD_PG); st_n, new, log_n = render(A, NEW_ENG, NEW_PG)
ok(st_o == 200 and st_n == 200, "Today renders, old and new (%s, %s)" % (st_o, st_n))
ok("Traceback" not in log_o + log_n, "no tracebacks in the board's log")
blk = fit_block(new)
ok(blk != "" and fit_block(old) == "", "the new block is on Today with the new code only")
ok(norm(old) == norm(new.replace(blk, "")), "Today minus the new block is identical, old vs new")
ok(fit_tickers(blk) == fit, "the board lists exactly the expected fitting names, in rank order: %s" % fit_tickers(blk))
m = re.search(r"(\d+) declined by the limits", text(blk))
ok(m and int(m.group(1)) == nd, "the declined count matches SQL (%s vs %d)" % (m and m.group(1), nd))
co, cn = cli(A, OLD_ENG), cli(A, NEW_ENG)
ok(cn.startswith(co.rstrip("\n").rsplit("\nLeft off", 1)[0]), "CLI: everything before the new section is unchanged")
cfit = re.findall(r"^\s+(\d+)\s+(\w+)\s+", cn.split("Best that fit your limits", 1)[-1].split("declined by the limits")[0], flags=re.M)
ok("Best that fit your limits" in cn and cfit == fit, "CLI lists the same fitting names: %s" % cfit)
ok(cli(A, OLD_ENG, "--all") == cli(A, NEW_ENG, "--all"), "CLI --all is unchanged")
ok(dump(A) == before, "rendering and the CLI changed no table in the copy")

# ---- 2. the whole top 5 fits -> nothing new shown
B = copy("allfit"); c = sqlite3.connect(B)
c.execute("update put_candidates set size_n=1 where run_id=? and rank<=5", (rid,)); c.commit()
st_o, old, _ = render(B, OLD_ENG, OLD_PG); st_n, new, _ = render(B, NEW_ENG, NEW_PG)
ok(st_n == 200 and fit_block(new) == "" and norm(old) == norm(new), "top 5 all fit: Today identical to the old code, no new block")
ok("Best that fit" not in cli(B, NEW_ENG), "top 5 all fit: the CLI shows no new section")

# ---- 3. nothing below the top 5 fits
Cdb = copy("nonefit"); c = sqlite3.connect(Cdb)
c.execute("update put_candidates set size_n=0, size_binding='sleeve_total' where run_id=? and rank>5", (rid,)); c.commit()
st_n, new, _ = render(Cdb, NEW_ENG, NEW_PG)
ok(st_n == 200 and "No name below the top 5 fits the limits." in new and fit_tickers(fit_block(new)) == [], "nothing fits: the board says so")
ok("no name below the top 5 fits the limits" in cli(Cdb, NEW_ENG), "nothing fits: the CLI says so")

# ---- 4. a fitting name INSIDE the top 5 is not repeated below
D = copy("onefit"); c = sqlite3.connect(D)
c.execute("update put_candidates set size_n=2 where run_id=? and rank=3", (rid,)); c.commit()
_, _, f4, _ = expected(D); st_n, new, _ = render(D, NEW_ENG, NEW_PG)
got = fit_tickers(fit_block(new))
ok(got == f4 and all(int(r) > 5 for r, _ in got), "a fitting top-5 name is not repeated in the new block")

print("\n%d checks, %d failed" % (n[0], len(fails)))
sys.exit(1 if fails else 0)
