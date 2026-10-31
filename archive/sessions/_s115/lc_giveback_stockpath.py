"""After a long call's option price peaks and declines, does the STOCK get
back to where it was at the peak before the option expires?

Upper bound on the option recovering: theta has run since the peak, so the
option needs the stock to exceed the peak-day price, not merely reach it.
Reads _s115/lc_giveback_events.json, writes _s115/lc_giveback_results.json.
yfinance daily bars, read-only, nothing in HELM touched.
"""
import json, sys, datetime as dt
H = "/Users/russmacbookpro/Projects/helm/_s115/"
import yfinance as yf
ev = json.load(open(H + "lc_giveback_events.json"))
today = dt.date.today().isoformat()
tickers = sorted({e["ticker"] for e in ev})
start = min(e["ev_day"] for e in ev)
px = {}
for t in tickers:
    try:
        h = yf.Ticker(t).history(start=start, end=None, auto_adjust=False, actions=False)
        px[t] = [(d.strftime("%Y-%m-%d"), float(r["High"]), float(r["Close"])) for d, r in h.iterrows()]
    except Exception as ex:
        px[t] = []
out = []
for e in ev:
    end = min(e["expiry"], today)
    path = [p for p in px.get(e["ticker"], []) if e["ev_day"] < p[0] <= end]
    tgt = e["hwm_spot"]
    rec_close = next((p for p in path if p[2] >= tgt), None)
    rec_high = next((p for p in path if p[1] >= tgt), None)
    max_close = max((p[2] for p in path), default=None)
    # a rough "with theta to spare" bar: 2% above the peak-day spot
    rec_close_2 = next((p for p in path if p[2] >= tgt * 1.02), None)
    o = dict(e)
    o.update(dict(obs_end=end, obs_days=len(path), expired=(e["expiry"] <= today),
                  stock_regained_close=bool(rec_close), stock_regained_high=bool(rec_high),
                  stock_regained_close_plus2=bool(rec_close_2),
                  days_to_regain=(path.index(rec_close) + 1) if rec_close else None,
                  max_close_after=round(max_close, 2) if max_close else None,
                  max_close_vs_peak=round(max_close / tgt * 100 - 100, 1) if max_close else None))
    out.append(o)
json.dump(out, open(H + "lc_giveback_results.json", "w"), indent=1)
print("wrote %d results; tickers with no price data: %s" % (len(out), [t for t in tickers if not px[t]]))
