"""Paper-book generate (orchestration, policy v0).

Runs the latest scan run's passed-on candidates through HELM's own paper-open
unit, booking one PAPER position per eligible (ticker, strategy). HELM acts on
its own top-ranked contract here; on the live book it only advises.

Scope: strategies that have a paper-open unit, via an EXPLICIT fail-closed
dispatch map (_PAPER_BOOKERS). Single-leg (CSP, COVERED_CALL, LONG_CALL,
LONG_PUT) book via paper_open_one as one contract at a single bid/ask fill;
credit verticals (BULL_PUT_SPREAD, BEAR_CALL_SPREAD) book via
paper_open_spread_one, and the BEAR_PUT_SPREAD debit vertical books via
paper_open_debit_spread_one, all as two conservatively-filled legs. Anything
absent from the map -- PERM and ANY future or unknown strategy -- is skipped with an explicit reason
and counted, never silently dropped, until its booker exists.
Fail-closed is deliberate: an unrecognised strategy is excluded, not booked.

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

open_position_with_snapshot is atomic (HELM-003): position, leg, and snapshot
are written in one operation, so a mid-way failure rolls back rather than
leaving an orphan PAPER position. This orchestration additionally wraps each
ticker, so a per-ticker failure surfaces as a skip reason and the batch
continues. The atomic open is shared with the live path.
"""
from __future__ import annotations

import sqlite3
from collections import Counter

from rich.console import Console

from helm.db import get_conn
from helm.cli.check_cmd import is_market_open
from helm.cli.open_cmd import STRATEGY_CONFIG
from helm.cli._paper_open import paper_open_one, paper_open_spread_one, paper_open_debit_spread_one, paper_open_condor_one, paper_open_diagonal_one, paper_open_straddle_one
from helm.models.position import Position

# Explicit, fail-closed dispatch: strategy -> the paper-open unit that books it.
# Single-leg strategies route to paper_open_one (one contract, one bid/ask fill);
# credit verticals route to paper_open_spread_one and the bear-put debit
# vertical routes to paper_open_debit_spread_one (two legs, conservative fills,
# via open_multileg_with_snapshot). NOT derived from config flags -- strategies
# still without a booker (PERM,
# straddle) live in STRATEGY_CONFIG too and must never slip
# through: anything absent from this map is skipped.
_PAPER_BOOKERS = {
    "CSP": paper_open_one,
    "COVERED_CALL": paper_open_one,
    "LONG_CALL": paper_open_one,
    "LONG_PUT": paper_open_one,
    "BULL_PUT_SPREAD": paper_open_spread_one,
    "BEAR_CALL_SPREAD": paper_open_spread_one,
    "BEAR_PUT_SPREAD": paper_open_debit_spread_one,
    "BULL_CALL_SPREAD": paper_open_debit_spread_one,
    "IRON_CONDOR": paper_open_condor_one,
    "DIAGONAL": paper_open_diagonal_one,
    "PMCC": paper_open_diagonal_one,
    "DIAGONAL_PUT": paper_open_diagonal_one,
    "LONG_STRADDLE": paper_open_straddle_one,
}


def paperable_strategies() -> set:
    """The paperable set: the explicit dispatch keys, intersected with
    STRATEGY_CONFIG so a booker's STRATEGY_CONFIG[strategy] lookup cannot
    KeyError on a misconfigured name."""
    return {s for s in _PAPER_BOOKERS if s in STRATEGY_CONFIG}


# W67 (s91): which screen produced a paper position. Written to
# positions.origin_screen on every booking so the dual-book A/B in
# HELM-101 section 5 has something to group by.
SELL_SCREEN = "SELL_SCREEN"
# HELM-136: names the earnings gate declined but the paper book books anyway,
# from strategy_shadow -- the gate on trial. Grading SELL_GATED against
# SELL_SCREEN is what eventually tells us whether the gate earns its keep.
SELL_GATED = "SELL_GATED"
LC_SCREEN = "LC_SCREEN"


def _scan_from_sig(sig: dict) -> dict:
    """Map an originating scan signal row to the scan_data keys the entry
    snapshot expects, so paper entries capture ATR/RSI/EMA/SMA/bias at open
    (HELM-023 Track B). Fields absent from the signal simply stay None."""
    if not sig:
        return {}
    return {
        'atr_14': sig.get('atr_14'),
        'rsi_14': sig.get('rsi_14'),
        'ema_20': sig.get('ema_20'),
        'sma_50': sig.get('sma_50'),
        'bias_score': sig.get('auto_bias_score'),
        'bias_factors': sig.get('auto_bias_reasoning'),
    }


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


# W75 / HELM-130 (s91): _open_real_tickers() removed, not merely unused.
# Both passes used to skip any ticker with an open REAL position. Russ's call:
# a real options position must not stop the paper book taking its own view on
# the same underlying. The paper book is evidence about the screens, and a
# guard keyed on the real book made it evidence about the portfolio instead --
# 18 of 67 watchlist names were unobservable.
#
# Note what this did NOT remove: addendum section 5 frames "not taken real" as
# a russ_action test, and that test is still in _latest_run_passed_on(). It is
# close to inert across batches -- each scan writes 67 fresh signals at
# PENDING, and only the signal from the scan a position was opened on is marked
# OPEN (33 rows of 5,899 all-time; 0 of the latest 67) -- which is precisely
# why this guard was load-bearing and why removing it leaves nothing behind it.
#
# The (ticker, strategy) paper guard is untouched: paper still will not stack a
# duplicate on itself.


def _book_and_stamp(sig: dict, ticker: str, strategy: str, spot, origin: str):
    """Book one paper position and stamp its provenance.

    Returns (position_id, None) on success, or (None, reason) on a skip.

    Shared by both passes so the sell wing and the buy wing cannot drift apart
    on error handling, signal linkage, provenance or vol capture. The paper
    booker inverting long-call direction for three months (W13 / HELM-120)
    happened because a fix landed on one path and not its sibling; one body
    here means there is no sibling to miss.
    """
    try:
        pos_id = _PAPER_BOOKERS[strategy](ticker, strategy, spot,
                                          scan_data=_scan_from_sig(sig))
    except Exception as exc:  # one bad ticker must not kill the batch
        return None, f"error: {type(exc).__name__}: {exc}"

    if pos_id is None:
        return None, "no viable real-chain contract (fidelity skip)"

    # HELM-049: link this paper position to its originating scan signal.
    # Non-destructive -- stamps positions.signal_id only, never consuming the
    # signal (a later real open on the same ticker still links). Best-effort;
    # a linkage stamp must never fail the batch. Outcome back-prop stays REAL-
    # only (close_cmd), so this reference can't contaminate signal outcomes.
    #
    # HELM-121 (W67, s91): origin_screen rides the same statement. The column
    # and a capture_entry_thesis argument both landed in s90, but no caller
    # ever passed it -- measured 2026-07-27, all 285 positions across both
    # books read NULL, so the dual-book A/B had nothing to group by. Stamped
    # here rather than through six booker signatures because this block
    # already runs for every booked position, whatever booked it.
    _sig_id = sig.get("id")
    try:
        from helm.db import transaction
        with transaction() as _conn:
            if _sig_id:
                _conn.execute(
                    "UPDATE positions SET signal_id = ?, origin_screen = ? "
                    "WHERE id = ?",
                    (_sig_id, origin, pos_id),
                )
            else:
                _conn.execute(
                    "UPDATE positions SET origin_screen = ? WHERE id = ?",
                    (origin, pos_id),
                )
    except Exception:
        pass

    # HELM-081: best-effort vol-context capture (hv_30d + skew) onto the
    # entry snapshot, right after booking. Never blocks the paper batch.
    try:
        from helm.vol_context import backfill_entry_vol
        backfill_entry_vol(pos_id, ticker)
    except Exception:
        pass

    return pos_id, None


def _lc_routable_survivors() -> list:
    """Screen survivors that sit far enough inside the G3 gate to route.

    W70 / HELM-125: passing the screen is necessary but not sufficient. A name
    must also clear G3 by at least ROUTE_MARGIN, so a position is never opened
    on a name sitting on the line -- GE crossed the gate on a ratio move of
    0.003 with implied vol unchanged, and booking off that would put a rounding
    step into the calibration log as a decision.

    Fails closed: no stored ratio, no routing. The screen cannot have passed a
    name on G3 without one, so a NULL here means something upstream is wrong and
    silence is the safe reading.

    Survivors already taken real are excluded by russ_action, exactly as the
    sell-side field is in _latest_run_passed_on(). Note that filter is close to
    inert across batches (HELM-130) -- it is kept for parity, not relied on.
    """
    from helm.lc_screen import G3_RATIO_MAX, ROUTE_MARGIN

    threshold = G3_RATIO_MAX - ROUTE_MARGIN
    conn = get_conn()
    try:
        conn.row_factory = sqlite3.Row
        latest = conn.execute(
            "SELECT MAX(generated_at) FROM signals WHERE lc_screen_pass IS NOT NULL"
        ).fetchone()[0]
        if not latest:
            return []
        rows = conn.execute(
            "SELECT * FROM signals WHERE generated_at = ? AND lc_screen_pass "
            "  AND (russ_action IS NULL OR russ_action != 'OPEN') "
            "ORDER BY lc_screen_rank, ticker",
            (latest,),
        ).fetchall()

        out = []
        for r in rows:
            ratio = r["iv_hv90_ratio"]
            if ratio is None:
                continue
            try:
                if float(ratio) <= threshold:
                    out.append(dict(r))
            except (TypeError, ValueError):
                continue
        return out
    finally:
        conn.close()


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

    eligible = paperable_strategies()
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
        origin = SELL_SCREEN
        # HELM-136: gated rows book their shadow route under SELL_GATED.
        if strategy == "NO_SELL_EARNINGS":
            _shadow = sig.get("strategy_shadow")
            if not _shadow:
                skipped.append((ticker, strategy, "gated, no shadow route"))
                continue
            strategy = _shadow
            origin = SELL_GATED
        if strategy not in eligible:
            skipped.append((ticker, strategy, "multi-leg / unsupported (deferred to v2)"))
            continue
        if (ticker, strategy) in seen:
            skipped.append((ticker, strategy, "already open in paper book"))
            continue
        if spot is None:
            skipped.append((ticker, strategy, "no scan spot_price"))
            continue

        pos_id, reason = _book_and_stamp(sig, ticker, strategy, spot, origin)
        if pos_id is None:
            skipped.append((ticker, strategy, reason))
            continue

        booked.append((ticker, strategy, pos_id))
        seen.add((ticker, strategy))

    # W67 / HELM-101 section 5 (s91): the buy wing. LC-screen survivors not
    # taken real are booked to PAPER at the step-4 config, graded by the four
    # v2 long-exit verdicts the paper exit agent already carries.
    #
    # Its own pass, deliberately. Writing top_strategy = 'LONG_CALL' onto the
    # signal would book with no further code change -- the s84 review said so --
    # and that is the argument against it: the two screens are a dual-book A/B
    # on the same scan, so overwriting the sell-side route would collapse them
    # into one and leave nothing between a screen bug and the paper book.
    #
    # Same guards as the sell pass (open in the real book, already open in
    # paper, no spot), plus the W70 routing margin inside _lc_routable_survivors.
    lc_field = _lc_routable_survivors()
    for sig in lc_field:
        ticker = sig.get("ticker")
        spot = sig.get("spot_price")
        strategy = "LONG_CALL"

        if strategy not in eligible:
            skipped.append((ticker, strategy, "LONG_CALL not paperable"))
            continue
        if (ticker, strategy) in seen:
            skipped.append((ticker, strategy, "already open in paper book"))
            continue
        if spot is None:
            skipped.append((ticker, strategy, "no scan spot_price"))
            continue

        pos_id, reason = _book_and_stamp(sig, ticker, strategy, spot, LC_SCREEN)
        if pos_id is None:
            skipped.append((ticker, strategy, reason))
            continue

        booked.append((ticker, strategy, pos_id))
        seen.add((ticker, strategy))

    _print_summary(console, field, lc_field, booked, skipped)
    # W21's lesson, applied here before it can bite: both fields are reported
    # separately rather than summed, so "field" cannot quietly come to mean
    # two different populations added together.
    return {"status": "ok", "field": len(field), "lc_field": len(lc_field),
            "booked": booked, "skipped": skipped}


def _print_summary(console: Console, field: list, lc_field: list,
                   booked: list, skipped: list) -> None:
    console.print()
    console.print(
        f"[bold cyan]Paper generate[/bold cyan] - latest run, "
        f"{len(field)} passed-on candidate(s), "
        f"{len(lc_field)} confirmed buy-screen survivor(s)"
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
