"""s125 step 2 (diagonal card review): the diagonal's effective basis on the card.
Net rent banked = every short sale's credit minus every buy-back's cost (Russ,
2026-10-01). The card shows: closing sale today, net rent banked, the long cost,
whole trade; the even line; a long-only break-even from the basis.

Renders /thesis/<id> for all positions through PG's own app.test_client() from a
VACUUM INTO copy -- BASELINE (helm/thesis.py.bak-20261001-s125b and
helm-pg/templates/thesis.html.bak-20261001-s125b) and CANDIDATE (working trees) --
in child processes with PYTHONHASHSEED=0, then checks:
  * every non-diagonal card byte-identical (static ?v= stamps stripped);
  * each diagonal's "closing sale" equals an INDEPENDENT sum of that check's
    leg_checks marks over the legs open at that check (long +, short -);
  * closing sale + net rent banked - long cost = the check's journaled P&L;
  * net rent banked equals an independent SQL sum over the shorts booked/closed by
    that check's time; known values AA +1,390 / IBM +1,602 / closed AMAT at its last
    check +2,275, and AMAT's final effective basis $93.50;
  * a long-only diagonal's break-even = strike + effective basis.
Usage: python3 tools/verify_s125_basis.py   (device VM; HELM_ROOT layout per the standing facts)
       python3 tools/verify_s125_basis.py render HELM_TREE PG_TREE OUT.json   (child)
Perturbation run 2026-10-01: buy-backs counted regardless of time -> FAIL on closed AMAT.
"""
import sys as _sys
if __name__ == "__main__" and (len(_sys.argv) < 2 or _sys.argv[1] == "main"):
    print("tools/verify_s125_basis.py: SUPERSEDED-s125e -- diagonals render thesis_diag.html since 2026-10-01; this harness checked the old diagonal card (step 2). Run tools/verify_s125_card.py and tools/verify_s125_loss_limit.py instead. Exiting without checking.")
    _sys.exit(0)

import html, json, os, re, shutil, site, sqlite3, subprocess, sys, tempfile, types
USERSITE = site.getusersitepackages()
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PGROOT = os.path.join(os.path.dirname(HERE), "helm-pg")
LIVE = os.path.join(HERE, "data", "helm.db")
TAG = "bak-20261001-s125b"
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

def money(s):
    s = s.replace(",", "").replace("−", "-").replace("$", "").replace("+", "")
    return float(s)

def check(old, new, copy):
    A = json.load(open(old)); B = json.load(open(new))
    print("baseline :", A["thesis"], A["app"]); print("candidate:", B["thesis"], B["app"])
    A, B = A["cards"], B["cards"]
    c = sqlite3.connect(copy); c.row_factory = sqlite3.Row
    meta = {r["id"]: dict(r) for r in c.execute("select * from positions")}
    P = F = 0
    def ok(n, cond, det=""):
        nonlocal P, F
        P += bool(cond); F += (not cond)
        if not cond: print("FAIL", n, det)
    strip = lambda h: re.sub(r"\?v=\d+", "", h)
    for p in A:
        ok("200 " + p, B[p]["status"] == 200 and len(B[p]["html"]) > 2000)
        if meta[p]["strategy"] not in DIAG:
            ok("non-diagonal identical " + p, strip(A[p]["html"]) == strip(B[p]["html"]))
    seen = {}
    for p, m in meta.items():
        if m["strategy"] not in DIAG:
            continue
        h = B[p]["html"]
        tiles = dict(re.findall(r'<span class="ct-lab">([^<]+)</span>\s*<span class="ct-val[^"]*">([^<]+)</span>', h))
        sale = tiles.get("closing sale today", tiles.get("closing sale (final check)"))
        if sale is None:
            ok("diag tiles present " + p, False, list(tiles)); continue
        sale, rent = money(sale), money(tiles["net rent banked"])
        cost, whole = money(tiles["the long cost"]), money(tiles["whole trade"])
        last = c.execute("select * from checks where position_id=? and pnl_unrealized is not null "
                         "order by checked_at desc limit 1", (p,)).fetchone()
        at = last["checked_at"]
        legs = [dict(r) for r in c.execute("select * from legs where position_id=?", (p,))]
        ts = lambda x: str(x or "").replace(" ", "T")
        # independent rent
        r_ind = 0.0; live = []
        for l in legs:
            if l["direction"] != "SHORT":
                live.append(l); continue
            if ts(l["created_at"]) > at: continue
            r_ind += l["open_price"] * l["contracts"] * (l["multiplier"] or 100)
            if l["status"] != "OPEN" and ts(l["close_date"]) <= at:
                r_ind -= l["close_price"] * l["contracts"] * (l["multiplier"] or 100)
            else:
                live.append(l)
        ok("rent independent " + p, abs(r_ind - rent) <= 1.0, (r_ind, rent))
        lng = [l for l in legs if l["direction"] == "LONG"][0]
        ok("long cost " + p, abs(lng["open_price"] * lng["contracts"] * 100 - cost) <= 1.0)
        ok("identity sale+rent-cost=pnl " + p, abs(sale + rent - cost - last["pnl_unrealized"]) <= 1.5,
           (sale, rent, cost, last["pnl_unrealized"]))
        ok("whole trade = pnl " + p, abs(whole - last["pnl_unrealized"]) <= 1.0)
        # independent sale from that check's leg marks
        mk = {r["leg_id"]: r["current_price"] for r in c.execute(
            "select leg_id,current_price from leg_checks where check_id=?", (last["id"],))}
        if all(l["id"] in mk and mk[l["id"]] is not None for l in live):
            s_ind = sum((1 if l["direction"] == "LONG" else -1) * mk[l["id"]] * l["contracts"] * 100 for l in live)
            ok("sale = leg marks " + p, abs(s_ind - sale) <= 1.5 * len(live), (s_ind, sale))
            seen[p] = True
        be = re.search(r"<b>Break-even</b>\s*\$([\d,.]+)", h)
        if len(live) == 1 and m["status"] == "OPEN":
            eff = (cost - rent) / (lng["contracts"] * 100)
            ok("long-only break-even " + p, be and abs(money(be.group(1)) - (lng["strike"] + eff)) <= 0.011,
               (be and be.group(1), lng["strike"] + eff))
        elif m["status"] == "OPEN":
            ok("no break-even with a short on " + p, be is None)
        ok("even line " + p, "Even on the whole trade" in h or "has repaid the long" in h)
    known = {"AA-DIAGONAL-20260901-219869": 1390, "IBM-DIAGONAL-20260917-A2D42A": 1602,
             "AMAT-DIAGONAL-20260923-C6C861": 2275}
    for p, v in known.items():
        t = dict(re.findall(r'<span class="ct-lab">([^<]+)</span>\s*<span class="ct-val[^"]*">([^<]+)</span>', B[p]["html"]))
        ok("known rent " + p, abs(money(t["net rent banked"]) - v) < 0.5, t.get("net rent banked"))
    sys.path.insert(0, os.environ["HELM_ROOT"])
    from helm import thesis as T
    am = [dict(r) for r in c.execute("select * from legs where position_id='AMAT-DIAGONAL-20260923-C6C861'")]
    ok("AMAT final effective 93.50", abs(T.diag_basis(am)["effective"] - 93.50) < 0.005, T.diag_basis(am))
    print("sale checked against leg marks on %d diagonals" % len(seen))
    print("PASS %d  FAIL %d" % (P, F))
    return F

if MODE == "render":
    render(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]); sys.exit(0)
if MODE == "check":
    os.environ["HELM_ROOT"] = sys.argv[5] if len(sys.argv) > 5 else HERE
    sys.exit(1 if check(sys.argv[2], sys.argv[3], sys.argv[4]) else 0)
# main
for f in (os.path.join(HERE, "helm", "thesis.py." + TAG), os.path.join(PGROOT, "templates", "thesis.html." + TAG)):
    if not os.path.exists(f):
        sys.exit("baseline %s is gone (removed under the .bak rule) -- this harness cannot run" % f)
t = tempfile.mkdtemp(prefix="s125b_"); copy = t + "/copy.db"
l = sqlite3.connect("file:" + LIVE + "?mode=ro", uri=True); l.execute("VACUUM INTO ?", (copy,)); l.close()
old, oldpg = t + "/old", t + "/oldpg"
os.makedirs(old); shutil.copytree(os.path.join(HERE, "helm"), old + "/helm")
shutil.copy(os.path.join(HERE, "helm", "thesis.py." + TAG), old + "/helm/thesis.py")
shutil.copytree(PGROOT, oldpg, ignore=shutil.ignore_patterns("*.bak*", "_to_delete", "*.log"))
shutil.copy(os.path.join(PGROOT, "templates", "thesis.html." + TAG), oldpg + "/templates/thesis.html")
env = dict(os.environ, PYTHONPATH=USERSITE, PYTHONHASHSEED="0")
me = os.path.abspath(__file__)
subprocess.run([sys.executable, me, "render", old, oldpg, t + "/old.json", copy], env=env, check=True)
subprocess.run([sys.executable, me, "render", HERE, PGROOT, t + "/new.json", copy], env=env, check=True)
os.environ["HELM_ROOT"] = HERE
sys.exit(1 if check(t + "/old.json", t + "/new.json", copy) else 0)
