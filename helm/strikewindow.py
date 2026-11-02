"""helm/strikewindow.py -- which strikes a proposal tool reads (s148, 2026-11-02).

The chain reader (`open_cmd.fetch_chain_from_ibkr`) reads only strikes within 3 x the
14-day range of the price unless it is given a `strike_range`. On calm stocks that window
stops short of the delta bands the tools need: the put list left off XLC, TGT, NVDA and VALE
on 10-28 -> 10-30 ("nearest 0.31-0.33", nothing lower read), and `helm pmcc` found no
0.20-0.30 short on AAPL or XLE on 11-02. So every proposal tool passes a range worked out
from the stock's own implied volatility instead. One function, so the tools can't drift.

A strike is placed by Black-Scholes:  K = spot * exp(z * sigma * sqrt(T) + (r + sigma^2/2) * T)
with z = -N^-1(delta) for a call and z = N^-1(|delta|) for a put. The range is the lowest
and highest K over the given expiries. sigma is the latest stored IV (iv_history, the 9:35
refresh), 50% when none is stored -- wide, so a missing IV reads more strikes, never fewer.
Reading-only: it chooses what to read; every rule is still applied by the tool itself.
"""
from __future__ import annotations

import math
import sqlite3
from statistics import NormalDist

RATE = 0.045
SIGMA_FALLBACK = 0.5
IV_MAX_AGE_DAYS = 10

# The deltas each tool reads, a little wider than its own net (so its reasons stay true).
PUT_NET = (0.40, 0.12)      # puttool's net 0.20-0.35; the far side wide for put skew
CALL_NET = (0.45, 0.12)     # calltool's net 0.15-0.40
ROLL_NET = (0.60, 0.08)     # rolltool's net 0.05-0.60, trimmed: a roll keeps delta <= 0.50
                            # and the reader asks IBKR for every strike at once


def z_for(delta, right):
    """z for a |delta| on a CALL or PUT."""
    n = NormalDist().inv_cdf(abs(delta))
    return -n if right == "CALL" else n


def strike_at(spot, sigma, dte, z):
    s = sigma if sigma and sigma > 0 else SIGMA_FALLBACK
    T = max(dte, 1) / 365.0
    return spot * math.exp(z * s * math.sqrt(T) + (RATE + 0.5 * s * s) * T)


def z_range(spot, sigma, dtes, z_a, z_b):
    """(lo, hi) strikes between z_a and z_b over every expiry in dtes."""
    ks = [strike_at(spot, sigma, d, z) for d in dtes for z in (z_a, z_b)]
    return min(ks), max(ks)


def delta_range(spot, sigma, dtes, right, deltas):
    """(lo, hi) strikes reading |delta| from deltas[0] to deltas[1] on a CALL or PUT."""
    return z_range(spot, sigma, dtes, z_for(deltas[0], right), z_for(deltas[1], right))


def latest_iv(ticker, today=None, db_path=None):
    """The stock's latest stored IV as a fraction (iv_history.iv_current is in percent),
    at most IV_MAX_AGE_DAYS old; None when there is none. Read-only connection."""
    from datetime import date, timedelta
    from helm.config import DB_PATH
    today = today or date.today()
    try:
        c = sqlite3.connect("file:%s?mode=ro" % (db_path or DB_PATH), uri=True, timeout=10)
        try:
            r = c.execute("SELECT iv_current FROM iv_history WHERE ticker=? AND date>=? AND date<=? "
                          "AND iv_current > 0 ORDER BY date DESC LIMIT 1",
                          (ticker, str(today - timedelta(days=IV_MAX_AGE_DAYS)), str(today))).fetchone()
        finally:
            c.close()
    except Exception:
        return None
    return (float(r[0]) / 100.0) if r and r[0] else None


def selftest():
    fails, n = [], [0]
    def ok(c, m):
        n[0] += 1
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            fails.append(m)
    # z mapping
    ok(abs(z_for(0.5, "CALL")) < 1e-12 and abs(z_for(0.5, "PUT")) < 1e-12, "0.50 delta sits at z 0 for both")
    ok(z_for(0.25, "PUT") < 0 < z_for(0.25, "CALL"), "a 0.25 put is below the price, a 0.25 call above")
    # A 0.25 put strike, priced back, has delta 0.25
    S, sg, d = 100.0, 0.25, 35
    K = strike_at(S, sg, d, z_for(0.25, "PUT"))
    T = d / 365.0
    d1 = (math.log(S / K) + (RATE + 0.5 * sg * sg) * T) / (sg * math.sqrt(T))
    ok(abs(NormalDist().cdf(-d1) - 0.25) < 1e-9, "a 0.25 put strike prices back to 0.25 delta (K %.2f)" % K)
    Kc = strike_at(S, sg, d, z_for(0.20, "CALL"))
    d1 = (math.log(S / Kc) + (RATE + 0.5 * sg * sg) * T) / (sg * math.sqrt(T))
    ok(abs(NormalDist().cdf(d1) - 0.20) < 1e-9, "a 0.20 call strike prices back to 0.20 delta (K %.2f)" % Kc)
    # Puts read below the price only to the near bound; the range covers the band
    lo, hi = delta_range(S, sg, [30, 45], "PUT", PUT_NET)
    b_lo = min(strike_at(S, sg, x, z_for(0.30, "PUT")) for x in (30, 45))
    b_hi = max(strike_at(S, sg, x, z_for(0.25, "PUT")) for x in (30, 45))
    ok(lo < b_lo and hi > b_hi and hi < S + 1.0, "the put range covers 0.25-0.30 at 30 and 45 days (%.2f-%.2f)" % (lo, hi))
    lo, hi = delta_range(S, sg, [30, 45], "CALL", CALL_NET)
    ok(lo < strike_at(S, sg, 30, z_for(0.35, "CALL")) and hi > strike_at(S, sg, 45, z_for(0.20, "CALL")),
       "the call range covers 0.20-0.35 at 30 and 45 days (%.2f-%.2f)" % (lo, hi))
    # XLC 11-02: $114 spot, IV 22.7%; the 3xATR window (~$3.5) missed 0.25 puts
    lo, hi = delta_range(114.0, 0.227, [32, 39], "PUT", PUT_NET)
    k25 = strike_at(114.0, 0.227, 32, z_for(0.25, "PUT"))
    ok(lo < k25 < hi and k25 < 114.0 - 3.6, "XLC-like: the 0.25 put (%.2f) is past 3xATR and inside the range" % k25)
    # No IV -> 50%, wider than a calm stock's own
    a = delta_range(S, None, [35], "PUT", PUT_NET)
    b = delta_range(S, 0.2, [35], "PUT", PUT_NET)
    ok(a[0] < b[0], "no IV reads wider, never narrower")
    print("strikewindow self-test: %d checks, %d failed" % (n[0], len(fails)))
    return not fails


if __name__ == "__main__":
    import sys
    sys.exit(0 if selftest() else 1)
