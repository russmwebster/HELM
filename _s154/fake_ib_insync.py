# s154 harness stand-in for ib_insync: FAKEIB=down refuses to connect; FAKEIB=csco serves CSCO's saved bars only.
import csv, os
class _B:
    def __init__(self, d, c): self.date, self.close = d, c
class Stock:
    def __init__(self, sym, *a, **k): self.symbol = sym
class IB:
    RequestTimeout = 0
    def connect(self, *a, **k):
        if os.environ.get("FAKEIB", "down") == "down":
            raise ConnectionRefusedError("stand-in: IB Gateway blocked by the harness")
    def reqHistoricalData(self, c, **k):
        if c.symbol != "CSCO": return []
        f = "/sessions/rcw-01tujrytpkwrspq5kpnttbqy/mnt/helm/_s154/csco_bars_ibkr.csv"
        return [_B(r["Date"], float(r["Close"])) for r in csv.DictReader(open(f))]
    def disconnect(self): pass
