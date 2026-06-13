"""Paper-book generate (orchestration, policy v0).

Runs the latest scan run's passed-on candidates through HELM's own paper-open
unit, booking one PAPER position per eligible (ticker, strategy). HELM acts on
its own top-ranked contract here; on the live book it only advises.

Scope (v1): SINGLE-LEG strategies only, via an EXPLICIT fail-closed allowlist
(CSP, COVERED_CALL, LONG_CALL, LONG_PUT) -- the strategies paper_open_one can
faithfully book as a single contract at a single bid/ask fill. Anything not on
the list -- Iron Condor, diagonals (DIAGONAL/DIAGONAL_PUT/PMCC), the credit and
debit spreads, straddle, PERM, and ANY future or unknown strategy -- is skipped
with an explicit reason and counted, never silently dropped, until a multi-leg
paper-open unit exists. Fail-closed is deliberate: an unrecognised strategy is
excluded, not accidentally booked as a broken single leg.

Guards:
  - RTH: the whole run is gated on is_market_open(). Market closed -> book
    nothing (paper must never price off frozen/close data).
  - Fidelity: paper_open_one itself returns None unless the contract came from a
    real IBKR chain, so a yfinance fallback (e.g. gateway down) books nothing.
  - Dedupe: one open PAPER position per (ticker, strategy); re-runs do not
    double-book.
  - Robustness: evaluate_contracts (inside paper_open_one) can raise on
    no-price / no-expiries; every call is wrapped so one bad ticker cannot kill
    the batch -- the failure is surfaced as a skip reason.

Passed-on field = the latest run's signals where russ_action is not 'OPEN'
(i.e. Russ did not turn the candidate into a real position).

Known limitation: open_position_with_snapshot is not atomic, so a mid-way
failure can leave an orphan PAPER position. This orchestration CONTAINS such a
failure (the batch continues and the error is reported) but does not clean up a
partial open -- atomic open is a separate fix at the open_position_with_snapshot
level, shared with the live path.
"""
from __future__ import annotations

import sqlite3
from collections import Counter

from rich.console import Console

from helm.db import get_conn
from helm.cli.check_cmd import is_market_open
from helm.cli.open_cmd import STRATEGY_CONFIG
from helm.cli._paper_open import paper_open_one
from helm.models.position import Position

# Explicit, fail-closed: only these single-option strategies are paperable in
# v1. NOT derived from config flags -- multi-leg strategies (IRON_CONDOR,
# DIAGONAL, DIAGONAL_PUT, PMCC, PERM, spreads, straddle) live in STRATEGY_CONFIG
# too and must never slip through.
_SINGLE_LEG_ALLOWLIST = ("CSP", "COVERED_CALL", "LONG_CALL", "LONG_PUT")


def single_leg_strategies() -> set:
    """The v1 paperable set: the explicit allowlist, intersected with
    STRATEGY_CONFIG so paper_open_one's STRATEGY_CONFIG[strategy] cannot
    KeyError on a misconfigured name."""
    return {s for s in _SINGLE_LEG_ALLOWLIST if s in STRATEGY_CONFIG}


def _latest_run_passed_on() -> list:
    """Latest scan run's passed-on signals (russ_action not 'OPEN').
    Returns a list of dicts, one per signal."""
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM signals "
            "WHERE generated_at = (SELECT MAX(generated_at) FROM signals) "
            "  AND (russ_action IS NULL OR russ_action != 'OPEN') "
            "ORDER BY ticker"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _open_paper_keys() -> set:
    """(ticker, strategy) pairs already open in the PAPER book."""
    return {(p.ticker, p.strategy) for p in Position.open_positions(book="PAPER")}


def paper_generate() -> dict:
    """Open HELM's paper picks for the latest run's passed-on, single-leg field.
    Returns a summary dict; prints a visible summary."""
    console = Console()

    if not is_market_open():
        console.print(
            "[yellow]Market closed - paper generate skipped "
            "(paper must not price off frozen/close data).[/yellow]"
        )
        return {"status": "skipped_market_closed", "field": 0,
                "booked": [], "skipped": []}

    eligible = single_leg_strategies()
    seen = _open_paper_keys()
    field = _latest_run_passed_on()

    booked = []           # (ticker, strategy, position_id)
    skipped = []          # (ticker, strategy, reason)

    for sig in field:
        ticker = sig.get("ticker")
        strategy = sig.get("top_strategy")
        spot = sig.get("spot_price")

        if not strategy:
            skipped.append((ticker, strategy, "no top_strategy on signal"))
            continue
        if strategy not in eligible:
            skipped.append((ticker, strategy, "multi-leg / unsupported (deferred to v2)"))
            continue
        if (ticker, strategy) in seen:
            skipped.append((ticker, strategy, "already open in paper book"))
            continue
        if spot is None:
            skipped.append((ticker, strategy, "no scan spot_price"))
            continue

        try:
            pos_id = paper_open_one(ticker, strategy, spot)
        except Exception as exc:  # one bad ticker must not kill the batch
            skipped.append((ticker, strategy, f"error: {type(exc).__name__}: {exc}"))
            continue

        if pos_id is None:
            skipped.append((ticker, strategy, "no viable real-chain contract (fidelity skip)"))
            continue

        booked.append((ticker, strategy, pos_id))
        seen.add((ticker, strategy))

    _print_summary(console, field, booked, skipped)
    return {"status": "ok", "field": len(field), "booked": booked, "skipped": skipped}


def _print_summary(console: Console, field: list, booked: list, skipped: list) -> None:
    console.print()
    console.print(
        f"[bold cyan]Paper generate[/bold cyan] - latest run, "
        f"{len(field)} passed-on candidate(s)"
    )
    console.print(
        f"  [green]booked {len(booked)}[/green]   "
        f"[dim]skipped {len(skipped)}[/dim]"
    )
    if booked:
        console.print("[green]Booked:[/green]")
        for ticker, strategy, pos_id in booked:
            console.print(f"  [green]+[/green] {ticker} {strategy}  ->  {pos_id}")
    if skipped:
        console.print("[dim]Skipped (by reason):[/dim]")
        for reason, count in Counter(r for _, _, r in skipped).most_common():
            console.print(f"  [dim]{count:>3}[/dim]  {reason}")
    console.print()
