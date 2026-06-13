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

from helm.cli.open_cmd import evaluate_contracts, STRATEGY_CONFIG
from helm.cli.entry_snapshot import open_position_with_snapshot


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
