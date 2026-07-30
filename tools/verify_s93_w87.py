#!/usr/bin/env python3
"""HELM-140 / W87 verification: the single-leg evaluator refuses a contract
whose delta cannot be measured (no greek, no IV for the BS fallback), on the
yfinance path MA came through. Monkeypatched chain -- no network, no DB writes.
"""
import os, sys, types
from datetime import date, timedelta
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HELM_ROOT", str(ROOT))

import pandas as pd
import helm.cli.open_cmd as oc

PASS = FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond: PASS += 1; print("  ok  " + name)
    else:    FAIL += 1; print("FAIL  " + name)

SPOT = 566.74
EXP = (date.today() + timedelta(days=37)).isoformat()

def mkrow(strike, bid, ask, iv, oi=500):
    return {"strike": strike, "bid": bid, "ask": ask, "lastPrice": (bid+ask)/2,
            "impliedVolatility": iv, "openInterest": oi, "volume": 50,
            "contractSymbol": f"MA{strike}"}

class FakeChain:
    def __init__(self, puts): self.puts = pd.DataFrame(puts); self.calls = pd.DataFrame([])
class FakeInfo:
    last_price = SPOT
class FakeTicker:
    def __init__(self, *a, **k): pass
    fast_info = FakeInfo()
    options = (EXP,)
    def option_chain(self, exp):
        return FakeChain([
            mkrow(585.0, 25.5, 26.5, 0.0),   # THE MA ARTIFACT: ITM, iv 0 -> no BS fallback, no delta
            mkrow(535.0, 6.1, 6.5, 0.30),    # honest OTM row: BS delta ~0.24, mid-band
        ])
    def history(self, *a, **k):
        idx = pd.date_range(end=date.today(), periods=30)
        return pd.DataFrame({"Close": [SPOT]*30, "High": [SPOT*1.01]*30, "Low": [SPOT*0.99]*30}, index=idx)

import yfinance as yf
_orig = yf.Ticker
yf.Ticker = FakeTicker
try:
    cfg = oc.STRATEGY_CONFIG["CSP"]
    rows = oc.evaluate_contracts("MA", "CSP", cfg)
finally:
    yf.Ticker = _orig

strikes = [r.get("strike") for r in rows]
print("returned strikes:", strikes)
check("the delta-less ITM 585 is REFUSED", 585.0 not in strikes)
check("the measurable OTM 535 still qualifies", 535.0 in strikes)
check("every returned row carries a delta", all(r.get("delta") is not None for r in rows))
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
