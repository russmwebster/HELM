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
FLAT = "FLAT"
DRIFT_FOR, DRIFT_AGAINST, VINDICATED = "DRIFT_FOR", "DRIFT_AGAINST", "VINDICATED"
CONTESTED, PARTIAL, UNKNOWN = "CONTESTED", "PARTIAL", "UNKNOWN"

_ICON = {HOLDS: "✓", FRAYING: "⚠", BROKEN: "✗", BROKEN_LOUD: "✗",
         DRIFT_FOR: "→", DRIFT_AGAINST: "→", VINDICATED: "✓",
         CONTESTED: "⚠", PARTIAL: "◐", UNKNOWN: "○", FLAT: "→"}
_WORD = {HOLDS: "holds", FRAYING: "warning", BROKEN: "broken",
         BROKEN_LOUD: "broken — loud", DRIFT_FOR: "moving your way",
         DRIFT_AGAINST: "moving against you", VINDICATED: "paid off",
         CONTESTED: "entry measures disagreed", PARTIAL: "incomplete data",
         UNKNOWN: "no data — not guessed", FLAT: "little changed"}

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


def _amt(x):
    """An unsigned dollar amount — a cost, a premium, an axis tick.

    _money() is for signed P&L and always prefixes + or −, which reads wrong on
    "a $7,030 debit" or on a y-axis. Negatives still show their sign, because a
    negative amount is a real thing and must not be hidden by abs()."""
    if x is None:
        return "—"
    return ("−$%s" % format(-round(x), ",")) if x < 0 else ("$%s" % format(round(x), ","))


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
                       UNKNOWN, "missing data is shown as missing, never estimated")
    d_last, (b, k, t_) = series[-1]
    side_safe = "above" if t_ == "PUT" else "below"
    side_bad = "below" if t_ == "PUT" else "above"
    # HELM-146: present tense may only assert the LATEST journaled reading.
    # Worst-of-day still governs the STATE (slice-1 discipline unchanged), but
    # "X is below the strike" built from it was false whenever the day
    # recovered intraday -- the HELM-144 lesson: when a rendered sentence
    # makes a factual claim, the fact must be an input to it.
    _lb = buffer_pct(legs, (latest_check or {}).get("spot_price"))
    cb, _ck, _ctp = _lb if _lb is not None else (b, k, t_)
    _ts = (((latest_check or {}).get("checked_at") or d_last)[:16]).replace("T", " ")
    _cside = ("above" if _ctp == "PUT" else "below") if cb >= 0 else ("below" if _ctp == "PUT" else "above")
    now = "%s is %.1f%% %s the $%.0f strike at the latest check (%s)" % (
        pos.get("ticker"), abs(cb), _cside, _ck, _ts)
    if cb >= 0 and b < 0:
        now += " — but the worst reading of that day dipped %.1f%% %s" % (abs(b), side_bad)
    healed = (state == HOLDS and worst is not None and worst < 1.0)
    if healed:
        fray_day = next(d for d, (bb, _kk, _tt) in series if bb < 1.0)
        now += " · dipped to %.1f%% on %s, since recovered" % (max(worst, -99.9), fray_day)
    recovered_latest = False
    if state in (BROKEN, BROKEN_LOUD):
        now += " · past the strike at the day's worst reading on %d consecutive days" % streak
        if cb >= 0:
            recovered_latest = True
            now += " — the break state clears only when a full check day stays clear of the strike"
    fine = ("distance from spot to the nearest short strike, recomputed from legs and "
            "journaled spot; the sentence reads the latest check, the state is judged on "
            "the worst reading of each day. Bands, measured on 194 closed "
            "positions: 1%+ = normal · under 1% = warning · past the strike on "
            "2+ consecutive check days = broken")
    if two_walls:
        fine += (". For condors these states describe where the position stands, not "
                 "its odds — the backtest found no predictive separation for condors")
    else:
        fine += (" — for CSPs and credit verticals a confirmed cross precedes losses "
                 "at 1.7–3.2× the base rate")
    extra = {}
    if recovered_latest:
        extra["recovered_latest"] = True
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
        extra["odds"] = "odds unavailable — a two-wall position needs both short deltas, which this card is not given"
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
        then_bits.append("entry realized vol was not captured at open — rich-vs-realized not gradable")
    then = " · ".join(then_bits) or "entry measures not captured"
    if e_ivr is None and e_iv is None:
        return _belief("premium", title, then,
                       "cannot grade — entry volatility was not captured when this was opened",
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
        # The attribution is true and worth keeping: for a short option falling IV
        # is a tailwind, for a long one a headwind, all else equal. What was wrong
        # was the gloss that used to hang off it -- "costs less to buy back" was
        # decided by the SIGN of this move alone and never consulted the price, so
        # it read backwards on 23 of the 34 open credit positions when measured on
        # 2026-08-02. The price claim now lives below, derived from the price.
        forces.append("IV %.1f → %.1f — %s you"
                      % (e_iv, c_iv, "for" if (ive < 0) == credit else "against"))
    elif ive is not None:
        forces.append("IV %+.1f vs entry — %s you"
                      % (ive, "for" if (ive < 0) == credit else "against"))
    _val = position_value(pos.get("net_premium"), (latest_check or {}).get("pnl_unrealized"))
    _paid = _f(pos.get("net_premium"))
    if _val is not None and _paid:
        _paid = abs(_paid)
        if credit:
            forces.append("costs %s to buy back what you sold for %s — %s than you were paid"
                          % (_money(_val), _money(_paid), "less" if _val < _paid else "more"))
        else:
            forces.append("sells for %s against the %s you paid — %s than you paid"
                          % (_money(_val), _money(_paid), "more" if _val > _paid else "less"))
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
        # magnitude matters: a 1-point IV twitch on an 84-vol name is noise.
        # paid off / against needs a move of 2+ points; inside that, FLAT.
        favorable = (ive < 0) if credit else (ive > 0)
        if abs(ive) < 2:
            state = FLAT
        else:
            state = VINDICATED if favorable else DRIFT_AGAINST
        # s111 (W162): at high delta this position barely has vega, so an
        # IV-move grade overstates a force that hardly prices in. State is
        # damped to FLAT and the ledger says why; the forces stay listed.
        if strat in _LONGS and state in (VINDICATED, DRIFT_AGAINST):
            _dnow = _f((latest_check or {}).get("delta"))
            if _dnow is not None and abs(_dnow) >= 0.85:
                forces.append("vega is small at %.2f delta -- IV moves barely "
                              "reprice this position" % abs(_dnow))
                state = FLAT
    fine = ("entry pricing affects P&L only — it can never trigger an exit. Scored "
            "for/against, not pass/fail: the price you were paid was fixed the day you traded")
    return _belief("premium", title, then, now, state, fine, {"forces": forces})


# ---- HELM-151 (W95): one source of truth for "what acts" ---------------------
# Three sentences on this card used to restate the acting doctrine by hand: the
# belief fine print, the broken-state line, and the footer. v3 changed the
# doctrine and rewrote only the panel, so the card told the trader that a bias
# flip could close the position -- which it cannot, and has not since HELM-150.
# Everything below derives from helm.long_exit.ACTING_VERDICTS.

_RULE_LABEL = {
    "STOP_LOSS": "the stop",
    "GIVE_BACK": "giving back the gain",
    "DTE_7": "the hard close",
    "DTE_21": "the calendar",
}


def acting_rules():
    """[(verdict, label)] for the rules that can actually close a long.

    Raises if the engine gains an acting verdict this card has no label for --
    a v4 rule must not render as silence on the surface that explains the book.
    """
    from helm import long_exit as _le
    names = tuple(getattr(_le, "ACTING_VERDICTS", ()) or ())
    missing = [n for n in names if n not in _RULE_LABEL]
    if missing:
        raise KeyError("thesis card has no label for acting verdict(s): %s"
                       % ", ".join(missing))
    return [(n, _RULE_LABEL[n]) for n in names]


def direction_acts():
    """Whether the direction belief is itself a gate. False since v3."""
    from helm import long_exit as _le
    return bool(getattr(_le, "DIRECTION_ACTS", False))


def _direction_verdict_phrase():
    """Tail of the broken-state line. Never says 'acting' unless it is."""
    if direction_acts():
        return "this is the acting exit"
    return ("recorded, but it closes nothing — kept as a counterfactual so the "
            "rule can be graded later")


def _direction_fine():
    """Fine print under the direction belief."""
    if direction_acts():
        return ("this belief ACTS (THESIS_BREAK) for longs — the one belief that "
                "is already a gate")
    labels = [lbl for _n, lbl in acting_rules()]
    which = (", ".join(labels[:-1]) + " and " + labels[-1]) if len(labels) > 1 \
        else (labels[0] if labels else "nothing")
    return ("this belief closes nothing — it is read, graded and journalled as "
            "information. What can close a long: %s" % which)


def doctrine_note(strategy=None, closed=False):
    """The card footer's leading sentence, derived rather than restated."""
    lead = "Doctrine: facts gate, judgments display"
    if closed:
        return ("%s — this is a frozen post-mortem; nothing on it acts, and it "
                "is not re-read against today's market." % lead)
    strat = (strategy or "").upper()
    if strat in _LONGS:
        labels = [lbl for _n, lbl in acting_rules()]
        which = (", ".join(labels[:-1]) + " and " + labels[-1]) if len(labels) > 1 \
            else (labels[0] if labels else "nothing")
        body = ("%s — what can close this position: %s. Nothing else on this "
                "card acts" % (lead, which))
    else:
        body = "%s — nothing on this card acts" % lead
    if strat in _LONGS or strat in _VERT_DEBIT:
        if direction_acts():
            body += ", except the direction belief, which is also a gate"
        else:
            body += ", the direction read included"
    return body + "."


def _direction_belief(pos, entry_thesis_row, checks, cur_sig, want_up=True):
    """P1/P2 for longs and debit verticals — the acting THESIS_BREAK machinery,
    rendered. Reads what HELM-112 already journals; computes nothing new."""
    word = "keeps going up" if want_up else "keeps going down"
    title = "%s %s" % (pos.get("ticker"), word)
    if not entry_thesis_row:
        return _belief("direction", title, "position predates thesis capture",
                       "never armed — no entry thesis on record",
                       UNKNOWN, "blank history means the position predates capture; nothing is invented")
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
        now += (" · broken %d consecutive checks (confirm %d) — %s"
                % (streak, confirm, _direction_verdict_phrase()))
    elif broken_today:
        state = FRAYING
        now += " · broken today, streak %d of %d — one more confirms" % (streak, confirm)
    else:
        state = HOLDS
        now += " · intact on the latest check"
    fine = ("entry bias vs current bias, from the same context the exit agent "
            "compares; 2-day confirmation; " + _direction_fine())
    return _belief("direction", title, then, now, state, fine)


def _own_belief(pos, ownership):
    title = "if I end up owning %s, I want to" % pos.get("ticker")
    if not ownership:
        return _belief("own", title, "asserted at entry",
                       "no ownership grade on file — coverage is partial",
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


_LONG_ENTRY_BAND = {
    # s113 (W164) -- the LC/LP entry gate's delta band, from
    # helm/cli/open_cmd.py STRATEGY_CONFIG (delta_min/delta_max). Duplicated
    # here for display, same as _recover_belief's "1.0x the expected move" --
    # thesis.py stays DB/CLI-free by design (see module docstring).
    "LONG_CALL": (0.65, 0.85),
    "LONG_PUT": (0.30, 0.70),
}


def _char_belief(pos, legs, checks, entry_snap):
    """s111 (W162; grading fixed s113 W164) -- position character for the
    long families: the delta you bought vs the delta you hold. Stock-
    replacement is the point of an ITM long-dated call; this grades whether
    the position still IS one. Reads legs.entry_delta / entry_snapshots.delta
    and the journaled check delta; computes nothing new. Display only -- it
    closes nothing.

    W164 (s112): grading the ABSOLUTE current delta let an entry that was
    NEVER stock-replacement (delta below the strategy's entry band) render as
    "eroded" -- false history. Grade the CHANGE from entry delta instead, and
    when entry itself never met the band, say that plainly rather than
    describing a change that never happened."""
    title = "this is stock-like exposure, not a lottery ticket"
    strat = (pos.get("strategy") or "").upper()
    e_d = None
    for l in legs or []:
        if l.get("direction") == "LONG" and l.get("option_type") not in (None, "STOCK"):
            e_d = _f(l.get("entry_delta"))
            break
    if e_d is None:
        e_d = _f((entry_snap or {}).get("delta"))
    c_d = None
    for r in reversed(checks or []):
        c_d = _f(r.get("delta"))
        if c_d is not None:
            break
    then = ("bought %.2f of stock-like exposure" % abs(e_d)) if e_d is not None \
        else "entry delta not captured"
    if c_d is None:
        return _belief("character", title, then,
                       "no journaled delta yet -- ungraded, not guessed", UNKNOWN,
                       "delta is journaled 3x/day; none on record for this position")
    a = abs(c_d)
    e_a = abs(e_d) if e_d is not None else None
    band = _LONG_ENTRY_BAND.get(strat)
    out_of_band_entry = (band is not None and e_a is not None and
                          not (band[0] <= e_a <= band[1]))

    if out_of_band_entry:
        then = ("bought %.2f -- entered OUTSIDE the %.2f-%.2f stock-"
                 "replacement band" % (e_a, band[0], band[1]))
        now = "holding %.2f" % a
        if a >= 0.90:
            state = HOLDS
            now += " -- mostly stock now despite the out-of-band entry"
        elif a >= band[0]:
            state = FRAYING
            now += (" -- inside the %.2f-%.2f band today, but it didn't enter "
                     "there; call it borrowed character, not held" % band)
        else:
            state = BROKEN
            now += (" -- entered outside the stock-replacement band; this was "
                     "never stock replacement, not something that eroded")
        fine = ("entry delta itself never met the %.2f-%.2f stock-replacement "
                 "band (a pinned entry the screen would have refused -- W97/"
                 "W132) -- so a low current delta reads as an out-of-band "
                 "entry, never as erosion; display only -- it closes nothing"
                 % band)
        return _belief("character", title, then, now, state, fine,
                       {"entry_delta": e_d, "cur_delta": c_d})

    now = "holding %.2f" % a
    if a >= 0.90:
        state = HOLDS
        now += " -- mostly stock now: little convexity left, and the option is doing little the shares would not"
    elif e_a is None:
        if a >= 0.60:
            state = HOLDS
            now += " -- still the position you chose"
        elif a >= 0.50:
            state = FRAYING
            now += " -- halfway to a coin-flip; entry delta wasn't captured, so this reads on level alone"
        else:
            state = BROKEN
            now += " -- deep into coin-flip territory; entry delta wasn't captured, so this reads on level alone"
    else:
        change = round(a - e_a, 2)
        if change <= -0.20:
            state = BROKEN
            now += (" -- down %.2f from the %.2f bought at entry: no longer "
                     "stock replacement" % (-change, e_a))
        elif change <= -0.10:
            state = FRAYING
            now += (" -- down %.2f from the %.2f bought at entry: the "
                     "character you bought is eroding" % (-change, e_a))
        elif change >= 0:
            state = HOLDS
            now += " -- up %.2f from the %.2f bought at entry" % (change, e_a)
        else:
            state = HOLDS
            now += (" -- still close to the %.2f bought at entry (down %.2f)"
                     % (e_a, -change))

    if band is not None:
        fine = ("current delta from the latest journaled check vs the CHANGE "
                 "from the delta bought at entry (bands: -0.10 fraying, -0.20 "
                 "broken); entry sat inside the %.2f-%.2f stock-replacement "
                 "band, so a falling delta here is erosion, not a bad entry; "
                 "display only -- it closes nothing" % band)
    else:
        fine = ("current delta from the latest journaled check vs the CHANGE "
                 "from the delta bought at entry (bands: -0.10 fraying, -0.20 "
                 "broken); display only -- it closes nothing")
    return _belief("character", title, then, now, state, fine,
                   {"entry_delta": e_d, "cur_delta": c_d})


def _recover_belief(pos, legs, latest_check):
    """s111 (W162) -- the entry gate (break-even within 1.0x the expected
    move), recomputed live over the REMAINING clock: distance to break-even
    in units of the one-sigma expected move at the latest check's IV. The
    number that separates recoverable-underwater from dead-money-underwater.
    Display only -- it closes nothing."""
    import math as _m
    strat = (pos.get("strategy") or "").upper()
    title = "the clock left can still carry this past break-even"
    bes = breakevens(legs)
    be = bes[0] if bes else None
    spot = _f((latest_check or {}).get("spot_price"))
    iv = _f((latest_check or {}).get("iv_current"))
    dte = _f((latest_check or {}).get("dte_now"))
    then = "entry gate: break-even within 1.0x the expected move"
    if be is None:
        return _belief("recover", title, then,
                       "cannot grade -- no break-even derivable from the legs "
                       "(W132: the pin path writes none)", UNKNOWN,
                       "shown as ungraded rather than estimated")
    if spot is None or not iv or dte is None:
        return _belief("recover", title, then,
                       "cannot grade -- the latest check is missing spot, IV or DTE",
                       UNKNOWN, "shown as ungraded rather than estimated")
    sigma = spot * (iv / 100.0) * _m.sqrt(max(dte, 0.0) / 365.0)
    if not sigma:
        return _belief("recover", title, then,
                       "cannot grade -- no time or volatility left to measure against",
                       UNKNOWN, "shown as ungraded rather than estimated")
    need = (spot - be) if strat == "LONG_PUT" else (be - spot)
    r = need / sigma
    pct = 100.0 * need / spot
    fine = ("one-sigma expected move at the latest check's IV over the remaining "
            "DTE -- the same test the entry gate applies, recomputed live; "
            "display only -- it closes nothing")
    if r <= 0:
        return _belief("recover", title, then,
                       "past break-even -- spot is %.1f%% beyond $%.2f" % (abs(pct), be),
                       HOLDS, fine, {"sigma_mult": round(r, 2)})
    body = ("break-even $%.2f is %.1f%% away -- %.2fx the expected move over the "
            "%d days left" % (be, pct, r, int(dte)))
    if r < 0.75:
        state, tail = HOLDS, "; time is real here"
    elif r <= 1.25:
        state, tail = FRAYING, "; recovery needs an above-average run"
    else:
        state, tail = BROKEN, "; 'time to recover' is a story at this distance"
    return _belief("recover", title, then, body + tail, state, fine,
                   {"sigma_mult": round(r, 2)})


def exit_track_long(pos, checks, closed):
    """s111 (W162) -- exit_track's twin for the long families: what the market
    has offered since the DIRECTION break was confirmed (the strike version
    cannot see a long). Same dict shape as exit_track, so the renderer needs
    nothing new. Built only from journaled GOOD checks; returns None rather
    than guessing."""
    if closed or not checks:
        return None
    seen = {}
    for r in checks:
        js = r.get("lc_arms_json")
        d = (r.get("checked_at") or "")[:10]
        if not js or not d:
            continue
        try:
            th = (json.loads(js) or {}).get("thesis") or {}
        except (ValueError, TypeError):
            continue
        if "broken_today" in th:
            seen[d] = bool(th.get("broken_today"))
    if not seen:
        return None
    days = sorted(seen)
    run = []
    for d in reversed(days):
        if seen[d]:
            run.append(d)
        else:
            break
    try:
        confirm_n = int(getattr(_le, "CONFIRM_DAYS", 2) or 2)
    except Exception:
        confirm_n = 2
    if len(run) < confirm_n:
        return None
    run.reverse()
    confirm = run[1] if len(run) >= 2 else run[0]
    byday = {}
    for r in checks:
        d = (r.get("checked_at") or "")[:10]
        p = _f(r.get("pnl_unrealized"))
        if not d or p is None or d < confirm:
            continue
        if d not in byday or p > byday[d]:
            byday[d] = p
    if not byday:
        return None
    ds = sorted(byday)
    best_date = min(ds, key=lambda d: (-byday[d], d))
    return {
        "confirm_date": confirm,
        "best": byday[best_date], "best_date": best_date,
        "today": byday[ds[-1]], "today_date": ds[-1],
        "prev": byday[ds[-2]] if len(ds) >= 2 else None,
        "prev_date": ds[-2] if len(ds) >= 2 else None,
        "n_days": len(ds),
        "kind": "direction",
    }


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
        verb = "adding" if gap > 0 else "costing"
        weeks = max(1.0, (dte or 7) / 7.0)
        conv = ("if nothing moves, expiry is worth %s vs today's mark %s — time is %s "
                "this position, %s ≈ %s/week" % (_money(here), _money(latest_mark),
                                                 direction, verb, _amt(abs(gap) / weeks)))
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
        _bes = breakevens(legs)
        if _bes:
            # HELM-146: profit starts at the break-even, not the strike -- the
            # facts row shows the BE and this sentence must not contradict it.
            return ("You paid %s for the right to profit if %s can %s past $%.2f — your "
                    "break-even %s the $%.0f strike — by %s." %
                    (_money(abs(prem or 0)), tk, word, _bes[0],
                     "over" if word == "rise" else "under",
                     _f(longs[0].get("strike")) or 0, exp))
        return ("You paid %s for the right to profit if %s can %s past $%.0f by %s." %
                (_money(abs(prem or 0)), tk, word, _f(longs[0].get("strike")) or 0, exp))
    if strat in _DIAG:
        return ("You own a long-dated option on %s and rent out short-dated ones "
                "against it while you wait." % tk)
    return "%s %s — %s of premium." % (tk, strat.replace("_", " ").title(), _money(prem or 0))


# ── the card ─────────────────────────────────────────────────────────────────

def exit_track(legs, checks, closed):
    """s95 addendum -- exit tracking since a confirmed break. Display only.

    For an OPEN position whose strike belief is broken (slice-1 confirmed),
    report what the market has actually offered for the exit since the break
    was confirmed: best journaled mark since (with date), the prior check
    day's best, and the latest day's best. Built only from journaled GOOD
    checks -- if no marks were journaled, returns None rather than guessing
    (HELM-095). This can never trigger an exit; it prices what waiting has
    cost."""
    if closed or not checks:
        return None
    series = day_series(legs, checks)
    state, streak, _w = _strike_state(series)
    if state not in (BROKEN, BROKEN_LOUD):
        return None
    run = [d for d, _b in series[-streak:]] if streak else []
    if not run:
        return None
    confirm = run[1] if len(run) >= 2 else run[0]
    byday = {}
    for r in checks:
        d = (r.get("checked_at") or "")[:10]
        p = _f(r.get("pnl_unrealized"))
        if not d or p is None or d < confirm:
            continue
        if d not in byday or p > byday[d]:
            byday[d] = p
    if not byday:
        return None
    days = sorted(byday)
    best_date = min(days, key=lambda d: (-byday[d], d))
    return {
        "confirm_date": confirm,
        "best": byday[best_date], "best_date": best_date,
        "today": byday[days[-1]], "today_date": days[-1],
        "prev": byday[days[-2]] if len(days) >= 2 else None,
        "prev_date": days[-2] if len(days) >= 2 else None,
        "n_days": len(days),
    }


def _day_marks(checks):
    """Journaled marks bucketed by check day, each day ordered by time.

    One place decides what a check day is, so every reader of the journal agrees
    on it. Rows without a timestamp or without a mark are skipped rather than
    defaulted — an absent mark is not a zero."""
    byday = {}
    for r in checks or []:
        ts = r.get("checked_at") or ""
        d, p = ts[:10], _f(r.get("pnl_unrealized"))
        if not d or p is None:
            continue
        byday.setdefault(d, []).append((ts, p))
    for d in byday:
        byday[d].sort()
    return byday


def position_value(net_premium, mark):
    """What the position is worth to close right now, from the journal alone.

    For a credit structure you were paid |premium| and you buy it back, so the
    value is |premium| − mark. For a debit structure you paid |premium| and you
    sell it back, so it is |premium| + mark. Both are the same thing — the market
    value of the structure — and both are identities rather than estimates: no
    greeks, no per-leg arithmetic, and correct on multi-leg structures, where a
    quoted leg price times contracts is NOT the position's price (LRCX's condor
    reads $133,240 that way against a true $17,020). None when either input is
    missing."""
    prem, mk = _f(net_premium), _f(mark)
    if prem is None or prem == 0 or mk is None:
        return None
    paid = abs(prem)
    return (paid - mk) if prem > 0 else (paid + mk)


def close_series(pos, checks, closed=False, today=None):
    """What closing the position would have cost — or paid — on every check day.

    ONE POINT PER CHECK DAY, the LAST check of that day. Deliberately not the
    cheapest: a minimum over however many times a check happened to run moves
    with the sampling rather than with the market, and the count is not constant
    (measured over ~2,045 position-days: three checks on 1,333, two on 401, one
    on 287). The last check is defined the same way on every day regardless.

    The last point is flagged provisional when it is TODAY'S and that day has not
    yet had its full three checks — i.e. more are still to come and it will move.
    `today` is passed in rather than read from the clock, so this stays pure and
    a card rendered from a fixture says the same thing every time. Prior days
    carry the time of their last check, since 24% of them end before the closing
    slot and a midday reading must not pass itself off as a close.

    Journaled marks only; None when there are none — never invented. Display
    only: nothing here can trigger an exit."""
    prem = _f(pos.get("net_premium"))
    if prem is None or prem == 0:
        return None
    byday = _day_marks(checks)
    if not byday:
        return None
    paid = abs(prem)
    pts = []
    for d in sorted(byday):
        rows = byday[d]
        vals = [v for v in (position_value(prem, p) for _ts, p in rows) if v is not None]
        if not vals:
            continue
        ts, mk = rows[-1]
        pts.append({"date": d, "time": ts[11:16], "value": position_value(prem, mk),
                    "mark": mk, "lo": min(vals), "hi": max(vals), "n": len(vals)})
    if not pts:
        return None
    tday = "" if closed else (today or "")[:10]
    if tday and pts[-1]["date"] == tday and pts[-1]["n"] < 3:
        pts[-1]["provisional"] = True
    last = pts[-1]
    trend = None
    if len(pts) >= 2:
        diffs = [pts[i + 1]["value"] - pts[i]["value"] for i in range(len(pts) - 1)]
        d1 = diffs[-1]
        sgn = (d1 > 0) - (d1 < 0)
        streak = 0
        for d in reversed(diffs):
            if sgn != 0 and ((d > 0) - (d < 0)) == sgn:
                streak += 1
            else:
                break
        prev = pts[-2]
        # HELM-147: the noise guard. A day-over-day move only counts as a
        # direction when it exceeds the overlap of the two days' own intraday
        # ranges -- overlapping mid-quote ranges whipsaw and must not read as
        # a trend (LRCX's condor swings thousands intraday on wide quotes).
        _ovl = min(prev["hi"], last["hi"]) - max(prev["lo"], last["lo"])
        trend = {"d1": d1, "streak": streak,
                 "from": pts[-1 - streak]["value"] if streak else prev["value"],
                 "better": (d1 < 0) if prem > 0 else (d1 > 0),
                 "clear": abs(d1) > max(0.0, _ovl)}
    return {
        "credit": prem > 0, "premium": paid, "points": pts,
        "now": last["value"], "now_date": last["date"], "now_time": last["time"],
        "net": last["mark"],
        "peak": max(p["value"] for p in pts),
        "trough": min(p["value"] for p in pts),
        "n_days": len(pts), "provisional": bool(last.get("provisional")),
        "trend": trend,
    }


def trend_sentence(track, closed=False):
    """Is the exit getting better or worse? Day-over-day on the same
    last-check-per-day series the chart draws (HELM-147). The noise guard
    keeps mid-quote whipsaw from reading as a direction; past tense on
    closed cards. Display only, like everything on the card."""
    t = (track or {}).get("trend")
    if not t:
        return None
    d1, streak, base = t["d1"], t["streak"], t["from"]
    if d1 == 0:
        return "unchanged from the prior check day"
    if not t["clear"]:
        return ("moved %s vs the prior check day — within the days' own quote spread, "
                "not a clear move" % _amt(abs(d1)))
    if track["credit"]:
        word = "cheaper" if d1 < 0 else "dearer"
        head = ("was getting %s into the close" % word) if closed else ("getting %s" % word)
    else:
        word = "fetching more" if d1 > 0 else "fetching less"
        head = ("the sale was %s into the close" % word) if closed else ("the sale is %s" % word)
    s = "%s — %s %s than the prior check day" % (head, _amt(abs(d1)),
                                                 "less" if d1 < 0 else "more")
    if streak >= 2:
        s += ", %s %d check days running (from %s)" % (
            "improving" if t["better"] else "worsening", streak, _amt(base))
    if track.get("provisional") and not closed:
        s += " · today still has checks to come"
    return s


def close_headline(track, closed=False):
    """One plain sentence for the track.

    Closing a credit structure is ALWAYS a debit — you sold it, you buy it back.
    What varies is whether that debit is smaller or larger than the credit taken
    in. A debit structure is the mirror: closing it always pays a credit."""
    if not track:
        return None
    now, paid, net = track["now"], track["premium"], track["net"]
    # HELM-146: the NET number leads -- a trader's "cost to close" is
    # new-money-out (or kept), not the gross debit, which follows as the
    # mechanism. Past tense on closed cards: a frozen post-mortem must not
    # say "today".
    if track["credit"]:
        if net > 0:
            return (("closing at the last check would have kept %s of the %s credit — a %s buy-back debit"
                     if closed else
                     "closing today keeps %s of the %s credit — the buy-back debit is %s")
                    % (_amt(net), _amt(paid), _amt(now)))
        return (("closing at the last check would have taken %s of new money — a %s debit against the %s credit you banked"
                 if closed else
                 "closing today takes %s of new money — a %s debit against the %s credit you banked")
                % (_amt(-net), _amt(now), _amt(paid)))
    if net > 0:
        return (("closing at the last check would have sold it back for a %s credit — %s ahead of the %s paid"
                 if closed else
                 "closing today sells it back for a %s credit — %s ahead of the %s you paid")
                % (_amt(now), _amt(net), _amt(paid)))
    return (("closing at the last check would have sold it back for a %s credit — %s of the %s paid was gone"
             if closed else
             "closing today sells it back for a %s credit — %s of the %s you paid is gone")
            % (_amt(now), _amt(-net), _amt(paid)))


def _nice_step(span):
    """A round y-axis step giving at most ~4 gridlines."""
    import math
    if span <= 0:
        return 1.0
    base = 10 ** math.floor(math.log10(span / 4.0))
    for k in (1, 2, 2.5, 5, 10):
        if span / (base * k) <= 4.5:
            return base * k
    return base * 10


def close_svg(track, width=760, height=230):
    """Inline SVG for the cost-to-close track. No chart library, no external
    asset, no script — it renders from the markup alone.

    Colour carries exactly one meaning: above the rule you would pay more than
    you were paid, below it less. The line itself is ink so it never competes
    with that. The blue/red pair is the one that clears colour-blind separation
    in both light and dark (red/green does not, by a wide margin), and the
    arrows, labels and figures beside the chart repeat the same information
    without relying on colour at all."""
    if not track or not track.get("points"):
        return None
    pts, paid = track["points"], track["premium"]
    n = len(pts)
    ml, mr, mt, mb = 66, 84, 20, 28
    pw, ph = width - ml - mr, height - mt - mb
    lo = min([p["lo"] for p in pts] + [paid])
    hi = max([p["hi"] for p in pts] + [paid])
    pad = (hi - lo) * 0.12 or max(abs(hi) * 0.1, 50.0)
    y0, y1 = lo - pad, hi + pad

    def X(i):
        return ml + (pw / 2.0 if n == 1 else pw * i / (n - 1.0))

    def Y(v):
        return mt + ph - ((v - y0) / (y1 - y0)) * ph if y1 > y0 else mt + ph / 2.0

    e = []
    step = _nice_step(y1 - y0)
    t, guard_n = (int(y0 / step) + (1 if y0 > 0 else 0)) * step, 0
    while t <= y1 and guard_n < 8:
        e.append('<line x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f" stroke="var(--viz-grid,#e1e0d9)" stroke-width="1"/>'
                 % (ml, ml + pw, Y(t), Y(t)))
        e.append('<text x="%.1f" y="%.1f" text-anchor="end" font-size="10.5" fill="var(--viz-muted,#898781)">%s</text>'
                 % (ml - 9, Y(t) + 3.5, _amt(t)))
        t += step
        guard_n += 1

    line = " ".join("%s%.1f %.1f" % ("L" if i else "M", X(i), Y(p["value"]))
                    for i, p in enumerate(pts))
    yp = Y(paid)
    area = "%s L%.1f %.1f L%.1f %.1f Z" % (line, X(n - 1), yp, X(0), yp)
    uid = str(abs(hash((pts[0]["date"], n, int(paid)))) % 100000)
    worse = "var(--viz-bad,#e34948)" if track["credit"] else "var(--viz-good,#2a78d6)"
    better = "var(--viz-good,#2a78d6)" if track["credit"] else "var(--viz-bad,#e34948)"
    e.append('<clipPath id="ca%s"><rect x="%.1f" y="%.1f" width="%.1f" height="%.1f"/></clipPath>'
             % (uid, ml, mt - 4, pw, max(0.0, yp - mt + 4)))
    e.append('<clipPath id="cb%s"><rect x="%.1f" y="%.1f" width="%.1f" height="%.1f"/></clipPath>'
             % (uid, ml, yp, pw, max(0.0, mt + ph - yp + 4)))
    e.append('<path d="%s" fill="%s" fill-opacity="0.15" clip-path="url(#ca%s)"/>' % (area, worse, uid))
    e.append('<path d="%s" fill="%s" fill-opacity="0.15" clip-path="url(#cb%s)"/>' % (area, better, uid))

    for i, p in enumerate(pts):
        if p["hi"] > p["lo"]:
            e.append('<line x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f" stroke="var(--viz-muted,#898781)" stroke-width="1" stroke-opacity="0.5"/>'
                     % (X(i), X(i), Y(p["lo"]), Y(p["hi"])))

    e.append('<line x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f" stroke="var(--viz-ink2,#52514e)" stroke-width="1.5" stroke-dasharray="5 4"/>'
             % (ml, ml + pw, yp, yp))
    e.append('<text x="%.1f" y="%.1f" font-size="10.5" fill="var(--viz-ink2,#52514e)">%s %s</text>'
             % (ml + pw + 8, yp + 3.5, "took in" if track["credit"] else "paid", _amt(paid)))
    e.append('<path d="%s" fill="none" stroke="var(--viz-ink,#0b0b0b)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>' % line)
    for i, p in enumerate(pts[:-1]):
        e.append('<circle cx="%.1f" cy="%.1f" r="2.4" fill="var(--viz-ink,#0b0b0b)"/>' % (X(i), Y(p["value"])))

    last = pts[-1]
    fill = "var(--viz-surface,#fcfcfb)" if last.get("provisional") else "var(--viz-ink,#0b0b0b)"
    ring = "var(--viz-ink,#0b0b0b)" if last.get("provisional") else "var(--viz-surface,#fcfcfb)"
    e.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="%s" stroke="%s" stroke-width="2"/>'
             % (X(n - 1), Y(last["value"]), fill, ring))
    e.append('<text x="%.1f" y="%.1f" font-size="11" font-weight="600" fill="var(--viz-ink,#0b0b0b)">%s</text>'
             % (X(n - 1) + 9, Y(last["value"]) + 4, _amt(last["value"])))
    tr = track.get("trend")
    if tr and tr.get("clear") and tr.get("d1"):
        # HELM-147: the day-over-day delta at the last point, coloured by
        # better/worse -- the same side-aware flip the area fill uses.
        _tc = "var(--viz-good,#2a78d6)" if tr["better"] else "var(--viz-bad,#e34948)"
        e.append('<text x="%.1f" y="%.1f" font-size="10.5" font-weight="600" fill="%s">%s %s</text>'
                 % (X(n - 1) + 9, Y(last["value"]) + 17, _tc,
                    "▼" if tr["d1"] < 0 else "▲", _amt(abs(tr["d1"]))))

    every = max(1, -(-n // 6))
    gap = -(-every * 7 // 10)
    for i, p in enumerate(pts):
        if i == n - 1 or (i % every == 0 and i <= n - 1 - gap):
            e.append('<text x="%.1f" y="%.1f" text-anchor="middle" font-size="10.5" fill="var(--viz-muted,#898781)">%s</text>'
                     % (X(i), mt + ph + 16, p["date"][5:]))
    e.append('<line x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f" stroke="var(--viz-axis,#c3c2b7)" stroke-width="1"/>'
             % (ml, ml + pw, mt + ph, mt + ph))
    return ('<svg viewBox="0 0 %d %d" role="img" aria-label="What it would cost to close, on each check day" '
            'style="display:block;width:100%%;height:auto;overflow:visible">%s</svg>'
            % (width, height, "".join(e)))


def exit_rules(pos, checks, latest, entry_thesis_row):
    """The long-family exit rules (v3), in precedence order, with what each one
    would do right now (HELM-148, rewritten for v3 in HELM-150).

    Constants and the trail come from the ENGINE (helm.long_exit), never
    re-declared here: a panel that drifts from the rule it describes is worse
    than no panel. Percentages are computed from journaled marks over the
    premium paid -- the same identity the engine uses -- rather than read from a
    stored percentage column. Display only.

    The direction read is deliberately NOT a row here. It stopped acting in v3;
    it lives on the card as a belief, and the fine print says it closes nothing.
    """
    from helm import long_exit as _le
    strat = (pos.get("strategy") or "").upper()
    if strat not in _LONGS or pos.get("status") == "CLOSED":
        return None
    prem = _f(pos.get("net_premium"))
    if not prem:
        return None
    paid = abs(prem)
    pcts = [_f(r.get("pnl_unrealized")) / paid for r in (checks or [])
            if _f(r.get("pnl_unrealized")) is not None]
    cur = pcts[-1] if pcts else None
    hwm = max(pcts) if pcts else None
    trail = _le.trail_floor(hwm) if hwm is not None else None
    dte = _f((latest or {}).get("dte_now"))
    positive = (cur is not None and cur > 0)

    dates = sorted({(r.get("checked_at") or "")[:10] for r in (checks or [])
                    if r.get("checked_at")})
    n_days = len(dates)
    opened = (pos.get("opened_at") or "")[:10]
    blind = None
    if opened and dates:
        try:
            blind = (date.fromisoformat(dates[0]) - date.fromisoformat(opened)).days
        except Exception:
            blind = None
    cov = " -- from %d check day%s" % (n_days, "" if n_days == 1 else "s")
    if blind and blind >= 1:
        cov += (", and the first %d day%s after opening %s never journaled, so the "
                "best-ever figure may be understated"
                % (blind, "" if blind == 1 else "s", "was" if blind == 1 else "were"))

    band = _le.GIVE_BACK_BAND * 100
    stop = abs(_le.STOP_LOSS_PCT) * 100
    rows = []

    if cur is None:
        rows.append({"key": "stop_loss", "label": "the stop", "state": "UNKNOWN",
                     "text": "no journaled mark"})
    elif cur <= _le.STOP_LOSS_PCT:
        rows.append({"key": "stop_loss", "label": "the stop", "state": "FIRES",
                     "text": "down %.0f%% -- past the %.0f%% stop" % (abs(cur) * 100, stop)})
    else:
        rows.append({"key": "stop_loss", "label": "the stop", "state": "CLEAR",
                     "text": "%s%.0f%% -- the stop is at %.0f%%"
                             % ("down " if cur < 0 else "up ", abs(cur) * 100, stop)})

    if hwm is None or trail is None:
        rows.append({"key": "give_back", "label": "giving back the gain",
                     "state": "UNKNOWN", "text": "no journaled marks yet"})
    elif cur is not None and cur <= trail:
        rows.append({"key": "give_back", "label": "giving back the gain",
                     "state": "FIRES",
                     "text": ("back to the %+.1f%% floor -- best was %+.1f%%, now %+.1f%%"
                              % (trail * 100, hwm * 100, cur * 100)) + cov})
    else:
        rows.append({"key": "give_back", "label": "giving back the gain",
                     "state": "CLEAR",
                     "text": ("floor at %+.1f%% -- best was %+.1f%%, now %+.1f%%; "
                              "a fall of %.0f points from the best closes it"
                              % (trail * 100, hwm * 100, (cur or 0) * 100, band)) + cov})

    if dte is None:
        rows.append({"key": "dte_7", "label": "the hard close", "state": "UNKNOWN",
                     "text": "no expiry on the latest check"})
    elif dte <= _le.DTE_HARD:
        rows.append({"key": "dte_7", "label": "the hard close", "state": "FIRES",
                     "text": "%d days left -- closes regardless, in or out of profit"
                             % dte})
    else:
        rows.append({"key": "dte_7", "label": "the hard close", "state": "CLEAR",
                     "text": "%d days left; the hard close is at %d days"
                             % (dte, _le.DTE_HARD)})

    if dte is None:
        rows.append({"key": "dte_21", "label": "the calendar", "state": "UNKNOWN",
                     "text": "no expiry on the latest check"})
    elif dte <= _le.DTE_SOFT and not positive:
        rows.append({"key": "dte_21", "label": "the calendar", "state": "FIRES",
                     "text": "%d days left and not in profit -- closes here" % dte})
    elif dte <= _le.DTE_SOFT:
        rows.append({"key": "dte_21", "label": "the calendar", "state": "CLEAR",
                     "text": "%d days left, but in profit -- held, and the give-back "
                             "floor governs from here" % dte})
    else:
        rows.append({"key": "dte_21", "label": "the calendar", "state": "CLEAR",
                     "text": "%d days left; the calendar closes a losing position at %d"
                             % (dte, _le.DTE_SOFT)})

    order = ["stop_loss", "give_back", "dte_7", "dte_21"]
    rows.sort(key=lambda r: order.index(r["key"]))
    firing = next((r["key"] for r in rows if r["state"] == "FIRES"), None)
    label = next((r["label"] for r in rows if r["key"] == firing), None)
    summary = ("%s: condition met"
               % label) if firing else "no rule's condition is met today"
    book = (pos.get("book") or "").upper()
    if book == "PAPER":
        note = "the paper book acts on these automatically at 15:35 each weekday"
    else:
        note = ("on the real book these are advisory -- they are computed and shown, "
                "and nothing acts on them")
    fine = ("Rules are listed in a fixed order: on the paper book the first match sets the exit label; on the real book that order is presentational and has never been confirmed. Percentages are measured against the "
            "premium paid, from journaled marks only. The direction read above is "
            "information: it closes nothing.")
    return {"rows": rows, "firing": firing, "summary": summary, "book_note": note,
            "fine": fine}


def evaluate(pos, legs, checks, entry_snap=None, entry_thesis_row=None,
             ownership=None, cur_sig=None, earnings=None, today=None):
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
            beliefs.append(_char_belief(pos, legs, checks, entry_snap))
            beliefs.append(_recover_belief(pos, legs, latest))
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
    elif n_unknown:
        # HELM-146: an ungraded belief must not vanish from the denominator --
        # a bare green check on a card whose acting belief was never armed is
        # a false green (the W15 ghosts wore exactly this).
        label = "✓ %d/%d · %d not graded" % (n_ok, len(beliefs) - n_unknown, n_unknown)
    else:
        label = "✓ %d/%d" % (n_ok, len(beliefs))

    mark = _f((latest or {}).get("pnl_unrealized"))
    dte = (latest or {}).get("dte_now")
    ladder, conv = (None, None)
    if not closed and latest:
        ladder, conv = expiry_ladder(pos, legs, latest.get("spot_price"), mark, _f(dte))
    xt = exit_track(legs, checks, closed)
    if xt is None and strat in _LONGS:
        xt = exit_track_long(pos, checks, closed)
    # The cost-to-close track. exit_track answers a different question — the BEST
    # exit offered since a confirmed break — and stays as it is; this one runs on
    # every position with a premium and traces the LAST check of each day. Both
    # read the journal through _day_marks, so they cannot drift on what a day is.
    ct = close_series(pos, checks, closed, today=today)
    if ct and latest is not None and not closed:
        _xpnl = _pnl_at_expiry(legs, latest.get("spot_price"))
        _xcost = position_value(_f(pos.get("net_premium")), _xpnl) if _xpnl is not None else None
        if (_xcost is not None and ct.get("now") is not None
                and _xcost - ct["now"] > max(100.0, 0.02 * _xcost)):
            # HELM-146: deep-ITM honesty -- when the mids quote the structure
            # below its value at expiry at today's spot, say so; a real fill
            # will not be kinder than intrinsic.
            ct["itm_note"] = ("a caveat on the quotes: at today's spot this structure is worth "
                              "%s at expiry, more than the %s the mid-quotes offer — deep "
                              "in-the-money legs quote wide and below true value, so a real "
                              "fill will likely cost nearer the higher number"
                              % (_amt(_xcost), _amt(ct["now"])))
    ct_head = close_headline(ct, closed)
    ct_svg = close_svg(ct)

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
        _broken = [b for b in beliefs if b["state"] in (BROKEN, BROKEN_LOUD)]
        if _broken and all((b.get("extra") or {}).get("recovered_latest") for b in _broken):
            cue = "on the graded beliefs alone — the confirmed break stands, but the latest " \
                  "check is back on the right side of the strike; decide the position " \
                  "rather than let the bounce decide it"
        else:
            cue = "on the graded beliefs alone — a confirmed break is information being ignored, " \
                  "and holding past it is what this book has paid for before"
    elif n_warn:
        cue = "on the graded beliefs alone, nothing is signalling yet — a warning is amber by measurement, not an alarm"
    else:
        if any(b.get("key") in ("direction", "strike") and b["state"] == UNKNOWN
               for b in beliefs):
            cue = "on the graded beliefs alone, nothing is signalling — but the belief that would signal one was never " \
                  "armed; any exit here is yours by hand"
        elif strat in _LONGS and mark is not None and mark > 0:
            # s111 (W162): the two branches below both assume a losing mark, and
            # keyed on the PREMIUM sign -- so a profitable long call was told it
            # had "a losing mark with beliefs intact". The mark decides now.
            cue = "on the graded beliefs alone, nothing is signalling — the position is ahead of its entry; " \
                  "sitting still is a decision"
        elif (_f(pos.get("net_premium")) or 0) < 0:
            cue = "on the graded beliefs alone, nothing is signalling — a losing mark with beliefs intact is drawdown " \
                  "inside the plan; sitting still is a decision"
        else:
            cue = "on the graded beliefs alone, nothing is signalling — a losing mark with beliefs intact is noise you " \
                  "are being paid to tolerate; sitting still is a decision"
    # s111 (W162): the long families separate recoverable-underwater from
    # dead-money-underwater -- the generic broken cue cannot.
    if not closed and strat in _LONGS and n_bad:
        _byk = {b.get("key"): b for b in beliefs}
        _dst = (_byk.get("direction") or {}).get("state")
        _cst = (_byk.get("character") or {}).get("state")
        _rst = (_byk.get("recover") or {}).get("state")
        if _dst in (BROKEN, BROKEN_LOUD):
            if (_cst in (FRAYING, BROKEN, BROKEN_LOUD)
                    or _rst in (FRAYING, BROKEN, BROKEN_LOUD)):
                cue = ("the direction is confirmed-broken and the position's character "
                       "or recovery odds are degrading with it -- the configuration this "
                       "book's worst losses shared; deciding it today beats watching it")
            elif _cst == HOLDS and _rst == HOLDS:
                cue = ("the direction is confirmed-broken, but delta is holding and the "
                       "break-even sits inside the remaining expected move -- recoverable "
                       "if you still believe the name; decide it deliberately rather than "
                       "let it drift")
    if bits:
        _lead = " ; ".join(bits) + ". "
    elif n_unknown:
        _ung = " · ".join("'%s'" % b["title"] for b in beliefs
                          if b["state"] in (UNKNOWN, PARTIAL))
        _lead = ("No graded belief is broken — but %s %s ungraded, so this card asserts "
                 "less than it appears to. " % (_ung, "is" if n_unknown == 1 else "are"))
    else:
        _lead = "Every graded belief holds. "
    _lead2 = ""
    if not closed and strat in _LONGS and latest is not None:
        # s111 (W162): leverage honesty -- the option's %% is not the stock's.
        _es0 = _f((entry_snap or {}).get("spot_price"))
        _ns0 = _f(latest.get("spot_price"))
        _prem0 = abs(_f(pos.get("net_premium")) or 0.0)
        if _es0 and _ns0 and mark is not None and _prem0:
            _sm = 100.0 * (_ns0 - _es0) / _es0
            _om = 100.0 * mark / _prem0
            if abs(_om) >= 10.0 and abs(_sm) < abs(_om):
                _lead2 = ("The mark's %+.0f%% is leverage on a stock move of "
                          "%+.1f%% since entry -- read the stock, not the option, "
                          "for how far this has actually moved. " % (_om, _sm))
    read = _lead + _lead2 + cue
    if conv:
        read += ". " + conv
    if not closed and strat in _LONGS:
        # s111 (W162): a checkpoint scaled to the entry clock, not the credit
        # book's 21/7 -- display only.
        _ed0 = _f(pos.get("entry_dte"))
        _dn0 = _f(dte)
        if _ed0 and _dn0 is not None and _dn0 <= 0.5 * _ed0 and (mark or 0.0) <= 0.0:
            read += (". Halfway checkpoint: %d of the %d entry days are spent and "
                     "the thesis has delivered nothing yet -- from here the clock "
                     "stops being the cheap part of the trade"
                     % (int(round(_ed0 - _dn0)), int(round(_ed0))))

    _exps = sorted({(l.get("expiration") or "")[:10] for l in legs or []
                    if l.get("option_type") not in (None, "STOCK") and l.get("expiration")})

    # W90 / HELM-142 -- earnings-inside-window flag. Display only; the W81
    # 10-day entry gate is untouched. States never guessed: inside (with
    # occurred/upcoming), outside (after expiry), stale (cached date already
    # past and not inside this window), unknown. Closed cards carry None --
    # a frozen post-mortem must not read today's calendar.
    earn = None
    if not closed:
        asof_d = ((latest or {}).get("checked_at") or "")[:10]
        opened = (pos.get("opened_at") or "")[:10]
        last_exp = _exps[-1] if _exps else None
        nxt = ((earnings or {}).get("next") or "")[:10] or None
        ent = ((earnings or {}).get("at_entry") or "")[:10] or None
        inside = None
        for d in sorted({x for x in (nxt, ent) if x}):
            if last_exp and opened and opened <= d <= last_exp:
                inside = d
                break
        if inside:
            earn = {"date": inside, "state": "inside",
                    "when": "occurred" if (asof_d and inside < asof_d)
                            else "upcoming"}
        elif nxt and asof_d and nxt < asof_d:
            earn = {"date": nxt, "state": "stale"}
        elif nxt:
            earn = {"date": nxt, "state": "outside"}
        else:
            earn = {"state": "unknown"}

    # s105 (W123): the header showed dollars only. The percentage comes from
    # the SAME journaled mark the exit rules read, so header and rules cannot
    # disagree about how much has been given back.
    _prem = _f(pos.get("net_premium")) or 0.0
    # Computed from the mark against the premium paid -- NOT read from the
    # check row, which carries no pnl_pct. This is the same arithmetic the
    # close-track and the give-back rule use, so the three cannot disagree.
    _mark_pct = (round(100.0 * mark / abs(_prem), 1)
                 if (mark is not None and _prem) else None)
    _real = _f(pos.get("realized_pnl"))
    _real_pct = (round(100.0 * _real / abs(_prem), 1)
                 if (closed and _real is not None and _prem) else None)
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
        "mark_pct": _mark_pct, "realized_pct": _real_pct,
        "realized": _f(pos.get("realized_pnl")) if closed else None,
        "exit_reason": pos.get("exit_reason") if closed else None,
        "dte": dte, "read": read,
        "ladder": ladder, "convergence": conv,
        "exit_track": xt,
        "close_track": ct, "close_headline": ct_head, "close_svg": ct_svg,
        "close_trend": trend_sentence(ct, closed),
        "exit_rules": exit_rules(pos, checks, latest, entry_thesis_row),
        "doctrine": doctrine_note(strat, closed),
        "earnings": earn,
        "condor_honesty": strat in ("IRON_CONDOR", "SHORT_STRANGLE", "JADE_LIZARD"),
    }
