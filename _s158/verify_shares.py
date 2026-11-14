"""s158: the snapshot reads the price of every stock HELM holds as shares (helm/stockreads.py, helm/cli/check_cmd.py),
the book uses the newer of that and any position's reading (helm/bookview.py share_rows), and the readings warning
names each line that is a day or more behind instead of "readings span A to B" (bookview.limits).

Old code = the .bak-20261114-s158 files; new = the working tree (or NEW_ENG NEW_PG dirs, copied). Fresh DB copies,
the board started as production starts it, IBKR out of reach. Expected numbers from SQL on the copy.
Usage: TMPDIR=/tmp PYTHONPATH=/tmp/pylib python3 -B _s158/verify_shares.py [NEW_ENG NEW_PG]
"""
import hashlib, html as H, json, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"; PGROOT = HOME + "/mnt/helm-pg"
W = "/tmp/s158"
ENG_BAK = {"helm/bookview.py": ".bak-20261114-s158", "helm/cli/check_cmd.py": ".bak-20261114-s158"}
ENG_NEW_ONLY = ["helm/stockreads.py"]
PG_BAK = {"templates/reference.html": ".bak-20261114-s158"}
ENG_IGN = ("data", "archive", "_s1*", "_to_delete", "*.bak-*", "__pycache__", ".git", "docs", "_records", "logs", "Claude outputs")
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
    NEW_ENG, NEW_PG = tree(os.path.abspath(sys.argv[1]), W + "/eng-new", ENG_IGN), tree(os.path.abspath(sys.argv[2]), W + "/pg-new", PG_IGN)
else:
    NEW_ENG, NEW_PG = tree(ROOT, W + "/eng-new", ENG_IGN), tree(PGROOT, W + "/pg-new", PG_IGN)
OLD_ENG = tree(ROOT, W + "/eng-old", ENG_IGN)
for f, b in ENG_BAK.items(): shutil.copy(ROOT + "/" + f + b, OLD_ENG + "/" + f)
for f in ENG_NEW_ONLY: os.remove(OLD_ENG + "/" + f)
OLD_PG = tree(PGROOT, W + "/pg-old", PG_IGN)
for f, b in PG_BAK.items(): shutil.copy(PGROOT + "/" + f + b, OLD_PG + "/" + f)
print("new engine:", NEW_ENG, "| new board:", NEW_PG)
for d, fs in ((NEW_ENG, list(ENG_BAK) + ENG_NEW_ONLY), (NEW_PG, list(PG_BAK))):
    for f in fs: print("   ", f, hashlib.sha256(open(d + "/" + f, "rb").read()).hexdigest()[:12])
fails, n = [], [0]
def ok(c, m):
    n[0] += 1; print(("  ok   " if c else "  FAIL ") + m)
    if not c: fails.append(m)
def fresh(name):
    p = W + "/" + name
    if os.path.exists(p): os.remove(p)
    sqlite3.connect("file:%s/data/helm.db?mode=ro" % ROOT, uri=True).execute("VACUUM INTO ?", (p,))
    c = sqlite3.connect(p); c.execute("DROP TABLE IF EXISTS stock_checks"); c.commit(); c.close()   # a known start
    return p
def dump(p):
    c = sqlite3.connect(p); out = {}
    for (t,) in c.execute("select name from sqlite_master where type='table' order by name"):
        h = hashlib.sha256(); h.update(repr([r[1] for r in c.execute('pragma table_info("%s")' % t)]).encode())
        for r in c.execute('select * from "%s" order by rowid' % t): h.update(repr(r).encode())
        out[t] = h.hexdigest()
    return out
PORT = [9160]
def render(eng, pg, db, paths):
    PORT[0] += 1; port = str(PORT[0])
    cx = sqlite3.connect(db)
    open(W + "/home/.helm_profile", "w").write(cx.execute("select id from accounts").fetchone()[0]); cx.close()
    env = dict(os.environ, HOME=W + "/home", HELM_ROOT=eng, HELM_DB=db, PORT=port, HELM_PG_ENGINE="real",
               PYTHONPATH="/tmp/pylib", HELM_ADVICE_DIR=W + "/advice-out", PYTHONHASHSEED="0")
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
def eng(engdir, db, code):
    """Run python in a child with the given engine and DB; returns stdout (last line JSON)."""
    env = dict(os.environ, HELM_ROOT=engdir, HELM_DB=db, PYTHONPATH=engdir + ":/tmp/pylib", HOME=W + "/home",
               HELM_ADVICE_DIR=W + "/advice-out", COLUMNS="200")
    r = subprocess.run([sys.executable, "-B", "-c", code], cwd=engdir, env=env, capture_output=True, text=True, timeout=170)
    if r.returncode: print(r.stdout[-1500:], r.stderr[-2500:])
    return r.stdout
VIEW = ("import json; from helm import bookview as V, advice as A\n"
        "v = V.view(None); d = A.build()\n"
        "sh = [r for r in v['positions'] if r['strategy']=='SHARES']\n"
        "print(json.dumps({'shares': sh, 'stale': v['limits']['stale'], 'bw': v['totals']['bw_delta'],\n"
        "  'flags': [f['reason'] for f in d['book_flags']],\n"
        "  'sh_entries': [e for e in d['entries'].values() if e['kind']=='shares']}, default=str))\n")
def view(engdir, db):
    return json.loads(eng(engdir, db, VIEW).strip().split("\n")[-1])
def norm(b):
    b = re.sub(r"\?v=\d+", "?v=X", b)
    b = re.sub(r'(clip|cp|g)[-_]?[0-9a-f]{6,}', 'ID', b)
    return re.sub(r'(["#])([a-z]{1,3})\d{3,}(["\)])', r'\1\2ID\3', b)

# ---------------------------------------------------------------- A: the live copy, no share reading yet
print("A. the live copy as it is (no share reading stored)")
DA = fresh("a.db"); before = dump(DA)
cx = sqlite3.connect(DA); cx.row_factory = sqlite3.Row
HELD = [r[0] for r in cx.execute("select distinct ticker from stock_positions where shares>0 order by ticker")]
ok(HELD == ["GOOGL"], "HELM holds shares in %s" % HELD)
last = cx.execute("select c.spot_price, c.checked_at from checks c join positions p on p.id=c.position_id where p.book='REAL' "
                  "and p.ticker='GOOGL' and c.spot_price is not null order by c.checked_at desc limit 1").fetchone()
beta = cx.execute("select beta from watchlist where ticker='GOOGL'").fetchone()[0]
eb = cx.execute("select effective_basis from stock_positions where ticker='GOOGL'").fetchone()[0]
newest = cx.execute("select max(checked_at) from checks c join positions p on p.id=c.position_id "
                    "where p.book='REAL' and p.status='OPEN'").fetchone()[0]
ALL = [r["id"] for r in cx.execute("select id from positions where book='REAL' and status='OPEN' order by ticker")]
print("   GOOGL last position reading", tuple(last), "| beta", beta, "| basis", eb, "| newest open reading", newest)
vo, vn = view(OLD_ENG, DA), view(NEW_ENG, DA)
ok(vo["shares"][0]["spot"] == last[0] == vn["shares"][0]["spot"], "no share reading: old and new both price GOOGL at the put's last reading $%s" % last[0])
ok(abs(vo["bw"] - vn["bw"]) < 1e-9, "book delta identical old vs new (%.0f)" % vn["bw"])
ok(any(s.startswith("readings span") for s in vo["stale"]), "old warning: %s" % vo["stale"])
ok("GOOGL shares priced %s" % last[1][5:10] in vn["stale"] and not any("span" in s for s in vn["stale"]),
   "new warning names the shares: %s" % vn["stale"])
ok([s for s in vn["stale"] if not s.startswith(("GOOGL", "no reading on"))] == [] and
   [s for s in vo["stale"] if s.startswith("no reading on")] == [s for s in vn["stale"] if s.startswith("no reading on")],
   "nothing else named behind; the other warnings unchanged: %s" % vn["stale"])
fo = [re.sub(r"Readings: .*? -- check before acting\.", "R", f) for f in vo["flags"]]
fn = [re.sub(r"Readings: .*? -- check before acting\.", "R", f) for f in vn["flags"]]
ok(fo == fn and all("GOOGL shares priced" in f for f in vn["flags"] if "check before acting" in f),
   "advice flags identical apart from the readings wording (%d flags)" % len(fn))
oe, ne = vo["sh_entries"], vn["sh_entries"]
ok(json.dumps(oe, sort_keys=True) == json.dumps(ne, sort_keys=True), "advice.json shares entry identical old vs new")
paths = ["/thesis/" + i for i in ALL] + ["/today", "/book", "/watchlist", "/reference"]
o, lo = render(OLD_ENG, OLD_PG, DA, paths); nw, ln = render(NEW_ENG, NEW_PG, DA, paths)
ok(all(o[p][0] == 200 and nw[p][0] == 200 for p in paths), "every page renders, old and new (%d pages)" % len(paths))
ok("Traceback" not in lo + ln, "no tracebacks in the board's log")
same = [p for p in paths if p != "/reference"]
diff = [p for p in same if norm(o[p][1]) != norm(nw[p][1])]
if diff:
    import difflib
    for p in diff[:2]:
        for l in list(difflib.unified_diff(norm(o[p][1]).split("\n"), norm(nw[p][1]).split("\n"), lineterm="", n=0))[:12]: print("   DIFF", p, l[:180])
ok(not diff, "every page but Reference identical old vs new: %s" % diff)
ro, rn = norm(o["/reference"][1]), norm(nw["/reference"][1])
ok("snapshot's own reading of the shares" in rn and re.sub(r"; the price is the newer of .*?older than the page\.", ".", rn) == ro,
   "Reference: only the shares' price line added")
ok(dump(DA) == before, "no table changed by the board or the views")

# ---------------------------------------------------------------- B: a share reading stored
print("B. share readings stored (an older one and Friday 15:15:40)")
DB = fresh("b.db")
c = sqlite3.connect(DB)
c.execute("CREATE TABLE stock_checks (ticker TEXT NOT NULL, checked_at TEXT NOT NULL, price REAL NOT NULL, source TEXT, live INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (ticker, checked_at))")
NEWT = newest[:10] + "T15:15:40"
c.execute("INSERT INTO stock_checks VALUES ('GOOGL','2026-11-10T12:30:30',301.10,'ibkr',1)")
c.execute("INSERT INTO stock_checks VALUES ('GOOGL',?,294.60,'ibkr',1)", (NEWT,))
c.commit(); c.close()
before = dump(DB)
vo, vn = view(OLD_ENG, DB), view(NEW_ENG, DB)
s = vn["shares"][0]
ok(s["spot"] == 294.60 and s["as_of"] == NEWT and not s.get("price_note"), "new: GOOGL priced at the newest share reading $294.60, %s" % s["as_of"])
ok(vo["shares"][0]["spot"] == last[0], "old ignores the share readings (still $%s)" % last[0])
ok(abs(s["bw_delta"] - 100 * 294.60 * beta) < 1e-6, "beta-weighted delta 100 × 294.60 × %s = %.0f" % (beta, s["bw_delta"]))
ok(abs((vn["bw"] - vo["bw"]) - 100 * (294.60 - last[0]) * beta) < 1e-6, "book delta up by exactly the price change × 100 × beta (%.0f)" % (vn["bw"] - vo["bw"]))
ok(not any("GOOGL" in x for x in vn["stale"]), "the GOOGL warning is gone: %s" % vn["stale"])
e = vn["sh_entries"][0]
ok(e["price"] == 294.60 and e["price_as_of"] == NEWT, "advice.json shares entry: price %s as of %s" % (e["price"], e["price_as_of"]))
nb, _ = render(NEW_ENG, NEW_PG, DB, ["/today", "/book"])
t = H.unescape(re.sub(r"<[^>]+>", " ", nb["/today"][1]))
pnl = (294.60 - eb) * 100
want = "−$%s" % format(round(abs(pnl)), ",")
ok(want in t, "Today's Holding: GOOGL's P&L %s vs the effective basis" % want)
def grow(page):
    m = re.search(r'GOOGL shares ×\d+.*?</tr>', page, re.S); return m.group(0) if m else ""
ok(grow(nw["/today"][1]) and '<span class="dim">(11-05)</span>' in grow(nw["/today"][1]),
   "A (no share reading): Today's GOOGL row is dated (11-05)")
ok(grow(nb["/today"][1]) and '<span class="dim">(' not in grow(nb["/today"][1]),
   "B: Today's GOOGL row carries no older date (priced on the page's day)")
b = H.unescape(re.sub(r"<[^>]+>", " ", nb["/book"][1]))
ok("$36,825" in b, "Details: the shares' S&P delta $36,825 (100 × 294.60 × beta)")
ok(dump(DB) == before, "no table changed by the board or the views")

# ---------------------------------------------------------------- C: the snapshot reads the shares
print("C. the snapshot, IBKR stood in")
SNAP = r'''
import json, sys, sqlite3, os
from helm.cli import check_cmd as K
import helm.market_calendar as MC
MC.agent_should_run = lambda *a, **k: (True, "test")
seen = []
K.check_one = lambda pos, legs, **k: seen.append(pos["ticker"])
mode = sys.argv[1]
if mode == "ok":
    K.fetch_ibkr_underlying = lambda t: {"price": 297.333, "live": True, "source": "ibkr", "error": None}
elif mode == "raise":
    def f(t): raise RuntimeError("gateway gone")
    K.fetch_ibkr_underlying = f
# mode "real": the real reader with IBKR out of reach
K.cmd_snapshot(sys.argv[2:])
print(json.dumps({"seen": seen, "info": dict(K.SNAPSHOT_INFO)}))
'''
open(W + "/snap.py", "w").write(SNAP)
def snap(engdir, db, mode, *args):
    env = dict(os.environ, HELM_ROOT=engdir, HELM_DB=db, PYTHONPATH=engdir + ":/tmp/pylib", HOME=W + "/home", COLUMNS="200")
    r = subprocess.run([sys.executable, "-B", W + "/snap.py", mode] + list(args), cwd=engdir, env=env, capture_output=True, text=True, timeout=170)
    return r.returncode, r.stdout, r.stderr
def runs(db):
    c = sqlite3.connect(db); r = c.execute("select attempted, journaled from agent_runs order by rowid desc limit 1").fetchone(); c.close(); return r
def sc(db):
    c = sqlite3.connect(db)
    try: return c.execute("select ticker, price, live from stock_checks order by checked_at").fetchall()
    except sqlite3.OperationalError: return None
    finally: c.close()
DC = fresh("c.db"); b0 = dump(DC)
rc, out, err = snap(NEW_ENG, DC, "ok", "--all", "--silent")
j = json.loads(out.strip().split("\n")[-1]) if rc == 0 else {}
ok(rc == 0 and "shares: GOOGL 297.33" in out, "new snapshot: reads the shares (log line %r)" % [l for l in out.split("\n") if l.startswith("shares")])
ok(sc(DC) == [("GOOGL", 297.33, 1)], "one GOOGL share reading stored: %s" % sc(DC))
ok(j.get("info", {}).get("attempted") == len(ALL) == len(j.get("seen", [])) and runs(DC)[0] == len(ALL),
   "attempted counts the %d option positions only (info %s, run row %s)" % (len(ALL), j.get("info", {}).get("attempted"), runs(DC)))
chg = sorted(t for t, h in dump(DC).items() if b0.get(t) != h)
DC2 = fresh("c2.db"); b2 = dump(DC2)
rc, out, err = snap(OLD_ENG, DC2, "ok", "--all", "--silent")
ok(rc == 0 and sc(DC2) is None and "shares:" not in out, "old snapshot: no share reading (control)")
chg_old = sorted(t for t, h in dump(DC2).items() if b2.get(t) != h)
ok(chg == sorted(chg_old + ["stock_checks"]), "new snapshot changes the old one's tables %s plus stock_checks: %s" % (chg_old, chg))
DC3 = fresh("c3.db")
rc, out, err = snap(NEW_ENG, DC3, "raise", "--all", "--silent")
ok(rc == 0 and "shares: GOOGL FAILED: RuntimeError: gateway gone" in out and runs(DC3)[0] == len(ALL),
   "a failed quote is logged and costs the snapshot nothing")
ok(not sc(DC3), "nothing stored on a failed quote: %s" % sc(DC3))
DC4 = fresh("c4.db")
rc, out, err = snap(NEW_ENG, DC4, "real", "--all", "--silent")
ok(rc == 0 and "shares: GOOGL not stored: " in out and not sc(DC4) and runs(DC4)[0] == len(ALL),
   "the real reader with IBKR out of reach: not stored, said (%s)" % [l for l in out.split("\n") if l.startswith("shares")])
DC5 = fresh("c5.db")
rc, out, err = snap(NEW_ENG, DC5, "ok", "--paper", "--silent")
ok(rc == 0 and not sc(DC5), "a paper-only snapshot reads no shares")
DC6 = fresh("c6.db")
c = sqlite3.connect(DC6); c.execute("PRAGMA foreign_keys=ON")
c.execute("UPDATE positions SET status='CLOSED' WHERE status='OPEN'"); c.commit(); c.close()
rc, out, err = snap(NEW_ENG, DC6, "ok", "--all", "--silent")
ok(rc == 0 and sc(DC6) == [("GOOGL", 297.33, 1)], "no open option positions: the shares are still read")
print("\n%d checks, %d failed" % (n[0], len(fails)))
for f in fails: print("  FAILED:", f)
sys.exit(1 if fails else 0)
