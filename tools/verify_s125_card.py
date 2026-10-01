"""s125 (2026-10-01): the four-question diagonal card (helm.thesis.diag_view +
helm-pg templates/thesis_diag.html, chosen in app.thesis_page for diagonals).

Renders every /thesis/<id> through PG's own app.test_client() from a VACUUM INTO
copy -- BASELINE (helm/thesis.py and exit_flags.py .bak-20261001-s125d; helm-pg
app.py and engine_store.py .bak-20261001-s125e) and CANDIDATE (working trees) --
in child processes with PYTHONHASHSEED=0, then checks:
  * every non-diagonal card byte-identical (static ?v= stamps stripped);
  * every diagonal renders the new layout (class="dcard"), status 200, no error;
  * OPEN: the header's whole trade = the latest GOOD check's pnl_unrealized
    (independent query); the ledger's lines sum to it; the loss limit shown =
    half of long open x contracts x 100 from the legs table; the firing rules =
    an independent exit_flags.diag_series on the latest check; "Close" move =
    the ledger's closing line;
  * CLOSED: the realized figure = positions.realized_pnl and the ledger sums to it;
  * day-by-day rows = distinct check days.
Usage: python3 tools/verify_s125_card.py
       python3 tools/verify_s125_card.py render HELM_TREE PG_TREE OUT.json COPY  (child)
Perturbation run 2026-10-01: closing sale computed without net rent -> FAIL.
"""
import html, json, os, re, shutil, site, sqlite3, subprocess, sys, tempfile, types
USERSITE = site.getusersitepackages()
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PGROOT = os.path.join(os.path.dirname(HERE), "helm-pg")
LIVE = os.path.join(HERE, "data", "helm.db")
TH, TP = "bak-20261001-s125d", "bak-20261001-s125e"
DIAG = ("DIAGONAL", "PMCC", "DIAGONAL_PUT")
MODE = sys.argv[1] if len(sys.argv) > 1 else "main"

def render(tree, pg, out, copy):
    sys.path.append(USERSITE)
    tmp = tempfile.mkdtemp()
    os.environ.update(HELM_DB=copy, HELM_ROOT=tree, HOME=tmp)
    open(tmp + "/.helm_profile", "w").write("fidelity_9e60c8")
    yf = types.ModuleType("yfinance"); yf.Ticker = lambda t: types.SimpleNamespace(
        fast_info=types.SimpleNamespace(last_price=None), options=(), history=lambda *a, **k: None, info={})
    sys.modules["yfinance"] = yf
    sys.path.insert(0, tree); sys.path.insert(0, pg)
    import app as A, helm.thesis as T
    ids = [r[0] for r in sqlite3.connect(copy).execute("select id from positions order by id")]
    cl = A.app.test_client(); res = {"thesis": T.__file__, "app": A.__file__, "cards": {}}
    for pid in ids:
        r = cl.get("/thesis/" + pid)
        res["cards"][pid] = {"status": r.status_code, "html": r.get_data(as_text=True)}
    json.dump(res, open(out, "w"))

def txt(s):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s))).strip()

def money(s):
    s = txt(s).replace(",", "").replace("−", "-").replace("$", "").replace("+", "").split()[0]
    return float(s)

def check(old, new, copy, tree):
    sys.path.insert(0, tree); os.environ["HELM_ROOT"] = tree
    from helm import exit_flags as EF, thesis as T
    A = json.load(open(old)); B = json.load(open(new))
    print("baseline :", A["thesis"], A["app"]); print("candidate:", B["thesis"], B["app"])
    A, B = A["cards"], B["cards"]
    c = sqlite3.connect(copy); c.row_factory = sqlite3.Row
    meta = {r["id"]: dict(r) for r in c.execute("select * from positions")}
    P = F = 0
    def ok(n, cond, det=""):
        nonlocal P, F
        P += bool(cond); F += (not cond)
        if not cond and F <= 30: print("FAIL", n, det)
    strip = lambda h: re.sub(r"\?v=\d+", "", h)
    nd = 0
    for p, m in meta.items():
        h = B[p]["html"]
        ok("200 " + p, B[p]["status"] == 200 and len(h) > 2000)
        if m["strategy"] not in DIAG:
            ok("non-diagonal identical " + p, strip(A[p]["html"]) == strip(h)); continue
        nd += 1
        ok("new layout " + p, 'class="dcard"' in h)
        ok("no error " + p, "could not be built" not in h and 'class="err"' not in h)
        led = re.search(r'<div class="ledger"><table>(.*?)</table>', h, re.S)
        lines = re.findall(r"<tr><td>[^<]*</td><td class=\"num\">([^<]+)</td></tr>", led.group(1)) if led else []
        tot = re.search(r'<tr class="tot"><td>[^<]*</td><td class="num[^"]*">([^<]+)</td>', led.group(1)) if led else None
        legs = [dict(r) for r in c.execute("select * from legs where position_id=?", (p,))]
        lg = [l for l in legs if l["direction"] == "LONG"][0]
        cost = lg["open_price"] * lg["contracts"] * 100
        nd_days = c.execute("select count(distinct substr(checked_at,1,10)) from checks where position_id=? "
                            "and data_quality='GOOD' and pnl_unrealized is not null", (p,)).fetchone()[0]
        dd = re.search(r"<b>Day by day</b><span class=\"s\">(\d+) check days", h)
        ok("day rows " + p, dd and int(dd.group(1)) == nd_days, (dd and dd.group(1), nd_days))
        if m["status"] == "OPEN":
            last = c.execute("select pnl_unrealized from checks where position_id=? and data_quality='GOOD' "
                             "and pnl_unrealized is not null order by checked_at desc limit 1", (p,)).fetchone()[0]
            hp = re.search(r'<span class="hpnl[^"]*">([^<]+)</span>', h)
            ok("header whole " + p, hp and abs(money(hp.group(1)) - last) <= 1, (hp and hp.group(1), last))
            ok("ledger sums to whole " + p, lines and abs(sum(money(x) for x in lines) - last) <= 2,
               ([txt(x) for x in lines], last))
            ok("ledger total " + p, tot and abs(money(tot.group(1)) - last) <= 1)
            lim = re.search(r"loss limit \$([\d,]+)</span>", h)
            ok("loss limit = half long cost " + p, lim and abs(float(lim.group(1).replace(",", "")) - 0.5 * cost) <= 1,
               (lim and lim.group(1), cost))
            L = EF._legs(c, p); ser = EF.diag_series(L, EF._diag_rows(c, p, L))
            want = sorted(T._DIAG_LABEL[k].capitalize() for k in ser[-1][1])
            dec = re.search(r'<section id="decide">(.*?)</section>', h, re.S)
            got = sorted(txt(x) for x in re.findall(r"<td>▲ ([^<]+)</td>", dec.group(1))) if dec else None
            ok("firing = rule " + p, got == want, (got, want))
            mv = re.search(r"<h4>Close[^<]*</h4><div class=\"res[^\"]*\">\+?\$?([^ <]+) back", h)
            closing = [x for x in re.findall(r"<tr><td>(Closing today[^<]*)</td><td class=\"num\">([^<]+)</td>", led.group(1))]
            if mv and closing:
                ok("close move = ledger " + p, abs(money(mv.group(1)) - money(closing[0][1])) <= 1, (mv.group(1), closing))
        else:
            rp = m["realized_pnl"]
            hp = re.search(r'<span class="hpnl[^"]*">([^<]+) realized</span>', h)
            ok("realized header " + p, rp is None or (hp and abs(money(hp.group(1)) - rp) <= 1), (hp and hp.group(1), rp))
            if rp is not None and lines and len(lines) > 1:
                ok("closed ledger sums " + p, abs(sum(money(x) for x in lines) - rp) <= 2, ([txt(x) for x in lines], rp))
    print("diagonal cards checked: %d" % nd)
    print("PASS %d  FAIL %d" % (P, F))
    return F

if MODE == "render":
    render(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]); sys.exit(0)
if MODE == "check":
    sys.exit(1 if check(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]) else 0)
need = [os.path.join(HERE, "helm", "thesis.py." + TH), os.path.join(HERE, "helm", "exit_flags.py." + TH),
        os.path.join(PGROOT, "app.py." + TP), os.path.join(PGROOT, "engine_store.py." + TP)]
for f in need:
    if not os.path.exists(f):
        sys.exit("baseline %s is gone (removed under the .bak rule) -- this harness cannot run" % f)
t = tempfile.mkdtemp(prefix="s125e_"); copy = t + "/copy.db"
l = sqlite3.connect("file:" + LIVE + "?mode=ro", uri=True); l.execute("VACUUM INTO ?", (copy,)); l.close()
old, oldpg = t + "/old", t + "/oldpg"
os.makedirs(old); shutil.copytree(os.path.join(HERE, "helm"), old + "/helm")
shutil.copy(need[0], old + "/helm/thesis.py"); shutil.copy(need[1], old + "/helm/exit_flags.py")
shutil.copytree(PGROOT, oldpg, ignore=shutil.ignore_patterns("*.bak*", "_to_delete", "*.log"))
shutil.copy(need[2], oldpg + "/app.py"); shutil.copy(need[3], oldpg + "/engine_store.py")
env = dict(os.environ, PYTHONPATH=USERSITE, PYTHONHASHSEED="0")
me = os.path.abspath(__file__)
subprocess.run([sys.executable, me, "render", old, oldpg, t + "/old.json", copy], env=env, check=True)
subprocess.run([sys.executable, me, "render", HERE, PGROOT, t + "/new.json", copy], env=env, check=True)
sys.exit(1 if check(t + "/old.json", t + "/new.json", copy, HERE) else 0)
