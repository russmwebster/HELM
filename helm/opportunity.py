"""s154 (Russ, 2026-11-08): keep vs switch for an open put -- the opportunity model.

From today, with the same money over the same period, which path is likely to leave more?
  KEEP   -- the put runs on (through any report before expiry); assigned if below the strike, then covered
            calls; when the money is free again, the next put on the same stock.
  SWITCH -- buy the put back at today's ask; sell the best put that fits from the latest `helm puts` run (the
            same name the card's "Keep vs switch" shows -- `switch_target`), priced by the model today; then the
            same cycle on that stock.
Both paths start from the same value: the collateral less the put's ask today. The loss so far is in that
number on both sides, so the entry price is not a factor (Russ, 2026-11-06). In an IRA closing has no tax.
A MODEL, NOT A FORECAST: no view on direction, so neither path has a built-in edge from a stock recovering;
the comparison is the premium each earns for the risk it carries, after costs.

Each stock: helm.recovery.fit (GARCH vol, report-day jumps, implied/realized ratio), started at its own vol
now and fading. HELM's rules on both paths:
  puts  -- puttool's delta band and 30-60 day window, monthly expiries, no new put within EARN_VETO_DAYS of a
           report (reports later in the trade allowed), the contract with the highest yearly return on the
           collateral, as many contracts as the cash covers; bought back at 50% of the credit; at 21 days
           left bought back if above the strike, else held -- assigned at expiry if below it (HELM's default:
           take the shares); effective basis = strike - that put's credit;
  calls -- as helm.recovery (strike >= the effective basis, the call band, no earnings inside, 50%, 21 days,
           called away at expiry if above the strike).
Not modelled: IV rank >= 50 and the spread rule at each new sale (no IV history in a simulation), the sector
and name limits after day one, rolling, interest on idle cash (the same on both paths), skew.
Measures at 90 and 365 days, each path against today's value: the typical result in dollars (median), the
average, the chance of ending below today's value, a bad case (1 in 10); and the gap (switch - keep) in the
typical and the average result. Each stock has its own random draws: how the two move together isn't known,
so there is no path-by-path difference (a market-wide day like 11-05 hits both).
"""
import math
from datetime import date, timedelta

import numpy as np

from helm import recovery as RC

RULE_VERSION = "opportunity-v1"
HZ = (90, 365)
N_PATHS = 4000


def _rules():
    from helm import puttool as P
    r = RC._rules()
    r.update({"pmin": P.DELTA_MIN, "pmax": P.DELTA_MAX, "veto": P.EARN_VETO_DAYS})
    return r


def switch_target(conn, ticker):
    """The best put that fits from the latest `helm puts` run, not on `ticker`: (candidate dict, run) or
    (None, run). The one rule the card's "Keep vs switch" and this model both read."""
    from helm import puttool as P
    run = P.latest(conn)
    if not run:
        return None, None
    return next((c for c in run["candidates"] if c.get("size_n") and c["ticker"] != ticker), None), run


def bs_put(S, K, T, v):
    c, d = RC.bs_call(S, K, T, v)
    return c - S + K, d - 1.0


def wheel(S0, start, prm, vol0, earn, cash0, put=None, n=N_PATHS, seed=RC.SEED, rules=None, stats=None):
    """Simulate one stock's cycle from today. put = (strike, expiry date, credit/share, contracts) for an open
    short put, else start in cash. Returns {hz: equity array} and stats."""
    rl = rules or _rules()
    rng = np.random.default_rng(seed)
    LR = prm["long_run_vol"] ** 2 / 252
    pers, a, b = prm["persistence"], prm["alpha"], prm["beta"]
    w = LR * (1 - pers)
    ratio = prm["ratio"]
    moves = np.asarray(prm.get("moves") or [], float)
    end = start + timedelta(days=max(HZ))
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    earn = [e for e in earn if start <= e <= end]
    jump = {min((x for x in days if x > e), default=None) for e in earn} - {None}
    EXP = RC._third_fridays(start, 16)
    st = stats if stats is not None else {}
    st.setdefault("min_call_vs_basis", math.inf)
    st.setdefault("put_in_veto", 0)
    st.setdefault("call_across_report", 0)
    st.setdefault("puts_sold", 0)
    st.setdefault("assigned", 0)

    def avg_vol(h, m):
        k = np.arange(1, max(int(m), 1) + 1)
        return np.sqrt(252 * (LR + np.outer(h - LR, pers ** k).mean(axis=1)))

    S = np.full(n, float(S0))
    h = np.full(n, vol0 ** 2 / 252)
    cash = np.full(n, float(cash0))
    sh = np.zeros(n)
    basis = np.full(n, np.nan)
    pK = np.full(n, np.nan); pX = np.full(n, -1); pC = np.zeros(n); pN = np.zeros(n); pHold = np.zeros(n, bool)
    cK = np.full(n, np.nan); cX = np.full(n, -1); cC = np.zeros(n); cHold = np.zeros(n, bool)
    if put:
        K0, X0, C0, N0 = put
        if X0 not in EXP:
            EXP = sorted(set(EXP) | {X0})
        pK[:] = K0; pX[:] = EXP.index(X0); pC[:] = C0; pN[:] = N0
    out = {}
    for t, day in enumerate(days):
        if t > 0:
            z = rng.standard_normal(n)
            e = np.sqrt(h) * z
            r = e - 0.5 * h
            if day in jump and len(moves):
                j = rng.choice(moves, n) * rng.choice([-1.0, 1.0], n)
                r = r + j - np.log(np.cosh(j))
            S = S * np.exp(r)
            h = w + a * e ** 2 + b * h
        cal = (day - start).days
        iv21 = avg_vol(h, 21) * ratio
        # -- the open put
        op = pX >= 0
        if op.any():
            dte = np.array([(EXP[i] - day).days if i >= 0 else 0 for i in pX])
            mk, _ = bs_put(S, np.nan_to_num(pK, nan=1.0), dte / 365.0, iv21)
            hit = op & ~pHold & (dte > 0) & (mk <= rl["take"] * pC)
            cash[hit] -= (rl["take"] * pC[hit] + RC.FEE) * 100 * pN[hit]
            at21 = op & ~hit & ~pHold & (dte <= rl["t21"])
            above = at21 & (S > pK)
            cash[above] -= (mk[above] * (1 + RC.HALF_SPREAD) + RC.FEE) * 100 * pN[above]
            pHold |= at21 & ~above
            expd = op & ~hit & ~above & (dte <= 0)
            asg = expd & (S < pK)
            st["assigned"] += int(asg.sum())
            cash[asg] -= pK[asg] * 100 * pN[asg]
            sh[asg] += 100 * pN[asg]
            basis[asg] = pK[asg] - pC[asg]
            done = hit | above | expd
            pX[done] = -1; pHold[done] = False; pN[done] = 0
        # -- the open call
        oc = cX >= 0
        if oc.any():
            dte = np.array([(EXP[i] - day).days if i >= 0 else 0 for i in cX])
            mk, _ = RC.bs_call(S, np.nan_to_num(cK, nan=1.0), dte / 365.0, iv21)
            hit = oc & ~cHold & (dte > 0) & (mk <= rl["take"] * cC)
            cash[hit] -= (rl["take"] * cC[hit] + RC.FEE) * sh[hit]
            at21 = oc & ~hit & ~cHold & (dte <= rl["t21"])
            below = at21 & (S < cK)
            cash[below] -= (mk[below] * (1 + RC.HALF_SPREAD) + RC.FEE) * sh[below]
            cHold |= at21 & ~below
            expd = oc & ~hit & ~below & (dte <= 0)
            ca = expd & (S >= cK)
            cash[ca] += cK[ca] * sh[ca]
            sh[ca] = 0
            basis[ca] = np.nan
            done = hit | below | expd
            cX[done] = -1; cHold[done] = False
        # -- open the next trade
        veto = any(0 <= (q - day).days <= rl["veto"] for q in earn)
        free = (pX < 0) & (sh == 0)
        if free.any() and not veto:
            best = np.full(n, -1.0); bK = np.full(n, np.nan); bE = np.full(n, -1); bP = np.zeros(n)
            fi = np.flatnonzero(free)
            Sf = S[fi]
            for i, ex in enumerate(EXP):
                T = (ex - day).days
                if not (rl["dte_lo"] <= T <= rl["dte_hi"]):
                    continue
                iv = avg_vol(h[fi], T * 252 / 365) * ratio
                step = RC.strike_step(float(np.median(Sf)))
                ks = np.arange(math.floor(float(np.min(Sf)) * 0.6 / step) * step,
                               math.ceil(float(np.max(Sf)) / step) * step + step, step)
                pr, de = bs_put(Sf[:, None], ks[None, :], T / 365.0, iv[:, None])
                fitm = (-de >= rl["pmin"]) & (-de <= rl["pmax"])
                ann = np.where(fitm, pr / ks[None, :] * 365.0 / T, -1.0)
                k = ann.argmax(axis=1)
                v = ann[np.arange(len(fi)), k]
                bt = v > best[fi] + 1e-12
                idx = fi[bt]
                best[idx] = v[bt]; bK[idx] = ks[k[bt]]; bE[idx] = i
                bP[idx] = pr[np.arange(len(fi)), k][bt]
            nct = np.floor(cash / (np.nan_to_num(bK, nan=1e12) * 100))
            sold = free & (bE >= 0) & (best > 0) & (nct >= 1)
            if sold.any():
                st["puts_sold"] += int(sold.sum())
            pK[sold] = bK[sold]; pX[sold] = bE[sold]; pN[sold] = nct[sold]
            pC[sold] = bP[sold] * (1 - RC.HALF_SPREAD) - RC.FEE
            cash[sold] += pC[sold] * 100 * pN[sold]
        need = (sh > 0) & (cX < 0)
        if need.any():
            ok_e = [i for i, ex in enumerate(EXP) if rl["dte_lo"] <= (ex - day).days <= rl["dte_hi"]
                    and not any(day <= q <= ex for q in earn)]
            best = np.full(n, -1.0); bK = np.full(n, np.nan); bE = np.full(n, -1); bP = np.zeros(n)
            ni = np.flatnonzero(need)
            Sn, Bn = S[ni], basis[ni]
            for i in ok_e:
                T = (EXP[i] - day).days
                iv = avg_vol(h[ni], T * 252 / 365) * ratio
                lo = float(np.nanmin(Bn))
                step = RC.strike_step(lo)
                ks = np.arange(math.ceil(lo / step - 1e-9) * step, max(lo, float(np.max(Sn))) * 1.25 + step, step)
                pr, de = RC.bs_call(Sn[:, None], ks[None, :], T / 365.0, iv[:, None])
                fitm = (de >= rl["dmin"]) & (de <= rl["dmax"]) & (ks[None, :] >= Bn[:, None] - 1e-9)
                ann = np.where(fitm, pr / Sn[:, None] * 365.0 / T, -1.0)
                k = ann.argmax(axis=1)
                v = ann[np.arange(len(ni)), k]
                bt = v > best[ni] + 1e-12
                idx = ni[bt]
                best[idx] = v[bt]; bK[idx] = ks[k[bt]]; bE[idx] = i
                bP[idx] = pr[np.arange(len(ni)), k][bt]
            sold = need & (bE >= 0) & (best > 0)
            if sold.any():
                st["min_call_vs_basis"] = min(st["min_call_vs_basis"], float(np.min(bK[sold] - basis[sold])))
                for i in set(bE[sold].tolist()):
                    if any(day <= q <= EXP[i] for q in earn):
                        st["call_across_report"] += 1
            cK[sold] = bK[sold]; cX[sold] = bE[sold]
            cC[sold] = bP[sold] * (1 - RC.HALF_SPREAD) - RC.FEE
            cash[sold] += cC[sold] * sh[sold]
        if any(cal >= hz and hz not in out for hz in HZ):
            pm = np.zeros(n); cm = np.zeros(n)
            op = pX >= 0
            if op.any():
                dte = np.array([(EXP[i] - day).days if i >= 0 else 0 for i in pX])
                pm[op] = bs_put(S[op], pK[op], np.maximum(dte[op], 0) / 365.0, iv21[op])[0] * 100 * pN[op]
            oc = cX >= 0
            if oc.any():
                dte = np.array([(EXP[i] - day).days if i >= 0 else 0 for i in cX])
                cm[oc] = RC.bs_call(S[oc], cK[oc], np.maximum(dte[oc], 0) / 365.0, iv21[oc])[0] * sh[oc]
            eq = cash + sh * S - pm - cm
            for hz in HZ:
                if cal >= hz and hz not in out:
                    out[hz] = eq
    for hz in HZ:
        out.setdefault(hz, cash + sh * S)
    return out


def summarize(eq, base):
    d = np.asarray(eq) - base
    return {"median": float(np.median(d)), "below": float(100 * np.mean(d < 0)), "bad": float(np.percentile(d, 10)),
            "mean": float(np.mean(d))}


def compare(conn, position_id, root, bars=None, n=N_PATHS, today=None):
    """Keep vs switch for one open put. bars: {ticker: IBKR rows}. Raises on missing data."""
    today = today or date.today()
    p = conn.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
    leg = conn.execute("SELECT * FROM legs WHERE position_id=? AND status='OPEN' AND direction='SHORT' "
                       "AND option_type='PUT'", (position_id,)).fetchone()
    if p is None or leg is None:
        raise ValueError("no open short put on %s" % position_id)
    t = p["ticker"]
    ck = conn.execute("SELECT spot_price, checked_at FROM checks WHERE position_id=? AND data_quality='GOOD' "
                      "AND spot_price IS NOT NULL ORDER BY checked_at DESC LIMIT 1", (position_id,)).fetchone()
    lc = conn.execute("SELECT current_ask, current_price, checked_at FROM leg_checks WHERE leg_id=? AND "
                      "current_ask IS NOT NULL ORDER BY checked_at DESC LIMIT 1", (leg["id"],)).fetchone()
    if ck is None or lc is None:
        raise ValueError("no stored reading of the put yet -- run after the next snapshot")
    K, N = float(leg["strike"]), int(leg["contracts"])
    X = date.fromisoformat(str(leg["expiration"])[:10])
    collateral = K * 100 * N
    base = collateral - (float(lc["current_ask"]) + RC.FEE) * 100 * N        # today's value, closing at the ask
    tgt, run = switch_target(conn, t)
    if tgt is None:
        raise ValueError("no put that fits in the latest `helm puts` run%s" % (" (%s)" % run["run_at"][:16] if run else ""))
    rl = _rules()
    end = today + timedelta(days=max(HZ))
    fk = RC.fit(conn, t, root, (bars or {}).get(t), reading_day=ck["checked_at"][:10])
    ek, _ = RC.future_earnings(conn, t, today, end, fk["listed"])
    s2 = tgt["ticker"]
    f2 = RC.fit(conn, s2, root, (bars or {}).get(s2), reading_day=today.isoformat())
    e2, enote2 = RC.future_earnings(conn, s2, today, end, f2["listed"])
    sk, s2s = {}, {}
    keep = wheel(float(ck["spot_price"]), today, fk, fk["start_vol"], ek, collateral,
                 put=(K, X, float(leg["open_price"] or 0), N), n=n, rules=rl, stats=sk)
    sw = wheel(f2["last_close"], today, f2, f2["start_vol"], e2, base, n=n, seed=RC.SEED + 1, rules=rl, stats=s2s)
    # each stock its own draws: how the two move together is not known, so no path-by-path difference
    res = {"position_id": position_id, "ticker": t, "spot": float(ck["spot_price"]), "reading": ck["checked_at"][:16],
           "ask": float(lc["current_ask"]), "collateral": collateral, "base": base, "contracts": N,
           "expiry": X.isoformat(), "keep_earn": [e.isoformat() for e in ek if e <= X],
           "switch": {"ticker": s2, "sector": tgt.get("sector"), "grade": tgt.get("grade"),
                      "run_at": run["run_at"][:16], "price": f2["last_close"], "price_day": f2["last"],
                      "vol": f2["start_vol"], "earn_note": enote2, "short_history": f2["short_history"]},
           "keep_vol": fk["start_vol"], "horizons": list(HZ), "paths": n, "rule_version": RULE_VERSION}
    for hz in HZ:
        res["keep_%d" % hz] = summarize(keep[hz], base)
        res["switch_%d" % hz] = summarize(sw[hz], base)
        res["gap_%d" % hz] = {"median": res["switch_%d" % hz]["median"] - res["keep_%d" % hz]["median"],
                              "mean": res["switch_%d" % hz]["mean"] - res["keep_%d" % hz]["mean"]}
    res["stats"] = {"keep": {k: (v if v != math.inf else None) for k, v in sk.items()},
                    "switch": {k: (v if v != math.inf else None) for k, v in s2s.items()}}
    return res


def _selftest():
    fails, ran = [], []

    def ok(c, m):
        ran.append(m)
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            fails.append(m)

    rl = {"dmin": 0.20, "dmax": 0.35, "dte_lo": 30, "dte_hi": 60, "take": 0.5, "t21": 21,
          "pmin": 0.25, "pmax": 0.30, "veto": 10}
    start = date(2026, 11, 9)
    g = {"alpha": 0.08, "beta": 0.88, "persistence": 0.96, "long_run_vol": 0.28, "ratio": 1.05, "moves": [0.05, -0.04, 0.06]}
    earn = [date(2027, 2, 10), date(2027, 5, 12)]
    st = {}
    r = wheel(100.0, start, g, 0.35, earn, 30000.0, n=2000, rules=rl, stats=st)
    ok(st["puts_sold"] > 0, "from cash: puts are sold (%d)" % st["puts_sold"])
    ok(st["min_call_vs_basis"] is math.inf or st["min_call_vs_basis"] >= -1e-9, "no call below its basis")
    ok(st["call_across_report"] == 0, "no call across a report")
    no_edge = dict(g, ratio=1.0)
    rich = dict(g, ratio=1.25)
    m0 = float(np.mean(wheel(100.0, start, no_edge, 0.35, [], 30000.0, n=3000, rules=rl)[365]))
    m1 = float(np.mean(wheel(100.0, start, rich, 0.35, [], 30000.0, n=3000, rules=rl)[365]))
    ok(m1 > m0, "richer options (implied 25%% over realized) earn more on average: %.0f vs %.0f" % (m1, m0))
    ok(abs(m0 - 30000.0) < 0.03 * 30000.0, "no edge: the average stays near the start after costs (%.0f)" % m0)
    # an open, deep in-the-money put held to expiry is assigned; the equity counts the shares
    st = {}
    r = wheel(80.0, start, dict(g, long_run_vol=0.02, persistence=2e-9, alpha=1e-9, beta=1e-9, moves=[]), 0.02, [],
              30000.0, put=(100.0, date(2026, 11, 20), 2.0, 3), n=500, rules=rl, stats=st)
    ok(st["assigned"] == 500, "a deep in-the-money put held to expiry is assigned on every path")
    ok(abs(float(np.median(r[90])) - (30000 - 30000 + 300 * 80)) < 1500,
       "after assignment the value is the shares at the price plus rent (%.0f)" % float(np.median(r[90])))
    s = summarize([1, 2, 3, -1], 0)
    ok(s["below"] == 25.0 and s["median"] == 1.5, "summary: median and share below")
    print("opportunity selftest:", "PASS (%d checks)" % len(ran) if not fails else "%d FAIL" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest() if "--selftest" in sys.argv else 0)
