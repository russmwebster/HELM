"""
helm open TICKER PERM

Pre-Earnings Run-up (PERM) — long call entered 2-6 weeks before earnings,
exited 1 day before the announcement to capture the price run-up and IV
expansion without taking binary earnings risk.

Key rules:
  - Enter:  7-42 days before earnings
  - Option: expires AFTER earnings (retains value during the run)
  - Exit:   1 day before earnings — hard rule, no exceptions
  - Stop:   -40% of premium paid
"""
from datetime import date, datetime, timedelta
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich import box

console = Console()

PERM_CONFIG = {
    "option_type":              "CALL",
    "label":                    "Pre-Earnings Run-up (PERM)",
    "delta_min":                0.35,
    "delta_max":                0.65,
    "delta_sweet":              0.50,
    # Entry window
    "min_days_to_earnings":     7,
    "max_days_to_earnings":     42,
    # Option must expire at least N days after earnings (IV premium held during run)
    "min_dte_past_earnings":    7,
    "max_dte_past_earnings":    60,
    # Exit & stop
    "days_before_earnings_exit": 1,
    "stop_pct":                 0.40,
}


def _get_earnings_date(ticker: str):
    """Fetch next upcoming earnings date from yfinance. Returns date or None."""
    import yfinance as yf
    try:
        cal = yf.Ticker(ticker).calendar
        if isinstance(cal, dict) and "Earnings Date" in cal:
            dates = cal["Earnings Date"]
            if dates:
                d = date.fromisoformat(str(dates[0])[:10])
                if d >= date.today():
                    return d
    except Exception:
        pass
    return None


def evaluate_perm(ticker: str) -> tuple:
    """
    Validate PERM timing and find suitable long call candidates.
    Returns (spot, earnings_date, days_to_earnings, exit_date, candidates).
    """
    import yfinance as yf
    cfg = PERM_CONFIG
    today = date.today()

    console.print(f"  [dim]Fetching earnings date for {ticker}...[/dim]")
    earnings_date = _get_earnings_date(ticker)
    if earnings_date is None:
        raise RuntimeError("No upcoming earnings date found. Cannot evaluate PERM.")

    days_to_earnings = (earnings_date - today).days
    console.print(
        f"  Earnings: [bold yellow]{earnings_date}[/bold yellow]"
        f"  ({days_to_earnings}d away)"
    )

    if days_to_earnings < cfg["min_days_to_earnings"]:
        raise RuntimeError(
            f"Earnings in {days_to_earnings}d — too close for PERM entry "
            f"(minimum {cfg['min_days_to_earnings']}d). Exit any existing PERM now."
        )
    if days_to_earnings > cfg["max_days_to_earnings"]:
        raise RuntimeError(
            f"Earnings in {days_to_earnings}d — too early for PERM entry "
            f"(enter within {cfg['max_days_to_earnings']}d of earnings)."
        )

    exit_date = earnings_date - timedelta(days=cfg["days_before_earnings_exit"])
    console.print(f"  Exit by:  [bold red]{exit_date}[/bold red]  [dim](1 day before earnings)[/dim]")

    console.print(f"  [dim]Fetching options chain for {ticker}...[/dim]")
    tk = yf.Ticker(ticker)
    info = tk.fast_info
    spot = getattr(info, "last_price", None) or getattr(info, "previous_close", 0) or 0

    candidates = []
    for exp in tk.options:
        try:
            exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
            dte = (exp_date - today).days
        except Exception:
            continue

        days_past = (exp_date - earnings_date).days
        if not (cfg["min_dte_past_earnings"] <= days_past <= cfg["max_dte_past_earnings"]):
            continue

        try:
            calls = tk.option_chain(exp).calls
        except Exception:
            continue

        mask = (
            calls["delta"].notna() &
            (calls["delta"] >= cfg["delta_min"]) &
            (calls["delta"] <= cfg["delta_max"])
        )
        for _, row in calls[mask].iterrows():
            bid = float(row.get("bid", 0) or 0)
            ask = float(row.get("ask", 0) or 0)
            mid = round((bid + ask) / 2, 2) if bid > 0 and ask > 0 else float(row.get("lastPrice", 0) or 0)
            if mid <= 0:
                continue
            candidates.append({
                "strike":       float(row["strike"]),
                "expiration":   exp,
                "dte":          dte,
                "days_past":    days_past,
                "delta":        float(row["delta"]),
                "bid":          bid,
                "ask":          ask,
                "mid":          mid,
                "iv":           float(row.get("impliedVolatility", 0) or 0) * 100,
                "volume":       int(row.get("volume", 0) or 0),
                "oi":           int(row.get("openInterest", 0) or 0),
            })

    if not candidates:
        raise RuntimeError(
            "No suitable contracts found. Option must expire 7-60 days after earnings "
            "with delta 0.35-0.65."
        )

    # Sort by delta closest to 0.50, then by cost
    candidates.sort(key=lambda c: (abs(c["delta"] - cfg["delta_sweet"]), c["mid"]))
    return spot, earnings_date, days_to_earnings, exit_date, candidates[:10]


def display_perm(ticker: str, spot: float, earnings_date, days_to_earnings: int,
                 exit_date, candidates: list, args: list):
    """Display PERM candidates and handle confirm/log flow."""
    console.print()
    console.print(
        f"  [bold]{ticker}[/bold]  Pre-Earnings Run-up (PERM)  \u00b7  "
        f"spot [bold cyan]${spot:.2f}[/bold cyan]"
    )
    console.print()

    tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold dim")
    tbl.add_column("#",         width=3)
    tbl.add_column("Exp",       width=6)
    tbl.add_column("DTE",       width=5)
    tbl.add_column("+earn",     width=6)
    tbl.add_column("Strike",    width=8)
    tbl.add_column("\u03b4",    width=6)
    tbl.add_column("Bid",       width=7)
    tbl.add_column("Ask",       width=7)
    tbl.add_column("Mid",       width=7)
    tbl.add_column("IV%",       width=6)
    tbl.add_column("OI",        width=7)

    for i, c in enumerate(candidates, 1):
        exp_short = c["expiration"][5:]
        tbl.add_row(
            f"#{i}",
            exp_short,
            str(c["dte"]),
            f"+{c['days_past']}d",
            f"${c['strike']:.0f}",
            f"{c['delta']:.2f}",
            f"${c['bid']:.2f}",
            f"${c['ask']:.2f}",
            f"[bold cyan]${c['mid']:.2f}[/bold cyan]",
            f"{c['iv']:.0f}%",
            f"{c['oi']:,}",
        )

    console.print(tbl)
    console.print(
        f"  [dim]Option expires [bold]+earn[/bold] days past earnings — "
        f"holds IV premium through the run.[/dim]"
    )
    console.print(
        f"  [dim]Exit by [bold red]{exit_date}[/bold red] \u2014 "
        f"do not hold through earnings. Stop: -40% of premium.[/dim]"
    )
    console.print()

    raw = "--confirm" in args
    if not raw:
        console.print(
            f"  [dim]To log: [bold]helm open {ticker} PERM --confirm[/bold][/dim]"
        )
        return

    choice = Prompt.ask(
        "  Select contract",
        choices=[str(i) for i in range(1, len(candidates) + 1)] + ["n"],
        default="1",
    )
    if choice == "n":
        console.print("[dim]  Cancelled.[/dim]")
        return

    sel = candidates[int(choice) - 1]
    contracts = int(Prompt.ask("  Contracts", default="1"))
    mid = sel["mid"]
    premium = round(mid * contracts * 100, 2)
    stop_loss = round(premium * PERM_CONFIG["stop_pct"], 2)

    console.print()
    console.print(
        f"  [bold]{ticker}[/bold] PERM  |  "
        f"Buy {contracts}\u00d7 ${sel['strike']:.0f} {sel['expiration']} call @ ${mid:.2f}"
    )
    console.print(f"  Net debit:  [yellow]-${premium:.2f}[/yellow]")
    console.print(f"  Max loss:   [red]-${premium:.2f}[/red]  (full premium if held to expiry)")
    console.print(f"  Stop:       [yellow]-${stop_loss:.2f}[/yellow]  (-40%)")
    console.print(f"  Exit rule:  Close by [bold red]{exit_date}[/bold red] — no exceptions")
    console.print()

    if not Confirm.ask("  Confirm and log?", default=True):
        console.print("[dim]  Cancelled.[/dim]")
        return

    from helm.models.position import Position
    from helm.models.leg import Leg
    from helm.config import get_active_account
    from helm.db import get_conn

    acct = get_active_account()
    conn = get_conn()
    row = conn.execute("SELECT id FROM accounts WHERE name = ?", (acct,)).fetchone()
    if not row:
        console.print("[red]No active account found.[/red]")
        return
    account_id = row[0]

    pos = Position.create(
        account_id=account_id,
        ticker=ticker,
        strategy="PERM",
        status="OPEN",
        total_contracts=contracts,
        net_premium=-premium,
        earnings_date=str(earnings_date),
        notes=(
            f"PERM: long ${sel['strike']:.0f} {sel['expiration']} call | "
            f"exit by {exit_date}"
        ),
    )

    Leg.create(
        position_id=pos.id,
        leg_role="LONG_CALL",
        direction="LONG",
        open_price=mid,
        option_type="CALL",
        strike=sel["strike"],
        expiration=sel["expiration"],
        contracts=contracts,
        multiplier=100,
    )

    console.print()
    console.print(f"  [green]OK[/green]  {ticker} PERM logged \u2014 {pos.id}")
    console.print(
        f"  [dim]Execute in Fidelity, then run [bold]helm activity[/bold] to confirm.[/dim]"
    )
    console.print(
        f"  [dim]Set a reminder: close by [bold]{exit_date}[/bold] \u2014 "
        f"do not hold through earnings.[/dim]"
    )
    console.print()
