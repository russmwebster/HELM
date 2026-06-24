# helm/decision.py
# Book-agnostic position verdict: hold or close, and why.
# Extracted from paper_manage (WS2, behaviour-preserving). Reads strategy_settings.

from helm.db import get_conn
from helm.cli.check_cmd import dte

DEFAULT_PROFIT_TARGET = 0.50   # fraction of credit captured
DEFAULT_STOP_MULT     = 2.0    # loss = N x credit
DEFAULT_DTE_EXIT      = 21     # days


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

    reason = None
    # Long straddle is a long-vol bet: the convex move/vol-pop tail IS the edge,
    # so the credit-family profit target (which would cap winners) and the
    # premium stop do not apply. It exits on the DTE/EXPIRY calendar rule only.
    is_long_vol = pos.strategy == 'LONG_STRADDLE'
    if not is_long_vol and credit and (total_pnl / abs(credit)) >= pt:
        reason = 'PROFIT_TARGET'
    elif not is_long_vol and credit:
        stop_dollars = stop_mult * abs(credit)
        if pos.max_loss:
            stop_dollars = min(stop_dollars, abs(pos.max_loss))
        if total_pnl <= -stop_dollars:
            reason = 'STOP'
    if reason is None and dte_now is not None:
        if dte_now <= 0:
            reason = 'EXPIRY'
        elif dte_now <= dte_exit:
            reason = 'DTE_MANAGE'
    return reason, total_pnl
