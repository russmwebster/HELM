"""s164: the display-only heads-up when a sold put's buy-back reaches twice the premium (helm/advice.py premium_row,
helm/todayview.py, helm/boardview.py; helm-pg templates today.html, put_card.html, reference.html).
Old and new are code directories copied through setup (HELM_PYTHON fixed). Fresh DB copies from one VACUUM INTO copy
(S164BASE). IBKR is never called (only pages and `helm advise` run). The board started as production starts it.
Usage: TMPDIR=/tmp python3 -B verify_s164.py OLD_ENG OLD_PG NEW_ENG NEW_PG     (exit 1 on any failure)"""
import hashlib, html as H, json, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
W = os.environ.get("S164W", "/tmp/s164w"); BASE = os.environ.get("S164BASE", "/tmp/s164/helm_copy.db")
ENG_IGN = ("data", "archive", "_s1*", "_to_delete", "*.bak-*", "__pycache__", ".git", "docs", "_records", "logs", "Claude outputs", "_study_screen")
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
a = [os.path.abspath(x) for x in sys.argv[1:5]]
OLD_ENG, OLD_PG = tree(a[0], W + "/eng-old", ENG_IGN), tree(a[1], W + "/pg-old", PG_IGN)
NEW_ENG, NEW_PG = tree(a[2], W + "/eng-new", ENG_IGN), tree(a[3], W + "/pg-new", PG_IGN)
print("code loaded from:", a[2], a[3])
for d, o, fs in ((NEW_ENG, OLD_ENG, ["helm/advice.py", "helm/todayview.py", "helm/boardview.py"]),
                 (NEW_PG, OLD_PG, ["templates/today.html", "templates/put_card.html", "templates/reference.html"])):
    for f in fs: print("   ", f, hashlib.sha256(open(d + "/" + f, "rb").read()).hexdigest()[:12],
                       "(old %s)" % hashlib.sha256(open(o + "/" + f, "rb").read()).hexdigest()[:12])
fails, n = [], [0]
def ok(c, m):
    n[0] += 1; print(("  ok   " if c else "  FAIL ") + m)
    if not c: fails.append(m)
def fresh(name):
    p = W + "/" + name
    for x in (p, p + "-wal", p + "-shm"):
        if os.path.exists(x): os.remove(x)
    shutil.copy(BASE, p); return p
def dump(p):
    c = sqlite3.connect(p); out = {}
    for (t,) in c.execute("select name from sqlite_master where type='table' order by name"):
        h = hashlib.sha256(); h.update(repr([r[1] for r in c.execute('pragma table_info("%s")' % t)]).encode())
        for r in c.execute('select * from "%s" order by rowid' % t): h.update(repr(r).encode())
        out[t] = h.hexdigest()
    c.close(); return out
PORT = [int(os.environ.get("S164PORT", "9460"))]
def render(eng, pg, db, paths):
    PORT[0] += 1; port = str(PORT[0])
    cx = sqlite3.connect(db)
    open(W + "/home/.helm_profile", "w").write(cx.execute("select id from accounts").fetchone()[0]); cx.close()
    env = dict(os.environ, HOME=W + "/home", HELM_ROOT=eng, HELM_DB=db, PORT=port, HELM_PG_ENGINE="real",
               PYTHONPATH="/tmp/pylib", HELM_ADVICE_DIR=W + "/advice-board", PYTHONHASHSEED="0", TMPDIR="/tmp")
    os.makedirs(W + "/advice-board", exist_ok=True)
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
def eng(engdir, db, code=None, argv=None, advdir=None):
    env = dict(os.environ, HELM_ROOT=engdir, HELM_DB=db, PYTHONPATH=engdir + ":/tmp/pylib", HOME=W + "/home",
               HELM_ADVICE_DIR=advdir or (W + "/advice-out"), COLUMNS="250", TMPDIR="/tmp")
    cmd = [sys.executable, "-B", "-c", code] if code else [sys.executable, "-B", "helm.py"] + argv
    r = subprocess.run(cmd, cwd=engdir, env=env, capture_output=True, text=True, timeout=170)
    if r.returncode: print(r.stdout[-1500:], r.stderr[-2500:])
    return r.stdout
VIEW = ("import json; from helm import todayview as T\nfrom helm.config import get_active_account\n"
        "print(json.dumps(T.view(account_id=get_active_account()), default=str))\n")
def view(engdir, db): return json.loads(eng(engdir, db, VIEW).strip().split("\n")[-1])
def norm(b):
    b = re.sub(r"\?v=\d+", "?v=X", b)
    b = re.sub(r'(clip|cp|g)[-_]?[0-9a-f]{6,}', 'ID', b)
    return re.sub(r'(["#])([a-z]{1,3})\d{3,}(["\)])', r'\1\2ID\3', b)
MARK = re.compile(r' <span class="amber" title="([^"]*)">2× premium</span> <a class="q" href="/reference#premium-2x" title="How this is worked out">\?</a>')
Q = ' <a class="q" href="/reference#premium-2x" title="How this is worked out">?</a>'
HOTROW = re.compile(r'<tr><td>Buy-back at 2× the premium' + re.escape(Q) + r'</td><td class="t amber">reached</td><td class="t">([^<]*)</td></tr>')
NEXTROW = re.compile(r'<tr><td>Buy-back at 2× the premium' + re.escape(Q) + r'</td><td class="t">([^<]*)</td><td class="t">([^<]*)</td></tr>')
REF = re.compile(r'<dt id="premium-2x">.*?</dd>\n', re.S)
def u(s): return H.unescape(s)

# ---------------------------------------------------------------- A: the live copy as it is
print("A. the live copy as it is")
DA = fresh("a.db"); before = dump(DA)
cx = sqlite3.connect(DA); cx.row_factory = sqlite3.Row
puts = [dict(r) for r in cx.execute("select p.id, p.ticker, l.open_price, l.id leg from positions p join legs l on l.position_id=p.id "
        "where p.book='REAL' and p.status='OPEN' and p.strategy='CSP' and l.status='OPEN' order by p.ticker")]
for q in puts:
    q["mid"] = cx.execute("select current_price from leg_checks where leg_id=? order by checked_at desc limit 1", (q["leg"],)).fetchone()[0]
    q["reached"] = q["mid"] >= 2 * q["open_price"] - 1e-9
print("   open puts:", [(q["ticker"], q["open_price"], q["mid"], q["reached"]) for q in puts])
other = [r[0] for r in cx.execute("select id from positions where book='REAL' and status='OPEN' and strategy!='CSP' order by ticker")]
closed = [r[0] for r in cx.execute("select id from positions where book='REAL' and status='CLOSED' and strategy in ('CSP','COVERED_CALL') "
          "order by closed_at desc limit 3")]
vo, vn = view(OLD_ENG, DA), view(NEW_ENG, DA)
def strip(v):
    v = json.loads(json.dumps(v))
    for k in ("decisions", "holding"):
        for r in v.get(k) or []: r.pop("heads", None); r.pop("heads_reading", None)
    return v
ok(json.dumps(strip(vn), sort_keys=True) == json.dumps(vo, sort_keys=True), "A: Today's view identical old vs new apart from the two new fields")
marked = sorted(r["ticker"] for k in ("decisions", "holding") for r in vn[k] if r.get("heads"))
ok(marked == sorted(q["ticker"] for q in puts if q["reached"]) and marked, "A: Today's view marks exactly the puts past twice: %s" % marked)
ok(all(r.get("heads") is None for k in ("decisions", "holding") for r in vn[k] if r["strategy"] != "CSP"), "A: no mark on anything but a put")
paths = ["/today", "/reference", "/book", "/watchlist"] + ["/thesis/" + q["id"] for q in puts] + ["/thesis/" + i for i in other + closed]
o, lo = render(OLD_ENG, OLD_PG, DA, paths); nw, ln = render(NEW_ENG, NEW_PG, DA, paths)
ok(all(o[p][0] == 200 and nw[p][0] == 200 for p in paths), "A: every page renders, old and new (%d pages)" % len(paths))
ok("Traceback" not in lo + ln, "A: no tracebacks in the board's log")
same = [p for p in paths if p not in ["/today", "/reference"] + ["/thesis/" + q["id"] for q in puts]]
diff = [p for p in same if norm(o[p][1]) != norm(nw[p][1])]
ok(not diff, "A: Book, Watchlist, PMCC, share and closed cards identical old vs new (%d pages) %s" % (len(same), diff))
t = nw["/today"][1]; marks = MARK.findall(t)
ok(len(marks) == len(marked) and MARK.sub("", norm(t)) == norm(o["/today"][1]), "A: Today identical apart from %d marks" % len(marks))
ok(sorted(u(m) for m in marks) == sorted("mid $%.2f, sold $%.2f" % (q["mid"], q["open_price"]) for q in puts if q["reached"]),
   "A: each mark carries the mid and the sale price: %s" % [u(m) for m in marks])
for q in puts:
    c = nw["/thesis/" + q["id"]][1]; rows = HOTROW.findall(c) if q["reached"] else NEXTROW.findall(c)
    base = HOTROW.sub("", NEXTROW.sub("", norm(c)))
    if rows and q["reached"]:   # the one new hot row also counts in the heading: "N fired or near"
        base = re.sub(r"(\d+) fired or near", lambda m: "%d fired or near" % (int(m.group(1)) - 1), base, count=1)
    ok(len(rows) <= 1 and base == norm(o["/thesis/" + q["id"]][1]) and (len(rows) == 1 or not q["reached"]),
       "A: %s card identical apart from the one new row: %s" % (q["ticker"], rows))
    if q["reached"]:
        ok(u(rows[0]) == "mid $%.2f, sold $%.2f" % (q["mid"], q["open_price"]), "A: %s reads 'reached' with its prices" % q["ticker"])
r = nw["/reference"][1]
ok(len(REF.findall(r)) == 1 and REF.sub("", norm(r)) == norm(o["/reference"][1]), "A: Reference identical apart from the one new entry")
ok("Buy-back at 2× the premium" in u(r) and "display only" in u(r).split('id="premium-2x"')[1][:300], "A: the entry reads the multiple from the engine")
ao, an = W + "/adv-old", W + "/adv-new"
for d in (ao, an): shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
so = eng(OLD_ENG, DA, argv=["advise", "--json"], advdir=ao); sn = eng(NEW_ENG, DA, argv=["advise", "--json"], advdir=an)
ok("advice.json: ok" in so and "advice.json: ok" in sn, "A: helm advise --json says ok, old and new")
jo, jn = json.load(open(ao + "/advice.json")), json.load(open(an + "/advice.json"))
for j in (jo, jn): j.pop("generated_at")
ok(jo == jn, "A: the Book's advice.json identical old vs new")
to_, tn = eng(OLD_ENG, DA, argv=["advise"]), eng(NEW_ENG, DA, argv=["advise"])
ok(to_ == tn and to_.strip(), "A: helm advise's own words identical old vs new")
cx.close()
ok(dump(DA) == before, "A: no table changed by any of it")

# ---------------------------------------------------------------- B: a put not tested, under and at the line
print("B. a put not tested (Pfizer moved above its strike), just under and exactly at twice the premium")
pf = next((q for q in puts if q["ticker"] == "PFE"), None)
if pf is None:
    ok(False, "B: no open Pfizer put on this copy")
else:
    for mid, reached in ((round(2 * pf["open_price"] - 0.01, 2), False), (round(2 * pf["open_price"], 2), True)):
        DB = fresh("b.db"); c = sqlite3.connect(DB)
        ck = c.execute("select id from checks where position_id=? and data_quality='GOOD' order by checked_at desc limit 1", (pf["id"],)).fetchone()[0]
        c.execute("update checks set spot_price=? where id=?", (30.0, ck))
        lk = c.execute("select rowid from leg_checks where leg_id=? order by checked_at desc limit 1", (pf["leg"],)).fetchone()[0]
        c.execute("update leg_checks set current_price=?, current_bid=?, current_ask=?, delta=? where rowid=?", (mid, mid - .02, mid + .02, -0.2, lk))
        c.execute("update legs set contracts=10 where id=?", (pf["leg"],))   # off the name limit, so the card lists its next lines
        c.commit(); c.close()
        v = view(NEW_ENG, DB); row = next(r for k in ("decisions", "holding") for r in v[k] if r["ticker"] == "PFE")
        pg, lg = render(NEW_ENG, NEW_PG, DB, ["/today", "/thesis/" + pf["id"]])
        card, today = pg["/thesis/" + pf["id"]][1], pg["/today"][1]
        tm = [m for m in MARK.findall(today) if u(m).startswith("mid $%.2f," % mid)]
        if reached:
            ok(row.get("heads") == "2× premium" and len(tm) == 1, "B: at $%.2f (exactly twice) Today marks Pfizer" % mid)
            ok(len(HOTROW.findall(card)) == 1 and "<th>Next line</th>" in card, "B: the card shows 'reached' and still lists its next lines")
        else:
            ok(row.get("heads") is None and not tm, "B: at $%.2f (a cent under twice) no mark on Today" % mid)
            nx = NEXTROW.findall(card)
            ok(len(nx) == 1 and u(nx[0][1]) == "buy-back $%.2f" % (2 * pf["open_price"]) and not HOTROW.findall(card),
               "B: under the line the card lists it as a next line with its price: %s" % nx)
        ok(row["strategy"] == "CSP" and "Traceback" not in lg, "B: renders cleanly")

print("\n%d checks, %d failed" % (n[0], len(fails)))
for f in fails: print("  FAIL:", f)
sys.exit(1 if fails else 0)
