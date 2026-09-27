# s122: download ~10y daily closes/volume for the watchlist + SPY + sector ETFs.
# Read-only on helm.db. Writes CSVs to _s122/px/. Usage: python3 fetch_px.py START COUNT
import sys, os, sqlite3
import yfinance as yf
HERE = os.path.dirname(os.path.abspath(__file__))
db = sqlite3.connect("file:" + os.path.join(HERE, "..", "data", "helm.db") + "?mode=ro", uri=True)
tick = sorted({r[0] for r in db.execute("select ticker from watchlist")})
tick = ["SPY","XLK","XLE","XLF","XLV","XLI","XLY","XLP","XLB","XLU","XLC","XLRE"] + [t for t in tick if t not in ("SPY",)]
a, n = int(sys.argv[1]), int(sys.argv[2])
chunk = tick[a:a+n]
d = yf.download(chunk, period="10y", auto_adjust=True, progress=False, group_by="ticker", threads=True)
ok = []
for t in chunk:
    try:
        x = d[t][["Close","Volume"]].dropna() if len(chunk) > 1 else d[["Close","Volume"]].dropna()
        if len(x) > 200:
            x.to_csv(os.path.join(HERE, "px", t + ".csv")); ok.append(t)
    except Exception:
        pass
print("total", len(tick), "chunk", a, "ok", len(ok), "missing", [t for t in chunk if t not in ok])
