"""
helm/models/close_snapshot.py
Captures a final snapshot when a position is closed.
Stored in lifecycle_events with event_type=CLOSE_SNAPSHOT... but CLOSE_SNAPSHOT not in CHECK constraint.
We use narrative field for JSON payload and event_type=CLOSED.
"""
from __future__ import annotations
from datetime import datetime, date
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
    Captures: realized P&L, close prices, spot, IVR/IVP, DTE remaining, days held.
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
        "realized_pnl":        realized_pnl,
        "close_prices":        {str(k): v for k, v in close_prices.items()},
        "spot_at_close":       spot_at_close,
        "iv_rank_at_close":    iv_rank_at_close,
        "iv_pct_at_close":     iv_pct_at_close,
        "iv_current_at_close": iv_current_at_close,
        "days_held":           days_held,
        "strategy":            strategy,
        "reason":              reason,
        "dte_remaining":       dte_remaining,
        "entry_iv_rank":       dict(entry_snap)["iv_rank"] if entry_snap else None,
        "entry_iv_current":    dict(entry_snap)["iv_current"] if entry_snap else None,
        "entry_delta":         dict(entry_snap)["delta"] if entry_snap else None,
        "entry_spot":          dict(entry_snap)["spot_price"] if entry_snap else None,
        "closed_at":           datetime.now().isoformat(),
    }

    narrative = (
        f"CLOSE_SNAPSHOT | reason={reason} | pnl=${realized_pnl:,.0f} "
        f"| days_held={days_held} | ivr_at_close={iv_rank_at_close} "
        f"| spot_at_close={spot_at_close} | payload={json.dumps(payload)}"
    )

    try:
        import uuid
        conn.execute("""
            INSERT OR IGNORE INTO lifecycle_events
                (id, position_id, event_type, occurred_at,
                 spot_price, pnl_at_event, narrative)
            VALUES (?, ?, 'CLOSED', datetime('now'), ?, ?, ?)
        """, (
            "LCE-CLOSE-" + uuid.uuid4().hex[:8].upper(),
            position_id,
            spot_at_close,
            realized_pnl,
            narrative,
        ))
        conn.commit()
    except Exception:
        import traceback; traceback.print_exc()
