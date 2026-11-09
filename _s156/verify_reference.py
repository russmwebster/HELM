"""s156 step 7: the reference page (helm/boardview.py reference_constants; helm-pg reference.html, app.py) and the "?" links.
(Harness built from verify_today.py.) Was: s156 step 4: Today (helm/todayview.py, helm/decide.py pmcc_read; helm-pg engine_store.today_view, templates/today.html).
(Harness built from verify_pmcc_card.py.) s156 step 3: the PMCC rule rows (helm/advice.py pmcc_rows), the card's data (helm/decide.py decide_pmcc), the
card's ruler (helm/thesis.py) and the open PMCC's new card (helm-pg pmcc_card.html, engine_store.py, app.py).

Board started as production starts it on a fresh DB copy, old code (.bak-20261108-s156) and new side by side from /tmp
copies. Expected numbers come from SQL on the copy, not from the code under test.
Usage: TMPDIR=/tmp PYTHONPATH=/tmp/pylib python3 -B _s156/verify_pmcc_card.py [NEW_ENGINE_DIR NEW_PG_DIR]
"""
import hashlib, html as H, json, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
from datetime import date
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"; PGROOT = HOME + "/mnt/helm-pg"
W = "/tmp/s156f"
ENG_BAK = {"helm/boardview.py": ".bak-20261109-s156"}
ENG_NEW_ONLY = []
PG_BAK = {"app.py": ".bak-20261109-s156", "templates/today.html": ".bak-20261109-s156", "templates/put_card.html": ".bak-20261109-s156",
          "templates/pmcc_card.html": ".bak-20261109-s156", "templates/book.html": ".bak-20261109-s156",
          "templates/put_closed.html": ".bak-20261109-s156"}
PG_NEW_ONLY = ["templates/reference.html"]
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
for f, b in ENG_BAK.items(): shutil.copy(ROOT + "/" + f + b, OLD_ENG + "/" + f)
OLD_PG = tree(PGROOT, W + "/pg-old", PG_IGN)
for f, b in PG_BAK.items(): shutil.copy(PGROOT + "/" + f + b, OLD_PG + "/" + f)
for f in PG_NEW_ONLY: os.remove(OLD_PG + "/" + f)
for f in ENG_NEW_ONLY: os.remove(OLD_ENG + "/" + f)
print("new engine:", NEW_ENG, "| new board:", NEW_PG)
for d, fs in ((NEW_ENG, list(ENG_BAK) + ENG_NEW_ONLY), (NEW_PG, list(PG_BAK) + PG_NEW_ONLY)):
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
PMCC = [r["id"] for r in cx.execute("select id from positions where book='REAL' and status='OPEN' and strategy='PMCC' order by ticker")]
OTHER = [r["id"] for r in cx.execute("select id from positions where book='REAL' and status='OPEN' and strategy!='PMCC' order by ticker")]
CLOSED = [r["id"] for r in cx.execute("select id from positions where book='REAL' and status!='OPEN' and strategy in "
                                      "('PMCC','DIAGONAL') order by closed_at desc limit 3")] + \
         [r["id"] for r in cx.execute("select id from positions where book='REAL' and status!='OPEN' and strategy='CSP' "
                                      "order by closed_at desc limit 2")]
PORT = [9360]
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


ALL = [r["id"] for r in cx.execute("select id from positions where book='REAL' and status='OPEN' order by ticker")]
CS = [r["id"] for r in cx.execute("select id from positions where book='REAL' and status!='OPEN' and strategy='CSP' order by closed_at desc limit 4")]
OTH = [r["id"] for r in cx.execute("select id from positions where status!='OPEN' and strategy!='CSP' order by closed_at desc limit 4")]
paths = ["/thesis/" + i for i in ALL + CS + OTH] + ["/today", "/book", "/watchlist", "/positions"]
o, lo = render(OLD_ENG, OLD_PG, paths); nw, ln = render(NEW_ENG, NEW_PG, paths + ["/reference"])
ok(all(o[p][0] == 200 for p in paths) and all(nw[p][0] == 200 for p in paths + ["/reference"]), "every page renders, the reference page too (%d)" % (len(paths) + 1))
ok("Traceback" not in lo + ln, "no tracebacks in the board's log")
def norm(b):
    b = re.sub(r"\?v=\d+", "?v=X", b)
    b = re.sub(r'(clip|cp|g)[-_]?[0-9a-f]{6,}', 'ID', b)
    return re.sub(r'(["#])([a-z]{1,3})\d{3,}(["\)])', r'\1\2ID\3', b)
def strip(b):
    b = re.sub(r' <a class="q" href="/reference#[a-z0-9-]+" title="How this is worked out">\?</a>', "", b)
    b = re.sub(r"  \.q \{[^\n]*\}\n", "", b)
    return b.replace('  <a href="/reference">Reference</a>\n', "")
diff = [p for p in paths if norm(strip(nw[p][1])) != norm(o[p][1])]
ok(not diff, "every page = the old page once the ? links are taken out (%d pages): %s" % (len(paths), diff))
R = nw["/reference"][1]
ids = set(re.findall(r'<dt id="([a-z0-9-]+)"', R))
tg = {}
for p in paths:
    for a in re.findall(r'href="/reference#([a-z0-9-]+)"', nw[p][1]):
        tg.setdefault(a, set()).add(p)
ok(tg and set(tg) <= ids, "every ? lands on an entry (%d targets, %d entries): missing %s" % (len(tg), len(ids), sorted(set(tg) - ids)))
ok('<a href="/reference">Reference</a>' in nw["/today"][1], "Today's footer links the reference page")
ok(len([p for p in tg.get("mid", ())]) >= 1 and "break-even" in tg and "rent-cover" in tg and "market-moves" in tg and "collateral" in tg,
   "? on Today, the put card, the PMCC card, Details and the closed card")
def text(b):
    b = re.sub(r"<style.*?</style>", " ", b, flags=re.S); b = re.sub(r"<[^>]+>", " ", b)
    return re.sub(r"\s+", " ", H.unescape(b)).strip()
rt = text(R)
sys.path.insert(0, NEW_ENG)
env = dict(os.environ, HELM_ROOT=NEW_ENG, PYTHONPATH=NEW_ENG + ":/tmp/pylib")
k = json.loads(subprocess.run([sys.executable, "-B", "-c", "import json;from helm import boardview as B, advice as A, puttool as P, sleeve as S, bookview as V;"
                               "print(json.dumps([A.PROFIT_TAKE, A.TIME_EXIT_DTE, A.TESTED_DELTA, A.NEAR_FRAC, S.NAME_PCT, S.SECTOR_PCT, V.THETA_LO, V.THETA_HI,"
                               "P.DELTA_MIN, P.DELTA_MAX, P.DTE_MIN, P.DTE_MAX, P.IVR_MIN, P.EARN_VETO_DAYS, A.PMCC_LONG_REVIEW_DTE, V.DELTA_FRAC]))"],
                              cwd=NEW_ENG, env=env, capture_output=True, text=True).stdout)
pt, te, td, nf, npct, spct, tlo, thi, dmin, dmax, dtmin, dtmax, ivr, veto, lr, dfr = k
for want in ("Fires when the short has kept %g%% of its credit" % (pt * 100), "delta %.2f or more with %d days or fewer left" % (td, te),
             "Near: %g%% of a limit" % (nf * 100), "at most %g%% of the sleeve. GOOG + GOOGL" % (npct * 100), "One sector at most %g%%" % (spct * 100),
             "between %g%% and %g%% of the sleeve" % (tlo * 100, thi * 100), "delta %.2f–%.2f; %d–%d days" % (dmin, dmax, dtmin, dtmax),
             "IV rank %g or more; no report within %d days" % (ivr, veto), "Long review at %d days" % lr, "at most %g%% of the sleeve. A flag" % (dfr * 100)):
    ok(want in rt, "reference quotes the engine: %s" % want)
for word in (" you ", " your ", "HELM says", "HELM thinks"):
    ok(word not in " " + rt + " ", "reference: no '%s'" % word.strip())
ok(dump(DB) == before, "no table changed")
print("\n%d checks, %d failed" % (n[0], len(fails)))
sys.exit(1 if fails else 0)
