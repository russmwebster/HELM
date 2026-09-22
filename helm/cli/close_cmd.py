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
from helm.models.lifecycle import LifecycleEvent

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


# HELM-161 (W105): a real close must be able to say WHY. Until now the CLI
# entrypoint hardcoded reason="manual" -- the parameter existed and was threaded
# all the way to pos.close(), but nothing ever passed anything else (roll.py is
# the sole exception). 55 real closes carry "manual" and 15 carry nothing, so
# "took profit at target" and "changed my mind" are the same row.
#
# The vocabulary is DELIBERATELY IDENTICAL to what the paper exit agent writes,
# so real and paper closes pool in one analysis. Do not add a synonym here that
# the agent does not emit. (Note: the real book also holds 5 legacy "TARGET"
# rows, which are PROFIT_TARGET under an older name -- not normalised here.)
EXIT_REASONS = (
    "PROFIT_TARGET",   # hit the profit objective
    "DTE_MANAGE",      # closed at/near the management deadline
    "STOP",            # cut the loss
    "GIVE_BACK",       # gave back too much from the peak (long side)
    "THESIS_BREAK",    # the reason for the trade stopped being true
    "PROFIT_FLOOR",    # locked a floor in on a winner
    "ASSIGNED",        # short leg assigned
    "EXPIRED",         # expired worthless
    "ROLLED",          # closed as one half of a roll
    "DISCRETIONARY",   # my judgement, no rule fired -- honest, not unrecorded
)
DEFAULT_EXIT_REASON = "manual"

# W180 step 3 (2026-09-22): reasons for closing ONE leg while the position
# stays open. The diagonal's short is harvested, floored, bought back on a
# confirmed breach, or managed at 21 DTE; DISCRETIONARY is the honest catch-all.
# DTE_MANAGE and DISCRETIONARY are shared with EXIT_REASONS so the two
# vocabularies can be pooled (standing rule: one exit_reason vocabulary).
LEG_EXIT_REASONS = (
    "HARVEST",         # premium captured to the target (25% / 50%)
    "WORTHLESS",       # the worthless floor: <= $0.05 or no bid
    "BREACH",          # bought back on a confirmed breach of the short strike
    "DTE_MANAGE",      # bought back at the management deadline
    "DISCRETIONARY",   # my judgement, no rule fired
)
DEFAULT_LEG_EXIT_REASON = "DISCRETIONARY"


def _finalize_close(pos, legs, close_prices, reason="manual"):
    """Write a close: net P&L across legs, close legs + position, snapshot.
    Pure persistence -- no prompts, no confirm. Shared by interactive close
    and the paper auto-manager. Returns {ok, realized_pnl, close_prices}."""
    total_pnl = 0.0
    # W180 step 3: a leg the book has ALREADY closed -- settled at expiry
    # (W131/W177) or bought back (close_leg) -- keeps its booked close_price
    # and is not closed again. Its realized P&L is part of the position's
    # total; before this, a whole-position close summed over every leg it
    # was handed and re-closed the dead one at whatever was typed for it.
    _already = [l for l in legs if str(getattr(l, "status", "") or "").upper() == "CLOSED"
                and l.close_price is not None]
    _open = [l for l in legs if l not in _already]
    for leg in _already:
        cp = leg.close_price
        if leg.direction == "SHORT":
            total_pnl += (leg.open_price - cp) * leg.contracts * leg.multiplier
        else:
            total_pnl += (cp - leg.open_price) * leg.contracts * leg.multiplier
    for leg in _open:
        cp = close_prices[leg.id]
        if leg.direction == "SHORT":
            total_pnl += (leg.open_price - cp) * leg.contracts * leg.multiplier
        else:
            total_pnl += (cp - leg.open_price) * leg.contracts * leg.multiplier
    now = datetime.now().isoformat()
    for leg in _open:
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


def close_leg(pos, legs, leg, close_price, reason=DEFAULT_LEG_EXIT_REASON, spot=None):
    """W180 step 3: close ONE leg at a fill and leave the position OPEN.

    The diagonal as a rented long (Russ, 2026-09-21): the short is bought back
    -- harvested, floored, or bought back on a breach -- and the long stays in
    play until the next short is sold against it. This is action (a); adding
    the next short is action (b), a separate command (step 6).

    Pure persistence, no prompts: shared by the CLI path below and, later, the
    paper harvest rule. Writes legs.close_price/close_date/status on the one
    leg and a lifecycle event carrying the fill, the contracts and the realized
    P&L. The event_type is ADJUSTED with a narrative that begins "LEG_CLOSED":
    lifecycle_events carries a CHECK constraint on event_type in the live
    schema, and widening it is a table rebuild on a SELECT * model (W155) --
    not a step-3 change. Readers find these rows by leg_id, or by the prefix. Touches nothing on positions: net_premium stays the
    original structure's debit (the sequence is reconstructed from the legs),
    and realized_pnl is written only when the position closes. Refuses to
    close the LAST open leg -- that is a position close, and `helm close`
    without --leg is the command for it.
    """
    if str(getattr(leg, "status", "") or "").upper() != "OPEN":
        return {"ok": False, "error": f"leg {leg.id} is not OPEN (status {leg.status})"}
    open_legs = [l for l in legs if str(getattr(l, "status", "") or "").upper() == "OPEN"]
    if len(open_legs) <= 1:
        return {"ok": False, "error": "last open leg -- close the position with helm close"}
    if reason not in LEG_EXIT_REASONS:
        return {"ok": False, "error": f"unknown leg reason {reason}"}
    if close_price is None or float(close_price) < 0:
        return {"ok": False, "error": "close price must be >= 0"}
    close_price = float(close_price)
    if leg.direction == "SHORT":
        pnl = (leg.open_price - close_price) * leg.contracts * leg.multiplier
    else:
        pnl = (close_price - leg.open_price) * leg.contracts * leg.multiplier
    now = datetime.now().isoformat()
    leg.close(close_price, close_date=now)
    try:
        LifecycleEvent.record(
            pos.id, "ADJUSTED", occurred_at=now, leg_id=leg.id,
            option_price=close_price, contracts=leg.contracts, pnl_at_event=round(pnl, 2),
            spot_price=spot,
            narrative=(f"LEG_CLOSED | reason={reason} | {leg.leg_role} {leg.direction} "
                       f"${leg.strike:g} {(leg.expiration or '')[:10]} | "
                       f"open {leg.open_price:.2f} -> close {close_price:.2f} | "
                       f"pnl=${pnl:.0f} | remaining_open_legs={len(open_legs) - 1} | W180"),
        )
    except Exception:
        pass  # the leg is closed; never let the event block it
    return {"ok": True, "leg_id": leg.id, "close_price": close_price,
            "realized_pnl": round(pnl, 2), "remaining_open_legs": len(open_legs) - 1}


def _resolve_leg(legs, spec):
    """--leg takes a FULL leg id, or a leg_role that names exactly one OPEN leg.
    Never a suffix (standing rule: leg-id tails are not unique)."""
    spec = (spec or "").strip()
    exact = [l for l in legs if l.id == spec]
    if len(exact) == 1:
        return exact[0], None
    role = spec.upper()
    by_role = [l for l in legs if l.leg_role == role
               and str(getattr(l, "status", "") or "").upper() == "OPEN"]
    if len(by_role) == 1:
        return by_role[0], None
    if len(by_role) > 1:
        return None, f"{role} names {len(by_role)} open legs -- give the full leg id"
    return None, (f"no open leg matches {spec!r} (full id or one of "
                  f"{', '.join(sorted({l.leg_role for l in legs}))})")


def close_leg_interactive(pos, legs, leg_spec, price, reason, assume_yes=False):
    leg, err = _resolve_leg(legs, leg_spec)
    if err:
        console.print(f"\n[red]{err}[/red]\n")
        return {"ok": False}
    _show_position_summary(pos, legs)
    if leg.direction == "SHORT":
        pnl = (leg.open_price - price) * leg.contracts * leg.multiplier
    else:
        pnl = (price - leg.open_price) * leg.contracts * leg.multiplier
    console.print(f"  Close [bold]{leg.leg_role}[/bold] ({leg.direction} ${leg.strike:g} "
                  f"{(leg.expiration or '')[:10]}) at {_fmt_price(price)}  "
                  f"-> {_fmt_pnl(pnl)}   reason {reason}")
    console.print(f"  [dim]The position stays OPEN on its remaining leg(s).[/dim]")
    console.print()
    if not assume_yes and not Confirm.ask("  Confirm leg close?", default=True):
        console.print("[dim]  Cancelled.[/dim]")
        return {"ok": False}
    result = close_leg(pos, legs, leg, price, reason=reason)
    if not result.get("ok"):
        console.print(f"\n[red]{result.get('error')}[/red]\n")
        return result
    console.print()
    console.print(f"  [green]OK[/green]  {pos.ticker} {leg.leg_role} [bold]CLOSED[/bold] at "
                  f"{_fmt_price(price)}  Realized: {_fmt_pnl(result['realized_pnl'])}  "
                  f"-- position OPEN, {result['remaining_open_legs']} leg(s) remain")
    console.print()
    return result


def close_position(pos, legs, reason="manual"):
    """Close legs interactively. Returns {ok, realized_pnl, close_prices}."""
    _show_position_summary(pos, legs)
    console.print("[dim]Enter the price you paid/received to close each leg.[/dim]")
    console.print("[dim]Short (CSP/CC): buy-to-close price. Long call: sell price.[/dim]")
    console.print()
    close_prices = {}
    # W180 step 3: prompt only for legs still open; a settled or bought-back
    # leg already has its price and is reported, not asked for.
    _closed_legs = [l for l in legs if str(getattr(l, "status", "") or "").upper() == "CLOSED"
                    and l.close_price is not None]
    for leg in _closed_legs:
        console.print(f"  [dim]{leg.leg_role} already closed at {_fmt_price(leg.close_price)} "
                      f"on {(leg.close_date or '')[:10]} -- counted, not re-closed[/dim]")
    for leg in [l for l in legs if l not in _closed_legs]:
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
        cp = close_prices.get(leg.id, leg.close_price)
        if cp is None:
            continue
        if leg.direction == "SHORT":
            leg_pnl = (leg.open_price - cp) * leg.contracts * leg.multiplier
        else:
            leg_pnl = (cp - leg.open_price) * leg.contracts * leg.multiplier
        total_pnl += leg_pnl
        _tag = "  (already closed)" if leg in _closed_legs else ""
        pnl_lines.append(
            f"  {leg.leg_role}: {_fmt_price(leg.open_price)} -> {_fmt_price(cp)} = {_fmt_pnl(leg_pnl)}{_tag}"
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
        console.print("\n[bold]Usage:[/bold]  helm close <TICKER> [--position-id ID]\n")
        console.print("  Manually close an open position and record realized P&L.\n")
        console.print("  [cyan]--position-id ID[/cyan]  Close this exact position, skipping")
        console.print("                     the selection prompt. For non-interactive")
        console.print("                     callers (the PG web UI) that must not")
        console.print("                     identify a trade by its place in a list.\n")
        console.print("  [cyan]--reason NAME[/cyan]     Why the position was closed. One of:")
        console.print("                     [dim]" + ", ".join(EXIT_REASONS) + "[/dim]")
        console.print("                     [dim]Omitted records the legacy 'manual'.[/dim]\n")
        console.print("  [bold]Close ONE leg, keep the position (W180):[/bold]")
        console.print("  [cyan]--leg ID|ROLE[/cyan]     Full leg id, or a role (SHORT_CALL) naming one open leg.")
        console.print("  [cyan]--price P[/cyan]         The fill for that leg. Required with --leg.")
        console.print("  [cyan]--reason NAME[/cyan]     With --leg, one of:")
        console.print("                     [dim]" + ", ".join(LEG_EXIT_REASONS) + "[/dim]")
        console.print("  [cyan]--yes[/cyan]             Skip the confirm (non-interactive callers).\n")
        return

    # Parse. --position-id is the only flag; everything else stays positional so
    # `helm close TICKER` is byte-for-byte the command it has always been.
    position_id = None
    reason = None
    leg_spec = None
    leg_price = None
    assume_yes = False
    positional = []
    i = 0
    while i < len(args):
        if args[i] == "--position-id" and i + 1 < len(args):
            position_id = args[i + 1].strip()
            i += 2
        elif args[i] == "--reason" and i + 1 < len(args):
            reason = args[i + 1].strip().upper()
            i += 2
        elif args[i] == "--leg" and i + 1 < len(args):
            leg_spec = args[i + 1].strip()
            i += 2
        elif args[i] == "--price" and i + 1 < len(args):
            try:
                leg_price = float(args[i + 1].strip().lstrip("$"))
            except ValueError:
                console.print(f"\n[red]--price must be a number, got {args[i + 1]!r}[/red]\n")
                return
            i += 2
        elif args[i] == "--yes":
            assume_yes = True
            i += 1
        else:
            positional.append(args[i])
            i += 1

    # W180 step 3: the leg path has its own reason vocabulary and its own gate.
    if leg_spec is not None:
        if leg_price is None or leg_price < 0:
            console.print("\n[red]--leg needs --price P (the fill, >= 0).[/red]\n")
            return
        reason = reason or DEFAULT_LEG_EXIT_REASON
        if reason not in LEG_EXIT_REASONS:
            console.print(f"\n[red]Unknown --reason {reason} for a leg close.[/red]")
            console.print("[dim]One of: " + ", ".join(LEG_EXIT_REASONS) + "[/dim]\n")
            return
    else:
        if leg_price is not None:
            console.print("\n[red]--price only applies with --leg.[/red]\n")
            return
        reason = reason or DEFAULT_EXIT_REASON

    if leg_spec is None and reason != DEFAULT_EXIT_REASON and reason not in EXIT_REASONS:
        console.print(f"\n[red]Unknown --reason {reason}.[/red]")
        console.print("[dim]One of: " + ", ".join(EXIT_REASONS) + "[/dim]\n")
        return

    if not positional:
        console.print("[red]Specify a ticker.[/red]  [dim]helm close AAPL[/dim]")
        return

    ticker = positional[0].upper()
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

    if position_id:
        # Named, not counted. The ordinal prompt below is fed on stdin by
        # non-interactive callers, and an ordinal computed from a list that has
        # since changed silently becomes the FIRST LEG'S CLOSE PRICE -- the
        # selection prompt is skipped, `float("2")` parses, every later price
        # shifts one leg, and the close commits wrong realized P&L reporting
        # success. Naming the position removes the failure mode rather than
        # narrowing the window: this branch never prompts.
        match = [p for p in positions if p.id == position_id]
        if not match:
            console.print(f"\n[red]No open real position [bold]{position_id}[/bold] "
                          f"for {ticker}.[/red]")
            console.print("[dim]It may have been closed already. Open now:[/dim]")
            for p in positions:
                console.print(f"  [dim]{p.id}  ·  {p.strategy}  ·  opened "
                              f"{str(p.opened_at)[:10]}[/dim]")
            console.print()
            return
        pos = match[0]
    elif len(positions) == 1:
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
    if leg_spec is not None:
        close_leg_interactive(pos, legs, leg_spec, leg_price, reason, assume_yes=assume_yes)
        return
    close_position(pos, legs, reason=reason)