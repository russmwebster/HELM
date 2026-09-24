# TEST STAND-IN for yfinance (W180 step 6 PG harness). Never on a real path.
import types
import pandas as pd
CHAIN = {"2026-10-16": [(55.0, 0.15, 0.19, 0.17, 0.60, 900)],
         "2026-11-20": [(50.0, 0.95, 1.05, 1.00, 0.55, 500), (55.0, 0.40, 0.50, 0.45, 0.52, 700)],
         "2026-12-18": [(47.0, 3.10, 3.30, 3.20, 0.50, 400)]}
class Ticker:
    def __init__(self, t):
        self.fast_info = types.SimpleNamespace(last_price=44.58, display_name="")
        self.options = tuple(CHAIN)
    def option_chain(self, exp):
        df = pd.DataFrame(CHAIN[exp], columns=["strike","bid","ask","lastPrice","impliedVolatility","openInterest"])
        return types.SimpleNamespace(calls=df, puts=df.iloc[0:0])
    def history(self, period="5d"):
        return pd.DataFrame({"Close": [44.58]})
