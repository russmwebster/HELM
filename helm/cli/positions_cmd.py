
# helm/cli/positions_cmd.py
# helm positions -- view open positions
#
# Usage:
#   helm positions                    All open positions
#   helm positions --strategy CSP     Filter by strategy
#   helm positions --ticker NVDA      Filter by ticker
#   helm positions --all              Include closed positions
#   helm positions show <id>          Detail view for one position

import sys
from pathlib import Path
from datetime import date, datetime
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from helm.config import get_active_account
from helm.db import get_conn, book_filter

console = Console()

STRATEGY_COLORS = {
    "CSP":            "green",
    "COVERED_CALL":   "cyan",
    "LONG_CALL":      "yellow",
    "LONG_PUT":       "yellow",
    "BULL_PUT_SPREAD":"blue",
    "BEAR_CALL_SPREAD":"red",
    "IRON_CONDOR":    "magenta",
    "DIAGONAL":       "cyan",
    "PMCC":           "cyan",
    "SHORT_STRANGLE": "magenta",
    "JADE_LIZARD":    "green",
    "PERM":           "green",
}

def dte(expiration: str) -> Optional[int]:
    if not expiration:
        return None
    try:
        exp = datetime.strptime(expiration, "%Y-%m-%d").date()
        return (exp - date.today()).days
    except Exception:
        return None

def format_premium(net_premium: Optional[float], strategy: str) -> str:
    if net_premium is None:
        return "--"
    if strategy in ("LONG_CALL", "LONG_PUT"):
        # Long options: cost is negative net premium
        return f"[yellow]-${abs(net_premium):.0f}[/yellow]"
    elif net_premium >= 0:
        return f"[green]+${net_premium:.0f}[/green]"
    else:
        return f"[red]-${abs(net_premium):.0f}[/red]"

def format_dte(days: Optional[int]) -> str:
    if days is None:
        return "--"
    if days < 0:
        return f"[red]expired[/red]"
    elif days <= 7:
        return f"[red]{days}d[/red]"
    elif days <= 21:
        return f"[yellow]{days}d[/yellow]"
    else:
        return f"[green]{days}d[/green]"

def get_position_legs(conn, position_id: str) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM legs WHERE position_id = ? ORDER BY created_at",
        (position_id,)
    ).fetchall()]

def format_legs_summary(legs: list) -> str:
    parts = []
    for leg in legs:
        if leg["option_type"] == "STOCK":
            parts.append(f"{leg['contracts']}sh")
        else:
            d = "S" if leg["direction"] == "SHORT" else "L"
            t = leg["option_type"][0] if leg["option_type"] else "?"
            strike = f"{leg['strike']:.0f}" if leg["strike"] else "?"
            exp = leg["expiration"][5:] if leg["expiration"] else "?"
            parts.append(f"{d}{t}{strike} {exp}")
    return "  ".join(parts)

def cmd_list(args):
    strategy_filter = None
    ticker_filter = None
    show_all = "--all" in args

    i = 0
    while i < len(args):
        if args[i] == "--strategy" and i+1 < len(args):
            strategy_filter = args[i+1].upper(); i += 2
        elif args[i] == "--ticker" and i+1 < len(args):
            ticker_filter = args[i+1].upper(); i += 2
        else:
            i += 1

    status_filter = None if show_all else "OPEN,PENDING"

    conn = get_conn()
    query = "SELECT * FROM positions WHERE account_id = ?"
    params = [get_active_account()]
    bc, bp = book_filter(args)
    query += bc
    params.extend(bp)

    if status_filter:
        if "," in status_filter:
            statuses = status_filter.split(",")
            placeholders = ",".join("?" * len(statuses))
            query += f" AND status IN ({placeholders})"
            params.extend(statuses)
        else:
            query += " AND status = ?"
            params.append(status_filter)
    if strategy_filter:
        query += " AND strategy = ?"
        params.append(strategy_filter)
    if ticker_filter:
        query += " AND ticker = ?"
        params.append(ticker_filter)

    query += " ORDER BY strategy, ticker"
    positions = [dict(r) for r in conn.execute(query, params).fetchall()]

    if not positions:
        console.print()
        console.print("[yellow]No positions found.[/yellow]")
        console.print("[dim]Run [bold]helm import fidelity[/bold] to import positions.[/dim]")
        console.print()
        conn.close()
        return

    # Build table
    console.print()
    title = f"Open Positions ({len(positions)})" if not show_all else f"All Positions ({len(positions)})"
    t = Table(box=box.SIMPLE_HEAD, title=title, show_header=True, padding=(0,1))
    t.add_column("Ticker",   style="bold cyan", width=7)
    t.add_column("Strategy", width=14)
    t.add_column("Legs",     width=22)
    t.add_column("Premium",  justify="right", width=9)
    t.add_column("DTE",      justify="right", width=7)
    t.add_column("Opened",   style="dim", width=11)
    t.add_column("Status",   width=8)

    for pos in positions:
        legs = get_position_legs(conn, pos["id"])
        legs_str = format_legs_summary(legs)

        # DTE from first option leg
        min_dte = None
        for leg in legs:
            if leg["expiration"] and leg["option_type"] != "STOCK":
                d = dte(leg["expiration"])
                if d is not None:
                    min_dte = d if min_dte is None else min(min_dte, d)

        strategy = pos["strategy"]
        color = STRATEGY_COLORS.get(strategy, "white")
        strategy_str = f"[{color}]{strategy}[/{color}]"
        premium_str = format_premium(pos["net_premium"], strategy)
        dte_str = format_dte(min_dte)
        opened = pos["opened_at"][:10] if pos["opened_at"] else "--"
        status = pos["status"]
        status_str = (
            f"[green]{status}[/green]" if status == "OPEN" else
            f"[yellow]{status}[/yellow]" if status == "PENDING" else
            f"[dim]{status}[/dim]"
        )

        t.add_row(
            pos["ticker"], strategy_str, legs_str,
            premium_str, dte_str, opened, status_str
        )

    console.print(t)

    # Summary by strategy
    strategy_counts = {}
    for pos in positions:
        s = pos["strategy"]
        strategy_counts[s] = strategy_counts.get(s, 0) + 1

    summary_parts = [f"{v}x {k}" for k, v in sorted(strategy_counts.items())]
    console.print(f"[dim]  {'  |  '.join(summary_parts)}[/dim]")
    console.print()
    conn.close()


def cmd_show(args):
    if not args:
        console.print("[red]Specify a position id or ticker.[/red]")
        return

    query_str = args[0]
    conn = get_conn()

    # Try by id first, then by ticker
    pos = conn.execute(
        "SELECT * FROM positions WHERE id LIKE ? AND status = 'OPEN'",
        (f"{query_str}%",)
    ).fetchone()

    if not pos:
        pos = conn.execute(
            "SELECT * FROM positions WHERE ticker = ? AND status = 'OPEN' ORDER BY opened_at DESC LIMIT 1",
            (query_str.upper(),)
        ).fetchone()

    if not pos:
        console.print(f"[yellow]No open position found for:[/yellow] {query_str}")
        conn.close()
        return

    pos = dict(pos)
    legs = get_position_legs(conn, pos["id"])
    conn.close()

    lines = [
        f"[bold cyan]{pos['ticker']}[/bold cyan]  [{STRATEGY_COLORS.get(pos['strategy'],'white')}]{pos['strategy']}[/{STRATEGY_COLORS.get(pos['strategy'],'white')}]",
        f"ID:       {pos['id']}",
        f"Status:   {pos['status']}",
        f"Opened:   {pos['opened_at'][:10]}",
        f"Premium:  {format_premium(pos['net_premium'], pos['strategy'])}",
        "",
        "[bold]Legs:[/bold]",
    ]

    for leg in legs:
        if leg["option_type"] == "STOCK":
            lines.append(f"  STOCK      {leg['contracts']} shares @ ${leg['open_price']:.2f}")
        else:
            d = dte(leg["expiration"])
            dte_str = f"{d}d" if d is not None else "--"
            lines.append(
                f"  {leg['leg_role']:<12} "
                f"${leg['strike']:.0f} {leg['expiration']}  "
                f"({dte_str})  "
                f"x{leg['contracts']}  "
                f"@ ${leg['open_price']:.2f}"
            )

    if pos["notes"]:
        lines.append("")
        lines.append(f"[dim]{pos['notes']}[/dim]")

    console.print()
    console.print(Panel("\n".join(lines), title=f"{pos['ticker']} Position", border_style="cyan"))
    console.print()


def run():
    args = sys.argv[1:]

    if args and args[0] in ("--help", "-h"):
        console.print()
        console.print("[bold]Usage:[/bold]  helm positions [options]")
        console.print("[dim]  --strategy CSP    Filter by strategy[/dim]")
        console.print("[dim]  --ticker NVDA     Filter by ticker[/dim]")
        console.print("[dim]  --all             Include closed positions[/dim]")
        console.print("[dim]  show <id>         Detail view[/dim]")
        console.print()
        return

    if not get_active_account():
        console.print("[red]No active account. Run helm setup first.[/red]")
        return

    if args and args[0] == "show":
        cmd_show(args[1:])
    else:
        cmd_list(args)


if __name__ == "__main__":
    run()
