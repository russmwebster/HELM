"""
helm stock -- Manage equity positions for covered call tracking

Commands:
  helm stock list                   Show all stock positions
  helm stock add TICKER SHARES      Add or update a stock position
  helm stock add TICKER SHARES COST Add with cost basis
  helm stock remove TICKER          Remove a stock position
"""

import sys
from rich.console import Console
from rich.table import Table
from rich import box
from helm.db import get_conn
from helm.config import get_active_account

console = Console()


def cmd_list():
    conn = get_conn()
    rows = conn.execute(
        "SELECT ticker, shares, cost_basis, acquired_at, notes, updated_at "
        "FROM stock_positions ORDER BY ticker"
    ).fetchall()

    if not rows:
        console.print()
        console.print("[dim]No stock positions. Use [bold]helm stock add TICKER SHARES[/bold] to add.[/dim]")
        console.print()
        return

    tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold dim")
    tbl.add_column("Ticker",     style="bold", width=8)
    tbl.add_column("Shares",     justify="right", width=8)
    tbl.add_column("Max CC",     justify="right", width=14)
    tbl.add_column("Cost basis", justify="right", width=12)
    tbl.add_column("Updated",    width=12)
    tbl.add_column("Notes",      width=30)

    for r in rows:
        max_cc = r["shares"] // 100
        cb = f"${r['cost_basis']:.2f}" if r["cost_basis"] else "--"
        tbl.add_row(
            r["ticker"],
            str(r["shares"]),
            f"{max_cc} contracts",
            cb,
            (r["updated_at"] or "")[:10],
            r["notes"] or "",
        )

    console.print()
    console.print("[bold]Stock Positions[/bold]  [dim](used for covered call sizing)[/dim]")
    console.print(tbl)
    console.print()


def cmd_add(args):
    if len(args) < 2:
        console.print("[red]Usage:[/red] helm stock add <TICKER> <SHARES> [COST_BASIS]")
        return

    ticker = args[0].upper()
    try:
        shares = int(args[1])
    except ValueError:
        console.print(f"[red]Invalid shares:[/red] {args[1]}")
        return

    cost_basis = None
    if len(args) >= 3:
        try:
            cost_basis = float(args[2])
        except ValueError:
            pass

    account_id = get_active_account()
    conn = get_conn()
    conn.execute("""
        INSERT INTO stock_positions (id, account_id, ticker, shares, cost_basis, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(account_id, ticker) DO UPDATE SET
            shares=excluded.shares,
            cost_basis=excluded.cost_basis,
            updated_at=datetime('now')
    """, (f"SP-{ticker}", account_id, ticker, shares, cost_basis))
    conn.commit()

    max_cc = shares // 100
    cb_str = f" at ${cost_basis:.2f}" if cost_basis else ""
    console.print(f"[green]\u2713[/green]  {ticker}: {shares} shares{cb_str} \u2014 max [bold]{max_cc} covered call contracts[/bold]")


def cmd_remove(args):
    if not args:
        console.print("[red]Usage:[/red] helm stock remove <TICKER>")
        return

    ticker = args[0].upper()
    account_id = get_active_account()
    conn = get_conn()
    conn.execute(
        "DELETE FROM stock_positions WHERE ticker = ? AND account_id = ?",
        (ticker, account_id)
    )
    conn.commit()
    console.print(f"[green]\u2713[/green]  {ticker} removed from stock positions.")


def run():
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        console.print("\n[bold]Usage:[/bold]  helm stock <command>\n")
        console.print("  list                        Show all stock positions")
        console.print("  add <TICKER> <SHARES>       Add or update a position")
        console.print("  add <TICKER> <SHARES> <CB>  Add with cost basis")
        console.print("  remove <TICKER>             Remove a position\n")
        return

    cmd = args[0].lower()
    rest = args[1:]

    if cmd == "list":
        cmd_list()
    elif cmd == "add":
        cmd_add(rest)
    elif cmd == "remove":
        cmd_remove(rest)
    else:
        console.print(f"[red]Unknown command:[/red] {cmd}")
