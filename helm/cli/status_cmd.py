
# helm/cli/status_cmd.py
# helm status -- portfolio dashboard

import sys
import logging
from pathlib import Path
from datetime import date, datetime

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

import warnings
warnings.filterwarnings("ignore")

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich import box

from helm.config import get_active_account
from helm.db import get_conn

console = Console()


def dte(expiration: str) -> int:
    if not expiration:
        return 999
    try:
        exp_date = datetime.strptime(expiration, "%Y-%m-%d").date()
        return (exp_date - date.today()).days
    except Exception:
        return 999


def run():
    account_id = get_active_account()
    if not account_id:
        console.print("[red]No active account. Run helm setup first.[/red]")
        return

    conn = get_conn()

    # ── Account ───────────────────────────────────────────────────────────────
    acct = conn.execute(
        "SELECT * FROM accounts WHERE id=?", (account_id,)
    ).fetchone()
    if not acct:
        console.print("[red]Account not found.[/red]")
        return
    acct = dict(acct)

    # ── Positions ─────────────────────────────────────────────────────────────
    open_pos = [dict(r) for r in conn.execute(
        "SELECT * FROM positions WHERE account_id=? AND status='OPEN' ORDER BY ticker",
        (account_id,)
    ).fetchall()]

    closed = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(realized_pnl),0) FROM positions WHERE account_id=? AND status='CLOSED'",
        (account_id,)
    ).fetchone()
    closed_count   = closed[0]
    total_realized = closed[1] or 0

    # Premium deployed
    premium_open = sum(abs(p["net_premium"] or 0) for p in open_pos)

    # Strategy breakdown
    strats = {}
    for p in open_pos:
        s = p["strategy"]
        strats[s] = strats.get(s, 0) + 1

    # Get legs for expiry info
    pos_legs = {}
    for p in open_pos:
        legs = conn.execute(
            "SELECT * FROM legs WHERE position_id=? AND option_type != 'STOCK'",
            (p["id"],)
        ).fetchall()
        if legs:
            pos_legs[p["id"]] = dict(legs[0])

    # Latest check results
    latest_checks = {}
    for p in open_pos:
        chk = conn.execute(
            "SELECT health_flag, pnl_pct, checked_at FROM checks WHERE position_id=? ORDER BY checked_at DESC LIMIT 1",
            (p["id"],)
        ).fetchone()
        if chk:
            latest_checks[p["id"]] = dict(chk)

    # Flag counts from latest checks
    flag_counts = {"GREEN": 0, "YELLOW": 0, "RED": 0}
    for chk in latest_checks.values():
        flag = chk.get("health_flag", "")
        if flag in flag_counts:
            flag_counts[flag] += 1

    # Universe
    watchlist_count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
    themes_count    = conn.execute("SELECT COUNT(*) FROM themes").fetchone()[0]

    # Last activity times
    last_check     = conn.execute("SELECT MAX(checked_at) FROM checks").fetchone()[0]
    last_reconcile = conn.execute(
        "SELECT MAX(occurred_at) FROM helm_events WHERE event_type='RECONCILE_RUN'"
    ).fetchone()[0]
    last_scan      = conn.execute(
        "SELECT MAX(occurred_at) FROM helm_events WHERE event_type='SCREEN_RUN'"
    ).fetchone()[0]

    conn.close()

    # ── Render ────────────────────────────────────────────────────────────────
    today = date.today().strftime("%B %d, %Y")
    console.print()
    console.print(Panel.fit(
        f"[bold cyan]HELM Status[/bold cyan]  —  "
        f"[dim]{acct['nickname']} ({acct['broker']})[/dim]  "
        f"[dim]{today}[/dim]",
        border_style="cyan"
    ))
    console.print()

    # ── Portfolio panel ───────────────────────────────────────────────────────
    pv  = acct.get("portfolio_value") or 0
    bp  = acct.get("buying_power") or 0
    pnl_color = "green" if total_realized >= 0 else "red"
    pnl_sign  = "+" if total_realized >= 0 else ""

    console.print("  [bold]Portfolio[/bold]")
    console.print(f"  Value:          [bold]${pv:>12,.0f}[/bold]    Buying Power:  [bold]${bp:>12,.0f}[/bold]")
    console.print(f"  Premium (open): [bold]${premium_open:>12,.0f}[/bold]    Realized P&L:  [{pnl_color}]{pnl_sign}${abs(total_realized):>11,.0f}[/{pnl_color}]")
    if pv > 0:
        pct_deployed = round(premium_open / pv * 100, 1)
        console.print(f"  [dim]Cash deployed: {pct_deployed:.1f}% of portfolio[/dim]")
    console.print()

    # ── Positions panel ───────────────────────────────────────────────────────
    strat_str = "  ".join(f"[dim]{s}:[/dim] {n}" for s, n in sorted(strats.items()))
    flag_str  = (
        f"[green]● {flag_counts['GREEN']} GREEN[/green]  "
        f"[yellow]● {flag_counts['YELLOW']} YELLOW[/yellow]  "
        f"[red]● {flag_counts['RED']} RED[/red]"
    )

    console.print(f"  [bold]Positions[/bold]  [dim]({len(open_pos)} open  |  {closed_count} closed)[/dim]")
    if strat_str:
        console.print(f"  {strat_str}")
    checks_total = sum(flag_counts.values())
    if checks_total == len(open_pos):
        console.print(f"  {flag_str}  [dim](from last check)[/dim]")
    elif checks_total > 0:
        console.print(f"  {flag_str}  [dim](partial — run helm check for full update)[/dim]")
    else:
        console.print(f"  [dim]No check data — run helm check to assess positions[/dim]")
    console.print()

    # ── Expiring soon ─────────────────────────────────────────────────────────
    expiring = []
    for p in open_pos:
        leg = pos_legs.get(p["id"])
        if not leg:
            continue
        days = dte(leg["expiration"])
        if days <= 30:
            chk = latest_checks.get(p["id"], {})
            expiring.append({
                "ticker":   p["ticker"],
                "strategy": p["strategy"],
                "leg":      leg,
                "dte":      days,
                "flag":     chk.get("health_flag", "--"),
                "pnl_pct":  chk.get("pnl_pct"),
            })

    expiring.sort(key=lambda x: x["dte"])

    if expiring:
        console.print("  [bold]Expiring Within 30 Days[/bold]")
        t = Table(box=box.SIMPLE, show_header=False, padding=(0,1))
        t.add_column(width=6)
        t.add_column(width=13)
        t.add_column(width=10)
        t.add_column(justify="right", width=5)
        t.add_column(justify="right", width=8)
        t.add_column(width=8)

        for e in expiring:
            leg = e["leg"]
            contract = f"{leg['option_type'][0]}{leg['strike']:.0f} {leg['expiration'][5:]}"
            flag = e["flag"]
            flag_colors = {"GREEN": "green", "YELLOW": "yellow", "RED": "red"}
            fc = flag_colors.get(flag, "dim")
            pnl_str = f"{e['pnl_pct']:+.1f}%" if e["pnl_pct"] is not None else "--"
            pnl_color = "green" if (e["pnl_pct"] or 0) >= 0 else "red"
            dte_color = "red" if e["dte"] <= 7 else "yellow" if e["dte"] <= 21 else "dim"

            t.add_row(
                f"[bold]{e['ticker']}[/bold]",
                f"[dim]{e['strategy']}[/dim]",
                f"[dim]{contract}[/dim]",
                f"[{dte_color}]{e['dte']}d[/{dte_color}]",
                f"[{pnl_color}]{pnl_str}[/{pnl_color}]",
                f"[{fc}]● {flag}[/{fc}]",
            )
        console.print(t)
        console.print()

    # ── Universe panel ────────────────────────────────────────────────────────
    console.print("  [bold]Universe[/bold]")
    console.print(f"  Watchlist: [bold]{watchlist_count}[/bold] tickers    Themes: [bold]{themes_count}[/bold]")
    console.print()

    # ── Last activity ─────────────────────────────────────────────────────────
    def fmt_time(ts):
        if not ts:
            return "[dim]never[/dim]"
        try:
            dt = datetime.fromisoformat(ts)
            delta = (datetime.now() - dt).total_seconds()
            if delta < 3600:
                return f"[green]{int(delta/60)}m ago[/green]"
            elif delta < 86400:
                return f"[green]{int(delta/3600)}h ago[/green]"
            else:
                return f"[dim]{int(delta/86400)}d ago[/dim]"
        except Exception:
            return "[dim]--[/dim]"

    console.print("  [bold]Last Activity[/bold]")
    console.print(f"  helm check:      {fmt_time(last_check)}")
    console.print(f"  helm reconcile:  {fmt_time(last_reconcile)}")
    console.print(f"  helm scan:       {fmt_time(last_scan)}")
    console.print()

    # ── Nudges ────────────────────────────────────────────────────────────────
    try:
        from helm.models.theme import check_nudges
        nudges = check_nudges()
        if nudges:
            for n in nudges:
                console.print(f"  {n}")
            console.print()
    except Exception:
        pass

    # ── Quick actions ─────────────────────────────────────────────────────────
    # Strategy CHECK reconciliation -- silent unless canonical (helm/strategies.py) and DB disagree
    from helm.strategies import verify_strategy_checks
    _drift = verify_strategy_checks()
    if not _drift["ok"]:
        console.print()
        console.print("  [red]Strategy CHECK drift[/red] [dim](helm/strategies.py vs DB)[/dim]")
        for _tbl, _d in _drift["tables"].items():
            if _d["missing"] or _d["extra"]:
                console.print(f"    [red]{_tbl}: missing={_d['missing']} extra={_d['extra']}[/red]")
        console.print()

    console.print("  [dim]Quick actions:[/dim]")
    console.print("  [dim]  helm check        Monitor all positions[/dim]")
    console.print("  [dim]  helm scan         Scan for opportunities[/dim]")
    console.print("  [dim]  helm reconcile    Verify Fidelity alignment[/dim]")
    console.print("  [dim]  helm activity     Import new trades[/dim]")
    console.print()


if __name__ == "__main__":
    run()
