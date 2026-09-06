"""The option's OWN price after HELM stopped watching. Daily MIDPOINT bars
from IBKR for each of the 14 event contracts (includeExpired), so the
recovery question is asked of the option, not the stock. Read-only,
clientId 61 (HELM uses 11; the ibkr-data MCP uses 84). Writes only to _s115/.
"""
import json, sys, datetime as dt
sys.path.insert(0, "/Users/russmacbookpro/Projects/helm")
H = "/Users/russmacbookpro/Projects/helm/_s115/"
from ib_insync import IB, Option
R = json.load(open(H + "lc_events_clean.json"))
ib = IB(); ib.connect("127.0.0.1", 4002, clientId=61, timeout=15, readonly=True)
ib.reqMarketDataType(3)
today = dt.date.today()
seen = {}
for r in R:
    exp = r["expiry"].replace("-", "")
    key = (r["ticker"], exp, r["strike"])
    if key in seen:
        r["opt"] = seen[key]; continue
    c = Option(r["ticker"], exp, float(r["strike"]), "C", "SMART", includeExpired=True)
    try:
        q = ib.qualifyContracts(c)
        if not q:
            r["opt"] = {"error": "unqualified"}; seen[key] = r["opt"]; continue
        end = min(dt.date.fromisoformat(r["expiry"]), today)
        endstr = end.strftime("%Y%m%d 23:59:59") if end < today else ""
        first = dt.date.fromisoformat(r["opened"])
        dur = "%d D" % max(10, (end - first).days + 6)
        bars = ib.reqHistoricalData(c, endDateTime=endstr, durationStr=dur,
                                    barSizeSetting="1 day", whatToShow="TRADES",
                                    useRTH=True, formatDate=1)
        src = "TRADES/1d"
        if not bars:
            hb = ib.reqHistoricalData(c, endDateTime=endstr, durationStr=dur,
                                      barSizeSetting="1 hour", whatToShow="MIDPOINT",
                                      useRTH=True, formatDate=1)
            # collapse hourly midpoint to one bar per day: last close, day high
            day = {}
            for b in hb:
                d = b.date.date().isoformat() if hasattr(b.date, "date") else str(b.date)[:10]
                hi = max(day.get(d, (0, 0))[1], b.high)
                day[d] = (b.close, hi)
            src = "MIDPOINT/1h"
            r["opt"] = {"src": src, "bars": [[d, round(v[0], 2), round(v[1], 2)] for d, v in sorted(day.items())]}
        else:
            r["opt"] = {"src": src, "bars": [[b.date.isoformat(), round(b.close, 2), round(b.high, 2)] for b in bars]}
    except Exception as ex:
        r["opt"] = {"error": str(ex)[:120]}
    seen[key] = r["opt"]
    ib.sleep(0.4)
ib.disconnect()
json.dump(R, open(H + "lc_events_clean2.json", "w"))
print([(r["ticker"], r["expiry"][:7], len(r["opt"].get("bars", [])) or r["opt"].get("error")) for r in R])
