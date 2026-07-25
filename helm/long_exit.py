# helm/long_exit.py
# HELM-101 §4 -- exit doctrine v2 for the LONG_* families (LONG_CALL / LONG_PUT).
#
# Doctrine, in precedence order:
#   1. THESIS_BREAK      -- the primary loser exit. Fires on information (the
#                           directional read that justified the trade is gone),
#                           confirmed over consecutive daily checks so a shakeout
#                           does not trigger it.
#   2. PROFIT_FLOOR      -- ratcheted winner management. No fixed profit target;
#                           a floor arms at +50% and ratchets in 10-point steps,
#                           trailing the high-water mark by one step.
#   3. DTE_GATE          -- a forced decision at 30 DTE, not a dawdle point.
#   4. CATASTROPHE_STOP  -- a -50% backstop for gaps that outrun confirmation.
#
# HELM-094 boundary: every verdict here is a DAILY-CHECK DECISION evaluated at
# mark time. None of them is a resting broker order, and none of them acts on the
# REAL book -- HELM-093 makes real-book exits advisory. The paper book acts.
#
# Parameters are provisional by design; the calibration period measures them,
# which is why every evaluation returns a full arm record (see build_arms).

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

# -- doctrine parameters (provisional; the paper period measures these) --------
PROFIT_FLOOR_ARM = 0.50    # floor arms once HWM reaches +50% of debit
RATCHET_STEP     = 0.10    # floor moves in 10-point steps
DTE_GATE_DAYS    = 30      # forced decision point
CATASTROPHE_PCT  = -0.50   # backstop
CONFIRM_DAYS     = 2       # consecutive daily checks before THESIS_BREAK fires
CTX_MAX_AGE_DAYS = 1       # a signals row older than this is not "today's read"

LONG_STRATEGIES = ('LONG_CALL', 'LONG_PUT')

_CTX_CACHE = {}   # (ticker, date) -> context dict, one pull per name per run


# -- inputs -------------------------------------------------------------------

def entry_thesis(conn, position_id):
    """The directional read recorded at open, or None when unarmed."""
    try:
        row = conn.execute(
            'SELECT source, signals_generated_at, bias_score, spot_price, '
            'sma_50, sma_200, adx FROM entry_thesis WHERE position_id = ?',
            (position_id,)).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return {'source': row[0], 'signals_generated_at': row[1],
            'bias_score': row[2], 'spot_price': row[3],
            'sma_50': row[4], 'sma_200': row[5], 'adx': row[6]}


def journal_state(conn, position_id):
    """High-water mark and the current thesis-break streak, from the checks
    journal. Journal-native by design (same shape as the HELM-093 Nd counter):
    the streak that produced an exit stays auditable after the fact."""
    out = {'hwm_pct': None, 'break_days': 0, 'checks_n': 0}
    try:
        rows = conn.execute(
            'SELECT pnl_pct, thesis_broken FROM checks WHERE position_id = ? '
            'ORDER BY checked_at DESC', (position_id,)).fetchall()
    except Exception:
        return out
    out['checks_n'] = len(rows)
    pnls = [r[0] for r in rows if r[0] is not None]
    if pnls:
        out['hwm_pct'] = max(pnls) / 100.0   # checks.pnl_pct is in percent
    streak = 0
    for r in rows:                            # newest first
        if r[1] == 1:
            streak += 1
        else:
            break
    out['break_days'] = streak
    return out


def current_context(conn, ticker):
    """Today's directional read: the day's scan row when there is one, else a
    fresh technicals pull. Hybrid by Russ's call (s82) -- the scan row is free
    and is what he saw that morning, the pull covers names the scan missed."""
    key = (str(ticker).upper(), date.today().isoformat())
    if key in _CTX_CACHE:
        return _CTX_CACHE[key]
    ctx = {'source': None, 'asof': None, 'spot': None,
           'sma_50': None, 'sma_200': None, 'bias_score': None}
    try:
        row = conn.execute(
            'SELECT generated_at, spot_price, sma_50, sma_200, auto_bias_score '
            'FROM signals WHERE ticker = ? ORDER BY generated_at DESC LIMIT 1',
            (str(ticker).upper(),)).fetchone()
    except Exception:
        row = None
    if row and row[2] is not None:
        try:
            age = (datetime.now() - datetime.fromisoformat(str(row[0]))).days
        except Exception:
            age = 999
        if age <= CTX_MAX_AGE_DAYS:
            ctx.update({'source': 'signals', 'asof': row[0], 'spot': row[1],
                        'sma_50': row[2], 'sma_200': row[3], 'bias_score': row[4]})
            _CTX_CACHE[key] = ctx
            return ctx
    # fall back to a live pull -- same computation the scan uses, so the two
    # sources are directly comparable
    try:
        from helm.cli.scan_cmd import fetch_technicals
        res = fetch_technicals(str(ticker).upper()) or {}
        if res.get('sma_50') is not None:
            ctx.update({'source': 'live', 'asof': datetime.now().isoformat(),
                        'spot': res.get('price'), 'sma_50': res.get('sma_50'),
                        'sma_200': res.get('sma_200'),
                        'bias_score': res.get('bias_score')})
    except Exception:
        pass
    _CTX_CACHE[key] = ctx
    return ctx


# -- doctrine -----------------------------------------------------------------

def floor_for(hwm):
    """The ratcheted profit floor for a given high-water mark, or None when the
    floor has not armed yet. Steps of 10 points, trailing HWM by one step: at
    HWM +50% the floor is +40%, at +73% it is +60%. Giveback from peak is
    therefore 10-20 points -- a winner can breathe without surrendering the run."""
    if hwm is None or hwm < PROFIT_FLOOR_ARM:
        return None
    steps = int(hwm / RATCHET_STEP)          # floor to the step boundary
    return round(steps * RATCHET_STEP - RATCHET_STEP, 4)


def thesis_is_broken(entry, cur):
    """True/False, or None when the test cannot be run (unarmed or no context).

    Broken = direction gone (bias <= 0, hysteresis against the >= +2 entry) OR
    price closed below the 50-day. Deliberately not 'bias weakened'."""
    if not entry or not cur:
        return None
    bias, spot, sma50 = cur.get('bias_score'), cur.get('spot'), cur.get('sma_50')
    if bias is None and (spot is None or sma50 is None):
        return None
    broken = False
    if bias is not None and bias <= 0:
        broken = True
    if spot is not None and sma50 is not None and spot < sma50:
        broken = True
    return broken


def long_verdict(total_pnl, debit, dte_now, entry, cur, jstate,
                 theta_per_day=None, mark=None):
    """Return (reason, arms). reason is None to hold.

    arms is the counterfactual record: what was armed, what fired, and the
    numbers needed to score the alternatives later. Logging it is mandatory --
    the parameters above only become measured choices if every evaluation
    leaves evidence."""
    jstate = jstate or {}
    pnl_pct = None
    if debit:
        try:
            pnl_pct = total_pnl / abs(float(debit))
        except Exception:
            pnl_pct = None

    broken_today = thesis_is_broken(entry, cur)
    streak = (jstate.get('break_days') or 0) + 1 if broken_today else 0
    hwm = jstate.get('hwm_pct')
    if pnl_pct is not None:
        hwm = pnl_pct if hwm is None else max(hwm, pnl_pct)
    floor = floor_for(hwm)

    arms = {
        'pnl_pct': None if pnl_pct is None else round(pnl_pct, 4),
        'hwm_pct': None if hwm is None else round(hwm, 4),
        'dte': dte_now,
        'thesis': {
            'armed': entry is not None and cur is not None and broken_today is not None,
            'broken_today': broken_today,
            'streak': streak,
            'confirm_days': CONFIRM_DAYS,
            'ctx_source': (cur or {}).get('source'),
            'entry_source': (entry or {}).get('source'),
            'entry_bias': (entry or {}).get('bias_score'),
            'cur_bias': (cur or {}).get('bias_score'),
            'cur_spot': (cur or {}).get('spot'),
            'cur_sma_50': (cur or {}).get('sma_50'),
        },
        'profit_floor': {'armed': floor is not None, 'floor': floor},
        'dte_gate': {'armed': dte_now is not None and dte_now <= DTE_GATE_DAYS,
                     'threshold': DTE_GATE_DAYS},
        'catastrophe': {'armed': pnl_pct is not None and pnl_pct <= CATASTROPHE_PCT,
                        'threshold': CATASTROPHE_PCT},
        # alternatives logged but never acted on -- the log decides between them
        'alt': {
            'theta_per_day': theta_per_day,
            'mark': mark,
            'theta_rate': (round(abs(theta_per_day) / mark, 4)
                           if theta_per_day and mark else None),
            'dte_ratchet_floor': _dte_ratchet_floor(dte_now),
        },
    }

    reason = None
    if arms['thesis']['armed'] and broken_today and streak >= CONFIRM_DAYS:
        reason = 'THESIS_BREAK'
    elif floor is not None and pnl_pct is not None and pnl_pct <= floor:
        reason = 'PROFIT_FLOOR'
    elif dte_now is not None and dte_now <= DTE_GATE_DAYS:
        reason = 'DTE_GATE'
    elif pnl_pct is not None and pnl_pct <= CATASTROPHE_PCT:
        reason = 'CATASTROPHE_STOP'
    arms['fired'] = reason
    return reason, arms


def _dte_ratchet_floor(dte_now, dte_open=180):
    """The DTE-driven ratchet variant: floor rises as theta burns, squeezing out
    stagnant winners. Logged in parallel with the HWM ratchet, never acted on --
    the two will disagree on real positions and the log decides which is right."""
    if dte_now is None:
        return None
    burned = max(0.0, min(1.0, (dte_open - dte_now) / float(dte_open)))
    return round(PROFIT_FLOOR_ARM * burned, 4)


def arms_json(arms):
    try:
        return json.dumps(arms, default=str)
    except Exception:
        return None
