"""s96 — cost-to-close track + the price-derived buy-back clause.

Behavioural checks for close_series / position_value / close_svg / the price force."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from helm import thesis as th

P, F = 0, 0
def ck(name, cond):
    global P, F
    if cond: P += 1; print("  PASS", name)
    else: F += 1; print("  FAIL", name)

# --- position_value: the identity, both directions, against real rows ---------
ck("META credit identity (2069 - -4961 = 7030)", th.position_value(2069.0, -4961.0) == 7030.0)
ck("CSCO credit identity (2935 - 310 = 2625)", th.position_value(2935.0, 310.0) == 2625.0)
ck("LRCX condor identity (9560 - -7460 = 17020)", th.position_value(9560.0, -7460.0) == 17020.0)
ck("APLD debit identity (4942 + -4844 = 98)", abs(th.position_value(-4942.0, -4844.0) - 98.0) < 1e-6)
ck("GOOG debit identity (4082 + -3082 = 1000)", abs(th.position_value(-4082.0, -3082.0) - 1000.0) < 1e-6)
ck("NKE debit identity (5000 + -3020 = 1980)", abs(th.position_value(-5000.0, -3020.0) - 1980.0) < 1e-6)
ck("None mark -> None", th.position_value(2069.0, None) is None)
ck("None premium -> None", th.position_value(None, -100.0) is None)
ck("zero premium -> None", th.position_value(0.0, -100.0) is None)

# --- close_series: last-of-day, not best-of-day -------------------------------
checks = [
    {"checked_at": "2026-07-30T10:00:00", "pnl_unrealized": -500.0},
    {"checked_at": "2026-07-30T12:30:00", "pnl_unrealized": -100.0},   # best of day
    {"checked_at": "2026-07-30T15:45:00", "pnl_unrealized": -800.0},   # LAST of day
    {"checked_at": "2026-07-31T10:00:00", "pnl_unrealized": -200.0},
]
pos = {"net_premium": 1000.0, "id": "X", "ticker": "X"}
tr = th.close_series(pos, checks, closed=False, today="2026-07-31")
ck("two days bucketed", tr["n_days"] == 2)
ck("day 1 takes the LAST check (1000 - -800 = 1800)", tr["points"][0]["value"] == 1800.0)
ck("day 1 is NOT the best-of-day value (1100)", tr["points"][0]["value"] != 1100.0)
ck("day 1 lo/hi span the whole day", (tr["points"][0]["lo"], tr["points"][0]["hi"]) == (1100.0, 1800.0))
ck("day 1 records 3 checks", tr["points"][0]["n"] == 3)
ck("day 1 time is the last check's", tr["points"][0]["time"] == "15:45")
ck("today's point flagged provisional", tr["points"][-1].get("provisional") is True)
ck("track-level provisional flag", tr["provisional"] is True)
ck("net equals the latest mark", tr["net"] == -200.0)
ck("credit flag true", tr["credit"] is True)

tr_closed = th.close_series(pos, checks, closed=True, today="2026-07-31")
ck("closed position is never provisional", tr_closed["provisional"] is False)

# --- debit side ---------------------------------------------------------------
dpos = {"net_premium": -1000.0}
dtr = th.close_series(dpos, [{"checked_at": "2026-07-31T15:45:00", "pnl_unrealized": -400.0}])
ck("debit value = paid + mark (600)", dtr["points"][0]["value"] == 600.0)
ck("debit flag false", dtr["credit"] is False)

# --- never invented ------------------------------------------------------------
ck("no checks -> None", th.close_series(pos, []) is None)
ck("marks all NULL -> None",
   th.close_series(pos, [{"checked_at": "2026-07-31T10:00:00", "pnl_unrealized": None}]) is None)
ck("no premium -> None", th.close_series({"net_premium": None}, checks) is None)

# --- headline: credit closes are always a debit --------------------------------
h_meta = th.close_headline(th.close_series({"net_premium": 2069.0},
         [{"checked_at": "2026-07-31T15:48:00", "pnl_unrealized": -4961.0}]))
ck("META headline says debit", "debit" in h_meta and "$7,030" in h_meta)
ck("META headline leads with the net (HELM-146 copy)", "new money" in h_meta)
h_csco = th.close_headline(th.close_series({"net_premium": 2935.0},
         [{"checked_at": "2026-07-31T15:47:00", "pnl_unrealized": 310.0}]))
ck("CSCO headline says debit too (credit structures always are)", "debit" in h_csco)
ck("CSCO headline says you keep $310", "keep" in h_csco and "$310" in h_csco)
h_long = th.close_headline(th.close_series({"net_premium": -4082.0},
         [{"checked_at": "2026-07-31T15:47:00", "pnl_unrealized": -3082.0}]))
ck("long headline says credit", "credit" in h_long and "$1,000" in h_long)

# --- svg -----------------------------------------------------------------------
svg = th.close_svg(th.close_series({"net_premium": 2069.0}, [
    {"checked_at": "2026-07-%02dT15:45:00" % d, "pnl_unrealized": -100.0 * d} for d in range(15, 28)]))
ck("svg produced", svg and svg.startswith("<svg"))
ck("svg has no script", "<script" not in svg)
ck("svg has no external ref", "http://" not in svg and "https://" not in svg)
ck("svg carries an aria-label", 'role="img"' in svg and "aria-label" in svg)
ck("svg none when no track", th.close_svg(None) is None)

# --- the old clause is gone ----------------------------------------------------
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "helm", "thesis.py")).read()
ck("old buy-back gloss removed", "costs %s to buy back)" not in src)
ck("old repriced gloss removed", "the option you own is repriced" not in src)
ck("IV attribution kept", 'forces.append("IV %.1f → %.1f — %s you"' in src)
ck("price-derived force present", "costs %s to buy back what you sold for %s" in src)
ck("exit_track untouched", "def exit_track(legs, checks, closed):" in src)

print("\n%d passed, %d failed" % (P, F))
sys.exit(1 if F else 0)
