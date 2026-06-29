"""Verdict -> display mapping and dict->namespace adapters for decision-core.

Shared by check_cmd (CLI) and health (/health GUI) so both render the same
decision-core verdict. Lifted from check_cmd.py (the WS4 adapter) during WS5 to
give /health a neutral import with no gui->cli dependency. Pure presentation +
adapter layer: imports nothing from cli, decision, or health.
"""

from types import SimpleNamespace


# -- WS4: decision-core verdict adapter (additive) -----------------------------
# reason -> flag band. None is the only HOLD; a non-None reason is exactly when
# paper_manage would auto-close, so every non-None reason is actionable.
# Distress (STOP/EXPIRY) -> RED; orderly management (PROFIT_TARGET/DTE_MANAGE)
# -> YELLOW. Unknown reasons default to YELLOW (surface, don't hide).
_REASON_HEADLINE = {
    "PROFIT_TARGET": ("YELLOW", "Profit target — consider closing"),
    "STOP":          ("RED",    "Stop breached — close or roll"),
    "EXPIRY":        ("RED",    "Expiring — act now"),
    "DTE_MANAGE":    ("YELLOW", "In management window"),
}
_FLAG_STYLE = {"GREEN": "bold green", "YELLOW": "bold yellow", "RED": "bold red"}

def band_for(reason, evidence=None):
    """Map a decision-core (reason, evidence) to {flag, flag_style, headline}.

    reason owns RED and every action state. On HOLD (reason is None) the position
    is GREEN unless evidence lifts it to YELLOW (attention without action): thin
    buffer / ITM (short single-leg) or an underwater P&L past the legacy
    thresholds. Evidence never escalates to RED -- that stays verdict-only.

    Stale/frozen marks do NOT change the band -- they only append a confirm-at-RTH
    caveat, so an after-hours board (everything frozen) still triages by state.
    """
    ev = evidence or {}
    if reason is not None:
        flag, headline = _REASON_HEADLINE.get(reason, ("YELLOW", "Manage"))
    else:
        direction = ev.get("direction")
        buf = ev.get("intrinsic_buffer")
        pct_buf = ev.get("pct_buffer")
        _short_single = (direction == "SHORT" and not ev.get("is_multileg")
                         and buf is not None)
        if _short_single and buf < 0:
            flag, headline = "YELLOW", "Holding — ITM, assignment risk"
        elif _short_single and pct_buf is not None and pct_buf < 3:
            flag, headline = "YELLOW", "Holding — thin buffer to strike"
        elif (ev.get("pnl_pct") is not None
              and ev["pnl_pct"] < (-15 if direction == "SHORT" else -25)):
            flag, headline = "YELLOW", "Holding — underwater, watch"
        else:
            flag, headline = "GREEN", "Holding — healthy"

    mc = ev.get("mark_confidence")
    if mc in ("frozen", "stale"):
        headline = f"{headline} · {mc}, confirm at RTH"
    return {"flag": flag, "flag_style": _FLAG_STYLE[flag], "headline": headline}

def _ns_pos(pos):
    """Wrap a check-side pos dict in the attribute surface evaluate() reads."""
    return SimpleNamespace(
        account_id=pos.get("account_id"),
        strategy=pos.get("strategy"),
        net_premium=pos.get("net_premium"),
        max_profit=pos.get("max_profit"),
        max_loss=pos.get("max_loss"),
    )

def _ns_leg(leg):
    """Wrap a check-side leg dict in the attribute surface evaluate() reads."""
    return SimpleNamespace(
        id=leg.get("id"),
        direction=leg.get("direction"),
        open_price=leg.get("open_price"),
        contracts=leg.get("contracts"),
        multiplier=leg.get("multiplier"),
        expiration=leg.get("expiration"),
    )
