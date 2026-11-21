"""s163: (1) advice.json gains premium_count (puttool.premium_count, advice.build); (2) the put list reads the broad
index funds whatever their IV rank and shows the best beside the list, display only (puttool index_rows / put_index,
cli/puts_cmd, todayview.puts_view; helm-pg templates/today.html, templates/reference.html).

Old and new are code directories, both copied through setup (HELM_PYTHON fixed). Old = the trees before the change
(rebuild them from the .bak-20261121-s163 files); new = the changed trees. Fresh DB copies from one VACUUM INTO copy
(S163BASE, default /tmp/s163/base.db); IBKR never called (the chain reader is replaced); the board started as
production starts it. Usage: TMPDIR=/tmp python3 -B verify_s163.py OLD_ENG OLD_PG NEW_ENG NEW_PG
The figures in check A ("69 of 102") are those of the 2026-11-20 database; on a later copy that one check will differ.
"""
import hashlib, html as H, json, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
W = os.environ.get("S163W", "/tmp/s163w"); BASE = os.environ.get("S163BASE", "/tmp/s163/base.db")
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
for d, fs in ((NEW_ENG, ["helm/puttool.py", "helm/advice.py", "helm/cli/puts_cmd.py", "helm/todayview.py"]),
              (NEW_PG, ["templates/today.html", "templates/reference.html"])):
    for f in fs: print("   ", f, hashlib.sha256(open(d + "/" + f, "rb").read()).hexdigest()[:12],
                       "(old %s)" % hashlib.sha256(open((OLD_ENG if d == NEW_ENG else OLD_PG) + "/" + f, "rb").read()).hexdigest()[:12])
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
def changed(b, a_):
    return sorted(t for t in set(b) | set(a_) if b.get(t) != a_.get(t))
PORT = [int(os.environ.get("S163PORT", "9360"))]
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
VIEW = ("import json, inspect, sqlite3; from helm import todayview as T, puttool as P, pmcctool as PM\n"
        "from helm.config import get_active_account, DB_PATH\n"
        "c = sqlite3.connect(str(DB_PATH)); c.row_factory = sqlite3.Row\n"
        "v = T.view(account_id=get_active_account())\n"
        "v['puts'] = T.puts_view(P.latest(c), book=v['figures']); v['pmcc'] = T.pmcc_view(PM.latest(c), book=v['figures'])\n"
        "print(json.dumps(v, default=str))\n")
def view(engdir, db):
    return json.loads(eng(engdir, db, VIEW).strip().split("\n")[-1])
def norm(b):
    b = re.sub(r"\?v=\d+", "?v=X", b)
    b = re.sub(r'(clip|cp|g)[-_]?[0-9a-f]{6,}', 'ID', b)
    return re.sub(r'(["#])([a-z]{1,3})\d{3,}(["\)])', r'\1\2ID\3', b)
REF_NEW = re.compile(r'<dt id="index-fund">Index fund</dt><dd>.*?</dd>\n', re.S)
IDX = re.compile(r'<tr class="sub"><td colspan="15">Index fund <a class="q" href="/reference#index-fund" title="How this is worked out">\?</a>'
                 r'(?: · IV rank (\d+) · bar (\d+))?</td></tr>(<tr>.*?</tr>)', re.S)
NOPUT = '<tr class="sub"><td colspan="15">no put passed every rule on the last run</td></tr>'
def usd(x): return "$" + "{:,.0f}".format(abs(x))

# ---------------------------------------------------------------- A: the live copy as it is (no index rows recorded)
print("A. the live copy as it is")
DA = fresh("a.db"); before = dump(DA)
cx = sqlite3.connect(DA); cx.row_factory = sqlite3.Row
vo, vn = view(OLD_ENG, DA), view(NEW_ENG, DA)
ok(vn["puts"].get("index", "missing") is None, "A: a run recorded before the change has no index row")
vn2 = json.loads(json.dumps(vn)); vn2["puts"].pop("index", None)
ok(json.dumps(vn2, sort_keys=True) == json.dumps(vo, sort_keys=True), "A: Today's view identical old vs new apart from puts.index")
ALL = [r["id"] for r in cx.execute("select id from positions where book='REAL' and status='OPEN' order by ticker")]
paths = ["/today", "/reference", "/book", "/watchlist"] + ["/thesis/" + i for i in ALL]
o, lo = render(OLD_ENG, OLD_PG, DA, paths); nw, ln = render(NEW_ENG, NEW_PG, DA, paths)
ok(all(o[p][0] == 200 and nw[p][0] == 200 for p in paths), "A: every page renders, old and new (%d pages)" % len(paths))
ok("Traceback" not in lo + ln, "A: no tracebacks in the board's log")
diff = [p for p in paths if p != "/reference" and norm(o[p][1]) != norm(nw[p][1])]
ok(not diff, "A: every page but Reference identical old vs new, Today included %s" % diff)
ok(len(REF_NEW.findall(nw["/reference"][1])) == 1 and REF_NEW.sub("", norm(nw["/reference"][1])) == norm(o["/reference"][1]),
   "A: Reference identical apart from the one new entry")
ok("bar (50)" in H.unescape(nw["/reference"][1]) and "delta 0.25" in H.unescape(nw["/reference"][1]).split('id="index-fund"')[1][:400],
   "A: the Reference entry reads the bar and the delta band from the engine")
# advice.json
ao, an = W + "/adv-old", W + "/adv-new"
for d in (ao, an):
    shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
so = eng(OLD_ENG, DA, argv=["advise", "--json"], advdir=ao); sn = eng(NEW_ENG, DA, argv=["advise", "--json"], advdir=an)
ok("advice.json: ok" in so and "advice.json: ok" in sn, "A: helm advise --json says ok, old and new")
jo, jn = json.load(open(ao + "/advice.json")), json.load(open(an + "/advice.json"))
pcn = jn.pop("premium_count", None)
for j in (jo, jn): j.pop("generated_at")
ok(pcn is not None and jo == jn, "A: advice.json identical old vs new apart from the new premium_count block")
ok(list(json.load(open(an + "/advice.json")).keys())[-2:] == ["premium_count", "note"] and jn["schema_version"] == 1,
   "A: the block sits after limits; the schema number is unchanged")
sto, stn = json.load(open(ao + "/advice-status.json")), json.load(open(an + "/advice-status.json"))
ok(sorted(sto) == sorted(stn) and stn["ok"] is True and stn["status"] == "ok", "A: advice-status.json has the same keys, ok true")
# the expected count, by SQL of our own
day = cx.execute("select max(date) from iv_history").fetchone()[0]
funds = {r[0] for r in cx.execute("select ticker from approved_funds")}
names = {r[0] for r in cx.execute("select ticker from watchlist where coalesce(active,0)=1")} | funds
grade = {}
for t, g in cx.execute("select ticker, grade from ownership_quality order by date, updated_at"): grade[t] = g
exp = {"checked": 0, "rich": 0, "no_reading": 0, "stocks": {"checked": 0, "rich": 0}, "funds": {"checked": 0, "rich": 0}}
for t in sorted(names):
    if not (t in funds or grade.get(t) in ("A", "B", "C")): continue
    r = cx.execute("select iv_rank from iv_history where ticker=? and date=?", (t, day)).fetchone()
    if not r or r[0] is None: exp["no_reading"] += 1; continue
    for d in (exp, exp["funds" if t in funds else "stocks"]):
        d["checked"] += 1; d["rich"] += int(r[0] >= 50)
got = {k: pcn.get(k) for k in exp} if pcn else None
ok(got == exp and pcn["as_of"] == day and pcn["bar"] == 50.0 and "error" not in pcn, "A: premium_count = our own SQL: %s as of %s" % (got, pcn and pcn.get("as_of")))
ok((exp["rich"], exp["checked"], exp["stocks"], exp["funds"]) == (69, 102, {"checked": 78, "rich": 51}, {"checked": 24, "rich": 18}),
   "A: and equals the figures given to Russ (69 of 102; stocks 51 of 78; funds 18 of 24) -- the 2026-11-20 database only")
at50 = cx.execute("select count(*) from iv_history where date=? and iv_rank=50", (day,)).fetchone()[0]
print("   names exactly at the bar on %s: %d" % (day, at50))
ok(dump(DA) == before, "A: no table changed (views, pages, advise --json)")
cx.close()

# ---------------------------------------------------------------- B: a put run with the chain reader replaced
print("B. a put run, IBKR replaced by a fixed chain; SPY and IWM put under the bar")
FAKE = '''
import sys, datetime
from helm import puttool as P
def fake(t, today=None):
    spot = 50.0 + (sum(map(ord, t)) * 7) % 400
    k = float(round(spot * 0.93))
    mid = round(k * 0.010 * (1 + (ord(t[0]) % 7) / 10.0), 2)
    if t == "SPY": mid = round(k * 0.030, 2)          # the best index put: under the bar
    exp = (datetime.date.today() + datetime.timedelta(days=35)).isoformat()
    row = {"source": "ibkr", "bid": round(mid * 0.99, 2), "ask": round(mid * 1.01, 2), "mid": mid, "delta": 0.27, "dte": 35,
           "spread_pct": 12.0 if t == "DIA" else 2.0, "strike": k, "expiration": exp, "gamma": 0.01, "theta": -0.02, "vega": 0.1, "iv": 0.3}
    return [row], spot, None
d = list(P.build.__defaults__); assert callable(d[1]); d[1] = fake; P.build.__defaults__ = tuple(d)
sys.argv = ["helm", "puts", "--force"] + ARGS
from helm.cli import puts_cmd; puts_cmd.run()
'''
def prep(name):
    p = fresh(name); c = sqlite3.connect(p)
    day = c.execute("select max(date) from iv_history").fetchone()[0]
    c.execute("update iv_history set iv_rank=35 where ticker='SPY' and date=?", (day,))
    c.execute("update iv_history set iv_rank=20 where ticker='IWM' and date=?", (day,))
    c.execute("update iv_history set iv_rank=80 where ticker in ('QQQ','DIA') and date=?", (day,))
    c.commit(); c.close(); return p
DBo = prep("b-old.db"); DBn = prep("b-new.db")
bo, bn = dump(DBo), dump(DBn)
to = eng(OLD_ENG, DBo, code="ARGS=['--why']\n" + FAKE); time.sleep(1.1)
tn = eng(NEW_ENG, DBn, code="ARGS=['--why']\n" + FAKE)
ao_, an_ = dump(DBo), dump(DBn)
ok(changed(bo, ao_) == ["put_candidates", "put_runs"], "B: the old run changes put_runs and put_candidates only: %s" % changed(bo, ao_))
ok(changed(bn, an_) == ["put_candidates", "put_index", "put_runs"], "B: the new run changes those and the new put_index only: %s" % changed(bn, an_))
def last(p):
    c = sqlite3.connect(p); c.row_factory = sqlite3.Row
    r = c.execute("select * from put_runs order by run_at desc, rowid desc limit 1").fetchone()
    cand = [tuple(x)[1:] for x in c.execute("select * from put_candidates where run_id=? order by rank", (r["id"],))]
    idx = [dict(x) for x in c.execute("select * from put_index where run_id=? order by ticker", (r["id"],))] if \
        c.execute("select 1 from sqlite_master where name='put_index'").fetchone() else None
    c.close(); return r, cand, idx
ro, co, io_ = last(DBo); rn, cn, ix = last(DBn)
ok(len(cn) > 20 and co == cn, "B: every candidate row identical old vs new -- names, ranks, contracts, sizes (%d rows)" % len(cn))
ok(ro["rejects_json"] == rn["rejects_json"] and (ro["n_universe"], ro["n_checked"], ro["n_found"], ro["sleeve_free"]) ==
   (rn["n_universe"], rn["n_checked"], rn["n_found"], rn["sleeve_free"]), "B: rejects and the run's counts identical old vs new")
rej = json.loads(rn["rejects_json"]); names_c = [c[0] for c in cn]
X = {x["ticker"]: x for x in ix or []}
ok(io_ is None and sorted(X) == ["DIA", "IWM", "QQQ", "SPY"], "B: one put_index row per broad index fund; none on the old run")
ok(rej.get("SPY") == "IV rank 35, below 50" and rej.get("IWM") == "IV rank 20, below 50" and "SPY" not in names_c and "IWM" not in names_c,
   "B: SPY and IWM stay off the list with the IV-rank reason")
ok(X["SPY"]["bar_met"] == 0 and X["SPY"]["strike"] and X["SPY"]["rank"] is None and X["SPY"]["iv_rank"] == 35
   and X["IWM"]["bar_met"] == 0 and X["IWM"]["strike"], "B: both are read and stored with their contract, bar not met, no rank")
qc = [c for c in cn if c[0] == "QQQ"]          # a candidate tuple is CAND_COLS: strike 6, mid 10, size_n 22
ok(qc and X["QQQ"]["bar_met"] == 1 and X["QQQ"]["rank"] == qc[0][1] and (X["QQQ"]["strike"], X["QQQ"]["mid"], X["QQQ"]["size_n"]) == (qc[0][6], qc[0][10], qc[0][22]),
   "B: QQQ (over the bar) is its list row, with its rank %s" % (qc and qc[0][1]))
ok(X["DIA"]["strike"] is None and "spread" in (X["DIA"]["note"] or "") and rej.get("DIA") == X["DIA"]["note"], "B: DIA keeps the list's reason (spread), no contract")
ok(abs(X["SPY"]["ann_return"] - X["SPY"]["mid"] / X["SPY"]["strike"] * 365 / 35) < 1e-9 and X["SPY"]["collateral"] == X["SPY"]["strike"] * 100
   and X["SPY"]["size_binding"] in (None, "sleeve_total", "sleeve_name", "sleeve_sector"), "B: SPY's yearly return and collateral are the list's arithmetic; sized by the limits (%s, %s)" % (X["SPY"]["size_n"], X["SPY"]["size_binding"]))
ok("Index fund" not in to and "Index fund" in tn, "B: the Terminal list shows the Index fund section on new only")
sec = tn.split("Index fund", 1)[1].split("Left off")[0] if "Index fund" in tn else ""
ok(len(re.findall(r"^\s*(?:—|\d+)\s+(SPY|QQQ|IWM|DIA)\b", sec, re.M)) == 1 and re.search(r"^\s*—\s+SPY\b.*\b35\b", sec, re.M) and "bar is 50" in sec,
   "B: one row -- SPY, the highest yearly return of the four -- with — for its rank and IV rank 35")
ta = eng(NEW_ENG, DBn, argv=["puts", "--last", "--all"])
seca = ta.split("Index fund", 1)[1].split("Left off")[0] if "Index fund" in ta else ""
ok([m for m in re.findall(r"^\s*(?:—|\d+)\s+(SPY|QQQ|IWM|DIA)\b", seca, re.M)] == [x["ticker"] for x in sorted((x for x in ix if x["strike"]), key=lambda x: -x["ann_return"])]
   and re.search(r"DIA\s+.*spread", seca), "B: --all shows every index fund with a contract, best first, and DIA's reason")
ok("Index fund" in tn and to.split("Left off")[0].split("New puts", 1)[1].split("\n", 1)[1].strip() == tn.split("Index fund")[0].split("New puts", 1)[1].split("\n", 1)[1].strip(),
   "B: the Terminal list above the Index fund section is identical old vs new")
# the board on the new run
vnb = view(NEW_ENG, DBn); vob = view(OLD_ENG, DBn)
I = vnb["puts"].get("index") or {}
ok(I and I["ticker"] == "SPY" and I["rank"] == "—" and I["iv_rank"] == 35 and I["bar"] == 50.0 and I["grade"] == "FUND"
   and I["collateral"] == X["SPY"]["collateral"] and abs(I["rate"] - X["SPY"]["ann_return"]) < 1e-12 and I["fits"] == bool(X["SPY"]["size_n"]),
   "B: Today's view carries the index row from put_index: %s" % (I and {k: I[k] for k in ("ticker", "rank", "put", "iv_rank", "limits")}))
cxn = sqlite3.connect(DBn)
beta = cxn.execute("select beta from watchlist where ticker='SPY'").fetchone(); cxn.close()
m = I.get("market"); b_ = 1.0 if (not beta or beta[0] is None) else float(beta[0])
ok(bool(m) and X["SPY"]["strike"] and abs(m["add"] - 0.27 * 100 * (X["SPY"]["size_n"] or 1) * X["SPY"]["spot"] * b_) < 1e-6 and m["one"] == (not X["SPY"]["size_n"]),
   "B: the index row's book-delta cell is the list's arithmetic (+$%.0f)" % (m["add"] if m else 0))
vnb2 = json.loads(json.dumps(vnb)); vnb2["puts"].pop("index", None)
ok(json.dumps(vnb2, sort_keys=True) == json.dumps(vob, sort_keys=True), "B: the rest of Today's view identical, old engine vs new on the same database")
pb = ["/today", "/reference"]
nb, lnb = render(NEW_ENG, NEW_PG, DBn, pb); ob, lob = render(OLD_ENG, OLD_PG, DBn, pb)
ok(nb["/today"][0] == 200 and ob["/today"][0] == 200 and "Traceback" not in lnb + lob, "B: Today renders, new and old code, on a database that has put_index")
tnew, told = norm(nb["/today"][1]), norm(ob["/today"][1])
mm = IDX.findall(tnew)
ok(len(mm) == 1 and mm[0][0] == "35" and mm[0][1] == "50", "B: Today has one Index fund line: IV rank 35 · bar 50")
row = H.unescape(mm[0][2]) if mm else ""
cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
ok(len(cells) == 15 and I and cells[0] == "—" and cells[1].startswith("SPY") and cells[2:5] == [I["held"] or "—", "FUND", I["put"]] and usd(X["SPY"]["collateral"]) in cells
   and ("%.0f%%" % (100 * X["SPY"]["ann_return"])) in "".join(cells) and cells[-1] == I["limits"], "B: its cells: — · SPY · held · FUND · the put · %%/yr · collateral · limits: %s" % cells)
ok(IDX.sub("", tnew) == told, "B: Today minus that line is the old page, byte for byte")
if IDX.sub("", tnew) != told:
    import difflib
    for l in list(difflib.unified_diff(told.split("\n"), IDX.sub("", tnew).split("\n"), lineterm="", n=0))[:8]: print("   DIFF", l[:220])
hrefs = set(re.findall(r'href="/reference#([a-z0-9-]+)"', nb["/today"][1])); ids = set(re.findall(r'<dt id="([a-z0-9-]+)"', nb["/reference"][1]))
ok("index-fund" in hrefs and hrefs <= ids, "B: every '?' on Today lands on a Reference entry, the new one included")
ok(dump(DBn) == an_, "B: showing the list and the pages changed no table")

# ---------------------------------------------------------------- C: a run where no put passed
print("C. a recorded run with no put on the list")
DC = fresh("c.db")
eng(NEW_ENG, DC, code="import sqlite3; from helm import puttool as P; from helm.config import DB_PATH\n"
    "c = sqlite3.connect(str(DB_PATH)); P.ensure_tables(c)\n"
    "run = {'id': 'puts_s163none', 'run_at': '2099-01-01 11:00:00', 'rule_version': P.RULE_VERSION, 'ivr_date': '2026-11-20', 'market_open': 1,\n"
    "       'n_universe': 102, 'n_checked': 0, 'n_found': 0, 'sleeve_free': 1000.0, 'rejects': {}, 'candidates': [],\n"
    "       'index': [{'ticker': 'DIA', 'bar_met': 0, 'note': None, 'rank': None, 'grade': 'FUND', 'sector': 'US equity index', 'expiry': '2026-12-31',\n"
    "                  'dte': 40, 'strike': 460.0, 'spot': 486.0, 'bid': 4.0, 'ask': 4.2, 'mid': 4.1, 'spread_pct': 4.9, 'delta': 0.27, 'iv_rank': 41.0,\n"
    "                  'ann_return': 4.1 / 460 * 365 / 40, 'collateral': 46000.0, 'earnings': 'fund -- no earnings', 'rest_price': 2.05,\n"
    "                  'size_n': 1, 'size_binding': None, 'size_note': ''}]}\n"
    "P.record(c, run)\n")
nc_, lnc = render(NEW_ENG, NEW_PG, DC, ["/today"]); oc_, loc = render(OLD_ENG, OLD_PG, DC, ["/today"])
tc = norm(nc_["/today"][1])
ok(nc_["/today"][0] == 200 and NOPUT in tc and len(IDX.findall(tc)) == 1 and IDX.findall(tc)[0][0] == "41" and ">DIA" in IDX.findall(tc)[0][2],
   "C: with no put on the list, Today says so in the table and still shows the index line (DIA, IV rank 41)")
ok('<div class="dim">no put passed every rule on the last run</div>' in oc_["/today"][1] and oc_["/today"][0] == 200, "C: the old page on the same database shows its plain message")
cx = sqlite3.connect(DC); cx.execute("delete from put_index where run_id='puts_s163none'"); cx.commit(); cx.close()
nc2, _ = render(NEW_ENG, NEW_PG, DC, ["/today"])
ok(norm(nc2["/today"][1]) == norm(oc_["/today"][1]), "C: no put and no index row -> the page is the old page")

print("\nverify_s163: %d checks, %s" % (n[0], "PASS" if not fails else "%d FAIL" % len(fails)))
sys.exit(1 if fails else 0)
