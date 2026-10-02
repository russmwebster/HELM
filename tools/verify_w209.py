"""W209 (Russ, 2026-10-02): the scan's earnings gate covers every route that sells
a short -- CSP, IRON_CONDOR, BEAR_CALL_SPREAD (as before) plus BULL_PUT_SPREAD,
DIAGONAL, PMCC, DIAGONAL_PUT. Covered calls stay ungated (deliberate).
Checks scan_cmd.sell_earn_gate case by case, the module's own constants, and
replays the signals table: which historical routes the wider gate would have
demoted. Usage: python3 tools/verify_w209.py   (HELM_ROOT set)
Perturbation run 2026-10-02: DIAGONAL removed from SELL_EARN_GATED -> FAIL."""
import os, sqlite3, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from helm.cli import scan_cmd as S
print("candidate:", S.__file__)
P = F = 0
def ok(n, cond, det=""):
    global P, F
    P += bool(cond); F += (not cond)
    print(("PASS " if cond else "FAIL ") + n + ("" if cond else "  %r" % (det,)))
def gate(strategy, d2e, **kw):
    r = dict(strategy=strategy, days_to_earnings=d2e, **kw)
    S.sell_earn_gate([r]); return r
ok("veto days is 10", S.SELL_EARN_VETO_DAYS == 10)
for st in ("CSP", "IRON_CONDOR", "BEAR_CALL_SPREAD", "BULL_PUT_SPREAD", "DIAGONAL", "PMCC", "DIAGONAL_PUT"):
    r = gate(st, 7)
    ok("%s at 7d gated, route kept" % st, r["strategy"] == "NO_SELL_EARNINGS" and r["strategy_shadow"] == st, r)
r = gate("DIAGONAL", 10); ok("DIAGONAL at 10d gated (edge)", r["strategy"] == "NO_SELL_EARNINGS", r)
r = gate("DIAGONAL", 11); ok("DIAGONAL at 11d not gated", r["strategy"] == "DIAGONAL", r)
r = gate("DIAGONAL", 0); ok("DIAGONAL on the day gated", r["strategy"] == "NO_SELL_EARNINGS", r)
r = gate("DIAGONAL", None); ok("missing date does not gate", r["strategy"] == "DIAGONAL", r)
r = gate("DIAGONAL", -2); ok("stale (past) date does not gate", r["strategy"] == "DIAGONAL", r)
r = gate("COVERED_CALL", 3); ok("COVERED_CALL not gated (deliberate)", r["strategy"] == "COVERED_CALL", r)
r = gate("LONG_CALL", 3); ok("LONG_CALL not gated", r["strategy"] == "LONG_CALL", r)
r = gate("DIAGONAL", 3, error="x"); ok("error row untouched", r["strategy"] == "DIAGONAL", r)
r = gate("DIAGONAL", 7); ok("rationale names the route", "route was DIAGONAL" in r["strategy_rationale"], r)
# replay
c = sqlite3.connect("file:" + os.path.join(HERE, "data", "helm.db") + "?mode=ro", uri=True)
new_only = ("BULL_PUT_SPREAD", "DIAGONAL", "PMCC", "DIAGONAL_PUT")
rows = c.execute("select ticker, substr(generated_at,1,16), top_strategy, days_to_earnings from signals "
                 "where top_strategy in (%s) and days_to_earnings between 0 and 10 order by generated_at"
                 % ",".join("?" * len(new_only)), new_only).fetchall()
print("historical scan rows the wider gate would have demoted: %d" % len(rows))
for r in rows: print("   ", r)
try:
    from helm.cli._paper_generate import paperable_strategies
    ok("paper books a gated DIAGONAL's shadow route (SELL_GATED)", "DIAGONAL" in paperable_strategies())
except Exception as e:
    ok("paperable_strategies readable", False, e)
print("PASS %d  FAIL %d" % (P, F)); sys.exit(1 if F else 0)
