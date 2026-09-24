"""tools/verify_w180_candidates.py -- the re-sell candidates (`helm resell TICKER
--candidates`): what could be sold against a held long, never inside its strike.

    HELM_ROOT=<tree> HELM_DB=<copy.db> python3 tools/verify_w180_candidates.py

Reads HELM_DB (a VACUUM INTO copy; nothing is written). The chain is a STAND-IN
with rows placed to trip every rule, so no case depends on the market.

Feared defects, each with a case that fails if it comes back:
  * a strike inside the long's (below it for a call, above for a put) -> never_inside
  * an expiry past the long's 21-DTE date                              -> expiry_window
  * the lift of _row_contract changing what chain_contract returns    -> contract_equiv
"""
import os, sys, io, json, types, hashlib, importlib.util
from datetime import date, timedelta
from importlib.machinery import SourceFileLoader

ROOT = os.environ["HELM_ROOT"]
sys.path.insert(0, ROOT)
FAILS = []


def case(name, got, want):
    ok = got == want
    print("%s  %-20s got %r%s" % ("PASS" if ok else "FAIL", name, got,
                                  "" if ok else "   want %r" % (want,)))
    if not ok:
        FAILS.append(name)


import pandas as pd
T = date.today()
E = lambda d: (T + timedelta(days=d)).isoformat()
# AA's long is the $47 call expiring 2026-12-18, so its 21-DTE date is 2026-11-27.
CUT = date(2026, 11, 27)
EXP = {E(8): "too_near", E(22): "in_band", E(36): "in_band", "2026-11-20": "late_ok",
       "2026-12-18": "after_cut"}
ROWS = [  # strike, bid, ask, last, iv, oi
    (44.0, 1.60, 1.70, 1.65, 0.55, 100),   # inside the long's $47 -- must never appear
    (47.0, 0.60, 0.70, 0.65, 0.50, 300),
    (50.0, 0.20, 0.26, 0.23, 0.52, 400),
    (55.0, 0.02, 0.05, 0.03, 0.60, 900),   # a nickel or less -- worthless floor
]


class _TK:
    def __init__(self, t):
        self.fast_info = types.SimpleNamespace(last_price=44.58)
        self.options = tuple(EXP)

    def option_chain(self, exp):
        df = pd.DataFrame(ROWS, columns=["strike", "bid", "ask", "lastPrice",
                                         "impliedVolatility", "openInterest"])
        return types.SimpleNamespace(calls=df, puts=df)

    def history(self, period="5d"):
        return pd.DataFrame({"Close": [44.58]})


sys.modules["yfinance"] = types.SimpleNamespace(Ticker=_TK)

from helm.cli import diagonal as DG, resell_cmd as R
from helm.cli.open_cmd import STRATEGY_CONFIG
for m in (DG, R):
    print("LOADED %s  %s" % (m.__file__, hashlib.sha256(open(m.__file__, "rb").read()).hexdigest()[:12]))

# ---- the universe ------------------------------------------------------------
spot, cands, st = DG.chain_candidates("AA", 47, "2026-12-18", "CALL")
case("never_inside", min(c["strike"] for c in cands) >= 47, True)
case("expiry_window", sorted({c["expiration"] for c in cands}),
     sorted(e for e in EXP if 14 <= (date.fromisoformat(e) - T).days
            and date.fromisoformat(e) <= CUT))
case("worthless_out", any(c["mid"] <= 0.05 for c in cands), False)
case("stats", (st["inside_long_strike"], st["last_expiry_allowed"]),
     (st["expiries_in_window"] * 1, "2026-11-27"))
case("count", len(cands), st["expiries_in_window"] * 2)       # $47 and $50 per expiry
_s, pc, pst = DG.chain_candidates("AA", 47, "2026-12-18", "PUT")
case("put_side_mirror", max(c["strike"] for c in pc) <= 47, True)

# ---- ranking -----------------------------------------------------------------
cfg = STRATEGY_CONFIG["DIAGONAL"]
rk = R.rank_candidates(cands, cfg, 5)
first_band = [(c["in_dte"] and c["in_delta"]) for c in rk]
case("bands_first", first_band == sorted(first_band, reverse=True), True)
case("rent_math", rk[0]["rent"] == round(rk[0]["mid"] * 500, 2), True)
case("per_day", all(c["rent_per_day"] == round(c["rent"] / c["dte"], 2) for c in rk), True)

# ---- the command, on the copy -------------------------------------------------
sys.argv = ["helm", "AA", "--candidates", "--json"]
buf = io.StringIO()
_o = sys.stdout
sys.stdout = buf
R.run()
sys.stdout = _o
j = json.loads(buf.getvalue())
case("json_ok", (j["ok"], j["long"]["strike"], j["long"]["contracts"]), (True, 47.0, 5))
case("json_never_inside", all(c["strike"] >= 47 for c in j["candidates"]), True)
case("json_note", bool(j["note"]) == (max(abs(c["delta"] or 0) for c in j["candidates"])
                                      < cfg["short_delta_min"]), True)
sys.argv = ["helm", "AA", "--candidates"]
b2 = io.StringIO()
R.console.file = b2
R.run()
txt = b2.getvalue()
case("table_next_line", "Next: helm resell AA --strike" in txt and "--dry-run" in txt, True)
sys.argv = ["helm", "ZZZZ", "--candidates", "--json"]
buf = io.StringIO()
sys.stdout = buf
R.run()
sys.stdout = _o
case("json_refusal", json.loads(buf.getvalue())["ok"], False)

# ---- _row_contract lift: chain_contract returns what it returned before --------
before_p = os.environ.get("HELM_BEFORE")
if before_p:
    spec = importlib.util.spec_from_loader("diag_before", SourceFileLoader("diag_before", before_p))
    before = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(before)
    for k in (47, 50):
        case("contract_equiv_%d" % k, DG.chain_contract("AA", k, E(22), "short"),
             before.chain_contract("AA", k, E(22), "short"))
else:
    print("NOTE  contract_equiv skipped: set HELM_BEFORE to the pre-change diagonal.py")

print("\nALL PASS" if not FAILS else "\n%d FAIL%s" % (len(FAILS), "" if len(FAILS) == 1 else "S"))
sys.exit(1 if FAILS else 0)
