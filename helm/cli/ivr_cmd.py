"""
helm ivr -- IV Rank and IV Percentile management

Commands:
  helm ivr refresh          Fetch IV history from IBKR, compute IVR/IVP for all watchlist tickers
  helm ivr refresh TICK...  Refresh specific tickers only
  helm ivr list             Show latest IVR/IVP for all tickers
  helm ivr show TICKER      Show IVR detail for one ticker
"""

import sys
import time
from datetime import date, datetime
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich import box

from helm.models.iv_history import IVHistory
from helm.db import get_conn

console = Console()

LOOKBACK_DAYS = 365
SLEEP_BETWEEN = 0.5   # seconds between batches
BATCH_SIZE    = 12    # concurrent requests per batch


# ── IBKR fetch ────────────────────────────────────────────────────────────────

def _fetch_iv_history(ib, ticker: str):
    """
    Fetch 365 days of daily IV history from IBKR for one ticker.
    Returns a pandas Series of IV values (in %) or None on failure.
    """
    import pandas as pd
    from ib_insync import Stock, util

    contract = Stock(ticker, 'SMART', 'USD')
    try:
        bars = ib.reqHistoricalData(
            contract,
            endDateTime='',
            durationStr='1 Y',
            barSizeSetting='1 day',
            whatToShow='OPTION_IMPLIED_VOLATILITY',
            useRTH=True,
            formatDate=1,
            keepUpToDate=False,
        )
        if not bars:
            return None

        df = util.df(bars)
        if df is None or df.empty or 'close' not in df.columns:
            return None

        # IBKR returns IV as decimal (0.35 = 35%)
        iv_series = df['close'].dropna()
        iv_series = iv_series[iv_series > 0] * 100  # convert to %

        return iv_series if len(iv_series) >= 30 else None

    except Exception:
        return None


# ── Refresh ───────────────────────────────────────────────────────────────────



def _fetch_iv_batch_async(ib, tickers: list) -> dict:
    """
    Fetch IV history for a batch of tickers concurrently using ib_insync async.
    Uses ib_insync.util.run() to execute coroutines on the existing event loop.
    Returns dict: {ticker: iv_series or None}
    """
    import pandas as pd
    from ib_insync import Stock, util as ib_util

    async def _fetch_all():
        async def _one(ticker):
            contract = Stock(ticker, 'SMART', 'USD')
            try:
                bars = await ib.reqHistoricalDataAsync(
                    contract,
                    endDateTime='',
                    durationStr='1 Y',
                    barSizeSetting='1 day',
                    whatToShow='OPTION_IMPLIED_VOLATILITY',
                    useRTH=True,
                    formatDate=1,
                    keepUpToDate=False,
                )
                if not bars:
                    return ticker, None
                df = ib_util.df(bars)
                if df is None or df.empty or 'close' not in df.columns:
                    return ticker, None
                iv = df['close'].dropna()
                if len(iv) < 30:
                    return ticker, None
                if iv.max() <= 5:
                    iv = iv * 100
                return ticker, iv
            except Exception:
                return ticker, None

        import asyncio
        tasks = [_one(t) for t in tickers]
        raw = await asyncio.gather(*tasks, return_exceptions=True)
        results = {}
        for item in raw:
            if isinstance(item, Exception):
                continue
            ticker, series = item
            results[ticker] = series
        return results

    return ib_util.run(_fetch_all())


def _chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def cmd_refresh(args: list) -> None:
    """Fetch IV history from IBKR and compute IVR/IVP for watchlist tickers."""
    from helm.ibkr import get_ib

    # Determine which tickers to refresh
    if args:
        tickers = [t.upper() for t in args if not t.startswith('--')]
    else:
        conn = get_conn()
        rows = conn.execute("SELECT ticker FROM watchlist ORDER BY ticker").fetchall()
        tickers = [r['ticker'] for r in rows]

    if not tickers:
        console.print("[yellow]No tickers found in watchlist.[/yellow]")
        return

    force = '--force' in args

    # Filter to stale tickers unless --force
    if not force:
        stale = []
        for t in tickers:
            days = IVHistory.staleness_days(t)
            if days is None or days >= 1:
                stale.append(t)
        tickers = stale
        if not tickers:
            console.print("[green]All tickers are up to date.[/green] Use --force to refresh anyway.")
            return

    console.print()
    console.print(Panel.fit(
        f"[bold cyan]HELM IVR Refresh[/bold cyan]\n"
        f"[dim]Fetching 365d IV history from IBKR for {len(tickers)} tickers[/dim]",
        border_style="cyan"
    ))
    console.print()

    # Connect to IBKR using dedicated IVR clientId (17) to avoid conflicts
    try:
        from ib_insync import IB
        ib = IB()
        ib.connect('127.0.0.1', 4002, clientId=17, timeout=15)
        ib.reqMarketDataType(2)  # frozen data — works pre-market and post-market
        import atexit; atexit.register(lambda: ib.disconnect() if ib.isConnected() else None)
    except Exception as e:
        console.print(f"[red]Could not connect to IBKR:[/red] {e}")
        console.print("[dim]Make sure TWS or IB Gateway is running on port 4002.[/dim]")
        return

    today = date.today().isoformat()
    results = {'ok': 0, 'fail': 0, 'skip': 0}
    results = {'ok': 0, 'fail': 0, 'skip': 0}
    failures = []
    batches = list(_chunks(tickers, BATCH_SIZE))

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"Fetching IV data...", total=len(tickers))

        for batch_num, batch in enumerate(batches, 1):
            preview = ', '.join(batch[:4]) + ('...' if len(batch) > 4 else '')
            progress.update(task,
                description=f"[cyan]Batch {batch_num}/{len(batches)}[/cyan] [{preview}]")

            batch_results = _fetch_iv_batch_async(ib, batch)

            for ticker in batch:
                iv_series = batch_results.get(ticker)
                if iv_series is None:
                    failures.append(ticker)
                    results['fail'] += 1
                else:
                    computed = IVHistory.compute(iv_series)
                    IVHistory.upsert(ticker, computed, as_of_date=today)
                    results['ok'] += 1
                progress.advance(task)

            if batch_num < len(batches):
                ib.sleep(SLEEP_BETWEEN)

    try:
        ib.disconnect()
    except Exception:
        pass

    console.print()
    console.print(f"  [green]✓[/green]  {results['ok']} tickers updated")
    if failures:
        console.print(f"  [yellow]![/yellow]  {results['fail']} failed: {', '.join(failures)}")
    console.print()


# ── List ──────────────────────────────────────────────────────────────────────

def cmd_list(args: list) -> None:
    """Show latest IVR/IVP for all tickers with data."""
    all_ivr = IVHistory.all_latest()

    if not all_ivr:
        console.print("[yellow]No IV data found. Run [bold]helm ivr refresh[/bold] first.[/yellow]")
        return

    # Sort options
    sort_by = 'rank'
    for a in args:
        if a in ('--rank', '--percentile', '--iv', '--ticker'):
            sort_by = a.lstrip('-')

    items = list(all_ivr.values())
    if sort_by == 'percentile':
        items.sort(key=lambda x: -(x.iv_percentile or 0))
    elif sort_by == 'iv':
        items.sort(key=lambda x: -(x.iv_current or 0))
    elif sort_by == 'ticker':
        items.sort(key=lambda x: x.ticker)
    else:
        items.sort(key=lambda x: -(x.iv_rank or 0))

    tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold dim")
    tbl.add_column("Ticker", style="bold", width=8)
    tbl.add_column("IV%", justify="right")
    tbl.add_column("IVR", justify="right")
    tbl.add_column("IVP", justify="right")
    tbl.add_column("52wk lo", justify="right")
    tbl.add_column("52wk hi", justify="right")
    tbl.add_column("Days", justify="right")
    tbl.add_column("Updated", justify="right")

    for ivr in items:
        days_old = IVHistory.staleness_days(ivr.ticker)
        age = ("[green]today[/green]" if days_old == 0 else f"[dim]{days_old}d ago[/dim]") if days_old is not None else "[dim]--[/dim]"
        tbl.add_row(
            ivr.ticker,
            f"{ivr.iv_current:.1f}%" if ivr.iv_current else "--",
            ivr.rank_label,
            ivr.percentile_label,
            f"{ivr.iv_52wk_low:.1f}%" if ivr.iv_52wk_low else "--",
            f"{ivr.iv_52wk_high:.1f}%" if ivr.iv_52wk_high else "--",
            str(ivr.days_history) if ivr.days_history else "--",
            age,
        )

    console.print()
    console.print(f"[bold]IV Rank / Percentile[/bold]  ({len(items)} tickers)")
    console.print("[dim]IVR and IVP shown as raw numbers — use helm scan to combine with directional signals for strategy[/dim]")
    console.print()
    console.print(tbl)
    console.print()


# ── Show ──────────────────────────────────────────────────────────────────────

def cmd_show(args: list) -> None:
    """Show IVR detail for one ticker."""
    if not args:
        console.print("[red]Usage:[/red] helm ivr show <TICKER>")
        return

    ticker = args[0].upper()
    ivr = IVHistory.latest(ticker)

    if not ivr:
        console.print(f"[yellow]No IV data for {ticker}. Run [bold]helm ivr refresh {ticker}[/bold] first.[/yellow]")
        return

    days_old = IVHistory.staleness_days(ticker)
    age_str = f"{days_old}d ago" if days_old is not None else "unknown"

    console.print()
    console.print(Panel(
        f"[bold]{ticker}[/bold]  IV History  ·  as of {ivr.date} ({age_str})\n\n"
        f"  Current IV:    [bold]{ivr.iv_current:.1f}%[/bold]\n"
        f"  IV Rank:       {ivr.rank_label}  [dim](0=low, 100=high)[/dim]\n"
        f"  IV Percentile: {ivr.percentile_label}  [dim](% of days below current)[/dim]\n"
        f"  52wk low:      {ivr.iv_52wk_low:.1f}%\n"
        f"  52wk high:     {ivr.iv_52wk_high:.1f}%\n"
        f"  Days history:  {ivr.days_history}",
        title="[bold]IVR Detail[/bold]",
        border_style="cyan",
        expand=False,
    ))
    console.print()


# ── Entry point ───────────────────────────────────────────────────────────────

def run() -> None:
    args = sys.argv[1:]

    if not args or args[0] in ('-h', '--help'):
        console.print("\n[bold]Usage:[/bold]  helm ivr <command>\n")
        console.print("  refresh [TICKERS]   Fetch IV history from IBKR, compute IVR/IVP")
        console.print("  list                Show latest IVR/IVP for all tickers")
        console.print("  show <TICKER>       Show IVR detail for one ticker")
        console.print("\n[dim]  --force   Re-fetch even if data is current[/dim]\n")
        return

    cmd = args[0].lower()
    rest = args[1:]

    if cmd == 'refresh':
        cmd_refresh(rest)
    elif cmd == 'list':
        cmd_list(rest)
    elif cmd == 'show':
        cmd_show(rest)
    else:
        console.print(f"[red]Unknown ivr command:[/red] {cmd}")
        console.print("[dim]Run [bold]helm ivr --help[/bold] for usage.[/dim]")
