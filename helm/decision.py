# helm/decision.py
# Book-agnostic position verdict: hold or close, and why.
# Extracted from paper_manage (WS2, behaviour-preserving). Reads strategy_settings.

from helm.db import get_conn
from helm.cli.check_cmd import dte

DEFAULT_PROFIT_TARGET = 0.50   # fraction of credit captured
DEFAULT_STOP_MULT     = 2.0    # loss = N x credit
DEFAULT_DTE_EXIT      = 21     # days


CREDIT_FAMILY = 'CREDIT'
LONG_DEBIT_FAMILY = 'LONG_DEBIT'
LONG_VOL_FAMILY = 'LONG_VOL'
DEBIT_SPREAD_FAMILY = 'DEBIT_SPREAD'


def _family(strategy: str) -> str:
    """Route a strategy to its management family."""
    if strategy == 'LONG_STRADDLE':
        return LONG_VOL_FAMILY
    if strategy in ('LONG_CALL', 'LONG_PUT'):
        return LONG_DEBIT_FAMILY
    if strategy in ('BEAR_PUT_SPREAD', 'BULL_CALL_SPREAD'):
        return DEBIT_SPREAD_FAMILY
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
                stop_dollars = min(stop_dollars, abs(pos.max_loss))
            if total_pnl <= -stop_dollars:
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
    # LONG_VOL (straddle): calendar-only; no profit/stop branch (the convex tail
    # IS the edge, so a profit cap or premium stop would defeat the position).
    if reason is None and dte_now is not None:
        if dte_now <= 0:
            reason = 'EXPIRY'
        elif dte_now <= dte_exit:
            reason = 'DTE_MANAGE'
    return reason, total_pnl
