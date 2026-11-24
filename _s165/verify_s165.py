"""s165: the bull put spread list (helm/spreadtool.py, helm/cli/spreads_cmd.py, `helm spreads` in helm.py,
sleeve.size's wording for a spread). Old and new are engine trees, copied through setup (HELM_PYTHON fixed);
the board tree is the same for both. Fresh DB copies from one VACUUM INTO copy (S165BASE); IBKR never
called (the chain reader raises if touched; chains come from _s165/fixtures.py); the board started as
production starts it.
Usage: TMPDIR=/tmp python3 -B verify_s165.py OLD_ENG NEW_ENG PG"""
import hashlib, json, math, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
W = os.environ.get("S165W", "/tmp/s165w"); BASE = os.environ.get("S165BASE", "/tmp/s165/base.db")
ENG_IGN = ("data", "archive", "_s1*", "_to_delete", "*.bak-*", "__pycache__", ".git", "docs", "_records", "logs",
           "Claude outputs", "_study_screen")
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
a = [os.path.abspath(x) for x in sys.argv[1:4]]
OLD, NEW, PG = tree(a[0], W + "/eng-old", ENG_IGN), tree(a[1], W + "/eng-new", ENG_IGN), tree(a[2], W + "/pg", PG_IGN)
print("code loaded from:", a[1])
for f in ("helm/spreadtool.py", "helm/cli/spreads_cmd.py", "helm/sleeve.py", "helm.py"):
    o = OLD + "/" + f
    print("   ", f, hashlib.sha256(open(NEW + "/" + f, "rb").read()).hexdigest()[:12],
          "(old %s)" % (hashlib.sha256(open(o, "rb").read()).hexdigest()[:12] if os.path.exists(o) else "none"))
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
def env_for(engdir, db):
    return dict(os.environ, HELM_ROOT=engdir, HELM_DB=db, PYTHONPATH=engdir + ":" + HERE + ":/tmp/pylib",
                HOME=W + "/home", HELM_ADVICE_DIR=W + "/advice-out", COLUMNS="250", TMPDIR="/tmp", PYTHONHASHSEED="0")
def eng(engdir, db, code=None, argv=None, extra=None):
    e = env_for(engdir, db); e.update(extra or {})
    cmd = [sys.executable, "-B", "-c", code] if code else [sys.executable, "-B", "helm.py"] + argv
    r = subprocess.run(cmd, cwd=engdir, env=e, capture_output=True, text=True, timeout=170)
    if r.returncode and not (argv and "--selftest" in argv): print(r.stdout[-1500:], r.stderr[-2500:])
    return r.stdout, r.returncode
RUN = ("import json, sqlite3, sys\nfrom datetime import date\nimport fixtures as F\n"
       "from helm.cli import open_cmd as oc\n"
       "def _no(*a, **k): raise SystemExit('IBKR touched')\n"
       "oc.fetch_chain_from_ibkr = _no\n"
       "from helm import spreadtool as SP\nfrom helm.config import DB_PATH\n"
       "c = sqlite3.connect(str(DB_PATH)); c.row_factory = sqlite3.Row\n"
       "r = SP.build(c, date(2026, 11, 23), TICKERS, fetch=F.fetch, db=str(DB_PATH))\n"
       "SP.record(c, r, 'harness')\n"
       "print(json.dumps(r, default=str))\n")
def run_build(engdir, db, tickers=None, extra=None):
    out, rc = eng(engdir, db, RUN.replace("TICKERS", repr(tickers)), extra=extra)
    return json.loads(out.strip().split("\n")[-1]) if rc == 0 else None

sys.path.insert(0, HERE)
os.environ["HELM_DB"] = BASE
import fixtures as F

# ---- A. the self-test, run as Russ would
out, rc = eng(NEW, fresh("a.db"), argv=["spreads", "--selftest"])
ok(rc == 0 and "0 failed" in out, "A. helm spreads --selftest passes (%s)" % (out.strip().split("\n")[-1] if out else rc))

# ---- B. a full run on a copy: only the spread tables change
db = fresh("b.db"); before = dump(db)
r = run_build(NEW, db)
ok(r is not None, "B. a full run completes with the chain reader switched off")
after = dump(db)
ok(changed(before, after) == ["spread_candidates", "spread_index", "spread_runs"],
   "B. only spread_runs, spread_candidates and spread_index change (%s)" % changed(before, after))
cands = r["candidates"] if r else []
print("    run: %d universe, %d checked, %d found; top: %s" % (r["n_universe"], r["n_checked"], r["n_found"],
      ", ".join("%s %g/%g" % (c["ticker"], c["short_strike"], c["long_strike"]) for c in cands[:5])) if r else "")

# ---- C. every line against the rules, recomputed from the fixed chains
def leg(t, exp, k):
    rows, S = F.chain(t)
    return next(x for x in rows if x["expiration"] == exp and abs(x["strike"] - k) < 1e-9), S, rows
bad = []
for c in cands:
    sh, S, rows = leg(c["ticker"], c["expiry"], c["short_strike"])
    lg, _, _ = leg(c["ticker"], c["expiry"], c["long_strike"])
    target = c["short_strike"] - 0.05 * S
    below = [x for x in rows if x["expiration"] == c["expiry"] and x["strike"] < c["short_strike"]]
    want = min(below, key=lambda x: (abs(x["strike"] - target), x["strike"]))["strike"]
    credit = round(((sh["bid"] + sh["ask"]) - (lg["bid"] + lg["ask"])) / 2 + 1e-9, 2)
    gap = (sh["ask"] - sh["bid"]) + (lg["ask"] - lg["bid"])
    width = c["short_strike"] - c["long_strike"]
    half = math.ceil(credit * 50 - 1e-9) / 100.0
    checks = {
        "sold delta 0.25-0.30": 0.25 - 1e-9 <= sh["delta"] <= 0.30 + 1e-9,
        "30-60 days": 30 <= c["dte"] <= 60,
        "bought put below, nearest 5%": c["long_strike"] == want,
        "both legs' quotes recorded as read": (c["short_bid"], c["short_ask"], c["long_bid"], c["long_ask"]) ==
                                              (sh["bid"], sh["ask"], lg["bid"], lg["ask"]),
        "the legs are on the same expiry": abs(c["long_mid"] - lg["mid"]) < 1e-9,
        "credit from the quotes": abs(c["credit"] - credit) < 1e-9,
        "market under 10% of the credit": gap / ((sh["bid"] + sh["ask"] - lg["bid"] - lg["ask"]) / 2) * 100 < 10.0,
        "most it can lose = (width - credit) x 100": abs(c["max_loss"] - (width - credit) * 100) < 0.01,
        "yearly return on the loss": abs(c["ann_return"] - credit / (width - credit) * 365 / c["dte"]) < 1e-9,
        "50% buy-back": abs(c["rest_price"] - half) < 1e-9 or abs(c["rest_price"] - round(credit / 2, 2)) < 1e-9 and credit * 100 % 2 == 0,
        "out 21 days before expiry": c["exit_date"] == str(__import__("datetime").date.fromisoformat(c["expiry"]) -
                                                         __import__("datetime").timedelta(days=21)),
        "stock if assigned = sold strike x 100": abs(c["stock_if_assigned"] - c["short_strike"] * 100) < 1e-9,
    }
    for k, v in checks.items():
        if not v: bad.append("%s: %s" % (c["ticker"], k))
ok(cands and not bad, "C. every line passes every rule, recomputed from the chains (%d lines%s)" % (len(cands), "; " + "; ".join(bad[:4]) if bad else ""))
ok([c["rank"] for c in cands] == list(range(1, len(cands) + 1)) and
   all(cands[i]["ann_return"] >= cands[i + 1]["ann_return"] for i in range(len(cands) - 1)),
   "C. ranked 1..n by yearly return on the most it can lose")
tks = [c["ticker"] for c in cands]
ok(not ({"GOOG", "GOOGL"} <= set(tks)) and len(tks) == len(set(tks)), "C. one spread per company (GOOG/GOOGL once)")
ok("market on the spread" in (r["rejects"].get("KO") or "") if r else False, "C. Coca-Cola's real quotes are refused for the market: %s" % (r["rejects"].get("KO") if r else ""))
ok(all(t in r["rejects"] or t in tks for t in ("MOD", "VRT", "FTI")) and not any(t in tks for t in ("MOD", "VRT", "FTI")),
   "C. the names quoted 30% wide are left off")
ok(all(c["dte"] != 67 for c in cands), "C. no spread on the 67-day expiry")

# ---- D. sizing: by the most it can lose; a put and a spread on one name are one bet
code = ("import json\nfrom helm import sleeve as S\nprint(json.dumps(S.book(), default=str))\n")
out, _ = eng(OLD, db, code); bk = json.loads(out.strip().split("\n")[-1])
bad = []
for c in cands:
    k = c["ticker"] if c["ticker"] not in ("GOOG", "GOOGL") else "GOOG"
    k = next((x for x in bk["by_name"] if x == c["ticker"] or (c["ticker"] in ("GOOG", "GOOGL") and x in ("GOOG", "GOOGL", "GOOG+GOOGL"))), c["ticker"])
    room = min(bk["share"] - bk["used"], bk["name_cap"] - bk["by_name"].get(k, 0.0),
               bk["sector_cap"] - bk["by_sector"].get(bk["sector_of_name"].get(c["ticker"]) or c["sector"], 0.0))
    want = max(int(math.floor(room / c["max_loss"] + 1e-9)), 0)
    want = min(want, 20)
    if c["size_n"] != want: bad.append("%s %s vs %s" % (c["ticker"], c["size_n"], want))
ok(cands and not bad, "D. every size = the room left / the most it can lose, at most 20 (%s)" % ("; ".join(bad[:4]) or "all"))
held = [c for c in cands if bk["by_name"].get(c["ticker"], 0) > 0]
ok(held, "D. at least one name on the list already holds something open (%s)" % ", ".join(c["ticker"] for c in held))
for c in held[:3]:
    room_name = bk["name_cap"] - bk["by_name"][c["ticker"]]
    ok(c["size_n"] <= max(int(room_name / c["max_loss"] + 1e-9), 0),
       "D. %s: what it already holds counts against the same name limit (room %.0f, spread %d x %.0f)"
       % (c["ticker"], room_name, c["size_n"], c["max_loss"]))

# ---- E. the index funds: 4 x 3 widths, each judged at its own width
idx = r["index"] if r else []
ok(sorted((x["ticker"], x["width_pct"]) for x in idx) == sorted((t, w) for t in ("DIA", "IWM", "QQQ", "SPY") for w in (0.02, 0.03, 0.05)),
   "E. every broad index fund at 2%%, 3%% and 5%% (%d rows)" % len(idx))
bad = []
for x in idx:
    if x.get("short_strike") is None:
        if not x.get("note"): bad.append("%s %s no spread and no note" % (x["ticker"], x["width_pct"]))
        continue
    sh, S, rows = leg(x["ticker"], x["expiry"], x["short_strike"])
    target = x["short_strike"] - x["width_pct"] * S
    below = [y for y in rows if y["expiration"] == x["expiry"] and y["strike"] < x["short_strike"]]
    if x["long_strike"] != min(below, key=lambda y: (abs(y["strike"] - target), y["strike"]))["strike"]:
        bad.append("%s %s width" % (x["ticker"], x["width_pct"]))
    if not x["gap_pct"] < 10: bad.append("%s %s market" % (x["ticker"], x["width_pct"]))
ok(not bad, "E. each index row's bought put is at its own width and its market passes (%s)" % ("; ".join(bad) or "all"))
spy = {x["width_pct"]: x for x in idx if x["ticker"] == "SPY"}
ok(spy.get(0.05, {}).get("short_strike") == 695 and spy[0.05]["long_strike"] == 660 and abs(spy[0.05]["credit"] - 4.93) < 1e-9,
   "E. the S&P fund at 5% is 695/660 for $4.93, as priced live at 13:19")
ok(spy.get(0.02, {}).get("short_strike") != 695 or spy[0.02].get("short_strike") is None,
   "E. the S&P fund at 2%% does not offer 695/680 (10.4%%): %s" % (spy.get(0.02, {}).get("note") or "%s/%s" % (spy.get(0.02, {}).get("short_strike"), spy.get(0.02, {}).get("long_strike"))))
on = {c["ticker"]: c for c in cands}
ok(all((x["rank"] == on[x["ticker"]]["rank"]) if (x["ticker"] in on and x["width_pct"] == 0.05) else x["rank"] is None for x in idx),
   "E. an index row carries a rank only at 5% and only when that fund is on the list")

# ---- F. a fund under the bar is still read, and the list's reason is unchanged
db = fresh("f.db"); cx = sqlite3.connect(db)
cx.execute("UPDATE iv_history SET iv_rank=30 WHERE ticker='IWM' AND date='2026-11-23'"); cx.commit(); cx.close()
rf = run_build(NEW, db)
iw = [x for x in rf["index"] if x["ticker"] == "IWM"] if rf else []
ok(rf and "IWM" not in [c["ticker"] for c in rf["candidates"]] and (rf["rejects"].get("IWM") or "").startswith("IV rank 30"),
   "F. IWM at IV rank 30 is off the list, its reason the bar (%s)" % (rf["rejects"].get("IWM") if rf else ""))
ok(len(iw) == 3 and all(x["bar_met"] == 0 for x in iw) and any(x.get("short_strike") for x in iw),
   "F. ...and still read at all three widths, marked under the bar")

# ---- G. the gateway going away stops the run, as on the put list
db = fresh("g.db")
rg = run_build(NEW, db, extra={"S165_GWDOWN": "AAPL,ABT,AMAT"})
ok(rg and rg.get("stopped") and rg["stopped"][:3] == ["AAPL", "ABT", "AMAT"] and len(rg["candidates"]) == 0,
   "G. three names in a row unreadable stops the run (%d names to rerun)" % (len(rg["stopped"]) if rg and rg.get("stopped") else 0))

# ---- H. the command's own words
out, rc = eng(NEW, W + "/b.db", argv=["spreads", "--last"])
ok(rc == 0 and "New bull put spreads" in out and "Index funds" in out and "695/660" in out and "Left off:" in out,
   "H. helm spreads --last shows the list, the index funds and the reasons")
ok(not re.search(r"\s10%\s", out),
   "H. no passing spread's market is printed as 10% (9.6% shows as 9.6%)")
sized = [c for c in cands[:5] if c.get("size_n")]
ok(sized and all(("$" + format(int(round(c["stock_if_assigned"] * c["size_n"])), ",d")) in out for c in sized),
   "H. 'If all assigned' shows the stock at the suggested size (%s)" % ", ".join("%s %d x %.0f" % (c["ticker"], c["size_n"], c["stock_if_assigned"]) for c in sized[:3]))
first = [l for l in out.split("\n") if re.match(r"\s*1\s+\S", l)]
ok(first and cands and cands[0]["ticker"] in first[0], "H. its first line is the top-ranked name")
out2, _ = eng(NEW, W + "/b.db", argv=["spreads", "--last", "--why"])
ok("KO" in out2 and "market on the spread" in out2, "H. --why lists each name left off with its reason")

# ---- I. nothing else changed: the old commands' words, old vs new
db = fresh("i.db")
for argv in (["puts", "--last"], ["pmcc", "--last", "--all"], ["calls", "--last"], ["sleeve"], ["book"], ["advise"]):
    o1, _ = eng(OLD, db, argv=argv); o2, _ = eng(NEW, db, argv=argv)
    ok(o1 == o2 and o1.strip(), "I. helm %s reads the same before and after" % " ".join(argv))
SZ = ("import json\nfrom helm import sleeve as S\nout = {}\n"
      "for st in ('CSP', 'PMCC', 'DIAGONAL', 'COVERED_CALL', 'LONG_CALL', 'IRON_CONDOR', 'BEAR_CALL_SPREAD', None):\n"
      "    for t, per in (('AAPL', 10**7), ('AAPL', 500.0), ('PFE', 2000.0)):\n"
      "        out['%s %s %s' % (st, t, per)] = S.size(st, t, per)\n"
      "print(json.dumps(out))\n")
s1, _ = eng(OLD, db, SZ); s2, _ = eng(NEW, db, SZ)
ok(s1 == s2 and s1.strip(), "I. the sizing note for every other strategy is word for word the same")
s3, _ = eng(NEW, db, SZ.replace("'CSP', 'PMCC'", "'BULL_PUT_SPREAD', 'CSP', 'PMCC'"))
ok("one contract risks $10,000,000" in s3, "I. a spread too big to fit says what one contract risks")
o1, _ = eng(OLD, db, argv=["help"]); o2, _ = eng(NEW, db, argv=["help"])
d = [l for l in o2.split("\n") if l not in o1.split("\n")]
ok(len(d) == 1 and "spreads" in d[0], "I. helm help gains only the spreads line")

# ---- J. the board, started as production starts it: every page the same
PORT = [int(os.environ.get("S165PORT", "9560"))]
def render(engdir, db, paths):
    PORT[0] += 1; port = str(PORT[0])
    cx = sqlite3.connect(db)
    open(W + "/home/.helm_profile", "w").write(cx.execute("select id from accounts").fetchone()[0]); cx.close()
    e = dict(os.environ, HOME=W + "/home", HELM_ROOT=engdir, HELM_DB=db, PORT=port, HELM_PG_ENGINE="real",
             PYTHONPATH="/tmp/pylib", HELM_ADVICE_DIR=W + "/advice-board", PYTHONHASHSEED="0", TMPDIR="/tmp")
    os.makedirs(W + "/advice-board", exist_ok=True)
    p = subprocess.Popen([sys.executable, "-B", "app.py"], cwd=PG, env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = {}
    try:
        for _ in range(120):
            try: urllib.request.urlopen("http://127.0.0.1:%s/health" % port, timeout=2); break
            except urllib.error.HTTPError: break
            except Exception: time.sleep(0.5)
        for path in paths:
            try:
                rr = urllib.request.urlopen("http://127.0.0.1:%s%s" % (port, path), timeout=120); out[path] = (rr.status, rr.read().decode())
            except Exception as ex: out[path] = (getattr(ex, "code", "ERR"), str(ex))
    finally:
        p.terminate(); p.communicate(timeout=10)
    return out
def norm(b):
    b = b.replace(OLD, "ENG").replace(NEW, "ENG")
    b = re.sub(r"\?v=\d+", "?v=X", b); b = re.sub(r'(clip|cp|g)[-_]?[0-9a-f]{6,}', 'ID', b)
    return re.sub(r'(["#])([a-z]{1,3})\d{3,}(["\)])', r'\1\2ID\3', b)
pages = ["/", "/book", "/reference", "/positions", "/watchlist", "/agents"]
d1, d2 = fresh("j1.db"), fresh("j2.db")
p1, p2 = render(OLD, d1, pages), render(NEW, d2, pages)
if norm(p1["/agents"][1]) != norm(p2["/agents"][1]):
    import difflib
    print("    /agents differs:", "\n    ".join(l[:160] for l in difflib.unified_diff(norm(p1["/agents"][1]).split("\n"), norm(p2["/agents"][1]).split("\n"), lineterm="", n=0) if l[:1] in "+-")[:1200])
for pth in pages:
    ok(p1[pth][0] == 200 and p1[pth][0] == p2[pth][0] and norm(p1[pth][1]) == norm(p2[pth][1]),
       "J. board %s identical before and after (%s)" % (pth, p1[pth][0]))

print("verify_s165: %d checks, %d failed" % (n[0], len(fails)))
sys.exit(1 if fails else 0)
