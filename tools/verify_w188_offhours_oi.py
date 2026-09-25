"""tools/verify_w188_offhours_oi.py -- W188: `watchlist add --evaluate` must never
mark a name illiquid on Yahoo's off-hours open interest.

    HELM_ROOT=~/Projects/helm python3 tools/verify_w188_offhours_oi.py

No network, no database: a stand-in yfinance serves either JUNK open interest
(what Yahoo returned pre-market on 2026-09-25: KO 11, MSFT 144, ECL 526) or a
REAL figure (MOD 27,351 during the session), and every case fixes its own clock.

The feared defect, with the case that fails if it comes back:
  * a junk pre-market OI turned into FLAG -> is_optionable 0 -> hidden from the
    scan (ECL, 09-25)                                   -> offhours_not_flagged
"""
import os, sys, types, hashlib
from datetime import datetime

ROOT = os.environ.get("HELM_ROOT") or os.getcwd()
sys.path.insert(0, ROOT)
FAILS = []


def case(name, got, want):
    ok = got == want
    print("%s  %-24s got %r%s" % ("PASS" if ok else "FAIL", name, got,
                                  "" if ok else "   want %r" % (want,)))
    if not ok:
        FAILS.append(name)


import pandas as pd

MODE = {"oi": 526, "cap": 77.2e9, "options": True}
CALLS = {"chain": 0}


class _TK:
    def __init__(self, t):
        self.fast_info = types.SimpleNamespace(last_price=275.0, market_cap=MODE["cap"],
                                               fifty_two_week_high=300.0, fifty_two_week_low=220.0)
        self.info = {"longName": "Test Co", "sector": "Basic Materials", "beta": 0.9}

    @property
    def options(self):
        return ("2026-10-16", "2026-11-20") if MODE["options"] else ()

    def option_chain(self, exp):
        CALLS["chain"] += 1
        # The whole figure on one row of the first expiry, so the total is exact
        # (a split across rows is truncated per expiry by int() -- a fixture
        # artefact, not a behaviour under test).
        first = exp == "2026-10-16"
        calls = pd.DataFrame({"openInterest": [MODE["oi"] if first else 0, 0], "volume": [10, 10]})
        puts = pd.DataFrame({"openInterest": [0, 0], "volume": [10, 10]})
        return types.SimpleNamespace(calls=calls, puts=puts)


sys.modules["yfinance"] = types.SimpleNamespace(Ticker=_TK)
from helm.cli import watchlist as W
print("LOADED %s  %s" % (W.__file__, hashlib.sha256(open(W.__file__, "rb").read()).hexdigest()[:12]))

PRE = datetime(2026, 9, 25, 6, 28)     # Friday, pre-market -- the ECL add
RTH = datetime(2026, 9, 25, 10, 30)    # Friday, in session -- the MOD add
POST = datetime(2026, 9, 25, 16, 5)
SAT = datetime(2026, 9, 26, 11, 0)

case("clock_pre", W.oi_measurable(PRE), False)
case("clock_rth", W.oi_measurable(RTH), True)
case("clock_post", W.oi_measurable(POST), False)
case("clock_weekend", W.oi_measurable(SAT), False)

# the ECL case: big cap, junk OI, pre-market
MODE.update(oi=526, cap=77.2e9, options=True); CALLS["chain"] = 0
r = W.quick_eval("ECL", include_options=True, now=PRE)
case("offhours_not_flagged", r["verdict"], "STRONG")
case("offhours_optionable", 0 if r["verdict"] == "FLAG" else 1, 1)   # _confirm_and_add's rule
case("offhours_oi_unread", (r["total_oi"], r["oi_level"], CALLS["chain"]), (None, "not measured", 0))
case("offhours_says_so", "OI not measured off-hours" in (r["verdict_reason"] or ""), True)

# a small cap pre-market: MARGINAL on cap, still never FLAG on OI
MODE.update(oi=11, cap=0.9e9); r = W.quick_eval("SMALL", now=PRE)
case("offhours_small_cap", r["verdict"], "MARGINAL")

# no options at all is a FACT and still flags, at any hour
MODE.update(options=False); r = W.quick_eval("NOOPT", now=PRE)
case("no_options_flags", (r["verdict"], r["verdict_reason"]), ("FLAG", "No options available"))
MODE.update(options=True)

# in session nothing changes: real OI reads and grades as before
MODE.update(oi=27351, cap=11e9); CALLS["chain"] = 0
r = W.quick_eval("MOD", now=RTH)
case("session_real_oi", (r["verdict"], r["total_oi"], r["oi_note"]), ("STRONG", 27351, None))
case("session_reads_chain", CALLS["chain"], 2)
MODE.update(oi=526, cap=77.2e9); r = W.quick_eval("THIN", now=RTH)
case("session_thin_still_flags", r["verdict"], "FLAG")

print("\nALL PASS" if not FAILS else "\n%d FAIL%s" % (len(FAILS), "" if len(FAILS) == 1 else "S"))
sys.exit(1 if FAILS else 0)
