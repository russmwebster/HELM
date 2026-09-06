import json, datetime as dt, yfinance as yf
H="/Users/russmacbookpro/Projects/helm/_s115/"
R=json.load(open(H+"lc_graph_input.json")); today=dt.date.today().isoformat()
cache={}
for r in R:
    t=r['ticker']; start=(dt.date.fromisoformat(r['opened'])-dt.timedelta(days=4)).isoformat()
    key=(t,start)
    if key not in cache:
        h=yf.Ticker(t).history(start=start,end=None,auto_adjust=False,actions=False)
        cache[key]=[[d.strftime("%Y-%m-%d"),round(float(x["Close"]),2)] for d,x in h.iterrows()]
    end=min(r['expiry'],today)
    r['stock']=[p for p in cache[key] if p[0]<=end]
json.dump(R,open(H+"lc_graph_data.json","w"))
print("ok", [(r['ticker'],len(r['stock'])) for r in R])
