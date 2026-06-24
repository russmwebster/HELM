# helm/cli/paper_manage.py
# Premium-spine auto-manager for the PAPER book.
# Invoked by `helm check --manage`. Acts ONLY on positions with book='PAPER';
# real positions are never touched.

from typing import Optional

from rich.console import Console
from rich.panel import Panel

from helm.config import get_active_account
from helm.db import get_conn
from helm.models.position import Position
from helm.models.leg import Leg
from helm.cli.check_cmd import fetch_ibkr_option, fetch_yf_data, dte
from helm.cli.close_cmd import _finalize_close
from helm.decision import evaluate

console = Console()

# v1 = pure-options premium-selling family. Directional/debit + diagonal
# families and COVERED_CALL (stock leg) get their own handling later.
PREMIUM_STRATEGIES = {
    'CSP', 'BULL_PUT_SPREAD', 'BEAR_CALL_SPREAD',
    'IRON_CONDOR', 'SHORT_STRANGLE', 'JADE_LIZARD',
}


def _leg_mark(ticker: str, leg) -> Optional[float]:
    """Current mid for one option leg: IBKR first, yfinance fallback. None if no data."""
    ib = fetch_ibkr_option(ticker, leg.expiration, leg.strike, leg.option_type)
    if ib.get('mid'):
        return ib['mid']
    yf = fetch_yf_data(ticker, leg.expiration, leg.strike, leg.option_type)
    return yf.get('mid')


def manage_paper_book(account_id: Optional[str] = None) -> dict:
    """Walk the open PAPER book; hold or close each premium position. Returns a tally."""
    account_id = account_id or get_active_account()
    conn = get_conn()
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM positions WHERE account_id = ? AND book = 'PAPER' AND status = 'OPEN' ORDER BY ticker",
        (account_id,),
    ).fetchall()]
    conn.close()

    if not ids:
        console.print("[dim]Paper auto-manage: no open paper positions.[/dim]")
        return {'closed': 0, 'held': 0, 'skipped': 0}

    console.print()
    console.print(Panel.fit(
        f"[bold cyan]Paper auto-manage[/bold cyan] — {len(ids)} open paper position(s)",
        border_style="cyan",
    ))

    closed = held = skipped = 0
    for pid in ids:
        pos = Position.get(pid)
        if pos is None:
            continue
        if getattr(pos, 'book', 'REAL') != 'PAPER':   # belt-and-suspenders book gate
            continue
        if pos.strategy not in PREMIUM_STRATEGIES:
            console.print(f"  [dim]SKIP[/dim] {pos.ticker} {pos.strategy} — not in v1 premium spine")
            skipped += 1
            continue

        legs = Leg.for_position(pos.id)
        if any((l.option_type in (None, 'STOCK')) for l in legs):
            console.print(f"  [dim]SKIP[/dim] {pos.ticker} {pos.strategy} — stock leg not supported yet")
            skipped += 1
            continue

        marks = {}
        incomplete = False
        for leg in legs:
            m = _leg_mark(pos.ticker, leg)
            if m is None:
                incomplete = True
                break
            marks[leg.id] = m
        if incomplete:
            console.print(f"  [yellow]SKIP[/yellow] {pos.ticker} {pos.strategy} — incomplete marks (never close on bad data)")
            skipped += 1
            continue

        reason, total_pnl = evaluate(pos, legs, marks)
        if reason is None:
            held += 1
            console.print(f"  [green]HOLD[/green]  {pos.ticker:<6} {pos.strategy:<16} P&L ${total_pnl:>8,.0f}")
            continue

        res = _finalize_close(pos, legs, marks, reason=reason)
        closed += 1
        console.print(f"  [bold magenta]CLOSE[/bold magenta] {pos.ticker:<6} {pos.strategy:<16} [{reason}]  Realized ${res['realized_pnl']:>8,.0f}")

    console.print()
    console.print(f"  closed {closed} · held {held} · skipped {skipped}")
    console.print()
    return {'closed': closed, 'held': held, 'skipped': skipped}
