"""helm/thesis.py — the Thesis Panel evaluator (W88 slice 2 · one evaluator, every surface).

Pure and DB-free (the lc_screen / vol_read pattern): callers fetch rows, this
module turns them into belief states and plain-language card content. Two
surfaces computing thesis state independently is how W78 happened — anything
that renders a thesis reads from here.

Doctrine: facts gate, judgments display. This module gates nothing.
UNKNOWN is never invented (HELM-095); NULL history means "predates capture"
(the W67 convention), not a guess.

Fray thresholds for the strike belief are TIER A — measured on 194 closed
credit positions by the W88 slice-1 backtest (two methods, permutation
p <= 0.033): the signal is at the BREACH, not the approach. For CSPs and
credit verticals a confirmed breach precedes losses at ~1.7-3.2x the base
rate. FOR CONDORS THE SAME STATE HAS NO MEASURED PREDICTIVE SEPARATION
(0.92x) — their cards say so and must not imply prediction.
See claude/HELM-W88-slice1-backtest-findings.md.
"""
from __future__ import annotations

import json
from datetime import date

# ── belief state vocabulary ──────────────────────────────────────────────────
HOLDS, FRAYING, BROKEN, BROKEN_LOUD = "HOLDS", "FRAYING", "BROKEN", "BROKEN_LOUD"
DRIFT_FOR, DRIFT_AGAINST, VINDICATED = "DRIFT_FOR", "DRIFT_AGAINST", "VINDICATED"
CONTESTED, PARTIAL, UNKNOWN = "CONTESTED", "PARTIAL", "UNKNOWN"

_ICON = {HOLDS: "✓", FRAYING: "⚠", BROKEN: "✗", BROKEN_LOUD: "✗",
         DRIFT_FOR: "→", DRIFT_AGAINST: "→", VINDICATED: "✓",
         CONTESTED: "⚠", PARTIAL: "◐", UNKNOWN: "○"}
_WORD = {HOLDS: "holds", FRAYING: "warning", BROKEN: "broken",
         BROKEN_LOUD: "broken — loud", DRIFT_FOR: "moving your way",
         DRIFT_AGAINST: "moving against you", VINDICATED: "paid off",
         CONTESTED: "entry measures disagreed", PARTIAL: "incomplete data",
         UNKNOWN: "no data — not guessed"}

_CREDIT = ("CSP", "COVERED_CALL", "BEAR_CALL_SPREAD", "BULL_PUT_SPREAD",
           "IRON_CONDOR", "SHORT_STRANGLE", "JADE_LIZARD")
_SINGLE_SHORT = ("CSP", "COVERED_CALL", "BEAR_CALL_SPREAD", "BULL_PUT_SPREAD")
_LONGS = ("LONG_CALL", "LONG_PUT")
_VERT_DEBIT = ("BEAR_PUT_SPREAD", "BULL_CALL_SPREAD")
_DIAG = ("DIAGONAL", "PMCC", "DIAGONAL_PUT")


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _money(x):
    if x is None:
        return "—"
    return ("+$%s" % format(round(x), ",")) if x >= 0 else ("−$%s" % format(-round(x), ","))


# ── the strike observable (nearest wall), same construction HELM-141 journals ─
def buffer_pct(legs, spot):
    """Signed % distance from spot to the nearest short strike; None if any
    short leg is unreadable (a wall you cannot see must not be assumed away)."""
    spot = _f(spot)
    if not spot:
        return None
    walls = []
    for l in legs or []:
        if l.get("direction") != "SHORT" or l.get("option_type") in (None, "STOCK"):
            continue
        k = _f(l.get("strike"))
        if k is None or not l.get("option_type"):
            return None
        d = (spot - k) if l["option_type"] == "PUT" else (k - spot)
        walls.append((d / spot * 100.0, k, l["option_type"]))
    if not walls:
        return None
    return min(walls, key=lambda w: w[0])


def day_series(legs, checks):
    """Worst-of-day nearest-wall buffer per check day (the GE 7/29 lesson:
    the 15:55 touch must register even when the morning looked fine)."""
    byday = {}
    for r in checks or []:
        d = (r.get("checked_at") or "")[:10]
        b = buffer_pct(legs, r.get("spot_price"))
        if not d or b is None:
            continue
        if d not in byday or b[0] < byday[d][0]:
            byday[d] = b
    return [(d, byday[d]) for d in sorted(byday)]


def _strike_state(series):
    """Slice-1 calibrated states. Returns (state, streak, worst_hist)."""
    if not series:
        return UNKNOWN, 0, None
    breach_streak = 0
    for _, (b, _k, _t) in reversed(series):
        if b < 0:
            breach_streak += 1
        else:
            break
    last = series[-1][1][0]
    worst = min(b for _, (b, _k, _t) in series)
    if breach_streak >= 3 or last < -2.0:
        return BROKEN_LOUD, breach_streak, worst
    if breach_streak >= 2:
        return BROKEN, breach_streak, worst
    if last < 0:
        return FRAYING, 1, worst          # one breached day — not yet confirmed
    if last < 1.0:
        return FRAYING, 0, worst          # inside the 0–1% amber band
    return HOLDS, 0, worst


# ── belief builders ──────────────────────────────────────────────────────────
def _belief(key, title, then, now, state, fine, extra=None):
    return {"key": key, "title": title, "then": then, "now": now,
            "state": state, "word": _WORD[state], "icon": _ICON[state],
            "fine_print": fine, "extra": extra or {}}


def _strike_belief(pos, legs, checks, latest_check):
    strat = (pos.get("strategy") or "").upper()
    series = day_series(legs, checks)
    state, streak, worst = _strike_state(series)
    two_walls = strat in ("IRON_CONDOR", "SHORT_STRANGLE", "JADE_LIZARD")
    if two_walls:
        _sk = sorted({_f(l.get("strike")) for l in legs or []
                      if l.get("direction") == "SHORT"
                      and l.get("option_type") not in (None, "STOCK")
                      and _f(l.get("strike")) is not None})
        if len(_sk) >= 2:
            title = "%s stays between $%.0f and $%.0f" % (pos.get("ticker"), _sk[0], _sk[-1])
        else:
            title = "%s stays between the short strikes" % pos.get("ticker")
    else:
        wall = series[-1][1] if series else None
        if wall:
            title = "%s stays %s $%.0f" % (pos.get("ticker"),
                                           "above" if wall[2] == "PUT" else "below", wall[1])
        else:
            title = "%s stays on your side of the strike" % pos.get("ticker")
    if not series:
        return _belief("strike", title, "asserted at entry",
                       "no computable distance — legs or spot unreadable",
                       UNKNOWN, "missing data is shown as missing, never estimated (HELM-095)")
    d_last, (b, k, t_) = series[-1]
    side_safe = "above" if t_ == "PUT" else "below"
    side_bad = "below" if t_ == "PUT" else "above"
    if b >= 0:
        now = "%s is %.1f%% %s the $%.0f strike (as of %s)" % (
            pos.get("ticker"), b, side_safe, k, d_last)
    else:
        now = "%s is %.1f%% %s the $%.0f strike (as of %s)" % (
            pos.get("ticker"), abs(b), side_bad, k, d_last)
    healed = (state == HOLDS and worst is not None and worst < 1.0)
    if healed:
        fray_day = next(d for d, (bb, _kk, _tt) in series if bb < 1.0)
        now += " · dipped to %.1f%% on %s, since recovered" % (max(worst, -99.9), fray_day)
    if state in (BROKEN, BROKEN_LOUD):
        now += " · %s the strike at %d consecutive daily checks" % (side_bad, streak)
    fine = ("distance from spot to the nearest short strike, worst reading of each day, "
            "recomputed from legs and journaled spot. Bands, measured on 194 closed "
            "positions (s95): 1%+ = normal · under 1% = warning · past the strike on "
            "2+ consecutive check days = broken")
    if two_walls:
        fine += (". For condors these states describe where the position stands, not "
                 "its odds — the backtest found no predictive separation for condors")
    else:
        fine += (" — for CSPs and credit verticals a confirmed cross precedes losses "
                 "at 1.7–3.2× the base rate")
    extra = {}
    dlt = _f((latest_check or {}).get("delta"))
    if strat in _SINGLE_SHORT and dlt is not None:
        odds = min(99, max(1, round(abs(dlt) * 100)))
        _wall = series[-1][1] if series else None
        if _wall is not None:
            _side_txt = "below" if _wall[2] == "PUT" else "above"
            _s = "the market prices ~%d%% odds %s finishes %s $%.0f at expiry" % (
                odds, pos.get("ticker"), _side_txt, _wall[1])
            if strat == "CSP":
                _s += " — assignment odds"
            elif strat == "COVERED_CALL":
                _s += " — call-away odds"
            extra["odds"] = _s
        else:
            extra["odds"] = "the market prices ~%d%% odds the price finishes past the strike" % odds
    elif strat in ("IRON_CONDOR", "SHORT_STRANGLE", "JADE_LIZARD"):
        extra["odds"] = "odds unavailable — per-leg greeks not captured (W27)"
    return _belief("strike", title,
                   "entered with the strike at $%.0f" % (series[0][1][1],),
                   now, state, fine, extra)


def _premium_belief(pos, legs, entry_snap, cur_sig, latest_check):
    """P4/P5 — entry pricing, read as a ledger of forces on the mark.
    The blend Russ chose (s95): itemize each input, its move, and its
    direction for/against, with the causal chain only where it adds
    information. This measure affects P&L only; it can never trigger an
    exit."""
    strat = (pos.get("strategy") or "").upper()
    credit = strat in _CREDIT
    prem = _f(pos.get("net_premium"))
    e_spot = _f((entry_snap or {}).get("spot_price"))
    e_ivr = _f((entry_snap or {}).get("iv_rank"))
    e_iv = _f((entry_snap or {}).get("iv_current"))
    e_hv = _f((entry_snap or {}).get("hv_30d"))
    if e_hv is not None and e_iv is not None and e_hv < 3 and e_iv > 3:
        e_hv *= 100  # snapshots store HV as a fraction on some rows; IV is in points
    e_vrp = (e_iv - e_hv) if (e_iv is not None and e_hv is not None) else None
    c_iv = _f((latest_check or {}).get("iv_current"))
    c_vrp = _f((cur_sig or {}).get("vrp"))
    ive = _f((latest_check or {}).get("iv_vs_entry"))
    if ive is None and e_iv is not None and c_iv is not None:
        ive = c_iv - e_iv
    n_spot = _f((latest_check or {}).get("spot_price"))
    title = ("I sold this premium when it was expensive" if credit
             else "I bought this option while it was cheap")
    then_bits = []
    if prem:
        then_bits.append(("banked %s" if credit else "paid %s") % _money(abs(prem)))
    if e_iv is not None:
        then_bits.append("IV %.1f" % e_iv)
    if e_ivr is not None:
        then_bits.append("rank %.0f" % e_ivr)
    if e_vrp is not None:
        then_bits.append("VRP %+.1f" % e_vrp)
    elif e_iv is not None or e_ivr is not None:
        then_bits.append("entry HV not captured (W26) — rich-vs-realized not gradable")
    then = " · ".join(then_bits) or "entry measures not captured"
    if e_ivr is None and e_iv is None:
        return _belief("premium", title, then,
                       "cannot grade — no entry volatility snapshot (W26)",
                       PARTIAL, "shown as incomplete rather than estimated; never back-filled")
    contested = (e_ivr is not None and e_ivr >= 50 and e_vrp is not None and e_vrp <= 0)
    forces = []
    if e_spot is not None and n_spot is not None and legs:
        b0, b1 = buffer_pct(legs, e_spot), buffer_pct(legs, n_spot)
        if b0 is not None and b1 is not None:
            closer = b1[0] < b0[0]
            good = (not closer) if credit else closer
            forces.append("price $%.2f → $%.2f — %s you" % (e_spot, n_spot,
                          "for" if good else "against"))
    if ive is not None and e_iv is not None and c_iv is not None:
        if credit:
            forces.append("IV %.1f → %.1f — %s you (the option you sold costs %s to buy back)"
                          % (e_iv, c_iv, "for" if ive < 0 else "against",
                             "less" if ive < 0 else "more"))
        else:
            forces.append("IV %.1f → %.1f — %s you (the option you own is repriced %s)"
                          % (e_iv, c_iv, "for" if ive > 0 else "against",
                             "higher" if ive > 0 else "lower"))
    elif ive is not None:
        forces.append("IV %+.1f vs entry — %s you"
                      % (ive, "for" if (ive < 0) == credit else "against"))
    theta = _f((latest_check or {}).get("theta"))
    if theta is not None and strat in _SINGLE_SHORT:
        wall = None
        for l in legs or []:
            if l.get("direction") == "SHORT" and l.get("option_type") not in (None, "STOCK"):
                wall = (_f(l.get("strike")), l.get("option_type"))
                break
        if wall and wall[0] is not None:
            forces.append("time ≈ $%.0f/day accrues to you while %s stays %s $%.0f"
                          % (abs(theta * 100), pos.get("ticker"),
                             "above" if wall[1] == "PUT" else "below", wall[0]))
        else:
            forces.append("time ≈ $%.0f/day accrues to you" % abs(theta * 100))
    elif theta is not None and strat in _LONGS:
        forces.append("time ≈ $%.0f/day decays against you" % abs(theta * 100))
    if c_vrp is not None:
        forces.append("VRP now %+.1f — IV %s realized vol"
                      % (c_vrp, "above" if c_vrp > 0 else "below"))
    now = "the forces on your mark since entry:" if forces else "no current volatility read"
    if contested:
        state = CONTESTED
        now = ("rank %.0f said expensive, VRP %+.1f said cheap — on the day you traded. "
               % (e_ivr, e_vrp)) + now
    elif ive is None:
        state = PARTIAL
    else:
        favorable = (ive < 0) if credit else (ive > 0)
        state = VINDICATED if favorable else DRIFT_AGAINST
    fine = ("entry pricing affects P&L only — it can never trigger an exit. Scored "
            "for/against, not pass/fail: the price you were paid was fixed the day you traded")
    return _belief("premium", title, then, now, state, fine, {"forces": forces})


def _direction_belief(pos, entry_thesis_row, checks, cur_sig, want_up=True):
    """P1/P2 for longs and debit verticals — the acting THESIS_BREAK machinery,
    rendered. Reads what HELM-112 already journals; computes nothing new."""
    word = "keeps going up" if want_up else "keeps going down"
    title = "%s %s" % (pos.get("ticker"), word)
    if not entry_thesis_row:
        return _belief("direction", title, "position predates thesis capture (s90)",
                       "never armed — no entry thesis on record",
                       UNKNOWN, "NULL means 'predates capture' (W67); nothing is invented")
    e_bias = _f(entry_thesis_row.get("bias_score"))
    then = "entry bias %+.1f · spot %.2f vs SMA50 %.2f" % (
        e_bias or 0.0, _f(entry_thesis_row.get("spot_price")) or 0.0,
        _f(entry_thesis_row.get("sma_50")) or 0.0)
    arms = None
    for r in reversed(checks or []):
        if r.get("lc_arms_json"):
            try:
                arms = json.loads(r["lc_arms_json"]).get("thesis") or None
            except (ValueError, TypeError):
                arms = None
            if arms:
                break
    if not arms:
        return _belief("direction", title, then,
                       "no thesis check journaled yet",
                       UNKNOWN, "the 3×/day check writes the streak; none on record for this position")
    cur = _f(arms.get("cur_bias"))
    streak = int(arms.get("streak") or 0)
    confirm = int(arms.get("confirm_days") or 2)
    broken_today = bool(arms.get("broken_today"))
    now = "bias %+.1f → %+.1f" % (e_bias or 0.0, cur if cur is not None else 0.0)
    if broken_today and streak >= confirm:
        state = BROKEN
        now += " · broken %d consecutive checks (confirm %d) — this is the acting exit" % (streak, confirm)
    elif broken_today:
        state = FRAYING
        now += " · broken today, streak %d of %d — one more confirms" % (streak, confirm)
    else:
        state = HOLDS
        now += " · intact on the latest check"
    fine = "the HELM-112 doctrine, displayed: entry bias vs current bias from the same " \
           "current_context() the exit agent compares; 2-day confirmation; this belief ACTS " \
           "(THESIS_BREAK) for longs — the one belief that is already a gate"
    return _belief("direction", title, then, now, state, fine)


def _own_belief(pos, ownership):
    title = "if I end up owning %s, I want to" % pos.get("ticker")
    if not ownership:
        return _belief("own", title, "asserted at entry",
                       "no ownership grade on file (coverage is partial — W23)",
                       UNKNOWN, "graded quarterly-ish by helm quality; absence is a fact")
    g = (ownership.get("grade") or "?").upper()
    when = (ownership.get("updated_at") or ownership.get("date") or "")[:10]
    now = "grade %s (confidence %s) as of %s" % (g, ownership.get("confidence", "—"), when or "—")
    if g == "A":
        state, note = HOLDS, "assignment is a plan, not a problem"
    elif g == "B":
        state, note = HOLDS, "tolerable — but B means the exit half is homework before shares land (the HD caveat)"
    else:
        state, note = FRAYING, "a grade below B makes assignment a problem, not a plan"
    return _belief("own", title, "asserted at entry", now + " — " + note, state,
                   "the assignment backstop; grade moves quarterly, so the date matters")


def _term_belief(pos):
    return _belief("term", "the long clock is worth owning while the short clock pays rent",
                   "asserted at entry", "not yet observable — back-leg IV vs front decay is slice 5",
                   UNKNOWN, "the weakest observable, UNKNOWN-capable from day one by design")


# ── expiry ladder + convergence (defined-risk structures) ────────────────────
def _pnl_at_expiry(legs, spot):
    """House-convention P&L if the position ran to expiry at this spot:
    per leg (intrinsic − open)×c×100 long / (open − intrinsic)×c×100 short —
    the mtm formula with mid → intrinsic."""
    spot = _f(spot)
    if spot is None:
        return None
    total = 0.0
    for l in legs or []:
        if l.get("option_type") in (None, "STOCK"):
            continue
        k, op, c = _f(l.get("strike")), _f(l.get("open_price")), _f(l.get("contracts")) or 1
        if k is None or op is None:
            return None
        intr = max(0.0, spot - k) if l["option_type"] == "CALL" else max(0.0, k - spot)
        total += (intr - op) * c * 100 if l.get("direction") == "LONG" else (op - intr) * c * 100
    return round(total, 2)


def breakevens(legs):
    """Expiry break-even spot(s): where the house-convention expiry P&L crosses
    zero. Computed from legs alone (strikes + open prices), the same authority
    the buffer uses -- never the stored columns. Empty when any leg is
    unpriced (never invent -- HELM-095)."""
    strikes = [_f(l.get("strike")) for l in legs or []
               if l.get("option_type") not in (None, "STOCK") and _f(l.get("strike")) is not None]
    if not strikes or _pnl_at_expiry(legs, strikes[0]) is None:
        return []
    lo, hi = min(strikes) * 0.5, max(strikes) * 1.5
    n = 2000
    pts, prev_s, prev_p = [], None, None
    for i in range(n + 1):
        s = lo + (hi - lo) * i / n
        p = _pnl_at_expiry(legs, s)
        # strict sign change only: a flat-zero stretch (degenerate zero-credit
        # structure) is not a break-even line worth naming
        if prev_p is not None and p is not None and (prev_p < 0) != (p < 0):
            a, b = prev_s, s
            for _ in range(40):                      # bisect to the cent
                m = (a + b) / 2
                pm = _pnl_at_expiry(legs, m)
                if pm == 0:
                    a = b = m; break
                if (pm < 0) == (_pnl_at_expiry(legs, a) < 0):
                    a = m
                else:
                    b = m
            be = round((a + b) / 2, 2)
            if not pts or abs(pts[-1] - be) > 0.01:
                pts.append(be)
        prev_s, prev_p = s, p
    return pts


def expiry_ladder(pos, legs, spot, latest_mark, dte):
    strat = (pos.get("strategy") or "").upper()
    if strat not in ("IRON_CONDOR", "BEAR_CALL_SPREAD", "BULL_PUT_SPREAD",
                     "BEAR_PUT_SPREAD", "BULL_CALL_SPREAD"):
        return None, None
    strikes = sorted({_f(l.get("strike")) for l in legs
                      if l.get("option_type") not in (None, "STOCK") and _f(l.get("strike")) is not None})
    if not strikes or _f(spot) is None:
        return None, None
    lo, hi = strikes[0], strikes[-1]
    probes = [("below the wings", lo * 0.97), ("at the lower strike", lo),
              ("between", (lo + hi) / 2), ("at the upper strike", hi),
              ("above the wings", hi * 1.03)]
    rows = [{"where": w, "spot": round(s, 2), "pnl": _pnl_at_expiry(legs, s)} for w, s in probes]
    for _be in breakevens(legs):
        rows.append({"where": "break-even", "spot": _be, "pnl": 0.0})
    rows.sort(key=lambda r: r["spot"])
    here = _pnl_at_expiry(legs, spot)
    conv = None
    if here is not None and latest_mark is not None:
        gap = here - latest_mark
        direction = "working FOR" if gap > 0 else "working AGAINST"
        weeks = max(1.0, (dte or 7) / 7.0)
        conv = ("if nothing moves, expiry is worth %s vs today's mark %s — time is %s "
                "this position, ≈ %s/week" % (_money(here), _money(latest_mark),
                                              direction, _money(abs(gap) / weeks)))
    return rows, conv


# ── conventional contract line ───────────────────────────────────────────────
def _fmt_exp(iso):
    try:
        d = date.fromisoformat((iso or "")[:10])
        return "%s %d '%s" % (d.strftime("%b"), d.day, d.strftime("%y"))
    except (ValueError, TypeError):
        return iso or "?"


def contract_line(pos, legs):
    """The instrument in chain notation: signed qty, expiry, strike, type.
    Single leg:  -10 GM Aug 28 '26 $85 P
    One expiry:  LRCX Aug 21 '26: +20 $350 P / -20 $360 P / -20 $560 C / +20 $570 C
    Mixed expiries fall back to per-leg with its own date."""
    tk = pos.get("ticker") or "?"
    opts = [l for l in legs or [] if l.get("option_type") not in (None, "STOCK")]
    if not opts:
        return None
    def qty(l):
        n = int(_f(l.get("contracts")) or 1)
        return ("−%d" % n) if l.get("direction") == "SHORT" else ("+%d" % n)
    def ks(l):
        k = _f(l.get("strike"))
        return ("$%g" % k) if k is not None else "$?"
    def cp(l):
        return "P" if l.get("option_type") == "PUT" else "C"
    exps = {(l.get("expiration") or "")[:10] for l in opts}
    if len(opts) == 1:
        l = opts[0]
        return "%s %s %s %s %s" % (qty(l), tk, _fmt_exp(l.get("expiration")), ks(l), cp(l))
    legs_sorted = sorted(opts, key=lambda l: (_f(l.get("strike")) or 0))
    if len(exps) == 1:
        body = " / ".join("%s %s %s" % (qty(l), ks(l), cp(l)) for l in legs_sorted)
        return "%s %s: %s" % (tk, _fmt_exp(opts[0].get("expiration")), body)
    body = " / ".join("%s %s %s %s" % (qty(l), _fmt_exp(l.get("expiration")), ks(l), cp(l))
                      for l in legs_sorted)
    return "%s: %s" % (tk, body)


# ── deal sentence ────────────────────────────────────────────────────────────
def deal_sentence(pos, legs):
    tk = pos.get("ticker")
    strat = (pos.get("strategy") or "").upper()
    prem = _f(pos.get("net_premium"))
    shorts = [l for l in legs or [] if l.get("direction") == "SHORT" and l.get("option_type") not in (None, "STOCK")]
    longs = [l for l in legs or [] if l.get("direction") == "LONG" and l.get("option_type") not in (None, "STOCK")]
    exp = (shorts or longs or [{}])[0].get("expiration") or "expiry"
    if strat == "CSP" and shorts:
        return ("You sold someone the right to sell you %s at $%.0f until %s. "
                "They paid you %s for it." % (tk, _f(shorts[0].get("strike")) or 0, exp, _money(abs(prem or 0))))
    if strat == "COVERED_CALL" and shorts:
        return ("You own %s and sold someone the right to buy it from you at $%.0f "
                "until %s, for %s." % (tk, _f(shorts[0].get("strike")) or 0, exp, _money(abs(prem or 0))))
    _ks = sorted(k for k in (_f(s.get("strike")) for s in shorts) if k is not None)
    if strat == "IRON_CONDOR" and len(_ks) >= 2:
        ks = _ks
        return ("You were paid %s to bet that %s stays between $%.0f and $%.0f "
                "until %s, with wings capping the damage if it doesn't." %
                (_money(abs(prem or 0)), tk, ks[0], ks[-1], exp))
    if strat in ("BEAR_CALL_SPREAD", "BULL_PUT_SPREAD") and shorts:
        side = "below" if strat == "BEAR_CALL_SPREAD" else "above"
        return ("You were paid %s to bet %s stays %s $%.0f until %s, risk capped "
                "by the wing." % (_money(abs(prem or 0)), tk, side, _f(shorts[0].get("strike")) or 0, exp))
    if strat in _LONGS and longs:
        word = "rise" if strat == "LONG_CALL" else "fall"
        return ("You paid %s for the right to profit if %s can %s past $%.0f by %s." %
                (_money(abs(prem or 0)), tk, word, _f(longs[0].get("strike")) or 0, exp))
    if strat in _DIAG:
        return ("You own a long-dated option on %s and rent out short-dated ones "
                "against it while you wait." % tk)
    return "%s %s — %s of premium." % (tk, strat.replace("_", " ").title(), _money(prem or 0))


# ── the card ─────────────────────────────────────────────────────────────────
def evaluate(pos, legs, checks, entry_snap=None, entry_thesis_row=None,
             ownership=None, cur_sig=None):
    """Assemble the full card content for one position. Pure."""
    strat = (pos.get("strategy") or "").upper()
    closed = (pos.get("status") == "CLOSED")
    latest = (checks or [])[-1] if checks else None

    beliefs = []
    if strat in _CREDIT:
        beliefs.append(_strike_belief(pos, legs, checks, latest))
        beliefs.append(_premium_belief(pos, legs, entry_snap, cur_sig, latest))
        if strat in ("CSP", "COVERED_CALL"):
            beliefs.append(_own_belief(pos, ownership))
    elif strat in _LONGS or strat in _VERT_DEBIT:
        beliefs.append(_direction_belief(pos, entry_thesis_row, checks, cur_sig,
                                         want_up=strat in ("LONG_CALL", "BULL_CALL_SPREAD")))
        if strat in _LONGS:
            beliefs.append(_premium_belief(pos, legs, entry_snap, cur_sig, latest))
    elif strat in _DIAG:
        beliefs.append(_direction_belief(pos, entry_thesis_row, checks, cur_sig, want_up=True))
        beliefs.append(_term_belief(pos))
    else:
        beliefs.append(_belief("unmapped", "%s %s" % (pos.get("ticker"), strat),
                               "no belief mapping for this strategy yet",
                               "states unavailable", UNKNOWN,
                               "add the conjunction to helm/thesis.py when this books again"))

    n_bad = sum(1 for b in beliefs if b["state"] in (BROKEN, BROKEN_LOUD))
    n_warn = sum(1 for b in beliefs if b["state"] in (FRAYING, CONTESTED))
    n_unknown = sum(1 for b in beliefs if b["state"] in (UNKNOWN, PARTIAL))
    n_ok = len(beliefs) - n_bad - n_warn - n_unknown
    if n_bad:
        label = "✗ %d of %d broken" % (n_bad, len(beliefs))
    elif n_warn:
        label = "⚠ %d of %d warning" % (n_warn, len(beliefs))
    elif n_unknown == len(beliefs):
        label = "○ unknown"
    else:
        label = "✓ %d/%d" % (n_ok, len(beliefs) - n_unknown)

    mark = _f((latest or {}).get("pnl_unrealized"))
    dte = (latest or {}).get("dte_now")
    ladder, conv = (None, None)
    if not closed and latest:
        ladder, conv = expiry_ladder(pos, legs, latest.get("spot_price"), mark, _f(dte))

    # The Read — synthesis, ending with the action cue (the HELM-134 job, per position)
    bits = []
    for b in beliefs:
        if b["state"] in (BROKEN, BROKEN_LOUD):
            bits.append("the '%s' belief is %s" % (b["title"], b["word"]))
    for b in beliefs:
        if b["state"] in (FRAYING, CONTESTED):
            bits.append("'%s' is %s" % (b["title"], b["word"]))
    if closed:
        cue = "closed %s — %s, realized %s; this card is the post-mortem, frozen as it stood" % (
            (pos.get("closed_at") or "")[:10], pos.get("exit_reason") or "—",
            _money(_f(pos.get("realized_pnl"))))
    elif n_bad:
        cue = "this needs a decision today — a confirmed break is information being ignored, " \
              "and holding past it is what the book has paid for before (W15)"
    elif n_warn:
        cue = "worth watching, not acting — a warning is amber by measurement, not an alarm"
    else:
        cue = "no decision needed today — a losing mark with beliefs intact is noise you " \
              "are being paid to tolerate; sitting still is a decision"
    read = ((" ; ".join(bits) + ". ") if bits else "Every graded belief holds. ") + cue
    if conv:
        read += ". " + conv

    _exps = sorted({(l.get("expiration") or "")[:10] for l in legs or []
                    if l.get("option_type") not in (None, "STOCK") and l.get("expiration")})
    return {
        "position_id": pos.get("id"), "ticker": pos.get("ticker"),
        "contract": contract_line(pos, legs),
        "spot": _f((latest or {}).get("spot_price")),
        "expirations": _exps,
        "breakevens": breakevens(legs),
        "strategy": strat, "book": pos.get("book"), "closed": closed,
        "deal": deal_sentence(pos, legs),
        "beliefs": beliefs,
        "summary": {"ok": n_ok, "warn": n_warn, "bad": n_bad,
                    "unknown": n_unknown, "label": label},
        "mark": mark, "mark_asof": (latest or {}).get("checked_at"),
        "realized": _f(pos.get("realized_pnl")) if closed else None,
        "exit_reason": pos.get("exit_reason") if closed else None,
        "dte": dte, "read": read,
        "ladder": ladder, "convergence": conv,
        "condor_honesty": strat in ("IRON_CONDOR", "SHORT_STRANGLE", "JADE_LIZARD"),
    }
