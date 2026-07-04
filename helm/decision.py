# helm/decision.py
# Book-agnostic position verdict: hold or close, and why.
# Extracted from paper_manage (WS2, behaviour-preserving). Reads strategy_settings.

from helm.db import get_conn
from helm.dates import dte

DEFAULT_PROFIT_TARGET = 0.50   # fraction of credit captured
DEFAULT_STOP_MULT     = 2.0    # loss = N x credit
DEFAULT_DTE_EXIT      = 21     # days


CREDIT_FAMILY = 'CREDIT'
LONG_DEBIT_FAMILY = 'LONG_DEBIT'
LONG_VOL_FAMILY = 'LONG_VOL'
DEBIT_SPREAD_FAMILY = 'DEBIT_SPREAD'
COVERED_FAMILY = 'COVERED'
DIAGONAL_FAMILY = 'DIAGONAL'


def _family(strategy: str) -> str:
    """Route a strategy to its management family."""
    if strategy == 'LONG_STRADDLE':
        return LONG_VOL_FAMILY
    if strategy in ('LONG_CALL', 'LONG_PUT'):
        return LONG_DEBIT_FAMILY
    if strategy in ('BEAR_PUT_SPREAD', 'BULL_CALL_SPREAD'):
        return DEBIT_SPREAD_FAMILY
    if strategy == 'COVERED_CALL':
        return COVERED_FAMILY
    if strategy in ('PMCC', 'DIAGONAL', 'DIAGONAL_PUT'):
        return DIAGONAL_FAMILY
    return CREDIT_FAMILY


def _settings(account_id: str, strategy: str) -> dict:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM strategy_settings WHERE account_id = ? AND strategy = ?",
        (account_id, strategy),
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def evaluate(pos, legs, marks: dict):
    """Return (reason, total_pnl). reason is None to hold."""
    credit = pos.net_premium or 0.0
    total_pnl = 0.0
    for leg in legs:
        cp = marks[leg.id]
        if leg.direction == 'SHORT':
            total_pnl += (leg.open_price - cp) * leg.contracts * leg.multiplier
        else:
            total_pnl += (cp - leg.open_price) * leg.contracts * leg.multiplier

    s = _settings(pos.account_id, pos.strategy)
    pt = s.get('profit_target_pct') or DEFAULT_PROFIT_TARGET
    pt = pt if pt <= 1 else pt / 100.0
    stop_mult = s.get('stop_loss_multiplier') or DEFAULT_STOP_MULT
    dte_exit = s.get('dte_exit_threshold') or DEFAULT_DTE_EXIT

    dtes = [dte(l.expiration) for l in legs if l.expiration]
    dte_now = min([d for d in dtes if d is not None], default=None)

    fam = _family(pos.strategy)
    reason = None
    if fam == CREDIT_FAMILY:
        # Premium-sellers: keep a fraction of credit; stop at a multiple of it.
        if credit and (total_pnl / abs(credit)) >= pt:
            reason = 'PROFIT_TARGET'
        elif credit:
            stop_dollars = stop_mult * abs(credit)
            if pos.max_loss:
                cap = abs(pos.max_loss)
                if pos.strategy in DEFINED_RISK_SPREADS:
                    cap = INTERIM_DR_STOP_FRAC * cap  # HELM-030 interim: real stop below max loss
                stop_dollars = min(stop_dollars, cap)
            if total_pnl <= -stop_dollars and not _ab_suppresses_stop(pos):
                reason = 'STOP'
    elif fam == LONG_DEBIT_FAMILY:
        # Long single options: profit is % gain on premium paid; max loss is the
        # premium itself, so no credit-style stop. Otherwise exit on the calendar.
        if credit and (total_pnl / abs(credit)) >= pt:
            reason = 'PROFIT_TARGET'
    elif fam == DEBIT_SPREAD_FAMILY:
        # Debit spreads are defined-reward: target a fraction of MAX PROFIT, not of
        # the debit. No stop (max loss is the defined debit). Otherwise calendar.
        if pos.max_profit and (total_pnl / pos.max_profit) >= pt:
            reason = 'PROFIT_TARGET'
    elif fam == COVERED_FAMILY:
        # Covered call: only the short call is a tracked leg (stock is external).
        # Take profit on the call credit; never stop (stock loss is invisible here,
        # and a rally just means assignment at a capped gain). Otherwise calendar.
        if credit and (total_pnl / abs(credit)) >= pt:
            reason = 'PROFIT_TARGET'
    # LONG_VOL (straddle): calendar-only; no profit/stop branch (the convex tail
    # IS the edge, so a profit cap or premium stop would defeat the position).
    # Diagonal family manages off the BACK (long) leg — the structure is only
    # "near expiry" when the leg defining its lifespan is. Front-leg roll is out
    # of scope (deferred roll layer). Others manage off the nearest leg.
    dte_cal = (max([d for d in dtes if d is not None], default=None)
               if fam == DIAGONAL_FAMILY else dte_now)
    if reason is None and dte_cal is not None:
        if dte_cal <= 0:
            reason = 'EXPIRY'
        elif dte_cal <= dte_exit:
            reason = 'DTE_MANAGE'
    return reason, total_pnl


# ---------------------------------------------------------------------------
# HELM-030  -  stop-loss A/B counterfactual capture
# ---------------------------------------------------------------------------
# Basis is per-family: defined-risk credit spreads grade stops as a fraction of
# MAX LOSS; naked credit (incl. JADE_LIZARD, by design) grades as a multiple of
# CREDIT. PERM is excluded (not a premium-spine trade). While the A/B is active
# the ACTING paper verdict runs no-stop (see _ab_suppresses_stop) so looser arms
# aren't censored; evaluate_arms reports each arm's counterfactual trigger/tick.

DEFINED_RISK_SPREADS = ('BULL_PUT_SPREAD', 'BEAR_CALL_SPREAD', 'IRON_CONDOR')
NAKED_CREDIT         = ('CSP', 'SHORT_STRANGLE', 'JADE_LIZARD')

INTERIM_DR_STOP_FRAC = 0.75   # HELM-030 interim: live defined-risk stop floor at 75% of
                              # max loss (loosest A/B arm 'ml_75') until the stop A/B grades
                              # a winner. Naked credit (CSP/strangle/jade) is unchanged.


def _stop_ab_active():
    """True iff the HELM-030 stop A/B is switched on (helm_meta flag)."""
    row = get_conn().execute(
        "SELECT value FROM helm_meta WHERE key = 'stop_ab_active'"
    ).fetchone()
    return bool(row) and row[0] == '1'


def _ab_suppresses_stop(pos):
    """During the A/B, paper credit positions (PERM excluded) run no-stop as the
    acting verdict so looser candidate arms aren't censored. REAL book and PERM
    are never suppressed; off-experiment this is a no-op."""
    if getattr(pos, 'book', 'REAL') != 'PAPER':
        return False
    if pos.strategy == 'PERM':
        return False
    return _stop_ab_active()


def evaluate_arms(pos, total_pnl):
    """HELM-030 counterfactual arm capture (pure; no DB writes).

    Returns one dict per candidate stop arm for a credit-family paper position
    (PERM excluded):  {arm, basis, threshold_dollars, would_trigger}.
    threshold_dollars is the dollar loss bar (None for no_stop), derived from
    entry-static fields (max_loss / net_premium) so it equals its frozen value
    barring manual position edits. would_trigger is True once total_pnl has
    reached -threshold at the current marks. Returns [] off-experiment."""
    credit = pos.net_premium or 0.0
    if not credit:
        return []

    if pos.strategy in DEFINED_RISK_SPREADS:
        if pos.max_loss is None:
            return []
        ml = abs(pos.max_loss)
        arms = [('no_stop', 'MAX_LOSS', None),
                ('ml_50',   'MAX_LOSS', 0.50 * ml),
                ('ml_75',   'MAX_LOSS', 0.75 * ml)]
    elif pos.strategy in NAKED_CREDIT:
        c = abs(credit)
        arms = [('no_stop', 'CREDIT_MULT', None),
                ('cr_2x',   'CREDIT_MULT', 2.0 * c),
                ('cr_3x',   'CREDIT_MULT', 3.0 * c)]
    else:
        return []

    return [
        {'arm': a, 'basis': b, 'threshold_dollars': t,
         'would_trigger': (t is not None and total_pnl <= -t)}
        for (a, b, t) in arms
    ]


# ---------------------------------------------------------------------------
# HELM-031  -  long-debit shadow stop capture (informational; no auto-close)
# ---------------------------------------------------------------------------
# A -DEBIT_SHADOW_STOP_PCT-of-premium loss "would-fire" flag for LONG_DEBIT
# positions (LONG_CALL / LONG_PUT). Pure and side-effect-free, mirroring
# evaluate_arms: it never mutates the verdict returned by evaluate() and never
# closes anything. The REAL check journal (Patch 2) persists would_fire so the
# real book accumulates counterfactual evidence before we consider acting.
# The threshold is an OBSERVATION level, NOT a stop.

DEBIT_SHADOW_STOP_PCT = 0.50   # fraction of premium paid; observation-only


def evaluate_shadow_debit_stop(pos, total_pnl):
    """HELM-031 shadow capture (pure; no DB writes, no verdict mutation).

    For a LONG_DEBIT position report whether an informational
    -DEBIT_SHADOW_STOP_PCT-of-premium loss level would fire at the current
    marks. Returns None off-family or when premium is unknown.

    loss_pct is computed here from total_pnl and premium (max loss on a long
    option is the premium paid, so it floors naturally at -1.0). It does NOT
    read the stored pnl_pct, so it is unaffected by the parked pnl_pct
    corruption -- this forward flag is trustworthy without that fix.
    """
    if _family(pos.strategy) != LONG_DEBIT_FAMILY:
        return None
    premium = abs(pos.net_premium or 0.0)
    if not premium:
        return None
    threshold = DEBIT_SHADOW_STOP_PCT * premium
    return {
        "signal": "DEBIT_STOP_50",
        "basis": "PREMIUM",
        "threshold_dollars": threshold,
        "loss_pct": total_pnl / premium,
        "would_fire": total_pnl <= -threshold,
    }
