"""
helm close [TICKER] - Manually close a position.
"""

import sys
from datetime import datetime, date
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich import box
from helm.config import get_active_account
from helm.models.position import Position
from helm.models.leg import Leg

console = Console()


def _fmt_pnl(v):
    if v is None: return "--"
    return f"[green]+${v:,.0f}[/green]" if v >= 0 else f"[red]-${abs(v):,.0f}[/red]"


def _fmt_price(v):
    if v is None: return "--"
    return f"${v:.2f}"


def _days_to(expiration):
    if not expiration: return None
    try:
        exp = datetime.strptime(expiration[:10], "%Y-%m-%d").date()
        return (exp - date.today()).days
    except Exception:
        return None


def _fetch_live_price(ticker, expiration, strike, option_type, direction):
    try:
        from helm.cli.check_cmd import fetch_ibkr_option
        result = fetch_ibkr_option(ticker, expiration, strike, option_type)
        if result and result.get("mid"):
            return result["mid"]
    except Exception:
        pass
    return None


def _show_position_summary(pos, legs):
    dte = _days_to(legs[0].expiration) if legs else None
    dte_str = f"{dte}d" if dte is not None else "--"
    entry_premium = sum(
        leg.open_value if leg.direction == "SHORT" else -leg.open_value
        for leg in legs
    )
    console.print()
    console.print(Panel(
        f"[bold]{pos.ticker}[/bold]  {pos.strategy}  \u00b7  {dte_str} DTE  \u00b7"
        f"  Entry: [bold]{_fmt_price(entry_premium / 100)}/contract[/bold]",
        title="[bold]Close Position[/bold]",
        border_style="yellow", expand=False,
    ))
    tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold dim")
    tbl.add_column("Leg", style="bold")
    tbl.add_column("Type")
    tbl.add_column("Dir")
    tbl.add_column("Strike")
    tbl.add_column("Expiry")
    tbl.add_column("Qty")
    tbl.add_column("Entry $")
    tbl.add_column("Live mid", justify="right")
    for leg in legs:
        live = _fetch_live_price(pos.ticker, leg.expiration or "",
                                 leg.strike or 0, leg.option_type or "", leg.direction)
        live_str = f"[dim]{_fmt_price(live)}[/dim]" if live else "[dim]--[/dim]"
        tbl.add_row(
            leg.leg_role, leg.option_type or "--", leg.direction,
            f"${leg.strike:.0f}" if leg.strike else "--",
            (leg.expiration or "")[:10], str(leg.contracts),
            _fmt_price(leg.open_price), live_str,
        )
    console.print(tbl)


def _finalize_close(pos, legs, close_prices, reason="manual"):
    """Write a close: net P&L across legs, close legs + position, snapshot.
    Pure persistence -- no prompts, no confirm. Shared by interactive close
    and the paper auto-manager. Returns {ok, realized_pnl, close_prices}."""
    total_pnl = 0.0
    for leg in legs:
        cp = close_prices[leg.id]
        if leg.direction == "SHORT":
            total_pnl += (leg.open_price - cp) * leg.contracts * leg.multiplier
        else:
            total_pnl += (cp - leg.open_price) * leg.contracts * leg.multiplier
    now = datetime.now().isoformat()
    for leg in legs:
        leg.close(close_prices[leg.id], close_date=now)
    pos.close(total_pnl, closed_at=now, exit_reason=reason)
    try:
        from helm.models.close_snapshot import save_close_snapshot
        save_close_snapshot(
            position_id=pos.id,
            ticker=pos.ticker,
            realized_pnl=total_pnl,
            close_prices=close_prices,
            legs=legs,
            reason=reason,
        )
    except Exception:
        pass  # never block a close
    # back-propagate the realized outcome onto the originating signal
    # (REAL book only -- HELM-049: paper positions may carry signal_id for pick-
    # linkage, but their outcomes stay on the paper position, never on the signal)
    try:
        if getattr(pos, "signal_id", None) and getattr(pos, "book", None) == "REAL":
            from helm.models.signal import Signal
            sig = Signal.get(pos.signal_id)
            if sig is not None:
                outcome = "WIN" if total_pnl > 0 else ("LOSS" if total_pnl < 0 else "BREAKEVEN")
                sig.record_outcome(total_pnl, outcome, notes=reason)
    except Exception:
        pass  # never block a close
    return {"ok": True, "realized_pnl": total_pnl, "close_prices": close_prices}


def close_position(pos, legs, reason="manual"):
    """Close legs interactively. Returns {ok, realized_pnl, close_prices}."""
    _show_position_summary(pos, legs)
    console.print("[dim]Enter the price you paid/received to close each leg.[/dim]")
    console.print("[dim]Short (CSP/CC): buy-to-close price. Long call: sell price.[/dim]")
    console.print()
    close_prices = {}
    for leg in legs:
        label = (f"  {leg.leg_role} ({leg.direction} {leg.option_type or ''}"
                 f" ${leg.strike:.0f} {(leg.expiration or '')[:10]})")
        while True:
            raw = Prompt.ask(f"{label}  close price").strip().lstrip("$")
            try:
                price = float(raw)
                if price < 0:
                    console.print("  [red]Price must be >= 0[/red]"); continue
                close_prices[leg.id] = price
                break
            except ValueError:
                console.print("  [red]Enter a number, e.g. 0.45[/red]")
    total_pnl = 0.0
    pnl_lines = []
    for leg in legs:
        cp = close_prices[leg.id]
        if leg.direction == "SHORT":
            leg_pnl = (leg.open_price - cp) * leg.contracts * leg.multiplier
        else:
            leg_pnl = (cp - leg.open_price) * leg.contracts * leg.multiplier
        total_pnl += leg_pnl
        pnl_lines.append(
            f"  {leg.leg_role}: {_fmt_price(leg.open_price)} -> {_fmt_price(cp)} = {_fmt_pnl(leg_pnl)}"
        )
    console.print()
    console.print("[bold]P&L breakdown:[/bold]")
    for line in pnl_lines: console.print(line)
    console.print()
    console.print(f"  [bold]Realized P&L:  {_fmt_pnl(total_pnl)}[/bold]")
    console.print()
    if not Confirm.ask("  Confirm close?", default=True):
        console.print("[dim]  Cancelled.[/dim]")
        return {"ok": False}
    result = _finalize_close(pos, legs, close_prices, reason)
    console.print()
    console.print(f"  [green]OK[/green]  {pos.ticker} [bold]CLOSED[/bold]  Realized P&L: {_fmt_pnl(result['realized_pnl'])}")
    console.print()
    return result


def run():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        console.print("\n[bold]Usage:[/bold]  helm close <TICKER>\n")
        console.print("  Manually close an open position and record realized P&L.\n")
        return
    ticker = args[0].upper()
    acct = get_active_account()
    if not acct:
        console.print("[red]No active account. Run [bold]helm setup[/bold] first.[/red]")
        return
    positions = Position.by_ticker(ticker, status="OPEN")
    # helm close is a REAL-money action: never consider paper positions.
    positions = [p for p in positions if getattr(p, "book", None) == "REAL"]
    if not positions:
        console.print(f"\n[yellow]No open real position found for {ticker}.[/yellow]\n")
        return
    if len(positions) == 1:
        pos = positions[0]
    else:
        # Two or more real positions on this ticker -- let the user pick.
        console.print(f"\n[yellow]{len(positions)} open real positions for {ticker} -- select one to close:[/yellow]\n")
        for i, p in enumerate(positions, 1):
            console.print(f"  [bold]{i}[/bold]. {p.strategy}  ·  opened {str(p.opened_at)[:10]}  ·  {p.total_contracts} contract(s)")
        console.print()
        choice = Prompt.ask("  Position number", choices=[str(i) for i in range(1, len(positions) + 1)])
        pos = positions[int(choice) - 1]
    legs = Leg.for_position(pos.id)
    if not legs:
        console.print(f"\n[red]No legs found for {ticker}.[/red]\n")
        return
    close_position(pos, legs, reason="manual")