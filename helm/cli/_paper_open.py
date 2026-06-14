"""Paper-book open (policy v0).

HELM auto-opens its own top-ranked contract for a candidate Russ passed on,
booked to book='PAPER'. Reuses the live open path's fetch+rank so paper entries
use the same real-chain data, then fills conservatively to match Russ's real
practice: short -> bid, long -> ask. The caller supplies `spot` (the underlying
price at decision time, e.g. the originating signal's spot_price) for the entry
snapshot.

Returns None (no entry) when spot is missing, the chain yields no viable
contract, the data came from a stale fallback rather than a real IBKR chain
(fidelity rule -- paper must price off the same real chain as live opens), or
there is no quote.
"""
from __future__ import annotations

from typing import Optional

from helm.cli.open_cmd import evaluate_contracts, evaluate_spreads, evaluate_debit_spreads, evaluate_condors, evaluate_diagonals, STRATEGY_CONFIG
from helm.cli.entry_snapshot import open_position_with_snapshot, open_multileg_with_snapshot


def paper_open_one(ticker: str, strategy: str, spot: Optional[float],
                   dte_target: Optional[int] = None, top_n: int = 3,
                   contracts: int = 1) -> Optional[str]:
    """Open HELM's top-ranked contract for (ticker, strategy) as a PAPER
    position. Returns the new position id, or None if nothing tradable."""
    if spot is None:
        return None
    config = STRATEGY_CONFIG[strategy]
    ranked = evaluate_contracts(ticker, strategy, config, dte_target, top_n)
    if not ranked:
        return None
    top = dict(ranked[0])
    if not str(top.get("source", "")).startswith("ibkr"):
        return None
    fill = top["bid"] if config["direction"] == "SHORT" else top["ask"]
    if not fill:
        return None
    top["spot"] = spot
    pos_id, _leg_id, _snap_id = open_position_with_snapshot(
        ticker=ticker,
        strategy=strategy,
        contract=top,
        fill_price=fill,
        contracts=contracts,
        scan_data=None,
        book="PAPER",
    )
    return pos_id


def paper_open_spread_one(ticker: str, strategy: str, spot: Optional[float],
                          dte_target: Optional[int] = None, top_n: int = 6,
                          contracts: int = 1) -> Optional[str]:
    """Open HELM's top-ranked vertical credit spread (BULL_PUT or BEAR_CALL)
    for (ticker, strategy) as a PAPER position. Mirrors paper_open_one but for
    two legs: fills conservatively (short -> bid, long -> ask) and books both
    legs under one position via open_multileg_with_snapshot.

    Returns the new position id, or None if nothing tradable (no spot, no
    ranked spread, or the conservative net credit is <= 0)."""
    if spot is None:
        return None
    config = STRATEGY_CONFIG[strategy]
    ranked = evaluate_spreads(ticker, strategy, config, dte_target, top_n)
    if not ranked:
        return None
    top = dict(ranked[0])

    opt_type = top["opt_type"]
    short_fill = top["short_bid"]
    long_fill = top["long_ask"]
    if not short_fill or not long_fill:
        return None

    net_credit = round(short_fill - long_fill, 2)
    if net_credit <= 0:
        return None

    width = top["width"]
    legs = [
        {
            "direction": "SHORT", "opt_type": opt_type,
            "strike": top["short_strike"], "expiration": top["expiration"],
            "fill_price": short_fill, "delta": top.get("delta"),
            "iv": top.get("iv"), "dte": top.get("dte"), "spot": spot,
        },
        {
            "direction": "LONG", "opt_type": opt_type,
            "strike": top["long_strike"], "expiration": top["expiration"],
            "fill_price": long_fill, "delta": None,
            "iv": top.get("iv"), "dte": top.get("dte"), "spot": spot,
        },
    ]

    position_fields = {
        "spread_width": width,
        "max_profit": round(net_credit * 100 * contracts, 2),
        "max_loss": round((width - net_credit) * 100 * contracts, 2),
        "credit_to_width_ratio": round(net_credit / width, 4) if width else None,
    }
    if opt_type == "PUT":
        position_fields["breakeven_low"] = round(top["short_strike"] - net_credit, 2)
    else:
        position_fields["breakeven_high"] = round(top["short_strike"] + net_credit, 2)

    pos_id, _leg_ids, _snap_ids = open_multileg_with_snapshot(
        ticker=ticker,
        strategy=strategy,
        legs=legs,
        contracts=contracts,
        spot=spot,
        scan_data=None,
        book="PAPER",
        position_fields=position_fields,
        pricing_source="yfinance",
    )
    return pos_id


def paper_open_diagonal_one(ticker: str, strategy: str, spot: Optional[float],
                            dte_target: Optional[int] = None, top_n: int = 6,
                            contracts: int = 1) -> Optional[str]:
    """Open HELM's top-ranked CALL diagonal (DIAGONAL or PMCC) as a PAPER
    position. Two legs at DIFFERENT expiries: long deeper-ITM back-month call
    (fill -> ask), short nearer-term higher-strike call (fill -> bid), so
    net_premium comes out a worst-case debit. max_loss = net debit; max_profit
    is left NULL (path-dependent: the legs don't co-expire). Returns the new
    position id, or None if nothing tradable."""
    if spot is None:
        return None
    config = STRATEGY_CONFIG[strategy]
    ranked = evaluate_diagonals(ticker, strategy, config, dte_target, top_n)
    if not ranked:
        return None
    top = dict(ranked[0])

    long_fill = top.get("long_ask")
    short_fill = top.get("short_bid")
    if not long_fill or short_fill is None:
        return None
    net_debit = round(long_fill - short_fill, 2)
    if net_debit <= 0:
        return None

    legs = [
        {"direction": "LONG", "opt_type": "CALL",
         "strike": top["long_strike"], "expiration": top["long_exp"],
         "fill_price": long_fill, "delta": top.get("long_delta"),
         "iv": top.get("long_iv"), "dte": top.get("long_dte"), "spot": spot},
        {"direction": "SHORT", "opt_type": "CALL",
         "strike": top["short_strike"], "expiration": top["short_exp"],
         "fill_price": short_fill, "delta": top.get("short_delta"),
         "iv": top.get("short_iv"), "dte": top.get("short_dte"), "spot": spot},
    ]

    position_fields = {
        "spread_width": top["width"],
        "max_loss": round(net_debit * 100 * contracts, 2),
        "breakeven_high": round(top["short_strike"] + net_debit, 2),
    }

    pos_id, _leg_ids, _snap_ids = open_multileg_with_snapshot(
        ticker=ticker, strategy=strategy, legs=legs, contracts=contracts,
        spot=spot, scan_data=None, book="PAPER",
        position_fields=position_fields, pricing_source="yfinance",
    )
    return pos_id


def paper_open_debit_spread_one(ticker: str, strategy: str, spot: Optional[float],
                                dte_target: Optional[int] = None, top_n: int = 6,
                                contracts: int = 1) -> Optional[str]:
    """Open HELM's top-ranked vertical DEBIT spread (BEAR_PUT / BULL_CALL) for
    (ticker, strategy) as a PAPER position. Mirrors paper_open_spread_one but
    the position pays: fills conservatively (long -> ask, short -> bid) so the
    net debit is the worst-case cost, books both legs under one position via
    open_multileg_with_snapshot. net_premium comes out negative (a debit).

    Returns the new position id, or None if nothing tradable (no spot, no
    ranked spread, or the conservative net debit / max profit is <= 0)."""
    if spot is None:
        return None
    config = STRATEGY_CONFIG[strategy]
    ranked = evaluate_debit_spreads(ticker, strategy, config, dte_target, top_n)
    if not ranked:
        return None
    top = dict(ranked[0])

    opt_type = config["option_type"]
    long_fill = top.get("long_ask")
    short_fill = top.get("short_bid")
    if not long_fill or short_fill is None:
        return None

    net_debit = round(long_fill - short_fill, 2)
    if net_debit <= 0:
        return None
    width = top["width"]
    max_profit = round(width - net_debit, 2)
    if max_profit <= 0:
        return None

    legs = [
        {
            "direction": "LONG", "opt_type": opt_type,
            "strike": top["long_strike"], "expiration": top["exp"],
            "fill_price": long_fill, "delta": None,
            "iv": None, "dte": top.get("dte"), "spot": spot,
        },
        {
            "direction": "SHORT", "opt_type": opt_type,
            "strike": top["short_strike"], "expiration": top["exp"],
            "fill_price": short_fill, "delta": None,
            "iv": None, "dte": top.get("dte"), "spot": spot,
        },
    ]

    position_fields = {
        "spread_width": width,
        "max_profit": round(max_profit * 100 * contracts, 2),
        "max_loss": round(net_debit * 100 * contracts, 2),
    }
    if opt_type == "PUT":
        position_fields["breakeven_low"] = round(top["long_strike"] - net_debit, 2)
    else:
        position_fields["breakeven_high"] = round(top["long_strike"] + net_debit, 2)

    pos_id, _leg_ids, _snap_ids = open_multileg_with_snapshot(
        ticker=ticker,
        strategy=strategy,
        legs=legs,
        contracts=contracts,
        spot=spot,
        scan_data=None,
        book="PAPER",
        position_fields=position_fields,
        pricing_source="yfinance",
    )
    return pos_id


def paper_open_condor_one(ticker: str, strategy: str, spot: Optional[float],
                          dte_target: Optional[int] = None, top_n: int = 6,
                          contracts: int = 1) -> Optional[str]:
    """Open HELM's top-ranked IRON_CONDOR for (ticker, strategy) as a PAPER
    position. Four legs (short put / long put / short call / long call) booked
    under one position via open_multileg_with_snapshot. Fills conservatively
    (each short -> its bid, each long -> its ask); net_premium is the resulting
    credit.

    Returns the new position id, or None if nothing tradable (no spot, no ranked
    condor, either wing's conservative credit <= 0, or max loss <= 0)."""
    if spot is None:
        return None
    config = STRATEGY_CONFIG[strategy]
    ranked = evaluate_condors(ticker, strategy, config, dte_target, top_n)
    if not ranked:
        return None
    top = dict(ranked[0])

    sp_fill = top.get("short_put_bid")
    lp_fill = top.get("long_put_ask")
    sc_fill = top.get("short_call_bid")
    lc_fill = top.get("long_call_ask")
    if not sp_fill or not lp_fill or not sc_fill or not lc_fill:
        return None

    put_credit = round(sp_fill - lp_fill, 2)
    call_credit = round(sc_fill - lc_fill, 2)
    if put_credit <= 0 or call_credit <= 0:
        return None
    net_credit = round(put_credit + call_credit, 2)

    put_width = round(top["short_put"] - top["long_put"], 2)
    call_width = round(top["long_call"] - top["short_call"], 2)
    width = max(put_width, call_width)
    max_loss = round(width - net_credit, 2)
    if max_loss <= 0:
        return None

    exp = top["expiration"]
    dte = top.get("dte")
    legs = [
        {
            "direction": "SHORT", "opt_type": "PUT",
            "strike": top["short_put"], "expiration": exp,
            "fill_price": sp_fill, "delta": top.get("put_delta"),
            "iv": top.get("put_iv"), "dte": dte, "spot": spot,
        },
        {
            "direction": "LONG", "opt_type": "PUT",
            "strike": top["long_put"], "expiration": exp,
            "fill_price": lp_fill, "delta": None,
            "iv": None, "dte": dte, "spot": spot,
        },
        {
            "direction": "SHORT", "opt_type": "CALL",
            "strike": top["short_call"], "expiration": exp,
            "fill_price": sc_fill, "delta": top.get("call_delta"),
            "iv": top.get("call_iv"), "dte": dte, "spot": spot,
        },
        {
            "direction": "LONG", "opt_type": "CALL",
            "strike": top["long_call"], "expiration": exp,
            "fill_price": lc_fill, "delta": None,
            "iv": None, "dte": dte, "spot": spot,
        },
    ]

    position_fields = {
        "spread_width": width,
        "max_profit": round(net_credit * 100 * contracts, 2),
        "max_loss": round(max_loss * 100 * contracts, 2),
        "credit_to_width_ratio": round(net_credit / width, 4) if width else None,
        "breakeven_low": round(top["short_put"] - net_credit, 2),
        "breakeven_high": round(top["short_call"] + net_credit, 2),
    }

    pos_id, _leg_ids, _snap_ids = open_multileg_with_snapshot(
        ticker=ticker,
        strategy=strategy,
        legs=legs,
        contracts=contracts,
        spot=spot,
        scan_data=None,
        book="PAPER",
        position_fields=position_fields,
        pricing_source="yfinance",
    )
    return pos_id
