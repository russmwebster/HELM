"""Bootstrap for the W163 harness: install a FAKE yfinance (the VM has no
market data), then run `helm open` in-process with the given argv. Chain
variant chosen by W163_CHAIN env: 'normal' (screen finds pairs; AA's 55/47 is
too thin to screen) or 'thin' (screen finds nothing at all)."""
import os, sys, types
import pandas as pd

VARIANT = os.environ.get("W163_CHAIN", "normal")
SPOT = 52.0
EXPS = ["2026-09-18", "2026-10-16", "2026-11-20", "2026-12-18", "2027-01-15"]

def _rows(exp):
    # strike, bid, ask, last, iv, oi
    if exp == "2026-10-16":
        r = [(54, 2.6, 2.8, 2.7, 0.45, 800), (55, 2.2, 2.4, 2.3, 0.45, 20), (57, 1.6, 1.8, 1.7, 0.45, 600)]
    elif exp == "2026-12-18":
        r = [(45, 9.8, 10.2, 10.0, 0.45, 300), (47, 8.8, 9.1, 8.95, 0.45, 15), (48, 8.0, 8.3, 8.1, 0.45, 400)]
    elif exp == "2026-11-20":
        r = [(46, 8.5, 8.9, 8.7, 0.45, 200)]
    else:
        r = [(50, 3.0, 3.3, 3.1, 0.45, 100)]
    if VARIANT == "thin":
        r = [(s, b, a, l, iv, 5) for (s, b, a, l, iv, _) in r]
    return pd.DataFrame([{"strike": float(s), "bid": b, "ask": a, "lastPrice": l,
                          "impliedVolatility": iv, "openInterest": oi} for s, b, a, l, iv, oi in r])

class _Chain:
    def __init__(self, exp): self.calls = _rows(exp); self.puts = self.calls.iloc[0:0]
class _FastInfo:
    last_price = SPOT
    display_name = "Alcoa Corporation"
class Ticker:
    def __init__(self, t): self.t = t; self.options = tuple(EXPS); self.fast_info = _FastInfo()
    def option_chain(self, exp): return _Chain(exp)
    def history(self, period="5d"): return pd.DataFrame({"Close": [SPOT]})

yf = types.ModuleType("yfinance"); yf.Ticker = Ticker
sys.modules["yfinance"] = yf

sys.path.insert(0, os.environ["HELM_ROOT"])
sys.argv = ["open"] + sys.argv[1:]
from helm.cli import open_cmd
open_cmd.run()
