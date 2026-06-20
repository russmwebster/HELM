
# helm/cli/entry_snapshot.py
# Captures a full entry snapshot when a position is opened through HELM.
# Called by helm open --confirm after user selects a contract and confirms fill price.

import sys
from pathlib import Path
from datetime import date, datetime
from typing import Optional
from contextlib import nullcontext

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
    # Contract liquidity at entry
    open_interest: Optional[int] = None,
    bid_ask_spread: Optional[float] = None,
    bid_ask_spread_pct: Optional[float] = None,
    conn=None,
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

    with (transaction() if conn is None else nullcontext(conn)) as conn:
        conn.execute("""
            INSERT INTO entry_snapshots (
                id, position_id, leg_id, snapshot_at,
                spot_price, spot_52wk_high, spot_52wk_low,
                iv_current, delta, gamma, theta, vega,
                atr_14, atr_strikes_otm, dte,
                days_to_earnings, premium_collected,
                open_interest, bid_ask_spread, bid_ask_spread_pct,
                theta_per_day, settings_snapshot, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            snap_id, position_id, leg_id, now,
            spot_price, week_52_high, week_52_low,
            iv_current, delta, gamma, theta, vega,
            atr_14, atr_strikes_otm, dte,
            days_to_earnings, premium_collected,
            open_interest, bid_ask_spread, bid_ask_spread_pct,
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
    book: str = 'REAL',
) -> tuple[str, str, str]:
    """
    Create position, leg, and entry snapshot in one atomic operation.
    Returns (position_id, leg_id, snapshot_id).
    """
    account_id = get_active_account()
    now = datetime.now().isoformat()
    today = date.today().isoformat()

    # Fetch company name for display
    try:
        import yfinance as _yf
        company_name = (_yf.Ticker(ticker).fast_info.display_name or "")
    except Exception:
        company_name = ""

    # Net premium: SHORT = positive (collected), LONG = negative (paid)
    direction = contract["direction"]
    mid = fill_price
    net_premium = round(mid * 100 * contracts, 2)
    if direction == "LONG":
        net_premium = -net_premium

    # Atomic (HELM-013): position + leg + OPENED event + snapshot in one
    # transaction. Any failure rolls the whole open back -- no orphans.
    with transaction() as conn:
        # Create position as OPEN -- --confirm means the trade was executed
        pos = Position.create(
            account_id=account_id,
            strategy=strategy,
            ticker=ticker,
            company_name=company_name,
            status='OPEN',
            opened_at=now,
            total_contracts=contracts,
            net_premium=net_premium,
            book=book,
            notes=f"Pending execution — opened via HELM on {today}",
            conn=conn,
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
            conn=conn,
        )

        # Log lifecycle event
        LifecycleEvent.record(
            position_id=pos.id,
            event_type="OPENED",
            narrative=f"Trade decision: {contracts}x {leg_role} ${contract['strike']} {contract['expiration']} @ ${fill_price:.2f} mid — pending execution in Fidelity",
            conn=conn,
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
            open_interest=contract.get("oi"),
            bid_ask_spread=contract.get("spread"),
            bid_ask_spread_pct=contract.get("spread_pct"),
            conn=conn,
        )

    # Close the decision->position link on the originating scan signal, if one
    # exists (latest unlinked russ_intent='OPEN' for this ticker). Best-effort:
    # a real position must never fail to open because of this stamp. Kept OUTSIDE
    # the transaction so this stamp can never roll back a real open.
    if book == 'REAL':
        try:
            from helm.models.signal import Signal
            Signal.link_position_opened(ticker, strategy, pos.id)
        except Exception:
            pass

    return pos.id, leg.id, snap_id


def open_multileg_with_snapshot(
    ticker: str,
    strategy: str,
    legs: list,
    contracts: int,
    spot: Optional[float] = None,
    scan_data: Optional[dict] = None,
    book: str = 'REAL',
    position_fields: Optional[dict] = None,
    pricing_source: Optional[str] = None,
) -> tuple:
    """
    Open a multi-leg position: one Position, N legs, ONE entry snapshot
    (entry_snapshots is UNIQUE per position; the snapshot anchors to the short
    leg, whose greeks define the position), and one OPENED lifecycle event.
    Sibling to open_position_with_snapshot; the single-leg live path is untouched.

    legs: list of dicts, each with keys:
        direction ('SHORT'|'LONG'), opt_type ('PUT'|'CALL'), strike,
        expiration, fill_price, and optionally delta/theta/gamma/vega/iv/dte/spot.
    net_premium is derived from the legs: SHORT adds (credit), LONG subtracts
    (debit), x100 x contracts -- a credit spread nets positive.
    position_fields supplies strategy-level Position columns (spread_width,
    breakeven_low/high, max_profit, max_loss, credit_to_width_ratio, ...).
    pricing_source ('ibkr'|'yfinance') is recorded in notes for source-bias
    auditing. Returns (position_id, [leg_ids], [snapshot_id]).
    """
    if not legs:
        return None, [], []

    account_id = get_active_account()
    now = datetime.now().isoformat()
    today = date.today().isoformat()

    try:
        import yfinance as _yf
        company_name = (_yf.Ticker(ticker).fast_info.display_name or "")
    except Exception:
        company_name = ""

    # Net premium from the legs: + for SHORT (collected), - for LONG (paid).
    net_premium = 0.0
    for lg in legs:
        sign = 1 if lg["direction"] == "SHORT" else -1
        net_premium += sign * float(lg["fill_price"]) * 100 * contracts
    net_premium = round(net_premium, 2)

    note = f"Pending execution - opened via HELM on {today}"
    if pricing_source:
        note += f" | priced: {pricing_source}"

    pf = dict(position_fields or {})

    # Atomic open (HELM-013): position + N legs + ONE snapshot + OPENED event in
    # a single transaction. Any failure rolls the whole open back -- no orphans.
    with transaction() as conn:
        pos = Position.create(
            account_id=account_id,
            strategy=strategy,
            ticker=ticker,
            company_name=company_name,
            status='OPEN',
            opened_at=now,
            total_contracts=contracts,
            net_premium=net_premium,
            book=book,
            notes=note,
            conn=conn,
            **pf,
        )

        # Portfolio value for the entry snapshot.
        account_conn = get_conn()
        acct = account_conn.execute(
            "SELECT portfolio_value FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        portfolio_value = acct["portfolio_value"] if acct else None
        account_conn.close()

        leg_ids = []
        role_parts = []
        leg_pairs = []  # (lg_dict, leg_id)
        for lg in legs:
            direction = lg["direction"]
            opt_type = lg["opt_type"]
            leg_role = ("SHORT_" if direction == "SHORT" else "LONG_") + opt_type
            leg = Leg.create(
                position_id=pos.id,
                leg_role=leg_role,
                direction=direction,
                open_price=lg["fill_price"],
                open_date=today,
                option_type=opt_type,
                strike=lg["strike"],
                expiration=lg["expiration"],
                contracts=contracts,
                entry_delta=lg.get("delta"),
                conn=conn,
            )
            leg_ids.append(leg.id)
            leg_pairs.append((lg, leg.id))
            role_parts.append(f"{leg_role} ${lg['strike']} @ ${float(lg['fill_price']):.2f}")

        # entry_snapshots is UNIQUE per position -> one snapshot, anchored to the
        # SHORT leg (its greeks define the position); fall back to the first leg.
        p_lg, p_leg_id = next(
            ((lg, lid) for (lg, lid) in leg_pairs if lg["direction"] == "SHORT"),
            leg_pairs[0],
        )
        snap_id = capture_entry_snapshot(
            position_id=pos.id,
            leg_id=p_leg_id,
            spot_price=(p_lg["spot"] if p_lg.get("spot") is not None else spot),
            delta=p_lg.get("delta"),
            theta=p_lg.get("theta"),
            gamma=p_lg.get("gamma"),
            vega=p_lg.get("vega"),
            iv_current=p_lg.get("iv"),
            dte=p_lg.get("dte"),
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
            open_interest=p_lg.get("oi"),
            bid_ask_spread=p_lg.get("spread"),
            bid_ask_spread_pct=p_lg.get("spread_pct"),
            conn=conn,
        )
        snap_ids = [snap_id]

        LifecycleEvent.record(
            position_id=pos.id,
            event_type="OPENED",
            narrative=(
                f"Trade decision: {contracts}x {strategy} "
                f"[{' / '.join(role_parts)}] "
                f"-- net {'credit' if net_premium >= 0 else 'debit'} ${abs(net_premium):.2f} "
                f"-- pending execution in Fidelity"
            ),
            conn=conn,
        )

    # Close the decision->position link on the originating scan signal, if one
    # matches (latest unlinked signal for this ticker whose top_strategy equals
    # the opened strategy). Best-effort, OUTSIDE the transaction: a real open
    # must never fail or roll back because of this stamp.
    if book == 'REAL':
        try:
            from helm.models.signal import Signal
            Signal.link_position_opened(ticker, strategy, pos.id)
        except Exception:
            pass

    return pos.id, leg_ids, snap_ids
