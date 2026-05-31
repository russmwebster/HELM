"""
helm/models/close_snapshot.py
Captures a final snapshot when a position is closed.
Stored in lifecycle_events with event_type=CLOSE_SNAPSHOT.
"""
from __future__ import annotations
from datetime import datetime, date
from typing import Optional
from helm.db import get_conn


def save_close_snapshot(
    position_id: str,
    ticker: str,
    realized_pnl: float,
    close_prices: dict,
    legs: list,
    reason: str = "manual",
) -> None:
    """
    Save a close snapshot to lifecycle_events.
    Captures: realized P&L, close prices per leg, spot at close,
    IVR/IVP at close, DTE remaining, days held.
    """
    import json
    import yfinance as yf

    conn = get_conn()

    pos_row = conn.execute(
        "SELECT opened_at, strategy FROM positions WHERE id = ?",
        (position_id,)
    ).fetchone()

    days_held = strategy = None
    if pos_row:
        strategy = pos_row["strategy"]
        try:
            opened = pos_row["opened_at"][:10]
            days_held = (date.today() - date.fromisoformat(opened)).days
        except Exception:
            pass

    spot_at_close = None
    try:
        info = yf.Ticker(ticker).fast_info
        spot_at_close = getattr(info, "last_price", None) or getattr(info, "previous_close", None)
    except Exception:
        pass

    iv_rank_at_close = iv_pct_at_close = iv_current_at_close = None
    try:
        from helm.models.iv_history import IVHistory
        ivr = IVHistory.latest(ticker)
        if ivr:
            iv_rank_at_close    = ivr.iv_rank
            iv_pct_at_close     = ivr.iv_percentile
            iv_current_at_close = ivr.iv_current
    except Exception:
        pass

    dte_remaining = {}
    for leg in legs:
        if leg.expiration:
            try:
                exp = datetime.strptime(leg.expiration[:10], "%Y-%m-%d").date()
                dte_remaining[leg.leg_role] = (exp - date.today()).days
            except Exception:
                pass

    entry_snap = None
    try:
        entry_snap = conn.execute(
            "SELECT * FROM entry_snapshots WHERE position_id = ? ORDER BY created_at DESC LIMIT 1",
            (position_id,)
        ).fetchone()
    except Exception:
        pass

    payload = {
        "realized_pnl":       realized_pnl,
        "close_prices":       close_prices,
        "spot_at_close":      spot_at_close,
        "iv_rank_at_close":   iv_rank_at_close,
        "iv_pct_at_close":    iv_pct_at_close,
        "iv_current_at_close": iv_current_at_close,
        "days_held":          days_held,
        "strategy":           strategy,
        "reason":             reason,
        "dte_remaining":      dte_remaining,
        "entry_iv_rank":      dict(entry_snap)["iv_rank"] if entry_snap else None,
        "entry_iv_current":   dict(entry_snap)["iv_current"] if entry_snap else None,
        "entry_delta":        dict(entry_snap)["delta"] if entry_snap else None,
        "entry_spot":         dict(entry_snap)["spot_price"] if entry_snap else None,
        "closed_at":          datetime.now().isoformat(),
    }

    conn.execute("""
        INSERT INTO lifecycle_events
            (id, position_id, event_type, event_at, notes, metadata)
        VALUES (?, ?, 'CLOSE_SNAPSHOT', datetime('now'), ?, ?)
    """, (
        "LCE-SNAP-" + position_id[-8:],
        position_id,
        f"Closed via {reason}. P&L: ${realized_pnl:,.0f}. Days held: {days_held}.",
        json.dumps(payload),
    ))
    conn.commit()
