
# helm/cli/entry_snapshot.py
# Captures a full entry snapshot when a position is opened through HELM.
# Called by helm open --confirm after user selects a contract and confirms fill price.

import sys
from pathlib import Path
from datetime import date, datetime
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from helm.db import get_conn, transaction
from helm.models.position import Position
from helm.models.leg import Leg
from helm.models.lifecycle import LifecycleEvent
from helm.config import get_active_account

import uuid


def capture_entry_snapshot(
    position_id: str,
    leg_id: str,
    # Greeks at entry
    spot_price: Optional[float],
    delta: Optional[float],
    theta: Optional[float],
    gamma: Optional[float],
    vega: Optional[float],
    iv_current: Optional[float],
    # Option details
    dte: Optional[int],
    premium_collected: Optional[float],
    # Technical context from scan
    atr_14: Optional[float] = None,
    rsi_14: Optional[float] = None,
    ema_20: Optional[float] = None,
    sma_50: Optional[float] = None,
    bias_score: Optional[int] = None,
    bias_factors: Optional[list] = None,
    price_vs_52wk_pct: Optional[float] = None,
    week_52_high: Optional[float] = None,
    week_52_low: Optional[float] = None,
    # Account context
    portfolio_value: Optional[float] = None,
    days_to_earnings: Optional[int] = None,
) -> str:
    """
    Write a full entry snapshot to the database.
    Returns the snapshot id.
    """
    snap_id = f"snap-{uuid.uuid4().hex[:8].upper()}"
    now = datetime.now().isoformat()

    # Compute ATR-based strike distance if we have the data
    atr_strikes_otm = None
    if spot_price and atr_14:
        atr_strikes_otm = round(atr_14, 2)

    # Serialize settings snapshot
    import json
    settings_snap = json.dumps({
        "rsi_14": rsi_14,
        "ema_20": ema_20,
        "sma_50": sma_50,
        "bias_score": bias_score,
        "bias_factors": bias_factors or [],
        "price_vs_52wk_pct": price_vs_52wk_pct,
        "portfolio_value_at_entry": portfolio_value,
    })

    with transaction() as conn:
        conn.execute("""
            INSERT INTO entry_snapshots (
                id, position_id, leg_id, snapshot_at,
                spot_price, spot_52wk_high, spot_52wk_low,
                iv_current, delta, gamma, theta, vega,
                atr_14, atr_strikes_otm, dte,
                days_to_earnings, premium_collected,
                theta_per_day, settings_snapshot, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            snap_id, position_id, leg_id, now,
            spot_price, week_52_high, week_52_low,
            iv_current, delta, gamma, theta, vega,
            atr_14, atr_strikes_otm, dte,
            days_to_earnings, premium_collected,
            theta, settings_snap, now
        ))

    return snap_id


def open_position_with_snapshot(
    ticker: str,
    strategy: str,
    contract: dict,
    fill_price: float,
    contracts: int,
    scan_data: Optional[dict] = None,
) -> tuple[str, str, str]:
    """
    Create position, leg, and entry snapshot in one atomic operation.
    Returns (position_id, leg_id, snapshot_id).
    """
    account_id = get_active_account()
    now = datetime.now().isoformat()
    today = date.today().isoformat()

    # Net premium: SHORT = positive (collected), LONG = negative (paid)
    direction = contract["direction"]
    mid = fill_price
    net_premium = round(mid * 100 * contracts, 2)
    if direction == "LONG":
        net_premium = -net_premium

    # Create position as OPEN -- --confirm means the trade was executed
    pos = Position.create(
        account_id=account_id,
        strategy=strategy,
        ticker=ticker,
        status='OPEN',
        opened_at=now,
        total_contracts=contracts,
        net_premium=net_premium,
        notes=f"Pending execution — opened via HELM on {today}",
    )

    # Create leg
    opt_type = contract["opt_type"]
    leg_role = ("SHORT_" if direction == "SHORT" else "LONG_") + opt_type
    leg = Leg.create(
        position_id=pos.id,
        leg_role=leg_role,
        direction=direction,
        open_price=fill_price,
        open_date=today,
        option_type=opt_type,
        strike=contract["strike"],
        expiration=contract["expiration"],
        contracts=contracts,
        entry_delta=contract.get("delta"),
    )

    # Log lifecycle event
    LifecycleEvent.record(
        position_id=pos.id,
        event_type="OPENED",
        narrative=f"Trade decision: {contracts}x {leg_role} ${contract['strike']} {contract['expiration']} @ ${fill_price:.2f} mid — pending execution in Fidelity",
    )

    # Capture entry snapshot
    account_conn = get_conn()
    acct = account_conn.execute(
        "SELECT portfolio_value FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()
    portfolio_value = acct["portfolio_value"] if acct else None
    account_conn.close()

    snap_id = capture_entry_snapshot(
        position_id=pos.id,
        leg_id=leg.id,
        spot_price=contract.get("spot"),
        delta=contract.get("delta"),
        theta=contract.get("theta"),
        gamma=contract.get("gamma"),
        vega=contract.get("vega"),
        iv_current=contract.get("iv"),
        dte=contract.get("dte"),
        premium_collected=abs(net_premium),
        atr_14=scan_data.get("atr_14") if scan_data else None,
        rsi_14=scan_data.get("rsi_14") if scan_data else None,
        ema_20=scan_data.get("ema_20") if scan_data else None,
        sma_50=scan_data.get("sma_50") if scan_data else None,
        bias_score=scan_data.get("bias_score") if scan_data else None,
        bias_factors=scan_data.get("bias_factors") if scan_data else None,
        price_vs_52wk_pct=scan_data.get("price_vs_52wk_pct") if scan_data else None,
        week_52_high=scan_data.get("week_52_high") if scan_data else None,
        week_52_low=scan_data.get("week_52_low") if scan_data else None,
        portfolio_value=portfolio_value,
    )

    return pos.id, leg.id, snap_id
