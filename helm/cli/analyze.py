"""
helm analyze -- Trade outcome analysis engine

Commands:
  helm analyze                     Overview: win rates, P&L by strategy
  helm analyze trends              Trade-life trends across all positions
  helm analyze position TICKER     Deep dive: full history for one position
"""

import sys
import json
from datetime import datetime, date
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.rule import Rule
from rich import box

from helm.db import get_conn
from helm.config import get_active_account

console = Console()


# -- helpers ------------------------------------------------------------------

def _fmt_pnl(v):
    if v is None: return "[dim]--[/dim]"
    return f"[green]+${v:,.0f}[/green]" if v >= 0 else f"[red]-${abs(v):,.0f}[/red]"

def _fmt_pct(v):
    if v is None: return "[dim]--[/dim]"
    return f"[green]+{v:.1f}%[/green]" if v >= 0 else f"[red]{v:.1f}%[/red]"

def _fmt_ivr(v):
    if v is None: return "[dim]--[/dim]"
    if v >= 70: return f"[red]{v:.0f}[/red]"
    if v >= 40: return f"[yellow]{v:.0f}[/yellow]"
    return f"[green]{v:.0f}[/green]"

def _days_held(opened_at, closed_at=None):
    try:
        start = date.fromisoformat(opened_at[:10])
        end   = date.fromisoformat((closed_at or datetime.now().isoformat())[:10])
        days = (end - start).days
        return max(0, days)  # clamp; negative = import artifact
    except Exception:
        return None

def _win(pnl):
    return pnl is not None and pnl > 0


# -- Overview -----------------------------------------------------------------

def cmd_overview(args):
    """Win rates, P&L, and key stats by strategy across all closed positions."""
    conn = get_conn()

    closed = conn.execute("""
        SELECT p.id, p.ticker, p.strategy, p.realized_pnl,
               p.opened_at, p.closed_at, p.total_contracts, p.net_premium,
               e.iv_rank as entry_ivr, e.delta as entry_delta,
               e.dte as entry_dte, e.spot_price as entry_spot
        FROM positions p
        LEFT JOIN entry_snapshots e ON p.id = e.position_id
        WHERE p.status IN ('CLOSED', 'EXPIRED', 'ASSIGNED', 'ROLLED_OUT')
        ORDER BY p.closed_at DESC
    """).fetchall()

    if not closed:
        console.print()
        console.print("[yellow]No closed positions yet — analysis will populate as trades complete.[/yellow]")
        console.print("[dim]Run [bold]helm check[/bold] regularly to build trade-life data.[/dim]")
        console.print()
        return

    # Group by strategy
    strategies = {}
    for row in closed:
        s = row['strategy'] or 'UNKNOWN'
        if s not in strategies:
            strategies[s] = []
        strategies[s].append(dict(row))

    console.print()
    console.print(Panel.fit(
        f"[bold cyan]HELM Analyze[/bold cyan] — Outcome Summary\n"
        f"[dim]{len(closed)} closed positions[/dim]",
        border_style="cyan"
    ))
    console.print()

    # Strategy summary table
    tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold dim")
    tbl.add_column("Strategy", style="bold", width=18)
    tbl.add_column("Trades", justify="right", width=7)
    tbl.add_column("Win%", justify="right", width=7)
    tbl.add_column("Total P&L", justify="right", width=12)
    tbl.add_column("Avg P&L", justify="right", width=10)
    tbl.add_column("Avg days", justify="right", width=9)
    tbl.add_column("Avg IVR entry", justify="right", width=13)
    tbl.add_column("Avg delta entry", justify="right", width=15)

    totals_pnl = 0
    totals_wins = 0
    totals_n = 0

    for strat, rows in sorted(strategies.items()):
        n = len(rows)
        wins = sum(1 for r in rows if _win(r['realized_pnl']))
        win_pct = wins / n * 100 if n else 0
        total_pnl = sum(r['realized_pnl'] or 0 for r in rows)
        avg_pnl = total_pnl / n if n else 0
        avg_days = sum(d for r in rows if (d := _days_held(r['opened_at'], r['closed_at'])) is not None)
        avg_days = avg_days / n if n else 0
        ivr_vals = [r['entry_ivr'] for r in rows if r['entry_ivr'] is not None]
        avg_ivr = sum(ivr_vals) / len(ivr_vals) if ivr_vals else None
        delta_vals = [r['entry_delta'] for r in rows if r['entry_delta'] is not None]
        avg_delta = sum(delta_vals) / len(delta_vals) if delta_vals else None

        totals_pnl += total_pnl
        totals_wins += wins
        totals_n += n

        win_color = "green" if win_pct >= 60 else ("yellow" if win_pct >= 40 else "red")
        tbl.add_row(
            strat,
            str(n),
            f"[{win_color}]{win_pct:.0f}%[/{win_color}]",
            _fmt_pnl(total_pnl),
            _fmt_pnl(avg_pnl),
            f"{avg_days:.0f}d" if avg_days else "--",
            _fmt_ivr(avg_ivr),
            f"{avg_delta:.2f}" if avg_delta else "[dim]--[/dim]",
        )

    console.print("[bold]Strategy Performance[/bold]")
    console.print(tbl)

    # Overall totals
    overall_win_pct = totals_wins / totals_n * 100 if totals_n else 0
    console.print(f"  Overall: {totals_n} trades  |  "
                  f"Win rate: {overall_win_pct:.0f}%  |  "
                  f"Total P&L: {_fmt_pnl(totals_pnl)}")
    console.print()

    # Recent closed positions detail
    console.print("[bold]Recent Closed Positions[/bold]")
    det = Table(box=box.SIMPLE, show_header=True, header_style="bold dim")
    det.add_column("Ticker", style="bold", width=7)
    det.add_column("Strategy", width=16)
    det.add_column("P&L", justify="right", width=10)
    det.add_column("Days", justify="right", width=6)
    det.add_column("IVR entry", justify="right", width=10)
    det.add_column("Delta entry", justify="right", width=11)
    det.add_column("DTE entry", justify="right", width=10)
    det.add_column("Closed", width=12)

    for row in closed[:15]:
        d = _days_held(row['opened_at'], row['closed_at'])
        det.add_row(
            row['ticker'],
            row['strategy'] or '--',
            _fmt_pnl(row['realized_pnl']),
            f"{d}d" if d else "--",
            _fmt_ivr(row['entry_ivr']),
            f"{row['entry_delta']:.2f}" if row['entry_delta'] else "[dim]--[/dim]",
            f"{row['entry_dte']}d" if row['entry_dte'] else "[dim]--[/dim]",
            (row['closed_at'] or '')[:10],
        )
    console.print(det)
    console.print()

    # Data quality note
    snaps_with_data = sum(1 for r in closed if r['entry_ivr'] is not None)
    if snaps_with_data < len(closed):
        console.print(f"[dim]  Note: {len(closed) - snaps_with_data} positions missing entry snapshots "
                      f"— IVR/delta analysis will improve as new trades are opened with HELM.[/dim]")
        console.print()


# -- Trends -------------------------------------------------------------------

def cmd_trends(args):
    """Trade-life trend analysis: drift, theta efficiency, IV movement."""
    conn = get_conn()

    # Get all positions with check history
    positions = conn.execute("""
        SELECT p.id, p.ticker, p.strategy, p.status, p.realized_pnl,
               p.opened_at, p.closed_at, p.net_premium
        FROM positions p
        WHERE EXISTS (SELECT 1 FROM checks c WHERE c.position_id = p.id)
        ORDER BY p.opened_at DESC
    """).fetchall()

    if not positions:
        console.print("[yellow]No check history yet. Run [bold]helm check[/bold] to build trade-life data.[/yellow]")
        return

    console.print()
    console.print(Panel.fit(
        f"[bold cyan]HELM Analyze[/bold cyan] — Trade-Life Trends\n"
        f"[dim]{len(positions)} positions with check history[/dim]",
        border_style="cyan"
    ))
    console.print()

    tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold dim")
    tbl.add_column("Ticker", style="bold", width=7)
    tbl.add_column("Strategy", width=14)
    tbl.add_column("Status", width=8)
    tbl.add_column("Checks", justify="right", width=7)
    tbl.add_column("P&L now", justify="right", width=10)
    tbl.add_column("Delta drift/d", justify="right", width=13)
    tbl.add_column("IV trend", justify="right", width=9)
    tbl.add_column("IVR now", justify="right", width=8)
    tbl.add_column("Health", width=8)

    for pos in positions:
        checks_raw = conn.execute("""
            SELECT checked_at, delta, iv_current, iv_rank, pnl_unrealized,
                   delta_vs_entry, iv_vs_entry, health_flag, rth_flag
            FROM checks
            WHERE position_id = ?
            ORDER BY checked_at ASC
        """, (pos['id'],)).fetchall()
        seen_days2 = {}
        for c_row in checks_raw:
            seen_days2[c_row['checked_at'][:10]] = c_row
        checks = list(seen_days2.values())

        if not checks:
            continue

        n = len(checks)
        latest = checks[-1]

        # Delta drift rate (change per day)
        delta_drift_per_day = None
        try:
            first = checks[0]
            last  = checks[-1]
            if first['delta'] is not None and last['delta'] is not None:
                d1 = datetime.fromisoformat(first['checked_at'])
                d2 = datetime.fromisoformat(last['checked_at'])
                days = max((d2 - d1).days, 1)
                delta_drift_per_day = (float(last['delta']) - float(first['delta'])) / days
        except Exception:
            pass

        # IV trend (first vs last)
        iv_trend = None
        try:
            first_iv = next((c['iv_current'] for c in checks if c['iv_current']), None)
            last_iv  = latest['iv_current']
            if first_iv and last_iv:
                iv_trend = round(float(last_iv) - float(first_iv), 1)
        except Exception:
            pass

        latest_health = latest['health_flag'] or '--'
        health_color  = {'GREEN': 'green', 'YELLOW': 'yellow', 'RED': 'red'}.get(latest_health, 'dim')
        pnl = latest['pnl_unrealized'] if pos['status'] == 'OPEN' else pos['realized_pnl']

        drift_str = "--"
        if delta_drift_per_day is not None:
            sign = "+" if delta_drift_per_day >= 0 else ""
            color = "red" if abs(delta_drift_per_day) > 0.01 else "green"
            drift_str = f"[{color}]{sign}{delta_drift_per_day:.4f}[/{color}]"

        iv_str = "--"
        if iv_trend is not None:
            sign = "+" if iv_trend >= 0 else ""
            color = "red" if iv_trend > 5 else ("yellow" if iv_trend > 0 else "green")
            iv_str = f"[{color}]{sign}{iv_trend:.1f}%[/{color}]"

        tbl.add_row(
            pos['ticker'],
            pos['strategy'] or '--',
            f"[dim]{pos['status']}[/dim]",
            str(n),
            _fmt_pnl(pnl),
            drift_str,
            iv_str,
            _fmt_ivr(latest['iv_rank']),
            f"[{health_color}]{latest_health}[/{health_color}]",
        )

    console.print("[bold]Trade-Life Trends[/bold]")
    console.print("[dim]Delta drift/d: daily rate of delta change | IV trend: IV move since first check[/dim]")
    console.print()
    console.print(tbl)
    console.print()


# -- Position deep dive -------------------------------------------------------

def cmd_position(args):
    """Full trade history for one position."""
    if not args:
        console.print("[red]Usage:[/red] helm analyze position <TICKER>")
        return

    ticker = args[0].upper()
    conn = get_conn()

    positions = conn.execute("""
        SELECT p.*, e.iv_rank as entry_ivr, e.iv_current as entry_iv,
               e.delta as entry_delta, e.spot_price as entry_spot,
               e.dte as entry_dte, e.theta as entry_theta
        FROM positions p
        LEFT JOIN entry_snapshots e ON p.id = e.position_id
        WHERE p.ticker = ?
        ORDER BY p.opened_at DESC
    """, (ticker,)).fetchall()

    if not positions:
        console.print(f"[yellow]No positions found for {ticker}.[/yellow]")
        return

    for pos in positions:
        checks_raw = conn.execute("""
            SELECT * FROM checks WHERE position_id = ?
            ORDER BY checked_at ASC
        """, (pos['id'],)).fetchall()
        # Keep only the last check per calendar day
        seen_days = {}
        for c_row in checks_raw:
            day = c_row['checked_at'][:10]
            seen_days[day] = c_row
        checks = list(seen_days.values())

        days = _days_held(pos['opened_at'], pos['closed_at'])
        status_color = {'OPEN': 'cyan', 'CLOSED': 'green', 'ROLLED_OUT': 'yellow'}.get(pos['status'], 'dim')

        console.print()
        console.print(Rule(f"[bold]{ticker}[/bold] {pos['strategy']} — [{status_color}]{pos['status']}[/{status_color}]"))
        console.print()

        # Entry context
        console.print(f"  Opened:      {(pos['opened_at'] or '')[:10]}  ({days}d ago)" if pos['status'] == 'OPEN'
                      else f"  Opened:      {(pos['opened_at'] or '')[:10]}")
        if pos['closed_at']:
            console.print(f"  Closed:      {pos['closed_at'][:10]}  ({days} days held)")
        if pos['realized_pnl'] is not None:
            console.print(f"  Realized:    {_fmt_pnl(pos['realized_pnl'])}")
        console.print()

        # Entry snapshot
        if pos['entry_ivr'] or pos['entry_delta']:
            console.print("  [bold]At entry:[/bold]")
            if pos['entry_spot']:
                console.print(f"    Spot:      ${pos['entry_spot']:.2f}")
            if pos['entry_iv']:
                console.print(f"    IV:        {pos['entry_iv']:.1f}%")
            if pos['entry_ivr']:
                console.print(f"    IV Rank:   {_fmt_ivr(pos['entry_ivr'])}")
            if pos['entry_delta']:
                console.print(f"    Delta:     {pos['entry_delta']:.3f}")
            if pos['entry_dte']:
                console.print(f"    DTE:       {pos['entry_dte']}d")
            console.print()

        if not checks:
            console.print("  [dim]No check history for this position.[/dim]")
            continue

        # Check history table
        console.print(f"  [bold]Check history[/bold] ({len(checks)} records)")
        ch_tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold dim", padding=(0,1))
        ch_tbl.add_column("Date", width=12)
        ch_tbl.add_column("Spot", justify="right", width=8)
        ch_tbl.add_column("P&L", justify="right", width=10)
        ch_tbl.add_column("Delta", justify="right", width=7)
        ch_tbl.add_column("IV%", justify="right", width=6)
        ch_tbl.add_column("IVR", justify="right", width=5)
        ch_tbl.add_column("DTE", justify="right", width=5)
        ch_tbl.add_column("Flag", width=8)
        ch_tbl.add_column("RTH", width=5)

        for c in checks:
            flag = c['health_flag'] or '--'
            fc = {'GREEN': 'green', 'YELLOW': 'yellow', 'RED': 'red'}.get(flag, 'dim')
            rth = "[dim]no[/dim]" if c['rth_flag'] == 'OUTSIDE_RTH' else "[green]yes[/green]"
            ch_tbl.add_row(
                c['checked_at'][:10],
                f"${c['spot_price']:.2f}" if c['spot_price'] else "--",
                _fmt_pnl(c['pnl_unrealized']),
                f"{c['delta']:.3f}" if c['delta'] else "[dim]--[/dim]",
                f"{c['iv_current']:.1f}%" if c['iv_current'] else "[dim]--[/dim]",
                _fmt_ivr(c['iv_rank']),
                str(c['dte_now']) if c['dte_now'] else "--",
                f"[{fc}]{flag}[/{fc}]",
                rth,
            )
        console.print(ch_tbl)

        # Trend summary
        if len(checks) >= 2:
            first_c, last_c = checks[0], checks[-1]
            console.print()
            console.print("  [bold]Trend summary:[/bold]")
            try:
                if first_c['delta'] and last_c['delta']:
                    d_drift = float(last_c['delta']) - float(first_c['delta'])
                    sign = "+" if d_drift >= 0 else ""
                    console.print(f"    Delta drift:  {sign}{d_drift:.3f} over {len(checks)} checks")
                if first_c['iv_current'] and last_c['iv_current']:
                    iv_move = float(last_c['iv_current']) - float(first_c['iv_current'])
                    sign = "+" if iv_move >= 0 else ""
                    console.print(f"    IV movement:  {sign}{iv_move:.1f}%")
                if first_c['pnl_unrealized'] and last_c['pnl_unrealized']:
                    pnl_move = float(last_c['pnl_unrealized']) - float(first_c['pnl_unrealized'])
                    sign = "+" if pnl_move >= 0 else ""
                    console.print(f"    P&L movement: {sign}${pnl_move:,.0f}")
            except Exception:
                pass
        console.print()


# -- Entry point --------------------------------------------------------------

def run():
    args = sys.argv[1:]

    if args and args[0] in ('-h', '--help'):
        console.print("\n[bold]Usage:[/bold]  helm analyze <command>\n")
        console.print("  (no command)          Overview: win rates and P&L by strategy")
        console.print("  trends                Trade-life trends across all positions")
        console.print("  position <TICKER>     Full check history for one position\n")
        return
    if not args:
        cmd_overview([])
        return

    cmd = args[0].lower()
    rest = args[1:]

    if cmd == 'trends':
        cmd_trends(rest)
    elif cmd == 'position':
        cmd_position(rest)
    else:
        # Default: treat first arg as potential ticker, or show overview
        if cmd.isupper() or (len(cmd) <= 5 and cmd.isalpha()):
            cmd_position([cmd.upper()] + rest)
        else:
            cmd_overview(rest)


if __name__ == '__main__':
    run()
