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

# W171 (s120): one retry when the morning pass comes back short. The 09:35 slot
# sits inside the gateway's warm-up window -- on 2026-09-04 it refused until
# ~09:20 after an overnight outage and the whole watchlist went a day stale
# with nothing on the board saying so. One retry, once, at RETRY_AT. Not 10:05:
# the 10:00 snapshot is still journaling until ~10:08 and this fetch is 107
# historical-data requests; 10:15 lands after it. --no-retry disables it
# (interactive use, tests). The retry is a SECOND ledger row whose note starts
# "retry of HH:MM", which is how `helm audit eod` tells it from a launchd
# catch-up firing at wake (W114/W96).
RETRY_AT = (10, 15)
RETRY_WINDOW_MIN = 60    # never sleep longer than this waiting for RETRY_AT


# ── IBKR fetch ────────────────────────────────────────────────────────────────

def _fetch_iv_history(ib, ticker: str):
    """
    Fetch 365 days of daily IV history from IBKR for one ticker.
    Returns a pandas Series of IV values (in %) or None on failure.
    """
    import pandas as pd
    from ib_insync import Stock, util
    from helm.ibkr import to_ibkr_symbol

    contract = Stock(to_ibkr_symbol(ticker), 'SMART', 'USD')
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
    from helm.ibkr import to_ibkr_symbol

    async def _fetch_all():
        async def _one(ticker):
            contract = Stock(to_ibkr_symbol(ticker), 'SMART', 'USD')
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

def _refresh_pass(tickers: list, today: str):
    """ONE fetch pass over `tickers`. Returns (results, failures, connected).

    connected=False means the gateway refused; nothing was fetched and every
    ticker counts as failed. The caller records the ledger row -- until s120
    this branch printed and returned, leaving no row at all (W113's shape:
    the worse the failure, the less evidence it leaves)."""
    results = {'ok': 0, 'fail': 0, 'skip': 0}
    failures = []
    try:
        from ib_insync import IB
        ib = IB()
        ib.connect('127.0.0.1', 4002, clientId=17, timeout=15)
        ib.reqMarketDataType(2)  # frozen data — works pre-market and post-market
        import atexit; atexit.register(lambda: ib.disconnect() if ib.isConnected() else None)
    except Exception as e:
        console.print(f"[red]Could not connect to IBKR:[/red] {e}")
        console.print("[dim]Make sure TWS or IB Gateway is running on port 4002.[/dim]")
        results['fail'] = len(tickers)
        return results, list(tickers), False

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
    return results, failures, True


def _record_ivr_run(started, attempted, ok, failed, notes):
    """One ledger row. Not silent on failure -- this whole path exists to stop
    silent gaps (s100)."""
    try:
        from helm import agent_runs as _ar
        _c = get_conn()
        _ar.ensure_table(_c)
        _ar.record_run(_c, _ar.AGENT_IVR, started, datetime.now().isoformat(),
                       attempted, ok, failed, notes=notes)
        _c.close()
    except Exception as _e:
        console.print('  [yellow]ledger write failed:[/yellow] %s' % _e)


def _seconds_until(hhmm, now=None):
    """Seconds from `now` to today's hh:mm; negative if it has passed."""
    now = now or datetime.now()
    target = now.replace(hour=hhmm[0], minute=hhmm[1], second=0, microsecond=0)
    return (target - now).total_seconds()


def cmd_refresh(args: list) -> None:
    """Fetch IV history from IBKR and compute IVR/IVP for watchlist tickers."""
    _ivr_started = datetime.now().isoformat()
    # W110 (s102): the IV refresh has no Weekday key at all, so it fires every
    # calendar day. Holidays are handled here; weekends are decision 1, a plist
    # change. A weekend/holiday reading skews low and overwrites the stored row
    # that `helm scan` reads.
    from helm.market_calendar import agent_should_run, stand_down, AGENT_IVR
    _run_ok, _why = agent_should_run()
    if not _run_ok:
        print("ivr refresh: market closed (%s) -- standing down" % _why)
        stand_down(AGENT_IVR, _ivr_started, _why)
        return
    # HELM-037: refresh open-position earnings_date on this pre-market run
    # (moved off /health render so /health is read-only)
    from helm.earnings import _refresh_earnings as _refresh_position_earnings
    from helm.db import get_conn as _earnings_conn
    _refresh_position_earnings(_earnings_conn())

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
    no_retry = '--no-retry' in args

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

    today = date.today().isoformat()
    results, failures, connected = _refresh_pass(tickers, today)

    # s100: record this run in the ledger. It used to leave only a log
    # file, so a morning it never fired read the same as a quiet one --
    # and it fires inside the gateway warm-up window, where failing is
    # the documented risk. s120: the could-not-connect branch records too.
    note = ("could not connect to IBKR" if not connected
            else ("; ".join(failures[:12]) or None))
    _record_ivr_run(_ivr_started, len(tickers), results['ok'], results['fail'], note)
    console.print()
    console.print(f"  [green]✓[/green]  {results['ok']} tickers updated")
    if failures:
        console.print(f"  [yellow]![/yellow]  {results['fail']} failed: {', '.join(failures)}")
    console.print()

    # W171: one retry, once, if the pass came back short and RETRY_AT is
    # still ahead of us (and not absurdly far -- an interactive run at 06:00
    # must not sit for four hours).
    if not failures or no_retry:
        return
    wait = _seconds_until(RETRY_AT)
    if wait < 0 or wait > RETRY_WINDOW_MIN * 60:
        console.print(f"  [dim]retry not scheduled: {RETRY_AT[0]:02d}:{RETRY_AT[1]:02d} "
                      f"is {'past' if wait < 0 else 'too far ahead'}[/dim]")
        return
    console.print(f"  [cyan]retrying {len(failures)} at "
                  f"{RETRY_AT[0]:02d}:{RETRY_AT[1]:02d}[/cyan] (in {int(wait // 60)} min)")
    time.sleep(max(wait, 0))
    _retry_started = datetime.now().isoformat()
    first_hhmm = _ivr_started[11:16]
    results2, failures2, connected2 = _refresh_pass(list(failures), today)
    recovered = len(failures) - len(failures2)
    note2 = ("retry of %s: recovered %d of %d" % (first_hhmm, recovered, len(failures))
             + ("; still failing: " + "; ".join(failures2[:12]) if failures2 else "")
             + ("" if connected2 else "; could not connect to IBKR"))
    _record_ivr_run(_retry_started, len(failures), results2['ok'], results2['fail'], note2)
    console.print(f"  [green]✓[/green]  retry: {recovered} of {len(failures)} recovered")
    if failures2:
        console.print(f"  [yellow]![/yellow]  still failing: {', '.join(failures2)}")
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
    console.print("[dim]IVR (IV Rank): 0-100 scale of where current IV sits in its own 52-week range. 0=at 52wk low, 100=at 52wk high.[/dim]")
    console.print("[dim]IVP (IV Percentile): % of trading days in the past year where IV was lower than today. IVP 90 = IV higher than 90% of past year.[/dim]")
    console.print("[dim]High IVR/IVP = elevated premium. Low = compressed. Direction still matters -- use helm scan for strategy.[/dim]")
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
        console.print("\n[dim]  --force     Re-fetch even if data is current[/dim]")
        console.print("[dim]  --no-retry  Do not retry a short pass at 10:15 (W171)[/dim]\n")
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
