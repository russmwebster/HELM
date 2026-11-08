"""s154: fit the inputs for the CSCO recovery model (read-only). Writes _s154/csco_params.json.

1. Daily closes: _s122/px/CSCO.csv (2016-09 -> 2026-09-25) + _s154/csco_bars_ibkr.csv (to 2026-11-06).
2. Earnings reaction days: the trading day after each report (CSCO reports after the close). Dates from
   earnings_history (2023-11 on); earlier ones INFERRED: in each Feb/May/Aug/Nov, the largest move between
   the 9th and the 22nd of the month (CSCO reports mid-month, Wednesday).
3. GARCH(1,1) on the ex-earnings daily log returns (variance targeting; grid-search likelihood): long-run
   vol, persistence, half-life of a vol shock.
4. Cross-checks: the half-life of CSCO's stored implied vol (iv_history, AR(1) on log IV); implied vs the
   realized vol that followed (the variance premium, CSCO's own); today's IV with the 11-12 report stripped.
"""
import csv, json, math, os, sqlite3
import numpy as np
H = os.path.expanduser("~/mnt/helm"); OUT = H + "/_s154/csco_params.json"
px = {}
for f in (H + "/_s122/px/CSCO.csv", H + "/_s154/csco_bars_ibkr.csv"):
    for r in csv.DictReader(open(f)):
        px[r["Date"]] = float(r["Close"])
days = sorted(px); P = np.array([px[d] for d in days]); R = np.diff(np.log(P)); rd = days[1:]
c = sqlite3.connect("file:%s/data/helm.db?mode=ro" % H, uri=True)
listed = sorted(r[0] for r in c.execute("select earn_date from earnings_history where ticker='CSCO'"))
def next_td(d):
    for x in rd:
        if x > d: return x
react = set(next_td(d) for d in listed if next_td(d))
inferred = []
for y in range(2016, 2024):
    for m in (2, 5, 8, 11):
        key = "%d-%02d" % (y, m)
        if any(d.startswith(key) for d in listed): continue
        win = [(abs(R[i]), rd[i]) for i in range(len(rd)) if rd[i].startswith(key) and 9 <= int(rd[i][8:]) <= 22]
        if win: d = max(win)[1]; react.add(d); inferred.append(d)
ear = np.array([d in react for d in rd])
moves = R[ear]
x = R[~ear]; x = x - x.mean()
var = x.var()
def nll(a, b):
    w = var * (1 - a - b); h = np.empty_like(x); h[0] = var
    for t in range(1, len(x)): h[t] = w + a * x[t - 1] ** 2 + b * h[t - 1]
    return 0.5 * np.sum(np.log(h) + x ** 2 / h)
best = None
for a in np.arange(0.01, 0.31, 0.02):
    for b in np.arange(0.50, 0.995, 0.02):
        if a + b < 0.998:
            v = nll(a, b)
            if best is None or v < best[0]: best = (v, a, b)
_, a0, b0 = best
for a in np.arange(max(0.002, a0 - 0.02), a0 + 0.021, 0.004):
    for b in np.arange(b0 - 0.02, b0 + 0.021, 0.002):
        if a + b < 0.999 and a > 0:
            v = nll(a, b)
            if v < best[0]: best = (v, a, b)
_, A, B = best; pers = A + B
w = var * (1 - pers); h = var
for t in range(1, len(x)): h = w + A * x[t - 1] ** 2 + B * h
h_now = w + A * x[-1] ** 2 + B * h
half = math.log(0.5) / math.log(pers)
ivr = c.execute("select date, iv_current from iv_history where ticker='CSCO' order by date").fetchall()
li = np.log([v for _, v in ivr]); phi = np.polyfit(li[:-1], li[1:], 1)[0]
iv_half = math.log(0.5) / math.log(phi) if 0 < phi < 1 else None
# implied vs the realized that followed (21 trading days, ex-earnings), CSCO's own overlap
ix = {d: i for i, d in enumerate(rd)}; ratios = []
for d, v in ivr:
    if d in ix and ix[d] + 21 < len(rd):
        seg = [R[j] for j in range(ix[d] + 1, ix[d] + 22) if not ear[j]]
        if len(seg) > 10: ratios.append((v / 100.0) / (np.std(seg) * math.sqrt(252)))
iv_now = ivr[-1][1] / 100.0; T = 30 / 365.0; m2 = float(np.mean(moves ** 2))
iv_ex = math.sqrt(max(iv_now ** 2 * T - m2, 1e-6) / T)
out = {"closes_first": days[0], "closes_last": days[-1], "last_close": float(P[-1]), "n_returns": int(len(R)),
       "earnings_days": int(ear.sum()), "earnings_inferred": inferred,
       "earnings_moves": [round(float(m), 5) for m in moves],
       "earn_abs_median": float(np.median(np.abs(moves))), "earn_abs_mean": float(np.mean(np.abs(moves))),
       "earn_rms": math.sqrt(m2),
       "garch": {"alpha": float(A), "beta": float(B), "persistence": float(pers), "half_life_days": half,
                 "long_run_vol": math.sqrt(var * 252), "vol_now": math.sqrt(h_now * 252)},
       "realized_vol_ex_earn_last21": float(np.std(x[-21:]) * math.sqrt(252)),
       "realized_vol_ex_earn_last63": float(np.std(x[-63:]) * math.sqrt(252)),
       "iv": {"now": iv_now, "rank": None, "ex_earnings_now": iv_ex, "n_days": len(ivr), "first": ivr[0][0],
              "ar1_phi": float(phi), "half_life_days": iv_half},
       "iv_over_realized": {"n": len(ratios), "median": float(np.median(ratios)) if ratios else None,
                            "mean": float(np.mean(ratios)) if ratios else None}}
json.dump(out, open(OUT, "w"), indent=1)
print(json.dumps({k: v for k, v in out.items() if k != "earnings_moves"}, indent=1))
