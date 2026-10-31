"""W171 (s120): `helm ivr refresh` retries ONCE, in-process, when the 09:35 pass
comes back short -- and a pass that could not connect now leaves a ledger row.

Dry-run by default; --apply writes with a backup + readback + py_compile.
"""
import re, sys, shutil, py_compile, time
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('--') else '.')
APPLY = '--apply' in sys.argv
F = ROOT / 'helm' / 'cli' / 'ivr_cmd.py'
src = F.read_text()

# ---- 1. constants -----------------------------------------------------------
old_const = "BATCH_SIZE    = 12    # concurrent requests per batch\n"
new_const = old_const + '''
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
'''
assert src.count(old_const) == 1
src = src.replace(old_const, new_const)

# ---- 2. split cmd_refresh into a pass + a driver ----------------------------
start = src.index("def cmd_refresh(args: list) -> None:")
end = src.index("# ── List ───")
body = src[start:end]

new_body = '''def _refresh_pass(tickers: list, today: str):
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
        f"[bold cyan]HELM IVR Refresh[/bold cyan]\\n"
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


'''
src = src[:start] + new_body + src[end:]

# ---- 3. usage line -----------------------------------------------------------
old_usage = '        console.print("\\n[dim]  --force   Re-fetch even if data is current[/dim]\\n")\n'
new_usage = ('        console.print("\\n[dim]  --force     Re-fetch even if data is current[/dim]")\n'
             '        console.print("[dim]  --no-retry  Do not retry a short pass at 10:15 (W171)[/dim]\\n")\n')
assert src.count(old_usage) == 1, src.count(old_usage)
src = src.replace(old_usage, new_usage)

print("would write %d bytes to %s" % (len(src), F))
if APPLY:
    bak = F.with_name(F.name + '.bak-s120-' + time.strftime('%Y%m%d-%H%M%S'))
    shutil.copy2(F, bak)
    F.write_text(src)
    py_compile.compile(str(F), doraise=True)
    rb = F.read_text()
    assert rb == src and 'RETRY_AT = (10, 15)' in rb and rb.count('def cmd_refresh') == 1
    assert rb.count('def _refresh_pass') == 1 and 'could not connect to IBKR' in rb
    print("applied; backup", bak.name, "; py_compile ok; readback ok")
else:
    print("dry run -- pass --apply")
