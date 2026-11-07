"""s153b: Today says when the last end-of-day check found a problem; Health off the menu.

Display only. Board started as production starts it (python3 app.py, real engine) on a fresh DB copy;
old code (.bak-20261107-s153) and new side by side from /tmp copies. Fixture: an engine copy whose
logs/ keep the 16:15 reports only up to 11-04 (the gateway outage day), so the "last check" is a
known-bad day from real records. Nothing is written outside /tmp.
Usage: TMPDIR=/tmp PYTHONPATH=/tmp/pylib python3 -B _s153/verify_health_line.py [NEW_ENGINE_DIR NEW_PG_DIR]
"""
import glob, hashlib, html as H, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"; PGROOT = HOME + "/mnt/helm-pg"
W = "/tmp/s153h"; BAK = ".bak-20261107-s153"
ENG_FILES = ["helm/advice.py"]; PG_FILES = ["engine_store.py", "templates/today.html", "app.py"]
ENG_IGN = ("data", "archive", "_s1*", "_to_delete", "*.bak-*", "__pycache__", ".git", "docs", "_records")
PG_IGN = ("*.bak-*", "__pycache__", ".git", "_to_delete")
def tree(src, dst, files, use_bak, ign):
    if os.path.exists(dst): shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*ign))
    if use_bak:
        for f in files: shutil.copy(src + "/" + f + BAK, dst + "/" + f)
    cfg = dst + "/helm/config.py"              # the Mac's interpreter path doesn't exist in the VM: copies only
    if os.path.exists(cfg):
        c = open(cfg).read()
        open(cfg, "w").write(re.sub(r"^HELM_PYTHON = .*$", "HELM_PYTHON = %r" % sys.executable, c, flags=re.M))
    return dst
os.makedirs(W + "/home", exist_ok=True)
NEW_ENG = os.path.abspath(sys.argv[1]) if len(sys.argv) > 2 else tree(ROOT, W + "/eng-new", ENG_FILES, False, ENG_IGN)
NEW_PG = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else tree(PGROOT, W + "/pg-new", PG_FILES, False, PG_IGN)
OLD_ENG = tree(ROOT, W + "/eng-old", ENG_FILES, True, ENG_IGN)
OLD_PG = tree(PGROOT, W + "/pg-old", PG_FILES, True, PG_IGN)
print("new engine:", NEW_ENG, "| new board:", NEW_PG)
for d, fs in ((NEW_ENG, ENG_FILES), (NEW_PG, PG_FILES)):
    for f in fs: print("   ", f, hashlib.sha256(open(d + "/" + f, "rb").read()).hexdigest()[:12])
def bad_day(eng, keep_upto="2026-11-04"):
    """an engine copy whose newest 16:15 report is keep_upto"""
    dst = eng + "-bad"
    if os.path.exists(dst): shutil.rmtree(dst)
    shutil.copytree(eng, dst)
    for p in glob.glob(dst + "/logs/audit_eod_*.txt"):
        if p[-14:-4] > keep_upto: os.remove(p)
    return dst
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
PORT = [8810]
def render(db, eng, pg, paths=("/today",)):
    PORT[0] += 1; port = str(PORT[0])
    open(W + "/home/.helm_profile", "w").write(sqlite3.connect(db).execute("select id from accounts").fetchone()[0])
    env = dict(os.environ, HOME=W + "/home", HELM_ROOT=eng, HELM_DB=db, PORT=port, HELM_PG_ENGINE="real",
               COLUMNS="200", PYTHONPATH="/tmp/pylib", HELM_ADVICE_DIR=W + "/advice-out")
    os.makedirs(W + "/advice-out", exist_ok=True)
    p = subprocess.Popen([sys.executable, "-B", "app.py"], cwd=pg, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = {}
    try:
        for _ in range(120):
            try: urllib.request.urlopen("http://127.0.0.1:%s/health" % port, timeout=2); break
            except urllib.error.HTTPError: break
            except Exception: time.sleep(0.5)
        for path in paths:
            try:
                r = urllib.request.urlopen("http://127.0.0.1:%s%s" % (port, path), timeout=120)
                out[path] = (r.status, r.read().decode())
            except Exception as e:
                out[path] = (getattr(e, "code", "ERR"), str(e))
    finally:
        p.terminate(); log = p.communicate(timeout=10)[0].decode()
    return out, log
NAV_HEALTH = re.compile(r'<a href="/agents" class="[^"]*">Health</a>')
HL = re.compile(r'<div class="tdy-health">.*?</div>', re.S)
def norm(b): return re.sub(r"\s+", " ", re.sub(r"\?v=\d+", "?v=N", b))
def text(h): return re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", h))).strip()

A = copy("live"); before = dump(A)
o, log_o = render(A, OLD_ENG, OLD_PG, ("/today", "/agents"))
nw, log_n = render(A, NEW_ENG, NEW_PG, ("/today", "/agents"))
ok(o["/today"][0] == 200 and nw["/today"][0] == 200, "Today renders, old and new")
ok(nw["/agents"][0] == 200 and "Nothing for you to do" in nw["/agents"][1],
   "the Health page is still served and reads the check (%s)" % nw["/agents"][0])
ok("Traceback" not in log_o + log_n, "no tracebacks in the board's log")
ok(NAV_HEALTH.search(o["/today"][1]) and not NAV_HEALTH.search(nw["/today"][1]), "Health is on the old menu, off the new")
ok('href="/agents"' in nw["/today"][1] and "Data health" in nw["/today"][1], "Today's footer still links to Data health")
ok(not HL.search(nw["/today"][1]), "a clean last check (11-06): no line on Today")
ok(norm(NAV_HEALTH.sub("", o["/today"][1])) == norm(nw["/today"][1]), "Today otherwise identical, old vs new (clean day)")
ok(norm(NAV_HEALTH.sub("", o["/agents"][1])) == norm(nw["/agents"][1].replace('<a href="/agents" class="active">Health</a>', "")),
   "the Health page itself is unchanged apart from the menu")

BAD_NEW = bad_day(NEW_ENG); BAD_OLD = bad_day(OLD_ENG)
o2, _ = render(A, BAD_OLD, OLD_PG); n2, log2 = render(A, BAD_NEW, NEW_PG)
m = HL.search(n2["/today"][1])
t = text(m.group(0)) if m else ""
print("    line:", t)
ok(m and t.startswith("Wed 11-04's check found a problem: The broker connection was down:") and "IB Gateway" in t,
   "a bad last check (11-04): Today names the day and the problem")
ok(m and 'href="/agents?date=2026-11-04"' in m.group(0), "the line links to that day's Health page")
ok(not HL.search(o2["/today"][1]), "the old code shows no such line")
ok(norm(NAV_HEALTH.sub("", o2["/today"][1])) == norm(HL.sub("", n2["/today"][1])), "Today otherwise identical, old vs new (bad day)")
ok("Traceback" not in log2, "no traceback on the bad day")
ok(dump(A) == before, "no table in the copy changed")
print("\n%d checks, %d failed" % (n[0], len(fails)))
sys.exit(1 if fails else 0)
