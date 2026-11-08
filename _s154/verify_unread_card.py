"""s154b: a put card with no readings yet (booked by the import after the day's last snapshot) rendered an
error page -- "Deciding from here" formatted the put's share of the book's delta, which is blank before the
first reading. Fix: helm/decide.py (delta share only with a reading) + helm-pg templates/thesis.html (says
"no reading yet"). Board started as production starts it; old = .bak-20261108-s154 (decide.py) and
.bak-20261108-s154b (thesis.html); a second DB copy with VRT's readings removed.
Usage: TMPDIR=/tmp PYTHONPATH=/tmp/pylib python3 -B _s154/verify_unread_card.py [NEW_ENGINE_DIR NEW_PG_DIR]
"""
import hashlib, html as H, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"; PGROOT = HOME + "/mnt/helm-pg"
W = "/tmp/s154b"
ENG_BAK = {"helm/decide.py": ".bak-20261108-s154"}; PG_BAK = {"templates/thesis.html": ".bak-20261108-s154b"}
ENG_IGN = ("data", "archive", "_s1*", "_to_delete", "*.bak-*", "__pycache__", ".git", "docs", "_records")
PG_IGN = ("*.bak-*", "__pycache__", ".git", "_to_delete")
def fixcfg(dst):
    cfg = dst + "/helm/config.py"
    c = open(cfg).read(); open(cfg, "w").write(re.sub(r"^HELM_PYTHON = .*$", "HELM_PYTHON = %r" % sys.executable, c, flags=re.M))
def tree(src, dst, ign, eng=False):
    if os.path.exists(dst): shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*ign))
    if eng: fixcfg(dst)
    return dst
os.makedirs(W + "/home", exist_ok=True)
if len(sys.argv) > 2: NEW_ENG, NEW_PG = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
else: NEW_ENG, NEW_PG = tree(ROOT, W + "/eng-new", ENG_IGN, True), tree(PGROOT, W + "/pg-new", PG_IGN)
OLD_ENG = tree(ROOT, W + "/eng-old", ENG_IGN, True); OLD_PG = tree(PGROOT, W + "/pg-old", PG_IGN)
for f, b in ENG_BAK.items(): shutil.copy2(ROOT + "/" + f + b, OLD_ENG + "/" + f)
for f, b in PG_BAK.items(): shutil.copy2(PGROOT + "/" + f + b, OLD_PG + "/" + f)
for d, fs in ((NEW_ENG, ENG_BAK), (NEW_PG, PG_BAK)):
    for f in fs: print("  new", f, hashlib.sha256(open(d + "/" + f, "rb").read()).hexdigest()[:12])
fails, n = [], [0]
def ok(c, m):
    n[0] += 1; print(("  ok   " if c else "  FAIL ") + m)
    if not c: fails.append(m)
def copydb(p, src):
    if os.path.exists(p): os.remove(p)
    sqlite3.connect(src).execute("VACUUM INTO ?", (p,))
DB1, DB2 = W + "/copy.db", W + "/copy2.db"
copydb(DB1, "file:%s/data/helm.db?mode=ro" % ROOT) if False else sqlite3.connect("file:%s/data/helm.db?mode=ro" % ROOT, uri=True).execute("VACUUM INTO ?", (DB1,)) if not os.path.exists(DB1) or os.remove(DB1) is None else None
copydb(DB2, DB1)
cx = sqlite3.connect(DB1); cx.row_factory = sqlite3.Row
CSPS = [r["id"] for r in cx.execute("select id from positions where book='REAL' and status='OPEN' and strategy='CSP' order by ticker")]
OTHER = [r["id"] for r in cx.execute("select id from positions where book='REAL' and status='OPEN' and strategy!='CSP' order by ticker")]
VRT = [i for i in CSPS if i.startswith("VRT-")][0]
c2 = sqlite3.connect(DB2)
c2.execute("DELETE FROM leg_checks WHERE leg_id IN (SELECT id FROM legs WHERE position_id=?)", (VRT,))
c2.execute("DELETE FROM checks WHERE position_id=?", (VRT,)); c2.commit(); c2.close()
def dump(p):
    c = sqlite3.connect(p); out = {}
    for (t,) in c.execute("select name from sqlite_master where type='table' order by name"):
        h = hashlib.sha256(); h.update(repr([r[1] for r in c.execute('pragma table_info("%s")' % t)]).encode())
        for r in c.execute('select * from "%s" order by rowid' % t): h.update(repr(r).encode())
        out[t] = h.hexdigest()
    return out
b1, b2 = dump(DB1), dump(DB2)
PORT = [8890]
def render(eng, pg, db, paths):
    PORT[0] += 1; port = str(PORT[0])
    open(W + "/home/.helm_profile", "w").write(cx.execute("select id from accounts").fetchone()[0])
    env = dict(os.environ, HOME=W + "/home", HELM_ROOT=eng, HELM_DB=db, PORT=port, HELM_PG_ENGINE="real",
               PYTHONPATH="/tmp/pylib", HELM_ADVICE_DIR=W + "/advice-out")
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
                r = urllib.request.urlopen("http://127.0.0.1:%s%s" % (port, path), timeout=120); out[path] = (r.status, r.read().decode())
            except Exception as e: out[path] = (getattr(e, "code", "ERR"), str(e))
    finally:
        p.terminate(); log = p.communicate(timeout=10)[0].decode()
    return out, log
card = "/thesis/" + VRT
o2, lo2 = render(OLD_ENG, OLD_PG, DB2, [card]); n2, ln2 = render(NEW_ENG, NEW_PG, DB2, [card, "/today"])
ok(o2[card][0] == 500, "control: the old code's unread VRT card is an error page (%s)" % o2[card][0])
ok(n2[card][0] == 200 and "Traceback" not in ln2, "the new code renders the unread VRT card (%s)" % n2[card][0])
txt = H.unescape(re.sub(r"<[^>]+>", " ", n2[card][1])); txt = re.sub(r"\s+", " ", txt)
ok("Its share of the book's delta: no reading yet — first reading at the next snapshot." in txt, "the card says the delta share has no reading yet")
dz = txt[txt.find("HELM's advice (as on Today)"):][:2200]
print("    VRT card, Deciding from here:", dz)
ok("moves like" not in dz, "no delta figure invented for the unread put")
ok(n2["/today"][0] == 200, "Today renders on the unread copy")
paths = ["/thesis/" + i for i in CSPS + OTHER]
o1, _ = render(OLD_ENG, OLD_PG, DB1, paths); n1, _ = render(NEW_ENG, NEW_PG, DB1, paths)
norm = lambda b: re.sub(r'\b(c[a-z]|clip|rc|g)[-_]?\d{4,}\b', 'ID', b)
ok(all(o1[p][0] == 200 and n1[p][0] == 200 for p in paths), "every open card renders on the real copy, old and new (%d)" % len(paths))
same = [p for p in paths if norm(o1[p][1]) == norm(n1[p][1])]
ok(len(same) == len(paths), "every card with readings identical to before the fix (%d of %d)" % (len(same), len(paths)))
ok(dump(DB1) == b1 and dump(DB2) == b2, "no table changed on either copy")
print("\n%d checks, %s" % (n[0], "ALL PASS" if not fails else "%d FAIL" % len(fails)))
sys.exit(1 if fails else 0)
