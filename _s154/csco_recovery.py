"""s154: if the CSCO 11/20 105 put is assigned -- how soon could HELM sell a covered call, how much rent, and
how likely is the trade made whole? Read-only; reads _s154/csco_params.json (csco_fit.py).

Paths: daily, weekdays from the assignment day (2026-11-20) for 365 calendar days. Volatility follows CSCO's
GARCH(1,1) fit (clustering, fading back to the long-run level), started at a chosen level; the half-life can
be overridden. Earnings days add a jump drawn from CSCO's own earnings moves, with a random sign (no view on
direction). No drift: the price is a martingale. Normal shocks; no skew.
Calls follow HELM's covered-call rules: strike >= HELM's effective basis (102.72 -- HELM does not lower it
with call premiums), 0.20-0.35 delta, 30-60 days, monthly expiries only, no earnings inside; ranked by
yearly premium on the shares' value, ties to the nearer expiry then the higher strike. Priced by Black-Scholes
at implied = the expected vol over the option's life x CSCO's implied/realized ratio. Sold at mid - 2.5%,
bought at mid + 2.5%, $0.67 a contract a leg. Managed: buy back at 50% of the credit (the resting order); at
21 days left, buy back if out of the money, else hold to expiry and let the shares be called away.
Measures (each by 30/60/90/180/365 days): (1) the gate -- a call HELM would allow has been sold;
(2) rent -- net call income per share; (3) back to basis on price alone; made whole -- shares + rent banked
- the open call >= 102.72, or called away.
Usage: python3 -B _s154/csco_recovery.py [--paths N] [--seed S]
"""
import json, math, os, sys
from datetime import date, timedelta
import numpy as np
H = os.path.expanduser("~/mnt/helm"); PRM = json.load(open(H + "/_s154/csco_params.json"))
BASIS = 105 - 2.28; START = date(2026, 11, 20); END = START + timedelta(days=365)
EARN = [date(2027, 2, 10), date(2027, 5, 12), date(2027, 8, 11), date(2027, 11, 10)]  # estimated (mid-month Wed)
HORIZ = (30, 60, 90, 180, 365); FEE = 0.0067; HALF_SPREAD = 0.025; DMIN, DMAX, DTE_LO, DTE_HI = 0.20, 0.35, 30, 60
args = sys.argv[1:]; NP = int(args[args.index("--paths") + 1]) if "--paths" in args else 20000
SEED = int(args[args.index("--seed") + 1]) if "--seed" in args else 154
G = PRM["garch"]; LR_VAR = G["long_run_vol"] ** 2 / 252; MOVES = np.array(PRM["earnings_moves"])
days = []; d = START
while d <= END:
    if d.weekday() < 5: days.append(d)
    d += timedelta(days=1)
jump_day = {min(x for x in days if x > e) for e in EARN if e < END}
def third_fri(y, m):
    d = date(y, m, 15)
    while d.weekday() != 4: d += timedelta(days=1)
    return d
EXP = [third_fri(2026 + (m - 1) // 12, (m - 1) % 12 + 1) for m in range(12, 12 + 15)]
_erf = np.vectorize(math.erf)
def ncdf(x): return 0.5 * (1 + _erf(np.asarray(x) / math.sqrt(2)))
def bs_call(S, K, T, v):
    T = np.maximum(T, 1e-6); v = np.maximum(v, 1e-4); sd = v * np.sqrt(T)
    d1 = (np.log(S / K) + 0.5 * sd ** 2) / sd
    return S * ncdf(d1) - K * ncdf(d1 - sd), ncdf(d1)
def avg_vol(h, n, pers):
    """expected average variance over the next n trading days (GARCH), annualised vol"""
    k = np.arange(1, n + 1); return np.sqrt(252 * (LR_VAR + np.outer(h - LR_VAR, pers ** k).mean(axis=1)))
def run(S0, vol0, half=None, ratio=PRM["iv_over_realized"]["median"], seed=SEED):
    rng = np.random.default_rng(seed)
    pers = G["persistence"] if half is None else 0.5 ** (1.0 / half)
    a = G["alpha"] * pers / G["persistence"]; b = pers - a; w = LR_VAR * (1 - pers)
    S = np.full(NP, float(S0)); h = np.full(NP, vol0 ** 2 / 252)
    rent = np.zeros(NP); K = np.full(NP, np.nan); X = [None] * NP; credit = np.zeros(NP)
    xi = np.full(NP, -1)                            # expiry index of the open call (-1 = none)
    hold = np.zeros(NP, bool)                       # holding an in-the-money call to expiry
    alive = np.ones(NP, bool); gate = np.full(NP, -1); touch = np.full(NP, -1); whole = np.full(NP, -1)
    called = np.full(NP, -1); rent_at = {}; strikes = np.arange(math.ceil(BASIS), 200, 1.0)
    for t, day in enumerate(days):
        if t > 0:
            z = rng.standard_normal(NP); r = np.sqrt(h) * z - 0.5 * h
            if day in jump_day:
                j = rng.choice(MOVES, NP) * rng.choice([-1.0, 1.0], NP); r = r + j - 0.5 * j ** 2 + 0.5 * 0  # ~martingale
            S = np.where(alive, S * np.exp(r), S); h = w + a * (np.sqrt(h) * z) ** 2 + b * h
        cal = (day - START).days
        # the open call: mark, 50% buy-back, 21 days, expiry
        op = alive & (xi >= 0)
        if op.any():
            ex = np.array([EXP[i] if i >= 0 else day for i in xi]); dte = np.array([(e - day).days for e in ex])
            iv = avg_vol(h, 21, pers) * ratio
            mk, _ = bs_call(S, np.nan_to_num(K, nan=1.0), dte / 365.0, iv)
            hit = op & ~hold & (mk <= 0.5 * credit) & (dte > 0)
            rent[hit] -= 0.5 * credit[hit] + FEE; xi[hit] = -1
            at21 = op & ~hit & ~hold & (dte <= 21)
            otm = at21 & (S < K); rent[otm] -= mk[otm] * (1 + HALF_SPREAD) + FEE; xi[otm] = -1
            hold |= at21 & (S >= K)
            exp_now = op & ~hit & ~otm & (dte <= 0)
            ca = exp_now & (S >= K); called[ca & (called < 0)] = cal; alive &= ~ca
            xi[exp_now & ~ca] = -1; hold[exp_now] = False
        # sell a call where none is open
        need = alive & (xi < 0)
        if need.any():
            ok_e = [i for i, e in enumerate(EXP) if DTE_LO <= (e - day).days <= DTE_HI
                    and not any(day <= q <= e for q in EARN)]
            best = np.full(NP, -1.0); bK = np.full(NP, np.nan); bE = np.full(NP, -1); bP = np.zeros(NP)
            for i in ok_e:
                T = (EXP[i] - day).days; iv = avg_vol(h, int(T * 252 / 365), pers) * ratio
                pr, de = bs_call(S[:, None], strikes[None, :], T / 365.0, iv[:, None])
                fit = (de >= DMIN) & (de <= DMAX)
                ann = np.where(fit, pr / S[:, None] * 365 / T, -1.0)
                k = ann.argmax(axis=1); v = ann[np.arange(NP), k]
                better = need & (v > best + 1e-12)
                best[better] = v[better]; bK[better] = strikes[k[better]]; bE[better] = i; bP[better] = pr[np.arange(NP), k][better]
            sold = need & (bE >= 0) & (best > 0)
            K[sold] = bK[sold]; xi[sold] = bE[sold]; credit[sold] = bP[sold] * (1 - HALF_SPREAD) - FEE
            rent[sold] += credit[sold]; gate[sold & (gate < 0)] = cal
        touch[(touch < 0) & (S >= BASIS)] = cal
        live_mark = np.zeros(NP)
        op = alive & (xi >= 0)
        if op.any():
            ex = np.array([EXP[i] if i >= 0 else day for i in xi]); dte = np.array([(e - day).days for e in ex])
            live_mark[op] = bs_call(S[op], K[op], np.maximum(dte[op], 0) / 365.0, (avg_vol(h, 21, pers) * ratio)[op])[0]
        eq = S + rent - live_mark
        whole[(whole < 0) & ((eq >= BASIS) | (called >= 0))] = cal
        for hz in HORIZ:                            # rent EARNED: banked, less what the open call would cost back
            if hz not in rent_at and cal >= hz: rent_at[hz] = rent - live_mark
    rent_at.setdefault(365, rent - live_mark)
    pct = lambda arr, hz: 100.0 * np.mean((arr >= 0) & (arr <= hz))
    return {"gate": [pct(gate, hz) for hz in HORIZ], "touch": [pct(touch, hz) for hz in HORIZ],
            "whole": [pct(whole, hz) for hz in HORIZ], "called": pct(called, 365),
            "rent_med": [float(np.median(rent_at[hz])) for hz in HORIZ],
            "rent_med_if_gate": [float(np.median(rent_at[hz][(gate >= 0) & (gate <= hz)])) if np.any((gate >= 0) & (gate <= hz)) else 0.0 for hz in HORIZ],
            "gate_median_day": float(np.median(gate[gate >= 0])) if np.any(gate >= 0) else None}
def touch_closed(S0, B, v, days_):
    """GBM touch probability, no drift (martingale), constant vol"""
    T = days_ / 365.0; a = math.log(B / S0); mu = -0.5 * v * v; sd = v * math.sqrt(T)
    N = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    return 100 * (N((-a + mu * T) / sd) + math.exp(2 * mu * a / (v * v)) * N((-a - mu * T) / sd))
def gate_price(v, T_days):
    """the price at which a 0.20-delta call T days out strikes exactly at the basis -- the LOWEST delta in
    HELM's band is the call furthest out of the money, so it reaches the basis first"""
    sd = v * math.sqrt(T_days / 365.0); z = 0.84162  # N^-1(0.80)
    return BASIS * math.exp(-(z * sd + 0.5 * sd * sd))
if __name__ == "__main__":
    hi, lo = G["vol_now"], G["long_run_vol"]
    print("basis %.2f | vol now %.1f%% (GARCH; implied ex-earnings %.1f%%) | long-run %.1f%% | half-life %.0f trading days"
          " | implied/realized %.2f | earnings jump median %.1f%% | paths %d"
          % (BASIS, 100 * hi, 100 * PRM["iv"]["ex_earnings_now"], 100 * lo, G["half_life_days"],
             PRM["iv_over_realized"]["median"], 100 * PRM["earn_abs_median"], NP))
    print("gate price (0.20-delta call strikes at 102.72): 45 days out at vol 49%% -> %.2f, at 35%% -> %.2f, at 24%% -> %.2f"
          % (gate_price(hi * PRM["iv_over_realized"]["median"], 45), gate_price(0.35, 45), gate_price(lo * PRM["iv_over_realized"]["median"], 45)))
    part = args[args.index("--part") + 1] if "--part" in args else "base"
    R = PRM["iv_over_realized"]["median"]
    if part == "base":
        runs = [(S0, "start vol %.0f%% (today's), fades" % (100 * hi), hi, None, R) for S0 in (90.0, PRM["last_close"], 100.0)]
        runs += [(S0, "start vol %.0f%% (calm: long-run)" % (100 * lo), lo, None, R) for S0 in (90.0, PRM["last_close"], 100.0)]
    else:
        S0 = PRM["last_close"]
        runs = [(S0, "today's vol, half-life %d days (CSCO's implied vol)" % round(PRM["iv"]["half_life_days"]), hi, PRM["iv"]["half_life_days"], R),
                (S0, "today's vol, half-life 63 days (slow)", hi, 63, R),
                (S0, "today's vol, implied/realized 1.15 (richer calls)", hi, None, 1.15),
                (S0, "calm vol, implied/realized 1.15", lo, None, 1.15)]
    for S0, name, v0, half, ratio in runs:
        if True:
            r = run(S0, v0, half, ratio)
            print("\nstart $%.2f | %s" % (S0, name))
            print("   by day:                 " + "".join("%8d" % x for x in HORIZ))
            print("   1 gate open (call sold) " + "".join("%7.0f%%" % x for x in r["gate"]))
            print("   2 rent earned, median   " + "".join("%8.2f" % x for x in r["rent_med"]))
            print("   3 back to basis (price) " + "".join("%7.0f%%" % x for x in r["touch"]))
            print("     made whole (+ rent)   " + "".join("%7.0f%%" % x for x in r["whole"]))
            print("     called away by day 365: %.0f%% | first call sold, median day %s" % (r["called"], r["gate_median_day"]))
    if part != "base":
        sys.exit(0)
    print("\ncheck, constant vol, no rent: touch 102.72 from $%.2f" % PRM["last_close"])
    for v in (hi, lo):
        print("   vol %.0f%%: " % (100 * v) + "  ".join("%dd %.0f%%" % (x, touch_closed(PRM["last_close"], BASIS, v, x)) for x in HORIZ))
