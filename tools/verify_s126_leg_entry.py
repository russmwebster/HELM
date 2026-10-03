#!/usr/bin/env python3
"""verify_s126_leg_entry -- credit verticals and condors are booked leg by leg.

Runs `helm open ... --confirm` through open_cmd.run() on a fresh VACUUM INTO
copy, IB connect blocked, yfinance stubbed, HELM's candidates stubbed with the
real NOW 09-24 pick (Oct 23 117/122/165/170). The display tables are bypassed
(display_* -> confirm_* directly); the dispatch, prompts and writer are real.

    python3 tools/verify_s126_leg_entry.py
    HARNESS_OPEN_CMD=helm/cli/open_cmd.py.bak-20261003-s126 python3 tools/verify_s126_leg_entry.py
        -> the pre-s126 code: the per-leg cases must FAIL (perturbation).
"""
import io, os, sys, sqlite3, tempfile, types, hashlib, contextlib

ROOT = os.environ.get("HELM_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SRC = os.path.join(ROOT, "data", "helm.db")
TMP = tempfile.mkdtemp(prefix="s126_")
COPY = os.path.join(TMP, "helm_copy.db")
_s = sqlite3.connect("file:" + SRC + "?mode=ro", uri=True); _s.execute("VACUUM INTO ?", (COPY,)); _s.close()
os.environ["HELM_DB"] = COPY

_yf = types.ModuleType("yfinance")
_yf.Ticker = lambda t: types.SimpleNamespace(
    fast_info=types.SimpleNamespace(last_price=135.0, display_name=str(t)),
    options=(), history=lambda *a, **k: types.SimpleNamespace(empty=True), info={})
sys.modules["yfinance"] = _yf
IB_ATTEMPTS = []
try:
    import ib_insync as _ibi
    def _no(self, *a, **k):
        IB_ATTEMPTS.append(1); raise ConnectionRefusedError("blocked in harness")
    _ibi.IB.connect = _no
except ImportError:
    pass
try:
    import helm.vol_context as _vc
    _vc.backfill_entry_vol = lambda *a, **k: None
except Exception:
    pass

from helm.config import DB_PATH
assert str(DB_PATH) == COPY, DB_PATH
alt = os.environ.get("HARNESS_OPEN_CMD")
if alt:
    from importlib.machinery import SourceFileLoader
    po = SourceFileLoader("helm.cli.open_cmd", os.path.join(ROOT, alt)).load_module()
else:
    import helm.cli.open_cmd as po
SHA = hashlib.sha256(open(po.__file__, "rb").read()).hexdigest()[:12]
print("harness: open_cmd from %s (sha %s)\n         db copy %s" % (po.__file__, SHA, COPY))

CONDOR = {"expiration": "2026-10-23", "dte": 29, "long_put": 117.0, "short_put": 122.0,
          "short_call": 165.0, "long_call": 170.0, "put_width": 5.0, "call_width": 5.0,
          "put_delta": 0.177, "call_delta": 0.159, "put_iv": 0.41, "call_iv": 0.38,
          "put_oi": 900, "call_oi": 700, "total_credit": 1.25, "max_loss": 3.75,
          "cw_pct": 25.0, "rr_ratio": 0.33, "short_put_bid": 2.50, "long_put_ask": 1.62,
          "short_call_bid": 1.76, "long_call_ask": 1.39}
BPS = {"opt_type": "PUT", "expiration": "2026-11-20", "dte": 48, "short_strike": 95.0,
       "long_strike": 90.0, "width": 5.0, "net_credit": 1.20, "max_loss": 3.80,
       "credit_to_width_pct": 24.0, "short_bid": 2.30, "long_ask": 1.10, "delta": 0.24,
       "long_delta": 0.12, "iv": 0.35, "short_oi": 500, "long_oi": 400}
BCS = dict(BPS, opt_type="CALL", short_strike=200.0, long_strike=205.0, short_bid=2.10, long_ask=0.80, net_credit=1.30)
CANDS = {"condor": [CONDOR], "spread": [BPS]}
po.evaluate_condors = lambda *a, **k: list(CANDS["condor"])
po.evaluate_spreads = lambda *a, **k: list(CANDS["spread"])
po.display_condors = lambda t, s, cfg, cs, spot, atr, acct, args: po.confirm_condor(t, s, cs, cfg, spot, args)
po.display_spreads = lambda t, s, cfg, ss, spot, atr, acct, args: po.confirm_spread(t, s, ss, cfg, spot, args, best=(ss[0] if ss else None), suggested=1)

N, FAILS = 0, []
def ok(label, cond, detail=""):
    global N; N += 1
    print(("PASS  " if cond else "FAIL  ") + label + (("   [" + str(detail) + "]") if detail else ""))
    if not cond: FAILS.append(label)

def q(sql, *a):
    c = sqlite3.connect(COPY); c.row_factory = sqlite3.Row
    try: return [dict(r) for r in c.execute(sql, a)]
    finally: c.close()

def run(argv, feed):
    for k in ("contracts", "fill"): po._EXPECT[k] = None
    if hasattr(po, "_LEGS"):
        po._LEGS["spec"] = None; po._LEGS["expiry"] = None
    before = q("select coalesce(max(rowid),0) m from positions")[0]["m"]
    sys.argv = ["helm"] + [a for a in argv if a != "open"]
    old_in = sys.stdin; sys.stdin = io.StringIO(feed)
    buf = io.StringIO()
    po.console.file = buf
    try:
        with contextlib.redirect_stdout(buf):
            try: po.run()
            except (SystemExit, EOFError, Exception) as e: print("RAISED %r" % e)
    finally:
        sys.stdin = old_in
    po.console.file = sys.stdout
    new = q("select * from positions where rowid > ?", before)
    return new, buf.getvalue()

def legs_of(pid):
    return q("select leg_role,direction,option_type,strike,expiration,open_price,contracts,entry_delta "
             "from legs where position_id=? order by direction desc, option_type desc, strike", pid)

def shape(pid):
    return sorted((l["direction"], l["option_type"], l["strike"], l["expiration"], round(l["open_price"], 2), l["contracts"]) for l in legs_of(pid))

NOW_LEGS = sorted([("SHORT","PUT",120.0,"2026-10-30",3.02,3), ("LONG","PUT",115.0,"2026-10-30",2.01,3),
                   ("SHORT","CALL",170.0,"2026-10-30",2.33,3), ("LONG","CALL",175.0,"2026-10-30",1.81,3)])
SPEC = "115P@2.01,120P@3.02,170C@2.33,175C@1.81"

# A. named NOW legs, the board's path
new, out = run(["open", "NOW", "IRON_CONDOR", "--confirm", "--legs", SPEC, "--leg-expiry", "2026-10-30",
                "--expect-contracts", "3", "--expect-fill", "1.53"], "3\ny\n")
ok("A named legs: one position booked", len(new) == 1, out[-300:] if len(new) != 1 else "")
if new:
    p = new[0]
    ok("A legs are the fills exactly", shape(p["id"]) == NOW_LEGS, shape(p["id"]))
    ok("A position: net 459 / max loss 1041 / BE 118.47-171.53 / ctw 0.306",
       (p["net_premium"], p["max_profit"], p["max_loss"], p["breakeven_low"], p["breakeven_high"], p["credit_to_width_ratio"], p["spread_width"])
       == (459.0, 459.0, 1041.0, 118.47, 171.53, 0.306, 5.0), p)
    ok("A changed legs carry no borrowed entry delta", all(l["entry_delta"] is None for l in legs_of(p["id"])))
    ok("A success marker printed", "CONDOR logged " + p["id"] in out)

# B. named legs equal to HELM's pick keep the pick's entry greeks
new, out = run(["open", "NOW", "IRON_CONDOR", "--confirm", "--legs", "117P@1.60,122P@2.55,165C@1.80,170C@1.40",
                "--leg-expiry", "2026-10-23"], "1\ny\n")
ok("B one position", len(new) == 1, out[-300:] if len(new) != 1 else "")
if new:
    d = {(l["direction"], l["option_type"]): l["entry_delta"] for l in legs_of(new[0]["id"])}
    ok("B short deltas come from the matching candidate", d[("SHORT","PUT")] == 0.177 and d[("SHORT","CALL")] == 0.159, d)
    ok("B net from legs 1.35 x1 = 135", new[0]["net_premium"] == 135.0, new[0]["net_premium"])

# C. interactive: pick rank 1, then enter what was filled
feed = "1\n3\n2026-10-30\n120\n3.02\n115\n2.01\n170\n2.33\n175\n1.81\ny\n"
new, out = run(["open", "NOW", "IRON_CONDOR", "--confirm"], feed)
ok("C interactive per-leg: one position", len(new) == 1, out[-300:] if len(new) != 1 else "")
if new:
    ok("C legs are what was typed", shape(new[0]["id"]) == NOW_LEGS, shape(new[0]["id"]))
    ok("C net 459 derived, not typed", new[0]["net_premium"] == 459.0, new[0]["net_premium"])

# D. interactive, every default accepted -> HELM's pick at its modeled prices
new, out = run(["open", "NOW", "IRON_CONDOR", "--confirm"], "1\n1\n\n\n\n\n\n\n\n\n\ny\n")
ok("D all defaults: one position", len(new) == 1, out[-300:] if len(new) != 1 else "")
if new:
    ok("D defaults = candidate strikes and modeled prices",
       shape(new[0]["id"]) == sorted([("SHORT","PUT",122.0,"2026-10-23",2.50,1), ("LONG","PUT",117.0,"2026-10-23",1.62,1),
                                     ("SHORT","CALL",165.0,"2026-10-23",1.76,1), ("LONG","CALL",170.0,"2026-10-23",1.39,1)]),
       shape(new[0]["id"]))

# E. refusals book nothing
for lab, argv, feed in (
    ("E debit fills", ["--legs", "115P@3.02,120P@2.01,170C@1.81,175C@2.33", "--leg-expiry", "2026-10-30"], "3\ny\n"),
    ("E receipt mismatch", ["--legs", SPEC, "--leg-expiry", "2026-10-30", "--expect-fill", "1.25"], "3\ny\n"),
    ("E unreadable leg", ["--legs", "115P,120P@3.02,170C@2.33,175C@1.81", "--leg-expiry", "2026-10-30"], "3\ny\n"),
    ("E legs without expiry", ["--legs", SPEC], "3\ny\n"),
    ("E three legs", ["--legs", "115P@2.01,120P@3.02,170C@2.33", "--leg-expiry", "2026-10-30"], "3\ny\n"),
    ("E non-integer contracts", ["--legs", SPEC, "--leg-expiry", "2026-10-30"], "three\ny\n"),
    ("E declined at the final prompt", ["--legs", SPEC, "--leg-expiry", "2026-10-30"], "3\nn\n"),
):
    new, out = run(["open", "NOW", "IRON_CONDOR", "--confirm"] + argv, feed)
    ok(lab + ": nothing booked", len(new) == 0, out[-200:] if new else "")
new, out = run(["open", "NOW", "IRON_CONDOR", "--confirm"], "1\n3\n2026-10-30\n120\nabc\n")
ok("E interactive unreadable fill: nothing booked", len(new) == 0)

# F. credit verticals
new, out = run(["open", "XYZ", "BULL_PUT_SPREAD", "--confirm", "--legs", "95P@2.40 90P@1.10",
                "--leg-expiry", "2026-11-20", "--expect-fill", "1.30"], "2\ny\n")
ok("F bull put named: one position", len(new) == 1, out[-300:] if len(new) != 1 else "")
if new:
    ok("F bull put legs/net/BE", shape(new[0]["id"]) == sorted([("SHORT","PUT",95.0,"2026-11-20",2.40,2), ("LONG","PUT",90.0,"2026-11-20",1.10,2)])
       and (new[0]["net_premium"], new[0]["max_loss"], new[0]["breakeven_low"]) == (260.0, 740.0, 93.7), (shape(new[0]["id"]), new[0]))
    ok("F success marker", "SPREAD logged " + new[0]["id"] in out)
CANDS["spread"] = [BCS]
new, out = run(["open", "XYZ", "BEAR_CALL_SPREAD", "--confirm"], "1\n1\n2026-11-27\n200\n2.25\n210\n0.60\ny\n")
ok("F bear call interactive, strikes/expiry changed: one position", len(new) == 1, out[-300:] if len(new) != 1 else "")
if new:
    ok("F bear call legs/width/BE from what was typed",
       shape(new[0]["id"]) == sorted([("SHORT","CALL",200.0,"2026-11-27",2.25,1), ("LONG","CALL",210.0,"2026-11-27",0.60,1)])
       and (new[0]["spread_width"], new[0]["net_premium"], new[0]["max_loss"], new[0]["breakeven_high"]) == (10.0, 165.0, 835.0, 201.65),
       (shape(new[0]["id"]), new[0]))

# G. no candidates at all -> named legs still book
CANDS["condor"] = []
new, out = run(["open", "NOW", "IRON_CONDOR", "--confirm", "--legs", SPEC, "--leg-expiry", "2026-10-30"], "3\ny\n")
ok("G no candidates, named legs: one position", len(new) == 1, out[-300:] if len(new) != 1 else "")
po.evaluate_condors = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("chain down"))
new, out = run(["open", "NOW", "IRON_CONDOR", "--confirm", "--legs", SPEC, "--leg-expiry", "2026-10-30"], "3\ny\n")
ok("G evaluator raises, named legs: one position", len(new) == 1, out[-300:] if len(new) != 1 else "")
new, out = run(["open", "NOW", "IRON_CONDOR", "--confirm"], "1\n3\n")
ok("G evaluator raises, no legs: nothing booked", len(new) == 0)

ok("no IB connect attempted", not IB_ATTEMPTS, len(IB_ATTEMPTS))
print("\n%d/%d PASS" % (N - len(FAILS), N) + ("" if not FAILS else "   FAILED: " + "; ".join(FAILS)))
sys.exit(1 if FAILS else 0)
