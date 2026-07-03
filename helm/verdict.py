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

PROX_PREP = 0.50
PROX_ACT = 0.75


def condor_proximity(spot, short_put, short_call):
    """Iron-condor short-strike proximity (greeks-free structural signal).

    Fraction of the body center -> tested short strike distance covered by
    spot: 0.0 = at center (safe), 1.0 = spot at the short strike (fully
    tested), >1.0 = short strike breached. Returns {proximity_pct,
    tested_side} or None when inputs are incomplete/degenerate. Pure.
    """
    if spot is None or short_put is None or short_call is None:
        return None
    if not (short_put < short_call):
        return None
    center = (short_put + short_call) / 2.0
    if spot >= center:
        span = short_call - center
        if span <= 0:
            return None
        prox = (spot - center) / span
        side = "call"
    else:
        span = center - short_put
        if span <= 0:
            return None
        prox = (center - spot) / span
        side = "put"
    return {"proximity_pct": round(prox, 4), "tested_side": side}


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
        # HELM-043 v1b: on an already-RED condor verdict, name the tested
        # short-strike side + depth from the v1a proximity evidence computed
        # upstream. Structural detail only -- never changes the flag or the
        # reason; silent when proximity is absent or below prep, and for
        # non-condor multileg (no proximity_pct in evidence).
        if flag == "RED":
            _p = ev.get("proximity_pct")
            if _p is not None and _p >= PROX_PREP:
                _side = ev.get("tested_side") or "short"
                _pct = int(round(_p * 100))
                _state = ("past strike" if _p >= 1.0
                          else "tested" if _p >= PROX_ACT else "approaching")
                headline = f"{headline} · short {_side} {_state} ({_pct}%)"
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
        elif (ev.get("is_multileg") and ev.get("proximity_pct") is not None
              and ev.get("proximity_pct") >= PROX_PREP):
            _side = ev.get("tested_side") or "short"
            _pct = int(round(ev.get("proximity_pct") * 100))
            _act = ev.get("proximity_pct") >= PROX_ACT
            _verb = "tested" if _act else "approaching"
            _tier = "manage" if _act else "watch"
            flag, headline = "YELLOW", f"Holding — short {_side} strike {_verb} ({_pct}%), {_tier}"
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
