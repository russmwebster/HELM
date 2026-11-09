"""s156 step 6: closed cards (helm/decide.py closed_short; helm/thesis.py close_svg plain; helm-pg put_closed.html,
engine_store.py, app.py). (Harness built from verify_today.py.) Was: s156 step 4: Today (helm/todayview.py, helm/decide.py pmcc_read; helm-pg engine_store.today_view, templates/today.html).
(Harness built from verify_pmcc_card.py.) s156 step 3: the PMCC rule rows (helm/advice.py pmcc_rows), the card's data (helm/decide.py decide_pmcc), the
card's ruler (helm/thesis.py) and the open PMCC's new card (helm-pg pmcc_card.html, engine_store.py, app.py).

Board started as production starts it on a fresh DB copy, old code (.bak-20261108-s156) and new side by side from /tmp
copies. Expected numbers come from SQL on the copy, not from the code under test.
Usage: TMPDIR=/tmp PYTHONPATH=/tmp/pylib python3 -B _s156/verify_pmcc_card.py [NEW_ENGINE_DIR NEW_PG_DIR]
"""
import hashlib, html as H, json, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
from datetime import date
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"; PGROOT = HOME + "/mnt/helm-pg"
W = "/tmp/s156c"
ENG_BAK = {"helm/decide.py": ".bak-20261108-s156c", "helm/thesis.py": ".bak-20261108-s156b"}
ENG_NEW_ONLY = []
PG_BAK = {"engine_store.py": ".bak-20261108-s156d", "app.py": ".bak-20261108-s156c"}
PG_NEW_ONLY = ["templates/put_closed.html"]
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
PORT = [9260]
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
CS = [r["id"] for r in cx.execute("select id from positions where book='REAL' and status!='OPEN' and strategy in ('CSP','COVERED_CALL') order by closed_at desc")]
OTH = [r["id"] for r in cx.execute("select id from positions where status!='OPEN' and not (book='REAL' and strategy in ('CSP','COVERED_CALL')) "
                                   "order by closed_at desc limit 12")]
paths = ["/thesis/" + i for i in ALL + CS + OTH] + ["/today", "/book", "/watchlist"]
o, lo = render(OLD_ENG, OLD_PG, paths); nw, ln = render(NEW_ENG, NEW_PG, paths)
ok(all(o[p][0] == 200 and nw[p][0] == 200 for p in paths), "every page renders, old and new (%d pages)" % len(paths))
ok("Traceback" not in lo + ln, "no tracebacks in the board's log")
def norm(b):
    b = re.sub(r"\?v=\d+", "?v=X", b)
    b = re.sub(r'(clip|cp|g)[-_]?[0-9a-f]{6,}', 'ID', b)
    return re.sub(r'(["#])([a-z]{1,3})\d{3,}(["\)])', r'\1\2ID\3', b)
same = [p for p in paths if p[8:] not in CS]
diff = [p for p in same if norm(o[p][1]) != norm(nw[p][1])]
ok(not diff, "every page other than the closed real puts' and covered calls' cards is identical old vs new (%d pages): %s" % (len(same), diff))
def text(b):
    b = re.sub(r"<style.*?</style>", " ", b, flags=re.S); b = re.sub(r"<script.*?</script>", " ", b, flags=re.S)
    b = re.sub(r"<svg.*?</svg>", " [chart] ", b, flags=re.S); b = re.sub(r"<[^>]+>", " ", b)
    return re.sub(r"\s+", " ", H.unescape(b)).strip()
def money(x, sign=True): return ("−" if x < 0 else ("+" if sign and x > 0 else "")) + "$" + "{:,.0f}".format(abs(x))
bad = []
for pid in CS:
    b = nw["/thesis/" + pid][1]; t = text(b).split("← Today", 1)[-1]
    p = cx.execute("select * from positions where id=?", (pid,)).fetchone()
    l = cx.execute("select * from legs where position_id=? and direction='SHORT' and option_type in ('PUT','CALL')", (pid,)).fetchone()
    n_ = l["contracts"]
    checks = [
        ('<div class="pc">' in b and "The read" not in t and "believ" not in t.lower() and "better —" not in b, "new card, no old sections"),
        (("Entry %s" % str(l["open_date"])[5:10]) in t, "entry date"),
        (money(l["open_price"] * 100 * n_) in t, "entry cash"),
        ((money(-(l["close_price"] or 0) * 100 * n_) if l["close_price"] else "$0") in t, "exit cash"),
        (p["strategy"] != "CSP" or ("Collateral %s" % money(l["strike"] * 100 * n_, False)) in t, "collateral"),
        (("%d days (" % (date.fromisoformat(str(l["close_date"] or p["closed_at"])[:10]) - date.fromisoformat(str(l["open_date"])[:10])).days) in t, "held days"),
    ]
    for word in (" you ", " your ", " would ", "HELM "):
        checks.append((word not in " " + t + " ", "no '%s'" % word.strip()))
    g = p["realized_pnl"]
    if (p["closed_at"] or "")[:10] < "2026-10-24":
        checks.append(((money(g) + " gross") in t, "gross before 10-24"))
    sn = cx.execute("select spot_price from entry_snapshots where position_id=? order by snapshot_at limit 1", (pid,)).fetchone()
    if sn and sn[0]:
        checks.append((("$%.2f" % sn[0]) in t, "entry spot from the snapshot"))
    for c_, m_ in checks:
        if not c_: bad.append((pid, m_))
ok(not bad, "all %d closed cards: layout, entry/exit cash, collateral, held days, basis, entry spot, no voice: %s" % (len(CS), bad[:6]))
def card(pre): return text(nw["/thesis/" + next(i for i in CS if i.startswith(pre))][1])
x = card("XLE-CSP-20261031")
fees = sum(r[0] for r in cx.execute("select 0"))
ok("−$3,275 net Closed 11-06 · discretionary" in x and "Held 7 days (10-30 → 11-06)" in x and "Collateral $53,600" in x
   and "On collateral −6.1%" in x and "Grade FUND · Energy" in x, "XLE header and facts: %s" % x[:260])
ok("Option price $1.22 sold $5.30 bought Cash +$976 −$4,240 XLE $70.02 $62.03 Days left 35 28 IV 29.3 28.1" in x, "XLE entry and exit table")
ok("Tested fired spot $62.03 below strike 67 Assignment default yes grade FUND Other rules: 2 not fired" in x, "XLE rules at the close")
ok("[chart]" in x and "sold $976" in nw["/thesis/" + next(i for i in CS if i.startswith("XLE-CSP-20261031"))][1], "XLE chart, the sale marked")
gq = card("GOOGL-CSP-20261015")
ok("+$837 net" in gq and "· assigned" in gq and "Option price $8.38 sold assigned" in gq and "21-day close-or-roll fired 15 days" in gq,
   "GOOGL assigned: %s" % gq[:300])
pt = next(i for i in CS if cx.execute("select exit_reason from positions where id=?", (i,)).fetchone()[0] == "PROFIT_TARGET")
ptt = text(nw["/thesis/" + pt][1])
ok("profit target" in ptt and "bought" in ptt, "a 50%% close (%s): %s" % (pt, ptt[ptt.find("Closed"):ptt.find("Closed") + 60]))
old = next(i for i in CS if cx.execute("select count(*) from checks where position_id=? and data_quality='GOOD'", (i,)).fetchone()[0] == 0)
ot = text(nw["/thesis/" + old][1])
ok("Rules at the close no reading" in ot and "Cost to buy back last check each day no readings" in ot, "an old close with no readings says so: %s" % old)
ok(dump(DB) == before, "no table changed")
def adv(eng):
    env = dict(os.environ, HELM_ROOT=eng, HELM_DB=DB, PYTHONPATH=eng + ":/tmp/pylib", HOME=W + "/home", COLUMNS="200")
    j = subprocess.run([sys.executable, "-B", "-c", "import json;from helm import advice;d=advice.build();d.pop('generated_at',None);"
                        "print(json.dumps(d,sort_keys=True,default=str))"], cwd=eng, env=env, capture_output=True, text=True)
    return j.stdout
ja, jb = adv(OLD_ENG), adv(NEW_ENG)
ok(ja and ja == jb, "advice.json identical old vs new")
print("\n%d checks, %d failed" % (n[0], len(fails)))
open(W + "/xle.txt", "w").write(x)
sys.exit(1 if fails else 0)
