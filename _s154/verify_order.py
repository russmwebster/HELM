"""s154: one order for the board's lists of open positions and the cards' previous / next.

helm/advice.py (order_key, position_keys, position_order, in_order); helm-pg engine_store.py (today_view,
today_order, book_view), app.py (comment), templates/book.html, thesis.html, thesis_diag.html.
Board started as production starts it, on a fresh DB copy, old code (.bak-20261108-s154) and new side by side
from /tmp copies. The expected order is worked out here from SQL, not from the code under test.
Usage: TMPDIR=/tmp PYTHONPATH=/tmp/pylib python3 -B _s154/verify_order.py [NEW_ENGINE_DIR NEW_PG_DIR]
"""
import hashlib, html as H, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"; PGROOT = HOME + "/mnt/helm-pg"
W = "/tmp/s154"; BAK = ".bak-20261108-s154"
ENG_BAK = ["helm/advice.py"]
PG_BAK = ["engine_store.py", "app.py", "templates/book.html", "templates/thesis.html", "templates/thesis_diag.html"]
ENG_IGN = ("data", "archive", "_s1*", "_to_delete", "*.bak-*", "__pycache__", ".git", "docs", "_records")
PG_IGN = ("*.bak-*", "__pycache__", ".git", "_to_delete")
def fixcfg(dst):
    cfg = dst + "/helm/config.py"
    if os.path.exists(cfg):
        c = open(cfg).read()
        open(cfg, "w").write(re.sub(r"^HELM_PYTHON = .*$", "HELM_PYTHON = %r" % sys.executable, c, flags=re.M))
def tree(src, dst, ign):
    if os.path.exists(dst): shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*ign)); fixcfg(dst); return dst
os.makedirs(W + "/home", exist_ok=True)
if len(sys.argv) > 2:
    NEW_ENG, NEW_PG = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
else:
    NEW_ENG, NEW_PG = tree(ROOT, W + "/eng-new", ENG_IGN), tree(PGROOT, W + "/pg-new", PG_IGN)
OLD_ENG = tree(ROOT, W + "/eng-old", ENG_IGN)
for f in ENG_BAK: shutil.copy2(ROOT + "/" + f + BAK, OLD_ENG + "/" + f)
OLD_PG = tree(PGROOT, W + "/pg-old", PG_IGN)
for f in PG_BAK: shutil.copy2(PGROOT + "/" + f + BAK, OLD_PG + "/" + f)
print("new engine:", NEW_ENG, "| new board:", NEW_PG)
for d, fs in ((NEW_ENG, ENG_BAK), (NEW_PG, PG_BAK)):
    for f in fs: print("   ", f, hashlib.sha256(open(d + "/" + f, "rb").read()).hexdigest()[:12])
fails, n = [], [0]
def ok(c, m):
    n[0] += 1; print(("  ok   " if c else "  FAIL ") + m)
    if not c: fails.append(m)
DB = W + "/copy.db"
if os.path.exists(DB): os.remove(DB)
sqlite3.connect("file:%s/data/helm.db?mode=ro" % ROOT, uri=True).execute("VACUUM INTO ?", (DB,))
def dump(p):
    c = sqlite3.connect(p); out = {}
    for (t,) in c.execute("select name from sqlite_master where type='table' order by name"):
        h = hashlib.sha256(); h.update(repr([r[1] for r in c.execute('pragma table_info("%s")' % t)]).encode())
        for r in c.execute('select * from "%s" order by rowid' % t): h.update(repr(r).encode())
        out[t] = h.hexdigest()
    return out
before = dump(DB)
cx = sqlite3.connect(DB); cx.row_factory = sqlite3.Row
RANK = {"CSP": 0, "COVERED_CALL": 1, "PMCC": 2, "DIAGONAL": 3, "DIAGONAL_PUT": 4, "SHARES": 5}
POS = [dict(r) for r in cx.execute(
    "select p.id, p.ticker, p.strategy, (select min(expiration) from legs l where l.position_id=p.id "
    "and l.status='OPEN') exp from positions p where book='REAL' and status='OPEN'")]
KEY = lambda t, s, e="9999-12-31", i="": (t.upper(), RANK.get(s, 6), str(e or "9999-12-31")[:10], i)
EXPECT = [p["id"] for p in sorted(POS, key=lambda p: KEY(p["ticker"], p["strategy"], p["exp"], p["id"]))]
TICK = {p["id"]: p["ticker"] for p in POS}
print("expected order:", [TICK[i] for i in EXPECT])
PORT = [8860]
def render(eng, pg, paths):
    PORT[0] += 1; port = str(PORT[0])
    open(W + "/home/.helm_profile", "w").write(cx.execute("select id from accounts").fetchone()[0])
    env = dict(os.environ, HOME=W + "/home", HELM_ROOT=eng, HELM_DB=DB, PORT=port, HELM_PG_ENGINE="real",
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
paths = ["/thesis/" + i for i in EXPECT] + ["/today", "/book"]
o, lo = render(OLD_ENG, OLD_PG, paths); nw, ln = render(NEW_ENG, NEW_PG, paths)
ok(all(o[p][0] == 200 and nw[p][0] == 200 for p in paths), "every card, Today and Details render, old and new (%d pages)" % len(paths))
ok("Traceback" not in lo + ln, "no tracebacks in the board's log")
norm = lambda b: re.sub(r'\b(c[a-z]|clip|rc|g)[-_]?\d{4,}\b', 'ID', b)   # the charts' random clipPath ids
NAV = re.compile(r'<div class="(?:cardnav|dnav)".*?</div>', re.S)
def nav(b):
    m = NAV.search(b); s = m.group(0) if m else ""
    g = lambda w: (re.search(r'href="/thesis/([^"]+)"[^>]*>%s</a>' % w, s) or [None, None])[1]
    c = re.search(r'(\d+) of (\d+) open positions', s)
    return g("previous"), g("next"), (int(c.group(1)), int(c.group(2))) if c else None, s
# 1. the cards' previous / next walk the expected order
walk, cur = [], EXPECT[0]
while cur and len(walk) <= len(EXPECT):
    walk.append(cur); cur = nav(nw["/thesis/" + cur][1])[1]
ok(walk == EXPECT, "next from the first card walks A-Z: %s" % [TICK.get(i, i) for i in walk])
navs = {i: nav(nw["/thesis/" + i][1]) for i in EXPECT}
ok(all(navs[i][0] == (EXPECT[k - 1] if k else None) for k, i in enumerate(EXPECT)), "previous is the card before, none on the first")
ok(all(navs[i][2] == (k + 1, len(EXPECT)) for k, i in enumerate(EXPECT)), "each card says 'N of %d open positions'" % len(EXPECT))
ok(all("on Today</span>" not in navs[i][3] for i in EXPECT), "no card still says 'on Today' in its counter")
oldwalk = [nav(o["/thesis/" + i][1])[1] for i in EXPECT]
ok(any(nav(o["/thesis/" + i][1])[1] != navs[i][1] for i in EXPECT), "control: the old cards' next differs somewhere (the old order is not A-Z)")
# 2. the cards are otherwise identical
same = [i for i in EXPECT if NAV.sub("", norm(o["/thesis/" + i][1])) == NAV.sub("", norm(nw["/thesis/" + i][1]))]
import difflib as _dl
for i in EXPECT:
    if i not in same:
        d = [l for l in _dl.unified_diff(NAV.sub("", norm(o["/thesis/" + i][1])).splitlines(),
                                          NAV.sub("", norm(nw["/thesis/" + i][1])).splitlines(), lineterm="", n=0)
             if l[:1] in "+-" and l[:3] not in ("+++", "---")]
        a, b = d[0][1:], (d[1][1:] if len(d) > 1 else "")
        k = next((j for j in range(min(len(a), len(b))) if a[j] != b[j]), 0)
        print("    differs", TICK[i], len(d), "lines; first at:", a[max(0, k - 60):k + 40], "|", b[max(0, k - 60):k + 40])
ok(len(same) == len(EXPECT), "every card identical apart from its previous / next bar (%d of %d)" % (len(same), len(EXPECT)))
# 3. Today: each section lists its positions in the order; the same items, only reordered
def sections(b):
    parts = re.split(r'<h2>', b); out = {}
    for p in parts[1:]:
        name = re.sub(r'<.*', '', p.split("</h2>")[0]).strip()
        ids = []
        for i in re.findall(r'href="/thesis/([^"]+)"', p):
            if i not in ids: ids.append(i)
        out[name] = ids
    return out
so, sn = sections(o["/today"][1]), sections(nw["/today"][1])
for name in ("Rules broken today", "Needs your attention", "Holding"):
    ids = sn.get(name, [])
    ok(ids == [i for i in EXPECT if i in ids], "Today '%s' in the order: %s" % (name, [TICK.get(i, i) for i in ids]))
    ok(sorted(ids) == sorted(so.get(name, [])), "Today '%s': the same positions as before" % name)
lines = lambda b: sorted(l.strip() for l in norm(b).splitlines() if l.strip())
ok(lines(o["/today"][1]) == lines(nw["/today"][1]), "Today: the same lines as before, only their order changed")
# 3b. a constructed case where urgency and A-Z disagree: on a second copy VRT has no readings, so it is the
#     most urgent item ("not read yet"); the old Today put it first in Needs, the new one A-Z (last).
DB2 = W + "/copy2.db"
if os.path.exists(DB2): os.remove(DB2)
sqlite3.connect(DB).execute("VACUUM INTO ?", (DB2,))
c2 = sqlite3.connect(DB2); vrt = [p["id"] for p in POS if p["ticker"] == "VRT"][0]
c2.execute("DELETE FROM leg_checks WHERE leg_id IN (SELECT id FROM legs WHERE position_id=?)", (vrt,))
c2.execute("DELETE FROM checks WHERE position_id=?", (vrt,)); c2.commit(); c2.close()
_db1 = DB; DB = DB2
o2, _lo2 = render(OLD_ENG, OLD_PG, ["/today"]); n2, _ln2 = render(NEW_ENG, NEW_PG, ["/today"]); open(W + "/board2.log", "w").write(_lo2 + "\n=====NEW=====\n" + _ln2)
DB = _db1
need_o, need_n = sections(o2["/today"][1]).get("Needs your attention", []), sections(n2["/today"][1]).get("Needs your attention", [])
ok(need_o[:1] == [vrt], "control: the old Today puts the unread VRT first in Needs: %s" % [TICK.get(i, i) for i in need_o])
ok(vrt in need_n and need_n == [i for i in EXPECT if i in need_n] and sorted(need_n) == sorted(need_o),
   "Needs A-Z with VRT unread (the urgent item takes its A-Z place): %s" % [TICK.get(i, i) for i in need_n])
# (the unread VRT card itself returns an error on old and new code alike -- a separate defect, reported, not tested here)
# 4. Details: the positions table (shares fall in A-Z) and the resting orders
def table(b, head):
    i = b.find(head); j = b.find("</table>", i)
    return re.findall(r'<td>(?:<a href="/thesis/[^"]+">)?<b>([A-Z.]+)</b>', b[i:j])
POSH = "<th>name</th><th>grade</th>"; ORDH = "<th>name</th><th>leg</th>"
tn, to = table(nw["/book"][1], POSH), table(o["/book"][1], POSH)
shares = [t for t in tn if t not in TICK.values()] + [t for t in tn if tn.count(t) > sum(1 for p in POS if p["ticker"] == t)]
exp_rows = sorted([KEY(p["ticker"], p["strategy"], p["exp"], p["id"]) for p in POS] + [KEY(t, "SHARES") for t in set(shares)])
ok(tn == [k[0] for k in exp_rows], "Details positions table A-Z, shares among them: %s" % tn)
ok(sorted(tn) == sorted(to), "Details positions table: the same rows as before")
on, oo = table(nw["/book"][1], ORDH), table(o["/book"][1], ORDH)
ok(on == sorted(on) and sorted(on) == sorted(oo) and len(on) > 0, "Details resting orders A-Z, same rows: %s (was %s)" % (on, oo))
ok(lines(o["/book"][1]) == lines(nw["/book"][1]), "Details: the same lines as before, only their order changed")
# 5. no table changed
ok(dump(DB) == before, "no table in the database copy changed")
# 6. the engine change only adds: advise() and everything else untouched
old_src = open(ROOT + "/helm/advice.py" + BAK).read().splitlines(); new_src = open(NEW_ENG + "/helm/advice.py").read().splitlines()
import difflib
rem = [l for l in difflib.unified_diff(old_src, new_src, lineterm="", n=0) if l.startswith("-") and not l.startswith("---")]
ok(not rem, "helm/advice.py: lines only added, none changed or removed (%d removed)" % len(rem))
print("\n%d checks, %s" % (n[0], "ALL PASS" if not fails else "%d FAIL" % len(fails)))
sys.exit(1 if fails else 0)
