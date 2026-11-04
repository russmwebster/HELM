#!/usr/bin/env python3
"""
s149 (2026-11-03) -- READ-ONLY probe: does HELM's chain reader see the short-call spreads too wide
because it reads the quotes too early?

Why: on 11-03 09:47 `helm pmcc MSFT,WMT,KO,JPM` left KO off ("narrowest spread in the band 19%")
and JPM ("13%"), while a separate read-only reader a few minutes later showed KO's 12-18 100 call
at 0.75/0.79 (5%) and JPM's 12-18 360 call at 3.35/3.70 (9.9%).

HELM's reader (`helm/cli/open_cmd.py` fetch_chain_from_ibkr) asks for every strike of an expiry at
once (reqMktData, generic ticks 106,101, market data type 2) and reads them all after ib.sleep(3).
This probe does exactly that, on HELM's own client number 15, and then keeps reading the SAME
tickers at 3, 6, 10 and 20 seconds, printing bid / ask / spread % / delta at each moment, for the
30-45 day call expiries 0-12% above the price. It changes nothing: no database, no files, no orders.

Run on the Mac, market hours, outside 10:00-10:35, 12:30-13:05, 15:15-15:50, while no other HELM
command is running (client 15 is the chain reader's):
    cd ~/Projects/helm && python3 _s149/spread_probe.py            # KO and JPM
    cd ~/Projects/helm && python3 _s149/spread_probe.py KO MSFT
"""
import datetime as dt
import math
import sys

WINDOWS = [((10, 0), (10, 35)), ((12, 30), (13, 5)), ((15, 15), (15, 50))]
READ_AT = [3, 6, 10, 20]          # seconds after the request, as HELM reads at 3
BAND = (0.20, 0.30)               # the PMCC short-call delta band
DTE = (30, 45)


def in_window(now):
    hm = (now.hour, now.minute)
    return any(a <= hm < b for a, b in WINDOWS)


def num(v):
    try:
        f = float(v)
        return None if math.isnan(f) or f in (-1.0, 0.0) else f
    except Exception:
        return None


def main():
    tickers = [a.upper() for a in sys.argv[1:] if not a.startswith("-")] or ["KO", "JPM"]
    now = dt.datetime.now()
    if in_window(now) and "--force" not in sys.argv:
        print("Refused: inside a snapshot window (10:00-10:35, 12:30-13:05, 15:15-15:50). Try again after it.")
        return 2
    from ib_insync import IB, Stock, Option
    ib = IB()
    try:
        ib.connect("127.0.0.1", 4002, clientId=15, readonly=True, timeout=15)
    except Exception as e:
        print(f"Could not connect on client 15 ({type(e).__name__}: {e}). Is IB Gateway up, and no other HELM command running?")
        return 1
    print(f"spread_probe  {now:%Y-%m-%d %H:%M:%S}  client 15, market data type 2 (as HELM)")
    ib.reqMarketDataType(2)
    today = dt.date.today()
    try:
        for tk in tickers:
            stk = Stock(tk, "SMART", "USD")
            ib.qualifyContracts(stk)
            st = ib.reqMktData(stk, "", False, False)
            ib.sleep(2)
            spot = num(st.last) or num(st.close) or ((num(st.bid) or 0) + (num(st.ask) or 0)) / 2
            ib.cancelMktData(stk)
            params = ib.reqSecDefOptParams(tk, "", "STK", stk.conId)
            ib.sleep(1)
            exps, strikes = set(), set()
            for p in params:
                exps.update(p.expirations); strikes.update(p.strikes)
            exps = sorted(e for e in exps
                          if DTE[0] <= (dt.datetime.strptime(e, "%Y%m%d").date() - today).days <= DTE[1])
            ks = sorted(k for k in strikes if spot <= k <= spot * 1.12)
            print(f"\n=== {tk}  spot {spot:.2f}  expiries {', '.join(exps)}  strikes {ks[0]:g}-{ks[-1]:g} ({len(ks)})")
            for e in exps:
                opts = [Option(tk, e, k, "C", "SMART", "", "USD") for k in ks]
                try:
                    ib.qualifyContracts(*opts)
                except Exception:
                    pass
                opts = [o for o in opts if getattr(o, "conId", 0)]
                t0 = dt.datetime.now()
                tick = {o.strike: ib.reqMktData(o, "106,101", False, False) for o in opts}   # all at once, as HELM
                seen = {k: [] for k in tick}
                elapsed = 0
                for s in READ_AT:
                    ib.sleep(s - elapsed); elapsed = s
                    for k, t in tick.items():
                        bid, ask = num(t.bid), num(t.ask)
                        g = t.modelGreeks or t.lastGreeks
                        d = abs(float(g.delta)) if g is not None and num(getattr(g, "delta", None)) is not None else None
                        sp = round((ask - bid) / ((ask + bid) / 2) * 100, 1) if bid and ask and bid > 0 and ask > 0 else None
                        seen[k].append((bid, ask, sp, d))
                for o in opts:
                    ib.cancelMktData(o)
                print(f"\n  {tk} {e} calls -- read at " + " / ".join(f"{s}s" for s in READ_AT)
                      + f"   (requested {t0:%H:%M:%S})")
                for k in sorted(seen):
                    rows = seen[k]
                    dl = next((r[3] for r in reversed(rows) if r[3] is not None), None)
                    if dl is None or dl > 0.40 or dl < 0.12:
                        continue          # print 0.12-0.40 delta only (the band and its neighbours)
                    mark = " <band" if BAND[0] <= dl <= BAND[1] else ""
                    cells = []
                    for bid, ask, sp, d in rows:
                        q = "  no quote " if sp is None else f"{bid:>6.2f}/{ask:<6.2f}{sp:>5.1f}%"
                        cells.append(q + ("" if d is not None else " (no Δ)"))
                    print(f"    {k:>8g}  Δ {dl:.2f}{mark:6} | " + " | ".join(cells))
                ib.sleep(1)
    finally:
        ib.disconnect()
    print("\nRead-only: nothing was written. Paste the output back into the session.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
