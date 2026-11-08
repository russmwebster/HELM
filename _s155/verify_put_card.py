"""s155 steps 1-2: rule rows (helm/advice.py, helm/decide.py) and the open put's new card (helm-pg put_card.html, app.py).

Board started as production starts it on a fresh DB copy, old code (.bak-20261108-s155) and new side by side from /tmp
copies. Expected numbers come from SQL on the copy, not from the code under test.
Usage: TMPDIR=/tmp PYTHONPATH=/tmp/pylib python3 -B _s155/verify_put_card.py [NEW_ENGINE_DIR NEW_PG_DIR]
"""
import hashlib, html as H, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
from datetime import date
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"; PGROOT = HOME + "/mnt/helm-pg"
W = "/tmp/s155v"
ENG_BAK = {"helm/advice.py": ".bak-20261108-s155", "helm/decide.py": ".bak-20261108-s155"}
PG_BAK = {"app.py": ".bak-20261108-s155"}
PG_NEW_ONLY = ["templates/put_card.html"]
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
print("new engine:", NEW_ENG, "| new board:", NEW_PG)
for d, fs in ((NEW_ENG, list(ENG_BAK)), (NEW_PG, list(PG_BAK) + PG_NEW_ONLY)):
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
CSPS = [r["id"] for r in cx.execute("select id from positions where book='REAL' and status='OPEN' and strategy='CSP' order by ticker")]
OTHER = [r["id"] for r in cx.execute("select id from positions where book='REAL' and status='OPEN' and strategy!='CSP' order by ticker")]
CLOSED = [r["id"] for r in cx.execute("select id from positions where book='REAL' and status!='OPEN' and strategy='CSP' "
                                      "order by closed_at desc limit 3")]
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
paths = ["/thesis/" + i for i in CSPS + OTHER + CLOSED] + ["/today", "/book", "/watchlist"]
o, lo = render(OLD_ENG, OLD_PG, paths); nw, ln = render(NEW_ENG, NEW_PG, paths)
ok(all(o[p][0] == 200 and nw[p][0] == 200 for p in paths), "every page renders, old and new (%d pages)" % len(paths))
ok("Traceback" not in lo + ln, "no tracebacks in the board's log")
def norm(b):
    b = re.sub(r"\?v=\d+", "?v=X", b)
    b = re.sub(r'(clip|cp|g)[-_]?[0-9a-f]{6,}', 'ID', b)
    return re.sub(r'(["#])([a-z]{1,3})\d{3,}(["\)])', r'\1\2ID\3', b)   # the charts' random clipPath ids
same = [p for p in paths if not p.startswith("/thesis/") or p[8:] not in CSPS]
diff = [p for p in same if norm(o[p][1]) != norm(nw[p][1])]
ok(not diff, "every page other than the open puts' cards is identical old vs new: %s" % diff)
def text(b):
    b = re.sub(r"<style.*?</style>", " ", b, flags=re.S); b = re.sub(r"<[^>]+>", " ", b)
    return re.sub(r"\s+", " ", H.unescape(b)).strip()
T = date.today()
def money(x): return ("−" if x < 0 else "") + "$" + "{:,.0f}".format(abs(x))
for pid in CSPS:
    b = nw["/thesis/" + pid][1]; t = text(b)
    leg = cx.execute("select * from legs where position_id=? and status='OPEN' and direction='SHORT'", (pid,)).fetchone()
    lc = cx.execute("select * from leg_checks where leg_id=? order by checked_at desc limit 1", (leg["id"],)).fetchone()
    tk = pid.split("-")[0]
    ok('<div class="pc">' in b and "Deciding from here" not in b and "HELM leans" not in t and "The read" not in t
       and "broken" not in t.lower() and "What it would cost to close" not in t,
       "%s: the new card, none of the old sections" % tk)
    ok("Reading: %s" % lc["checked_at"][:16].replace("T", " ") in t, "%s: one clock = the leg's latest reading" % tk)
    dte = (date.fromisoformat(leg["expiration"][:10]) - T).days
    ok("Days left %d" % dte in t, "%s: days left %d (from today)" % (tk, dte))
    n_, k = leg["contracts"], leg["strike"]
    ok("Cash today (mid) %s" % money(-lc["current_price"] * 100 * n_) in t, "%s: close at the mid %s" % (tk, money(-lc["current_price"] * 100 * n_)))
    ok("Cash today (ask) %s" % money(-lc["current_ask"] * 100 * n_) in t, "%s: close at the ask" % tk)
    coll = money(k * 100 * n_)
    t2 = re.sub(r"name limit \(20%\) \w+ \$[\d,]+ /", "", t)    # the name's total is its own quantity
    ok(t2.count(coll) == 1, "%s: collateral %s appears once (count %d)" % (tk, coll, t2.count(coll)))
    ok("IV %.1f" % lc["iv_current"] in t, "%s: IV from the leg's reading" % tk)
    pl = "+$" if False else ""
    for word in (" you ", " would ", "HELM says", "HELM thinks", "advises"):
        ok(word not in " " + t + " ", "%s: no '%s' on the card" % (tk, word.strip()))
# CSCO and FCX in detail (the two shapes)
def card(tk): return text(nw["/thesis/" + next(i for i in CSPS if i.startswith(tk + "-"))][1])
c = card("CSCO")
ok("Tested fired spot $94.39 below strike 105" in c and "21-day close-or-roll fired" in c
   and "Assignment default yes grade B" in c and "Covered calls if assigned blocked basis $102.72, spot 8.1% below" in c,
   "CSCO: tested and 21-day fired; assignment default; covered calls blocked")
ok("Technology sector limit (30%) near $80,790 / $88,713" in c and "Book delta limit (half the sleeve) over" in c,
   "CSCO: sector near (91%), book delta over")
ok("Roll: earnings 2026-11-12" in c and "Roll prices from 11-02" in c, "CSCO: no roll, its reason; stale roll -> one command line")
f = card("FCX")
ok("Next line" in f and "50% profit-take" in f and "buy back $1.09" in f and "11-27" in f and "Tested" not in f
   and "Book delta limit (half the sleeve) over" in f,
   "FCX: its own rules none fired -> the next lines (buy-back $1.09, 21 days on 11-27); the book's delta still listed")
g = card("GE")
ok("Industrials" not in g or "near" not in g, "GE: Industrials at 89.6% is not 'near' (90% line)")
# keep vs switch: the four rows from the recorded run
import json
r = json.loads(cx.execute("select r.result from recovery_results r join recovery_runs u on u.id=r.run_id "
                          "where r.ticker='CSCO' order by u.run_at desc limit 1").fetchone()[0])["opportunity"]
exp = "Average result %s %s %s %s" % tuple(("+" if x > 0 else "") + money(x) for x in (
    r["keep_90"]["mean"], r["switch_90"]["mean"], r["keep_365"]["mean"], r["switch_365"]["mean"]))
ok(exp.replace("+−", "−") in c, "CSCO keep vs switch averages = the recorded run: %s" % exp)
ok("Worst 1 in 10 −$5,585 −$4,655 −$9,160 −$10,753" in c, "CSCO worst 1 in 10 = the recorded run")
ok(dump(DB) == before, "no table changed")
print("\n%d checks, %d failed" % (n[0], len(fails)))
open(W + "/new-CSCO.html", "w").write(nw["/thesis/" + next(i for i in CSPS if i.startswith("CSCO-"))][1])
sys.exit(1 if fails else 0)
