"""s159: Today's proposed trades show the stock's market multiple and the book's beta-weighted delta after the trade
(helm/bookview.py beta_of / added_bw_delta, helm/todayview.py market_cell, puts_view / pmcc_view book=, view()'s new
calls; helm-pg engine_store.today_view, templates/today.html, templates/reference.html). Display only.

Old code = the .bak-20261114-s159 files; new = the working tree (or NEW_ENG NEW_PG dirs, copied through setup).
Fresh DB copies, the board started as production starts it. Expected numbers from SQL on the copy.
Usage: TMPDIR=/tmp PYTHONPATH=/tmp/pylib python3 -B _s159/verify_market_cols.py [NEW_ENG NEW_PG]
"""
import hashlib, html as H, json, os, re, shutil, sqlite3, subprocess, sys, time, urllib.request
HOME = os.path.expanduser("~"); ROOT = HOME + "/mnt/helm"; PGROOT = HOME + "/mnt/helm-pg"
W = "/tmp/s159w"
ENG_BAK = {"helm/bookview.py": ".bak-20261114-s159", "helm/todayview.py": ".bak-20261114-s159"}
PG_BAK = {"engine_store.py": ".bak-20261114-s159", "templates/today.html": ".bak-20261114-s159",
          "templates/reference.html": ".bak-20261114-s159"}
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
OLD_PG = tree(PGROOT, W + "/pg-old", PG_IGN)
for f, b in PG_BAK.items(): shutil.copy(PGROOT + "/" + f + b, OLD_PG + "/" + f)
print("new engine:", NEW_ENG, "| new board:", NEW_PG)
for d, fs in ((NEW_ENG, list(ENG_BAK)), (NEW_PG, list(PG_BAK))):
    for f in fs: print("   ", f, hashlib.sha256(open(d + "/" + f, "rb").read()).hexdigest()[:12])
fails, n = [], [0]
def ok(c, m):
    n[0] += 1; print(("  ok   " if c else "  FAIL ") + m)
    if not c: fails.append(m)
def fresh(name):
    p = W + "/" + name
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
PORT = [9260]
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
    env = dict(os.environ, HELM_ROOT=engdir, HELM_DB=db, PYTHONPATH=engdir + ":/tmp/pylib", HOME=W + "/home",
               HELM_ADVICE_DIR=W + "/advice-out", COLUMNS="200")
    r = subprocess.run([sys.executable, "-B", "-c", code], cwd=engdir, env=env, capture_output=True, text=True, timeout=170)
    if r.returncode: print(r.stdout[-1500:], r.stderr[-2500:])
    return r.stdout
# the engine's view of Today, as the board builds it (engine_store.today_view's three calls)
VIEW = ("import json, inspect; from helm import todayview as T, puttool as P, pmcctool as PM, sqlite3 as _s\n"
        "from helm.config import get_active_account\n" if False else
        "import json, inspect, sqlite3; from helm import todayview as T, puttool as P, pmcctool as PM\n"
        "from helm.config import get_active_account, DB_PATH\n"
        "c = sqlite3.connect(str(DB_PATH)); c.row_factory = sqlite3.Row\n"
        "v = T.view(account_id=get_active_account())\n"
        "pr, mr = P.latest(c), PM.latest(c)\n"
        "kw = {'book': v['figures']} if 'book' in inspect.signature(T.puts_view).parameters else {}\n"
        "v['puts'] = T.puts_view(pr, **kw); v['pmcc'] = T.pmcc_view(mr, **kw)\n"
        "print(json.dumps(v, default=str))\n")
def view(engdir, db):
    return json.loads(eng(engdir, db, VIEW).strip().split("\n")[-1])
def strip_market(o, top=True):
    """Drop the new per-row 'market' entries; Today's own top-level 'market' (the market line) stays."""
    if isinstance(o, dict): return {k: strip_market(v, False) for k, v in o.items() if top or k != "market"}
    if isinstance(o, list): return [strip_market(x, False) for x in o]
    return o
def norm(b):
    b = re.sub(r"\?v=\d+", "?v=X", b)
    b = re.sub(r'(clip|cp|g)[-_]?[0-9a-f]{6,}', 'ID', b)
    return re.sub(r'(["#])([a-z]{1,3})\d{3,}(["\)])', r'\1\2ID\3', b)
TH = ('<th>Multiple <a class="q" href="/reference#multiple" title="How this is worked out">?</a></th>'
      '<th>Book delta after <a class="q" href="/reference#delta-after" title="How this is worked out">?</a></th>')
CELL = re.compile(r'<td>(-?[\d.]+)( <span class="dim">assumed</span>)?</td><td class="(red|amber|)">([^<]*)</td>')
def strip_html(b):
    b = b.replace(TH, "")
    b = CELL.sub("", b)
    return b.replace('colspan="15">Best that fit', 'colspan="13">Best that fit')
def usd(x, sign=False):
    return ("−" if x < 0 else ("+" if sign and x > 0 else "")) + "$" + "{:,.0f}".format(abs(x))
NEAR = 0.90

def check_live(DA, label, expect_calls):
    before = dump(DA)
    cx = sqlite3.connect(DA); cx.row_factory = sqlite3.Row
    beta = {r["ticker"]: r["beta"] for r in cx.execute("select ticker, beta from watchlist")}
    vo, vn = view(OLD_ENG, DA), view(NEW_ENG, DA)
    ok(json.dumps(strip_market(vn), sort_keys=True, default=str) == json.dumps(vo, sort_keys=True, default=str),
       "%s: Today's view identical old vs new apart from the new 'market' entries" % label)
    bw, cap = vn["figures"]["bw_delta"], vn["figures"]["delta_cap"]
    def expect(tk, dsh, spot, one):
        b = beta.get(tk); assumed = b is None; b = 1.0 if b is None else float(b)
        add = dsh * spot * b; after = bw + add
        st = "fired" if abs(after) > cap + 1e-6 else ("near" if abs(after) >= NEAR * cap else "pass")
        return {"beta": b, "beta_assumed": assumed, "add": add, "after": after, "state": st, "one": one}
    def same(m, e):
        return m and all(abs(m[k] - e[k]) < 1e-6 if isinstance(e[k], float) else m[k] == e[k] for k in e)
    # puts: SQL on the latest run
    rid = cx.execute("select id from put_runs order by run_at desc, rowid desc limit 1").fetchone()[0]
    rows = {(r["ticker"], r["expiry"], r["strike"]): r for r in cx.execute("select * from put_candidates where run_id=?", (rid,))}
    shown = vn["puts"]["top"] + vn["puts"]["fit"]
    good = 0
    for s in shown:
        r = next(r for k, r in rows.items() if k[0] == s["ticker"] and abs(k[2] - float(s["put"].split()[0])) < 1e-9)
        e = expect(s["ticker"], abs(r["delta"]) * 100 * (r["size_n"] or 1), r["spot"], not r["size_n"])
        good += bool(same(s["market"], e))
    ok(shown and good == len(shown), "%s: every put row's multiple, addition, book after and colour = SQL (%d rows)" % (label, len(shown)))
    rid = cx.execute("select id from pmcc_runs order by run_at desc, rowid desc limit 1").fetchone()[0]
    pr = {r["ticker"]: r for r in cx.execute("select * from pmcc_candidates where run_id=?", (rid,))}
    good = 0
    for s in vn["pmcc"]["top"]:
        r = pr[s["ticker"]]
        e = expect(s["ticker"], (abs(r["long_delta"]) - abs(r["short_delta"])) * 100 * (r["size_n"] or 1), r["spot"], not r["size_n"])
        good += bool(same(s["market"], e))
    ok(vn["pmcc"]["top"] and good == len(vn["pmcc"]["top"]),
       "%s: every PMCC row: long delta less the short's, at its sized count = SQL (%d rows)" % (label, len(vn["pmcc"]["top"])))
    nc = vn["new_calls"]
    ok(len(nc) == expect_calls, "%s: %d new covered-call rows" % (label, len(nc)))
    good = 0
    for c in nc:
        e = expect(c["ticker"], -abs(c["delta"]) * 100 * c["contracts"], c["spot"], False)
        good += bool(same(c["market"], e)) and c["market"]["add"] < 0
    ok(good == len(nc), "%s: every covered call subtracts, = SQL" % label)
    # the board
    ALL = [r["id"] for r in cx.execute("select id from positions where book='REAL' and status='OPEN' order by ticker")]
    paths = ["/today", "/reference", "/book", "/watchlist"] + ["/thesis/" + i for i in ALL]
    o, lo = render(OLD_ENG, OLD_PG, DA, paths); nw, ln = render(NEW_ENG, NEW_PG, DA, paths)
    ok(all(o[p][0] == 200 and nw[p][0] == 200 for p in paths), "%s: every page renders, old and new (%d pages)" % (label, len(paths)))
    ok("Traceback" not in lo + ln, "%s: no tracebacks in the board's log" % label)
    diff = [p for p in paths if p not in ("/today", "/reference") and norm(o[p][1]) != norm(nw[p][1])]
    ok(not diff, "%s: every other page identical old vs new %s" % (label, diff))
    t_old, t_new = norm(o["/today"][1]), norm(nw["/today"][1])
    cells = CELL.findall(t_new)
    nrows = len(shown) + len(vn["pmcc"]["top"]) + len(nc)
    ok(len(cells) == nrows and t_new.count(TH) == 2 + (1 if nc else 0),
       "%s: Today has one pair of new cells per proposed row (%d) and the headers on each list" % (label, len(cells)))
    ok(strip_html(t_new) == t_old, "%s: Today minus the new columns identical to the old page" % label)
    if strip_html(t_new) != t_old:
        import difflib
        for l in list(difflib.unified_diff(t_old.split("\n"), strip_html(t_new).split("\n"), lineterm="", n=0))[:10]: print("   DIFF", l[:200])
    # the cells say the numbers
    texts = [H.unescape(c[3]) for c in cells]
    exp = []
    for s in nc + shown + vn["pmcc"]["top"]:        # the page's order: New calls, New puts, New PMCCs
        m = s["market"]
        exp.append((usd(m["add"], True) + (" (×1)" if m["one"] else "") + " → " + usd(m["after"]),
                    {"fired": "red", "near": "amber"}.get(m["state"], ""), "%.2f" % m["beta"], bool(m["beta_assumed"])))
    got = [(t, c[2], c[0], bool(c[1])) for t, c in zip(texts, cells)]
    ok(got == exp, "%s: each cell reads '+$add → $after', amber/red by the limit, multiple and 'assumed' as the engine says" % label)
    if got != exp:
        for a, b in list(zip(got, exp))[:4]: print("   got", a, "| want", b)
    print("   e.g.", got[:3])
    r_new = H.unescape(nw["/reference"][1])
    ok('id="multiple"' in r_new and 'id="delta-after"' in r_new and "at or above 90% of the book delta limit" in r_new,
       "%s: Reference has both entries; the near line read from the engine (90%%)" % label)
    ok(strip_html(norm(nw["/reference"][1])) != norm(o["/reference"][1]) and
       re.sub(r'<dt id="multiple">.*?</dd>\n<dt id="delta-after">.*?</dd>\n', "", norm(nw["/reference"][1]), flags=re.S) == norm(o["/reference"][1]),
       "%s: Reference otherwise identical" % label)
    hrefs = set(re.findall(r'href="/reference#([a-z0-9-]+)"', nw["/today"][1]))
    ids = set(re.findall(r'<dt id="([a-z0-9-]+)"', nw["/reference"][1]))
    ok(hrefs <= ids, "%s: every '?' on Today lands on a Reference entry" % label)
    ok(dump(DA) == before, "%s: no table changed" % label)
    return vn

# ---------------------------------------------------------------- A: the live copy as it is
print("A. the live copy (Friday's runs)")
DA = fresh("a.db")
vA = check_live(DA, "A", 0)
st = {s["ticker"]: s["market"]["state"] for s in vA["puts"]["top"] + vA["puts"]["fit"]}
print("   book", round(vA["figures"]["bw_delta"]), "of", round(vA["figures"]["delta_cap"]), "| states", st)

# ---------------------------------------------------------------- B: constructed: a covered-call choice, a missing beta, a fitting PMCC
print("B. constructed: a covered-call choice on GOOGL; DELL with no stored beta")
DB = fresh("b.db")
cx = sqlite3.connect(DB); cx.row_factory = sqlite3.Row
last = cx.execute("select * from call_runs where hypothetical=0 order by run_at desc, rowid desc limit 1").fetchone()
cx.execute("insert into call_runs (id, run_at, rule_version, market_open, n_holdings, hypothetical, rejects_json, note) "
           "values ('calls_s159test', '2026-11-13 17:40:00', ?, 1, 1, 0, '{}', 's159 test')", (last["rule_version"],))
cols = [r[1] for r in cx.execute("pragma table_info(call_candidates)")]
row = dict.fromkeys(cols)
row.update(run_id="calls_s159test", kind="shares", ticker="GOOGL", choice=1, hypothetical=0, shares=100, contracts=1,
           cost_basis=335.0, effective_basis=326.62, expiry="2026-12-31", dte=47, strike=330.0, spot=294.6, bid=1.0,
           ask=1.2, mid=1.1, spread_pct=8.0, delta=0.21, gamma=0.01, theta=-0.05, vega=0.3, iv=0.32, ann_return=0.03,
           if_called=4000.0, earnings="none", exdiv=None, rest_price=0.55)
cx.execute("insert into call_candidates (%s) values (%s)" % (",".join(cols), ",".join("?" * len(cols))), [row[c] for c in cols])
cx.execute("update watchlist set beta=NULL where ticker='DELL'")
cx.commit(); cx.close()
vB = check_live(DB, "B", 1)
d = next(s for s in vB["puts"]["top"] if s["ticker"] == "DELL")
ok(d["market"]["beta"] == 1.0 and d["market"]["beta_assumed"], "B: DELL with no beta stored counts at 1.00, marked assumed")
c = vB["new_calls"][0]["market"]
ok(abs(c["add"] - (-0.21 * 100 * 294.6 * 1.25)) < 1e-6, "B: the GOOGL call takes $%.0f off the book's delta" % -c["add"])

print("\nverify_market_cols: %d checks, %s" % (n[0], "PASS" if not fails else "%d FAIL" % len(fails)))
sys.exit(1 if fails else 0)
