#!/usr/bin/env python3
"""HELM-075 market-data boundary sampler (temporary diagnostic).

One-shot: connect to IBKR, sample a few tickers, append one CSV row each,
disconnect. Scheduled via launchd (com.helm.mktsampler) every 10 min around
the clock to map when IBKR flips live<->frozen at the pre/post-market and
overnight boundaries. Records t.marketDataType (1=live, 2=frozen, 3=delayed,
4=delayed-frozen) alongside last/close so the transitions are visible.

A NOCONN row (connected=0) is itself signal -- it marks when the IB gateway
is down (e.g. the overnight reset).

Not part of HELM proper. Remove the launch agent + this file once the
boundaries are documented into HELM-075.
"""
import csv, os, math
from datetime import datetime

TICKERS = ["LRCX", "AAPL", "OKLO"]   # liquid, very-liquid, thin spec name
HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(HERE, "logs", "mktdata_samples.csv")
HEADER = ["ts", "ticker", "connected", "got_type", "last", "close",
          "bid", "ask", "volume", "note"]


def _n(v):
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return ""
        return round(float(v), 3)
    except Exception:
        return ""


def _write(rows):
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    new = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(HEADER)
        for r in rows:
            w.writerow(r)


def main():
    ts = datetime.now().isoformat(timespec="seconds")
    rows = []
    try:
        from ib_insync import IB, Stock
        ib = IB()
        try:
            ib.connect("127.0.0.1", 4002, clientId=21, readonly=True, timeout=10)
        except Exception as e:
            for tk in TICKERS:
                rows.append([ts, tk, 0, "", "", "", "", "", "", "NOCONN:" + str(e)[:40]])
            _write(rows)
            return
        try:
            ib.reqMarketDataType(2)  # frozen -> upgrades to live(1) if a feed is available
            for tk in TICKERS:
                try:
                    s = Stock(tk, "SMART", "USD")
                    ib.qualifyContracts(s)
                    t = ib.reqMktData(s, "", False, False)
                    ib.sleep(2.5)
                    rows.append([ts, tk, 1, t.marketDataType, _n(t.last),
                                 _n(t.close), _n(t.bid), _n(t.ask), _n(t.volume), ""])
                    ib.cancelMktData(s)
                except Exception as e:
                    rows.append([ts, tk, 1, "", "", "", "", "", "", "ERR:" + str(e)[:40]])
        finally:
            try:
                ib.disconnect()
            except Exception:
                pass
    except Exception as e:
        for tk in TICKERS:
            rows.append([ts, tk, 0, "", "", "", "", "", "", "FAIL:" + str(e)[:40]])
    _write(rows)


if __name__ == "__main__":
    main()
