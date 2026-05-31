"""
helm roll [TICKER]

Roll a position: close the current position then launch helm open
for the same ticker and strategy.
"""

import sys
from rich.console import Console
from helm.config import get_active_account
from helm.models.position import Position
from helm.models.leg import Leg
from helm.cli.close_cmd import close_position

console = Console()


def run():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        console.print("\n[bold]Usage:[/bold]  helm roll <TICKER>\n")
        console.print("  Roll a position: close the current leg(s), then open a replacement.")
        console.print("  Records realized P&L, then launches helm open for the new leg.\n")
        return

    ticker = args[0].upper()
    acct = get_active_account()
    if not acct:
        console.print("[red]No active account. Run [bold]helm setup[/bold] first.[/red]")
        return

    positions = Position.by_ticker(ticker, status="OPEN")
    if not positions:
        console.print(f"\n[yellow]No open position found for {ticker}.[/yellow]\n")
        return

    if len(positions) > 1:
        console.print(f"\n[yellow]Multiple open for {ticker} -- rolling most recent.[/yellow]")

    pos = positions[-1]
    legs = Leg.for_position(pos.id)

    if not legs:
        console.print(f"\n[red]No legs found for {ticker}.[/red]\n")
        return

    strategy = pos.strategy

    console.print(f"\n[bold]Roll[/bold] {ticker} ({strategy}) -- Step 1/2: close existing position")

    result = close_position(pos, legs, reason="rolled")
    if not result["ok"]:
        console.print("[dim]Roll cancelled.[/dim]\n")
        return

    # Upgrade status from CLOSED to ROLLED_OUT
    pos.mark_rolled()
    console.print(f"  [dim]{ticker} marked [bold]ROLLED_OUT[/bold][/dim]")
    console.print()
    console.print(f"[bold]Roll[/bold] {ticker} -- Step 2/2: open replacement")
    console.print(f"[dim]  Launching helm open {ticker} {strategy} ...[/dim]\n")

    try:
        # Inject args for open_cmd which also reads sys.argv
        sys.argv = [f"helm open"] + [ticker, strategy]
        from helm.cli.open_cmd import run as open_run
        open_run()
    except Exception as e:
        console.print(f"[red]Error launching helm open: {e}[/red]")
        console.print(f"[dim]  Run manually: [bold]helm open {ticker} {strategy}[/bold][/dim]\n")