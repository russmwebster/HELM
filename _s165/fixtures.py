"""s165: fixed put chains for the spread-list harness -- never IBKR. SPY, QQQ and KO are the real IB quotes
of 2026-11-23 13:19 (delayed, Dec 24 only); every other name is a Black-Scholes chain from its stored IV, on
three expiries (Dec 24 = 31 days, Jan 15 = 53, Jan 29 = 67, outside 30-60). `fetch(ticker)` has the shape of
spreadtool.fetch_puts. GATEWAY_DOWN names return the gateway error; WIDE names quote 30% wide."""
import hashlib, math, os, sqlite3
from statistics import NormalDist
N = NormalDist()
TODAY = "2026-11-23"
EXPS = (("2026-12-24", 31), ("2027-01-15", 53), ("2027-01-29", 67))
GATEWAY_DOWN = set(os.environ.get("S165_GWDOWN", "").split(",")) - {""}
WIDE = {"MOD", "VRT", "FTI"}
def _put(exp, dte, k, bid, ask, delta, src="ibkr"):
    mid = round((bid + ask) / 2, 2)
    return {"ticker": None, "expiration": exp, "dte": dte, "strike": k, "opt_type": "PUT", "bid": bid, "ask": ask,
            "mid": mid, "spread_pct": round((ask - bid) / mid * 100, 1) if mid else 99, "delta": delta,
            "gamma": None, "theta": None, "vega": None, "iv": None, "source": src}
REAL = {
 "SPY": (724.27, [("2026-12-24", 31, k, b, a, d) for k, b, a, d in (
   (645, 3.46, 3.56, .1033), (650, 3.81, 3.91, .1142), (655, 4.19, 4.30, .1255), (660, 4.63, 4.74, .139),
   (665, 5.10, 5.23, .1546), (670, 5.67, 5.78, .1704), (675, 6.27, 6.40, .185), (680, 6.97, 7.09, .2051),
   (685, 7.72, 7.86, .2297), (690, 8.60, 8.73, .2534), (695, 9.54, 9.69, .2808), (700, 10.62, 10.77, .3094),
   (705, 11.82, 11.97, .3384))]),
 "QQQ": (652.17, [("2026-12-24", 31, k, b, a, d) for k, b, a, d in (
   (590, 7.02, 7.16, .1707), (595, 7.71, 7.85, .1854), (600, 8.45, 8.59, .2025), (605, 9.28, 9.44, .2209),
   (610, 10.18, 10.33, .2393), (615, 11.17, 11.35, .2612), (620, 12.25, 12.44, .2843), (625, 13.45, 13.64, .3085),
   (630, 14.76, 14.96, .3363))]),
 "KO": (96.03, [("2026-12-24", 31, k, b, a, d) for k, b, a, d in (
   (85, .16, .28, .0522), (86, .11, .72, .0679), (87, .22, .80, .0891), (88, .23, .48, .1139), (89, .44, .89, .1433),
   (90, .53, .73, .1794), (91, .74, .93, .2216), (92, .94, 1.15, .2684), (93, 1.20, 1.50, .323), (94, 1.49, 1.94, .3835))]),
}
def spot_of(t):
    return round(20 + int(hashlib.sha256(t.encode()).hexdigest()[:6], 16) % 480 + 0.37, 2)
def iv_of(t, db=None):
    try:
        c = sqlite3.connect("file:%s?mode=ro" % (db or os.environ["HELM_DB"]), uri=True)
        r = c.execute("SELECT iv_current FROM iv_history WHERE ticker=? AND iv_current>0 ORDER BY date DESC LIMIT 1", (t,)).fetchone()
        c.close()
        return (r[0] / 100.0) if r and r[0] else 0.30
    except Exception:
        return 0.30
def bs_put(S, K, T, s, r=0.045):
    d1 = (math.log(S / K) + (r + .5 * s * s) * T) / (s * math.sqrt(T)); d2 = d1 - s * math.sqrt(T)
    return K * math.exp(-r * T) * N.cdf(-d2) - S * N.cdf(-d1), N.cdf(-d1)
def chain(t):
    if t in REAL:
        S, rows = REAL[t]
        return [dict(_put(*x), ticker=t) for x in rows], S
    S, s = spot_of(t), iv_of(t)
    step = 0.5 if S < 50 else (1.0 if S < 150 else (2.5 if S < 300 else 5.0))
    out = []
    for exp, dte in EXPS:
        k = math.floor(S * 0.70 / step) * step
        while k < S:
            p, d = bs_put(S, k, dte / 365.0, s)
            if p >= 0.05:
                half = max(0.01, (0.15 if t in WIDE else 0.012) * p)
                bid, ask = round(max(p - half, 0.01), 2), round(p + half, 2)
                out.append(dict(_put(exp, dte, k, bid, ask, round(d, 4)), ticker=t))
            k = round(k + step, 2)
    return out, S
def fetch(t):
    if t in GATEWAY_DOWN:
        return [], None, "IB Gateway not reachable on client 15 (ConnectionRefusedError)"
    rows, S = chain(t)
    return rows, S, None
