"""s154 (Russ, 2026-11-08): "If assigned" odds for an open put -- the recovery model.

If the put is assigned at today's price, how soon could HELM sell a covered call, how much rent would the
calls earn, and how likely is the trade back to its effective basis? A MODEL, NOT A FORECAST.

Per stock (fit):
  * daily closes: the hand-refreshed file `_s122/px/TICKER.csv` (up to ten years), topped up with IBKR's
    daily bars (a year) -- or IBKR's year alone, labelled a short history;
  * earnings reaction days: for each stored report date (`earnings_history`, the watchlist's next date), the
    bigger move of the report day and the next trading day (before-open and after-close reporters alike);
    report days before the first stored date are inferred from the stored pattern (same month of the
    quarter, within a week of the usual day), labelled;
  * GARCH(1,1) on the returns with earnings days removed (variance targeting; grid-searched likelihood):
    the long-run vol, how fast a shock fades (half-life), and the vol now;
  * implied vs the realized vol that followed (CSCO's own way, from `iv_history`), median, held to 1.0-1.3;
    1.05 when there are fewer than 30 days to measure;
  * the start vol: GARCH's vol now when the closes reach the latest reading (within 4 days); otherwise the
    stored implied vol with any report inside its 30 days taken out, divided by the ratio above -- labelled.
Simulation: daily weekdays from the put's expiry for 365 days; vol follows the GARCH fit; each future report
day adds a jump drawn from the stock's own report-day moves with a random sign; no drift (a martingale);
normal shocks; no skew. Covered calls on HELM's rules: strike >= the effective basis (HELM does not lower it
with call premiums), CALL_DELTA band, the put tool's 30-60 day window, monthly expiries only, no earnings
inside; ranked by yearly premium on the shares' value; priced by Black-Scholes at the expected vol over the
call's life x the ratio; sold at mid - 2.5%, bought at mid + 2.5%, $0.67 a contract a leg; bought back at
50% of the credit, or at 21 days left if below the strike, else held and called away. Two cases: the start
vol above, fading ("now"), and the long-run vol from the start ("calm").
Measures by 30/60/90/180/365 days: the gate (a call HELM would allow has been sold), rent earned (banked
less what the open call would cost back), back to basis on price alone (touched), made whole (shares +
rent banked - the open call >= basis, or called away).
`helm recovery` runs and records it (Russ runs it; nothing scheduled); `latest_for` is the card's read.
"""
import csv
import json
import math
import os
import sqlite3
from datetime import date, datetime, timedelta

import numpy as np

RULE_VERSION = "recovery-v1"
HORIZ = (30, 60, 90, 180, 365)
N_PATHS = 8000
SEED = 154
FEE = 0.0067                 # $0.67 a contract a leg (the downloads' median, rolltool), per share
HALF_SPREAD = 0.025          # sell at mid - 2.5%, buy at mid + 2.5%
RATIO_DEFAULT, RATIO_MIN, RATIO_MAX, RATIO_MIN_N = 1.05, 1.0, 1.3, 30
FRESH_DAYS = 4               # a reading (implied vol, or the closes) this close to the latest check counts as current
HL_CAP = 126                 # s154: a shock's half-life is capped at ~6 months (a near-endless fit is a regime change)
MIN_ASSIGNED = 300           # paths ending below the strike needed before the recovery is modelled
MAX_BATCHES = 8
STEP_DAYS = 91               # an estimated report date follows the last known one by a quarter


def _rules():
    from helm import calltool as C, puttool as P, advice as A
    return {"dmin": C.DELTA_MIN, "dmax": C.DELTA_MAX, "dte_lo": P.DTE_MIN, "dte_hi": P.DTE_MAX,
            "take": A.PROFIT_TAKE, "t21": A.TIME_EXIT_DTE}


# ---------------------------------------------------------------- math
_erf = np.vectorize(math.erf, otypes=[float])


def ncdf(x):
    return 0.5 * (1.0 + _erf(np.asarray(x, dtype=float) / math.sqrt(2.0)))


def bs_call(S, K, T, v):
    """(price, delta) of a call; arrays broadcast."""
    T = np.maximum(T, 1e-6)
    v = np.maximum(v, 1e-4)
    sd = v * np.sqrt(T)
    d1 = (np.log(S / K) + 0.5 * sd ** 2) / sd
    return S * ncdf(d1) - K * ncdf(d1 - sd), ncdf(d1)


def touch_prob(S0, B, v, days_):
    """Chance a no-drift (martingale) lognormal price at constant vol touches B > S0 within days_."""
    if S0 >= B:
        return 1.0
    T = days_ / 365.0
    a = math.log(B / S0)
    mu = -0.5 * v * v
    sd = v * math.sqrt(T)
    N = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))  # noqa: E731
    return N((-a + mu * T) / sd) + math.exp(2 * mu * a / (v * v)) * N((-a - mu * T) / sd)


def gate_price(basis, v, t_days, dmin):
    """The price at which a call t_days out at the band's LOWEST delta strikes exactly at the basis -- the
    lowest delta is the call furthest out of the money, so it reaches the basis first."""
    from statistics import NormalDist
    sd = v * math.sqrt(t_days / 365.0)
    z = NormalDist().inv_cdf(1.0 - dmin)
    return basis * math.exp(-(z * sd + 0.5 * sd * sd))


def strike_step(price):
    """Listed strike spacing, approximated by price (an assumption, shown)."""
    return 1.0 if price < 200 else (2.5 if price < 500 else 5.0)


# ---------------------------------------------------------------- fit
def garch_fit(x):
    """GARCH(1,1) with variance targeting on demeaned returns x; grid-searched likelihood, all grid points
    filtered together. Returns {alpha, beta, persistence, half_life_days, long_run_var, var_now}."""
    x = np.asarray(x, float) - np.mean(x)
    var = float(x.var())

    def search(As, Bs):
        A, B = np.meshgrid(As, Bs)
        A, B = A.ravel(), B.ravel()
        keep = (A > 0) & (B > 0) & (A + B < 0.999)
        A, B = A[keep], B[keep]
        w = var * (1 - A - B)
        h = np.full(A.shape, var)
        ll = np.zeros(A.shape)
        for t in range(len(x)):
            ll += np.log(h) + x[t] ** 2 / h
            h = w + A * x[t] ** 2 + B * h
        k = int(np.argmin(ll))
        return A[k], B[k]

    a, b = search(np.arange(0.01, 0.31, 0.02), np.arange(0.50, 0.995, 0.01))
    a, b = search(np.arange(max(0.002, a - 0.02), a + 0.0201, 0.002), np.arange(b - 0.02, b + 0.0201, 0.002))
    pers = a + b
    w = var * (1 - pers)
    h = var
    for t in range(len(x)):
        h = w + a * x[t] ** 2 + b * h
    return {"alpha": float(a), "beta": float(b), "persistence": float(pers),
            "half_life_days": float(math.log(0.5) / math.log(pers)), "long_run_var": var, "var_now": float(h)}


def load_closes(ticker, root, ib_rows=None):
    """{date: close} from the hand file then IBKR's rows (IBKR wins on overlap); (closes, source note)."""
    px, src = {}, []
    f = os.path.join(root, "_s122", "px", "%s.csv" % ticker)
    if os.path.exists(f):
        for r in csv.DictReader(open(f)):
            try:
                px[r["Date"][:10]] = float(r["Close"])
            except (KeyError, ValueError):
                pass
        if px:
            src.append("%s to %s (price file)" % (min(px), max(px)))
    if ib_rows:
        for d, c in ib_rows:
            px[str(d)[:10]] = float(c)
        src.append("IBKR daily bars to %s" % max(str(d)[:10] for d, _ in ib_rows))
    return px, "; ".join(src) or "no closes"


def reaction_days(days, R, listed):
    """Indices of earnings reaction days: for each listed date, the bigger |move| of the report day and the
    next trading day; before the first listed date, inferred from the listed pattern. (indices, inferred dates)"""
    idx = {d: i for i, d in enumerate(days)}
    out, inferred = set(), []

    def at_or_after(d):
        for i, x in enumerate(days):
            if x >= d:
                return i
        return None

    for d in listed:
        i = at_or_after(d)
        if i is None:
            continue
        cand = [j for j in (i, i + 1) if j < len(R)]
        if days[i] != d:                     # report on a non-trading day: the next trading day reacts
            cand = [i]
        if cand:
            out.add(max(cand, key=lambda j: abs(R[j])))
    if listed and days and days[0] < min(listed):
        mq = sorted({int(d[5:7]) % 3 for d in listed})
        dom = sorted(int(d[8:10]) for d in listed)
        mid = dom[len(dom) // 2]
        first = min(listed)
        y0, y1 = int(days[0][:4]), int(first[:4])
        for y in range(y0, y1 + 1):
            for m in range(1, 13):
                if m % 3 not in mq:
                    continue
                key = "%d-%02d" % (y, m)
                if key >= first[:7]:
                    continue
                win = [j for j, d in enumerate(days[:len(R)]) if d.startswith(key) and abs(int(d[8:]) - mid) <= 7]
                if win:
                    j = max(win, key=lambda k: abs(R[k]))
                    out.add(j)
                    inferred.append(days[j])
    del idx
    return sorted(out), inferred


def future_earnings(conn, ticker, start, end, listed):
    """Report dates in [start, end]: the watchlist's next date if ahead, then a quarter apart; estimated."""
    nxt = None
    try:
        r = conn.execute("SELECT next_earnings FROM watchlist WHERE ticker=?", (ticker,)).fetchone()
        nxt = str(r[0])[:10] if r and r[0] else None
    except sqlite3.Error:
        pass
    known = sorted(set(listed + ([nxt] if nxt else [])))
    if not known:
        return [], "no report dates known -- no earnings in the model"
    d = date.fromisoformat(known[-1])
    while d < start:
        d += timedelta(days=STEP_DAYS)
    out = []
    while d <= end:
        out.append(d)
        d += timedelta(days=STEP_DAYS)
    return out, "next report %s, then a quarter apart (estimated)" % (out[0].isoformat() if out else "none")


def fit(conn, ticker, root, ib_rows=None, reading_day=None):
    """Everything the simulation needs for one stock, from stored data (+ IBKR rows if given)."""
    px, src = load_closes(ticker, root, ib_rows)
    days = sorted(px)
    if len(days) < 250:
        raise ValueError("only %d daily closes for %s (need 250)" % (len(days), ticker))
    P = np.array([px[d] for d in days])
    R = np.diff(np.log(P))
    rd = days[1:]
    listed = {str(r[0])[:10] for r in conn.execute(
        "SELECT earn_date FROM earnings_history WHERE ticker=?", (ticker,)) if r[0]}
    try:                                     # s154: the watchlist's date counts too (VRT had no stored dates)
        w = conn.execute("SELECT next_earnings FROM watchlist WHERE ticker=?", (ticker,)).fetchone()
        if w and w[0]:
            listed.add(str(w[0])[:10])
    except sqlite3.Error:
        pass
    listed = sorted(listed)
    ear, inferred = reaction_days(rd, R, listed)
    mask = np.ones(len(R), bool)
    mask[ear] = False
    g = garch_fit(R[mask])
    capped = g["half_life_days"] > HL_CAP
    if capped:                               # keep alpha's share of the persistence
        p_new = 0.5 ** (1.0 / HL_CAP)
        g["alpha"] *= p_new / g["persistence"]
        g["beta"] = p_new - g["alpha"]
        g["persistence"], g["half_life_days"] = p_new, float(HL_CAP)
    moves = [float(R[j]) for j in ear]
    # implied vs the realized that followed (21 trading days, report days out)
    iv = conn.execute("SELECT date, iv_current FROM iv_history WHERE ticker=? AND iv_current IS NOT NULL "
                      "ORDER BY date", (ticker,)).fetchall()
    ix = {d: i for i, d in enumerate(rd)}
    ratios = []
    for d, v in iv:
        if d in ix and ix[d] + 21 < len(rd):
            seg = [R[j] for j in range(ix[d] + 1, ix[d] + 22) if mask[j]]
            if len(seg) > 10 and np.std(seg) > 0:
                ratios.append((v / 100.0) / (float(np.std(seg)) * math.sqrt(252)))
    if len(ratios) >= RATIO_MIN_N:
        ratio, ratio_note = min(RATIO_MAX, max(RATIO_MIN, float(np.median(ratios)))), "own, %d days" % len(ratios)
    else:
        ratio, ratio_note = RATIO_DEFAULT, "default (%d days to measure)" % len(ratios)
    iv_now = iv[-1][1] / 100.0 if iv else None
    iv_day = iv[-1][0] if iv else None
    iv_ex = None
    if iv_now:
        T = 30 / 365.0
        ev = 0.0
        if moves and iv_day:
            d0 = date.fromisoformat(iv_day)
            nxt, _ = future_earnings(conn, ticker, d0, d0 + timedelta(days=30), listed)
            if nxt:
                ev = float(np.mean(np.square(moves)))
        iv_ex = math.sqrt(max(iv_now ** 2 * T - ev, 1e-6) / T)
    vol_now = math.sqrt(g["var_now"] * 252)
    lr = math.sqrt(g["long_run_var"] * 252)
    ref = date.fromisoformat(reading_day) if reading_day else date.fromisoformat(days[-1])
    fresh = (ref - date.fromisoformat(days[-1])).days <= FRESH_DAYS
    iv_fresh = iv_day is not None and abs((ref - date.fromisoformat(iv_day)).days) <= FRESH_DAYS
    # s154: the market's forecast first -- implied vol (report taken out) / the implied-realized ratio
    if iv_ex is not None and (iv_fresh or not fresh):
        start_vol, start_note = iv_ex / ratio, "implied vol %s, report taken out, / %.2f" % (iv_day, ratio)
        if not iv_fresh:
            start_note += " -- implied vol older than the latest reading"
    else:
        start_vol, start_note = vol_now, "the stock's own recent moves (GARCH)"
        if not fresh:
            start_note += " -- closes end %s, older than the latest reading" % days[-1]
    return {"ticker": ticker, "closes": src, "first": days[0], "last": days[-1], "last_close": float(P[-1]),
            "n_returns": int(len(R)), "short_history": len(R) < 500,
            "earnings_days": len(ear), "earnings_inferred": len(inferred), "moves": moves,
            "earn_move_median": float(np.median(np.abs(moves))) if moves else None,
            "alpha": g["alpha"], "beta": g["beta"], "persistence": g["persistence"],
            "half_life_days": g["half_life_days"], "half_life_capped": capped, "long_run_vol": lr, "vol_now": vol_now,
            "iv_now": iv_now, "iv_day": iv_day, "iv_ex_earnings": iv_ex, "ratio": ratio, "ratio_note": ratio_note,
            "start_vol": start_vol, "start_note": start_note, "listed": listed}


# ---------------------------------------------------------------- simulation
def _third_fridays(start, n=16):
    out, y, m = [], start.year, start.month
    for _ in range(n):
        d = date(y, m, 15)
        while d.weekday() != 4:
            d += timedelta(days=1)
        out.append(d)
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def price_to(S0, vol0, start, end, prm, earn=(), n=N_PATHS, seed=SEED):
    """Prices and daily variances on `end` (weekday steps from `start`), no options: (S, h) arrays. s154: the
    run-up to a put's expiry, so the recovery starts only from the paths where it is assigned."""
    rng = np.random.default_rng(seed)
    LR = prm["long_run_vol"] ** 2 / 252
    a, b, pers = prm["alpha"], prm["beta"], prm["persistence"]
    w = LR * (1 - pers)
    moves = np.asarray(prm.get("moves") or [], float)
    S = np.full(n, float(S0))
    h = np.full(n, vol0 ** 2 / 252)
    d, prev = start, None
    jumps = set()
    for e in earn:
        if start <= e < end:
            x = e + timedelta(days=1)
            while x.weekday() >= 5:
                x += timedelta(days=1)
            jumps.add(x)
    d = start + timedelta(days=1)
    while d <= end:
        if d.weekday() < 5:
            z = rng.standard_normal(n)
            e = np.sqrt(h) * z
            r = e - 0.5 * h
            if d in jumps and len(moves):
                j = rng.choice(moves, n) * rng.choice([-1.0, 1.0], n)
                r = r + j - np.log(np.cosh(j))
            S = S * np.exp(r)
            h = w + a * e ** 2 + b * h
        d += timedelta(days=1)
    return S, h


def simulate(S0, basis, start, prm, vol0, earn=(), calls=True, n=N_PATHS, seed=SEED, rules=None, stats=None, h0=None):
    """One case. prm: alpha, beta, persistence, long_run_vol, ratio, moves. S0 a number or one start a path;
    h0 (daily variances, one a path) overrides vol0. Returns the measures."""
    rl = rules or _rules()
    rng = np.random.default_rng(seed)
    LR = prm["long_run_vol"] ** 2 / 252
    pers = prm["persistence"]
    a = prm["alpha"]
    b = prm["beta"]
    w = LR * (1 - pers)
    moves = np.asarray(prm.get("moves") or [], float)
    end = start + timedelta(days=365)
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    earn = [e for e in earn if start <= e <= end]
    jump = {min((x for x in days if x > e), default=None) for e in earn} - {None}
    EXP = _third_fridays(start)
    step = strike_step(basis)
    strikes = np.arange(math.ceil(basis / step - 1e-9) * step, max(basis, float(np.max(S0))) * 2.0 + step, step)  # s154: reaches paths well above the basis

    def avg_vol(h, m):
        k = np.arange(1, max(int(m), 1) + 1)
        return np.sqrt(252 * (LR + np.outer(h - LR, pers ** k).mean(axis=1)))

    S = np.broadcast_to(np.asarray(S0, float), (n,)).copy()
    h = (np.broadcast_to(np.asarray(h0, float), (n,)).copy() if h0 is not None else np.full(n, vol0 ** 2 / 252))
    rent = np.zeros(n)
    K = np.full(n, np.nan)
    credit = np.zeros(n)
    xi = np.full(n, -1)
    hold = np.zeros(n, bool)
    alive = np.ones(n, bool)
    gate = np.full(n, -1)
    touch = np.full(n, -1)
    whole = np.full(n, -1)
    called = np.full(n, -1)
    rent_at = {}
    st = stats if stats is not None else {}
    st.setdefault("min_strike", math.inf)
    st.setdefault("earn_inside", 0)
    for t, day in enumerate(days):
        if t > 0:
            z = rng.standard_normal(n)
            e = np.sqrt(h) * z
            r = e - 0.5 * h
            if day in jump and len(moves):
                j = rng.choice(moves, n) * rng.choice([-1.0, 1.0], n)
                r = r + j - np.log(np.cosh(j))          # E[exp(+-j)] = cosh(j): keeps the price a martingale
            S = np.where(alive, S * np.exp(r), S)
            h = w + a * e ** 2 + b * h
        cal = (day - start).days
        op = alive & (xi >= 0)
        if calls and op.any():
            dte = np.array([(EXP[i] - day).days if i >= 0 else 0 for i in xi])
            iv = avg_vol(h, 21) * prm["ratio"]
            mk, _ = bs_call(S, np.nan_to_num(K, nan=1.0), dte / 365.0, iv)
            hit = op & ~hold & (mk <= rl["take"] * credit) & (dte > 0)
            rent[hit] -= rl["take"] * credit[hit] + FEE
            xi[hit] = -1
            at21 = op & ~hit & ~hold & (dte <= rl["t21"])
            otm = at21 & (S < K)
            rent[otm] -= mk[otm] * (1 + HALF_SPREAD) + FEE
            xi[otm] = -1
            hold |= at21 & (S >= K)
            expd = op & ~hit & ~otm & (dte <= 0)
            ca = expd & (S >= K)
            called[ca & (called < 0)] = cal
            alive &= ~ca
            xi[expd & ~ca] = -1
            hold[expd] = False
        need = alive & (xi < 0)
        if calls and need.any():
            ok_e = [i for i, ex in enumerate(EXP) if rl["dte_lo"] <= (ex - day).days <= rl["dte_hi"]
                    and not any(day <= q <= ex for q in earn)]
            best = np.full(n, -1.0)
            bK = np.full(n, np.nan)
            bE = np.full(n, -1)
            bP = np.zeros(n)
            for i in ok_e:
                T = (EXP[i] - day).days
                iv = avg_vol(h, T * 252 / 365) * prm["ratio"]
                pr, de = bs_call(S[:, None], strikes[None, :], T / 365.0, iv[:, None])
                fitm = (de >= rl["dmin"]) & (de <= rl["dmax"])
                ann = np.where(fitm, pr / S[:, None] * 365.0 / T, -1.0)
                k = ann.argmax(axis=1)
                v = ann[np.arange(n), k]
                better = need & (v > best + 1e-12)
                best[better] = v[better]
                bK[better] = strikes[k[better]]
                bE[better] = i
                bP[better] = pr[np.arange(n), k][better]
            sold = need & (bE >= 0) & (best > 0)
            if sold.any():
                st["min_strike"] = min(st["min_strike"], float(np.min(bK[sold])))
                for i in set(bE[sold].tolist()):
                    if any(day <= q <= EXP[i] for q in earn):
                        st["earn_inside"] += 1
            K[sold] = bK[sold]
            xi[sold] = bE[sold]
            credit[sold] = bP[sold] * (1 - HALF_SPREAD) - FEE
            rent[sold] += credit[sold]
            gate[sold & (gate < 0)] = cal
        touch[(touch < 0) & (S >= basis)] = cal
        live = np.zeros(n)
        op = alive & (xi >= 0)
        if calls and op.any():
            dte = np.array([(EXP[i] - day).days if i >= 0 else 0 for i in xi])
            live[op] = bs_call(S[op], K[op], np.maximum(dte[op], 0) / 365.0, (avg_vol(h, 21) * prm["ratio"])[op])[0]
        whole[(whole < 0) & ((S + rent - live >= basis) | (called >= 0))] = cal
        for hz in HORIZ:
            if hz not in rent_at and cal >= hz:
                rent_at[hz] = rent - live
    for hz in HORIZ:
        rent_at.setdefault(hz, rent.copy())
    st["mean_end"] = float(np.mean(S))
    pct = lambda arr, hz: float(100.0 * np.mean((arr >= 0) & (arr <= hz)))  # noqa: E731
    return {"gate": [pct(gate, hz) for hz in HORIZ], "touch": [pct(touch, hz) for hz in HORIZ],
            "whole": [pct(whole, hz) for hz in HORIZ], "called": pct(called, 365),
            "rent": [float(np.median(rent_at[hz])) for hz in HORIZ],
            "gate_day": float(np.median(gate[gate >= 0])) if np.any(gate >= 0) else None,
            "earn": [e.isoformat() for e in earn]}


def assess(conn, position_id, root, ib_rows=None, n=N_PATHS, today=None):
    """Fit and both cases for one open put. Returns a dict (raises on a missing reading or history)."""
    today = today or date.today()
    p = conn.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
    leg = conn.execute("SELECT * FROM legs WHERE position_id=? AND status='OPEN' AND direction='SHORT' "
                       "AND option_type='PUT'", (position_id,)).fetchone()
    if p is None or leg is None:
        raise ValueError("no open short put on %s" % position_id)
    ck = conn.execute("SELECT spot_price, checked_at FROM checks WHERE position_id=? AND data_quality='GOOD' "
                      "AND spot_price IS NOT NULL ORDER BY checked_at DESC LIMIT 1", (position_id,)).fetchone()
    if ck is None:
        raise ValueError("no stored reading yet -- run after the next snapshot")
    t = p["ticker"]
    basis = float(leg["strike"]) - float(leg["open_price"] or 0)
    spot = float(ck["spot_price"])
    exp = date.fromisoformat(str(leg["expiration"])[:10])
    start = max(exp, today)
    f = fit(conn, t, root, ib_rows, reading_day=ck["checked_at"][:10])
    earn, earn_note = future_earnings(conn, t, start, start + timedelta(days=365), f["listed"])
    rl = _rules()
    K = float(leg["strike"])
    # s154: run each path to the expiry first; the recovery starts only where the put is assigned
    if exp > today:
        pre_earn, _ = future_earnings(conn, t, today, exp, f["listed"])
        Ss, hs, total = [], [], 0
        for i in range(MAX_BATCHES):
            S1, h1 = price_to(spot, f["start_vol"], today, exp, f, pre_earn, n=n, seed=SEED + 100 + i)
            total += n
            Ss.append(S1[S1 < K]); hs.append(h1[S1 < K])
            if sum(len(x) for x in Ss) >= MIN_ASSIGNED:
                break
        Sa, ha = np.concatenate(Ss), np.concatenate(hs)
        p_assign = float(100.0 * len(Sa) / total)
    else:
        Sa, ha, p_assign, pre_earn = np.array([spot]) if spot < K else np.array([]), np.array([f["start_vol"] ** 2 / 252]), (
            100.0 if spot < K else 0.0), []
    now = calm = None
    a_price = float(np.median(Sa)) if len(Sa) else None
    if len(Sa) >= (1 if exp <= today else MIN_ASSIGNED // 6):
        pick = np.random.default_rng(SEED).integers(0, len(Sa), n)
        S0s = Sa[pick]
        h0s = ha[pick] if len(ha) == len(Sa) else None
        now = simulate(S0s, basis, start, f, f["start_vol"], earn, n=n, rules=rl, h0=h0s)
        calm = simulate(S0s, basis, start, f, f["long_run_vol"], earn, n=n, rules=rl)
    shares = int(leg["contracts"]) * 100
    f.pop("listed")
    f.pop("moves")
    return {"position_id": position_id, "ticker": t, "spot": spot, "reading": ck["checked_at"][:16],
            "strike": K, "p_assign": p_assign, "assigned_price": a_price, "assigned_paths": int(len(Sa)),
            "pre_earn": [e.isoformat() for e in pre_earn if e < exp],
            "basis": basis, "shares": shares, "start": start.isoformat(), "earn_note": earn_note,
            "gate_now": gate_price(basis, f["start_vol"] * f["ratio"], 45, rl["dmin"]),
            "gate_calm": gate_price(basis, f["long_run_vol"] * f["ratio"], 45, rl["dmin"]),
            "fit": f, "now": now, "calm": calm, "horizons": list(HORIZ), "paths": n, "rule_version": RULE_VERSION}


# ---------------------------------------------------------------- record
def ensure_tables(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS recovery_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, run_at TEXT, "
                 "n_paths INTEGER, rule_version TEXT, note TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS recovery_results (run_id INTEGER, position_id TEXT, ticker TEXT, "
                 "result TEXT, error TEXT, PRIMARY KEY (run_id, position_id))")


def record(conn, results, n, note=None, when=None):
    """results: [(position_id, ticker, dict|None, error|None)]; returns the run id. Commits."""
    ensure_tables(conn)
    cur = conn.execute("INSERT INTO recovery_runs (run_at, n_paths, rule_version, note) VALUES (?,?,?,?)",
                       ((when or datetime.now()).isoformat(timespec="seconds"), n, RULE_VERSION, note))
    rid = cur.lastrowid
    for pid, t, res, err in results:
        conn.execute("INSERT INTO recovery_results VALUES (?,?,?,?,?)",
                     (rid, pid, t, json.dumps(res) if res is not None else None, err))
    conn.commit()
    return rid


def latest_for(conn, position_id):
    """The newest recorded result for a position: {run_at, result|None, error|None}, or None. Read-only;
    None when no run has recorded it (or the tables don't exist yet)."""
    try:
        r = conn.execute("SELECT r.run_at, x.result, x.error FROM recovery_results x JOIN recovery_runs r "
                         "ON r.id = x.run_id WHERE x.position_id=? ORDER BY r.id DESC LIMIT 1",
                         (position_id,)).fetchone()
    except sqlite3.Error:
        return None
    if r is None:
        return None
    return {"run_at": r[0], "result": json.loads(r[1]) if r[1] else None, "error": r[2]}


# ---------------------------------------------------------------- self-test
def _selftest():
    import tempfile
    fails, ran = [], []

    def ok(c, m):
        ran.append(m)
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            fails.append(m)

    rl = {"dmin": 0.20, "dmax": 0.35, "dte_lo": 30, "dte_hi": 60, "take": 0.5, "t21": 21}
    start = date(2026, 11, 20)
    # 1. constant vol, no calls, no earnings: the simulation agrees with the touch formula
    v = 0.30
    flat = {"alpha": 1e-9, "beta": 1e-9, "persistence": 2e-9, "long_run_vol": v, "ratio": 1.0, "moves": []}
    st = {}
    r = simulate(94.0, 102.0, start, flat, v, calls=False, n=12000, rules=rl, stats=st)
    b_adj = 102.0 * math.exp(0.5826 * v * math.sqrt(1 / 252.0))   # Broadie-Glasserman: checked once a day
    for k, hz in ((2, 90), (4, 365)):
        cf = 100 * touch_prob(94.0, b_adj, v, hz * 261 / 252.0)      # 261 weekdays carry a year's 252 of variance
        ok(abs(r["touch"][k] - cf) < 2.0, "touch at %d days: simulation %.1f%% vs formula %.1f%% (daily-check "
           "correction)" % (hz, r["touch"][k], cf))
    ok(abs(st["mean_end"] / 94.0 - 1) < 0.015, "no drift: the mean price after a year stays at the start (%.2f)" % st["mean_end"])
    ok(r["gate"] == [0.0] * 5 and r["rent"] == [0.0] * 5, "calls off: no gate, no rent")
    # 2. the gate price: a 0.20-delta call at the basis
    gp = gate_price(102.72, 0.30, 45, 0.20)
    _, de = bs_call(gp, 102.72, 45 / 365.0, 0.30)
    ok(abs(float(de) - 0.20) < 1e-3, "gate price %.2f: the call at the basis there has delta %.3f" % (gp, float(de)))
    # 3. calls follow the rules: never below the basis; never across a report
    st = {}
    earn = [date(2027, 2, 10), date(2027, 5, 12)]
    gv = {"alpha": 0.10, "beta": 0.85, "persistence": 0.95, "long_run_vol": 0.30, "ratio": 1.05, "moves": [0.05, -0.04, 0.06]}
    r = simulate(98.0, 102.72, start, gv, 0.45, earn, n=1500, rules=rl, stats=st)
    ok(st["min_strike"] >= 102.72, "no call sold below the basis (lowest %.2f)" % st["min_strike"])
    ok(st["earn_inside"] == 0, "no call sold across a report date")
    ok(r["gate"][0] > 0 and r["rent"][-1] > 0, "calls sold and rent earned when the gate is open")
    ok(all(r["whole"][i] <= r["whole"][i + 1] for i in range(4)) and all(r["touch"][i] <= r["touch"][i + 1] for i in range(4)),
       "the odds never fall with a longer horizon")
    r2 = simulate(98.0, 102.72, start, gv, 0.45, earn, n=1500, rules=dict(rl, dmin=0.36, dmax=0.40), stats={})
    ok(r2["gate"][0] <= r["gate"][0], "a narrower, nearer band opens the gate no sooner")
    # 4. GARCH recovers known parameters from simulated returns
    rng = np.random.default_rng(1)
    A, B, LR = 0.10, 0.85, 0.02 ** 2
    h, xs = LR, []
    for _ in range(4000):
        e = math.sqrt(h) * rng.standard_normal()
        xs.append(e)
        h = LR * (1 - A - B) + A * e * e + B * h
    g = garch_fit(xs)
    ok(abs(g["persistence"] - 0.95) < 0.04 and abs(g["alpha"] - 0.10) < 0.05,
       "GARCH fit recovers alpha 0.10 / persistence 0.95 (got %.3f / %.3f)" % (g["alpha"], g["persistence"]))
    # 5. report days found, the pattern inferred before the first stored date
    days = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(1500)]
    days = [d for d in days if date.fromisoformat(d).weekday() < 5]
    Rr = np.full(len(days) - 1, 0.001)
    for d in ("2021-02-12", "2021-05-14", "2022-02-11", "2023-05-12"):
        Rr[days[1:].index(d)] = 0.08
    ear, inf = reaction_days(days[1:], Rr, ["2022-02-10", "2023-05-11"])
    found = {days[1:][j] for j in ear}
    ok({"2022-02-11", "2023-05-12"} <= found, "stored report dates: the reaction day is the bigger move")
    ok({"2021-02-12", "2021-05-14"} <= found and inf, "earlier report days inferred from the stored pattern")
    # 5b. the run-up to expiry: a deep in-the-money put is nearly always assigned, a deep out-of-the-money one rarely
    S1, _ = price_to(80.0, 0.30, date(2026, 11, 9), date(2026, 11, 20), gv, n=4000)
    ok(float(np.mean(S1 < 100.0)) > 0.97, "11 days out, $80 vs a $100 strike: assigned on nearly every path")
    S2, _ = price_to(130.0, 0.30, date(2026, 11, 9), date(2026, 11, 20), gv, n=4000)
    ok(float(np.mean(S2 < 100.0)) < 0.01, "11 days out, $130 vs a $100 strike: assigned almost never")
    S3, _ = price_to(100.0, 0.30, date(2026, 11, 9), date(2026, 11, 20), dict(gv, moves=[0.5]), [date(2026, 11, 12)], n=4000)
    ok(abs(float(np.mean(S3)) / 100.0 - 1) < 0.03 and float(np.std(np.log(S3))) > 0.4, "a report before expiry moves the price (and keeps no drift)")
    # 6. record and read back
    db = os.path.join(tempfile.mkdtemp(), "r.db")
    c = sqlite3.connect(db)
    ok(latest_for(c, "X") is None, "no tables yet -> no result, no error")
    record(c, [("X", "XX", {"a": 1}, None)], 100, when=datetime(2026, 11, 8, 10))
    record(c, [("X", "XX", None, "no reading")], 100, when=datetime(2026, 11, 9, 10))
    lt = latest_for(c, "X")
    ok(lt["error"] == "no reading" and lt["result"] is None and lt["run_at"].startswith("2026-11-09"),
       "the newest run is the one read back")
    print("recovery selftest:", "PASS (%d checks)" % len(ran) if not fails else "%d FAIL" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest() if "--selftest" in sys.argv else 0)
