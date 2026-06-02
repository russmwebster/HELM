
# helm/cli/screen.py
# helm screen -- optionability screen for watchlist tickers
#
# First-pass filter: is this ticker worthy of further scrutiny?
# Criteria (ALL must pass):
#   1. Total OI across 4 near-term expiries >= min_oi (default 5,000)
#   2. ATM spread % on nearest expiry <= max_spread_pct (default 15%)
#   3. Avg daily options volume >= min_volume (default 500)
#   4. IV available from yfinance
#
# Also fetches and stores fundamentals:
#   market_cap, avg_daily_volume, 52wk high/low, beta, dividend_yield, next_earnings
#
# Concurrent: uses ThreadPoolExecutor (default 20 workers) for speed.
# 300 tickers ~5-8 seconds vs ~90 seconds sequential.

import sys, time
from pathlib import Path
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich import box

import logging, warnings
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
warnings.filterwarnings('ignore')
from helm.config import get_active_account
from helm.models.watchlist import WatchlistItem

console = Console()

DEFAULT_MIN_OI         = 500
DEFAULT_MAX_SPREAD_PCT = 15.0
DEFAULT_MIN_VOLUME     = 500
DEFAULT_WORKERS        = 8
EXPIRIES_TO_SCAN       = 4


def oi_tier(total_oi):
    if total_oi >= 200_000: return "1", "[green]Tier 1[/green]"
    elif total_oi >= 50_000: return "2", "[cyan]Tier 2[/cyan]"
    elif total_oi >= 500:   return "3", "[yellow]Tier 3[/yellow]"
    else:                     return "X", "[red]Below min[/red]"


def fetch_fundamentals(tk, info):
    """Fetch fundamental data from yfinance fast_info and info."""
    try:
        market_cap = getattr(info, "market_cap", None)
        if market_cap: market_cap = round(market_cap / 1e9, 2)
        avg_vol = getattr(info, "three_month_average_volume", None)
        high_52 = getattr(info, "fifty_two_week_high", None)
        low_52  = getattr(info, "fifty_two_week_low", None)
        beta, div_yield, next_earn = None, None, None
        try:
            full = tk.info
            beta = full.get("beta")
            div_yield = full.get("dividendYield")
            next_earn = full.get("earningsDate")
            if isinstance(next_earn, list) and next_earn:
                next_earn = str(next_earn[0])[:10]
            elif next_earn:
                next_earn = str(next_earn)[:10]
        except Exception:
            pass
        return {
            "market_cap": market_cap,
            "avg_daily_volume": avg_vol,
            "week_52_high": high_52,
            "week_52_low": low_52,
            "beta": round(beta, 2) if beta else None,
            "dividend_yield": round(div_yield, 4) if div_yield else None,
            "next_earnings": next_earn,
        }
    except Exception:
        return {}



def _friendly_fail(e):
    msg = str(e)
    if 'exchangeTimezoneName' in msg or 'regularMarketPrice' in msg:
        return 'Not found in market data — may be a company name, delisted, or OTC'
    if 'No timezone' in msg or 'NoneType' in msg:
        return 'No market data available'
    if 'HTTPError' in msg or '404' in msg:
        return 'Ticker not found'
    return f'Data error: {msg[:60]}'

def screen_ticker(ticker, min_oi, max_spread_pct, min_volume):
    import yfinance as yf

    result = {
        "ticker": ticker, "passed": False, "fail_reason": None,
        "total_oi": 0, "call_oi": 0, "put_oi": 0, "avg_volume": 0,
        "atm_spread_pct": None, "atm_mid": None,
        "iv_available": False, "iv_current": None,
        "tier": "X", "tier_label": "[red]Below min[/red]",
        "spot_price": None, "error": None, "fundamentals": {},
    }

    try:
        tk = yf.Ticker(ticker)
        info = tk.fast_info

        spot = getattr(info, "last_price", None) or getattr(info, "regular_market_price", None)
        if spot is None:
            hist = tk.history(period="1d")
            spot = float(hist["Close"].iloc[-1]) if not hist.empty else None
        if spot is None:
            result["fail_reason"] = "No price data"; return result
        result["spot_price"] = round(spot, 2)

        # Fetch fundamentals concurrently with options scan
        result["fundamentals"] = fetch_fundamentals(tk, info)

        expirations = tk.options
        if not expirations:
            result["fail_reason"] = "No options available"; return result

        today = date.today()
        liquid_expiries = []
        for exp in expirations:
            if (datetime.strptime(exp, "%Y-%m-%d").date() - today).days >= 7:
                liquid_expiries.append(exp)
            if len(liquid_expiries) >= EXPIRIES_TO_SCAN:
                break

        if not liquid_expiries:
            result["fail_reason"] = "No liquid expiries"; return result

        total_oi = call_oi = put_oi = total_vol = vol_count = 0
        atm_spread_pct = atm_mid = iv_current = None
        iv_available = False

        # OI: sum across ALL expiries (DTE >= 1) -- total market interest picture
        # Volume: today only, informational display, NOT a pass/fail gate
        for exp in tk.options:
            dte = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
            if dte < 1:
                continue
            try:
                chain = tk.option_chain(exp)
                calls, puts = chain.calls, chain.puts
                c_oi = int(calls["openInterest"].fillna(0).sum())
                p_oi = int(puts["openInterest"].fillna(0).sum())
                call_oi += c_oi
                put_oi  += p_oi
                total_oi += c_oi + p_oi
                today_vol = int(calls["volume"].fillna(0).sum() +
                                puts["volume"].fillna(0).sum())
                total_vol += today_vol
                vol_count += 1
                if not atm_spread_pct:
                    pts = puts.copy()
                    pts["dist"] = (pts["strike"] - spot).abs()
                    row = pts.nsmallest(1, "dist").iloc[0]
                    bid, ask = row.get("bid"), row.get("ask")
                    if bid and ask and bid > 0 and ask > 0:
                        mid = (bid + ask) / 2
                        if mid > 0:
                            atm_spread_pct = round(((ask - bid) / mid) * 100, 1)
                            atm_mid = round(float(mid), 2)
                    iv_val = row.get("impliedVolatility")
                    if iv_val and float(iv_val) > 0:
                        iv_current = round(float(iv_val) * 100, 1)
                        iv_available = True
            except Exception:
                continue

        result.update({
            "total_oi": total_oi, "call_oi": call_oi, "put_oi": put_oi,
            "avg_volume": int(total_vol / vol_count) if vol_count > 0 else 0,
            "atm_spread_pct": atm_spread_pct, "atm_mid": atm_mid,
            "iv_available": iv_available, "iv_current": iv_current,
        })
        tier, tier_label = oi_tier(total_oi)
        result["tier"] = tier; result["tier_label"] = tier_label

        # Apply criteria
        if total_oi < min_oi:
            result["fail_reason"] = f"OI {total_oi:,} < {min_oi:,}"; return result
        # Volume is informational only -- not a pass/fail gate
        # (2-week avg volume needs paid API, planned for IBKR integration)
        # Spread % removed from screen -- it is strike/DTE dependent
        # and belongs in helm open (entry evaluation), not the first-pass screen.
        # IV check: if we can't get any IV data, options data is unreliable
        if not iv_available:
            result["fail_reason"] = "IV not available"; return result

        result["passed"] = True
        return result

    except Exception as e:
        result["error"] = _friendly_fail(e)[:60]
        result["fail_reason"] = f"Error: {str(e)[:40]}"
        return result


def run():
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        console.print()
        console.print("[bold]Usage:[/bold]  helm screen [tickers] [options]")
        console.print("[dim]  --min-oi N        Minimum total OI (default: 5,000)[/dim]")
        console.print("[dim]  --max-spread N    Maximum ATM spread % (default: 15)[/dim]")
        console.print("[dim]  --min-volume N    Minimum avg daily volume (default: 500)[/dim]")
        console.print("[dim]  --workers N       Concurrent workers (default: 20)[/dim]")
        console.print("[dim]  --show-all        Show passes and failures[/dim]")
        console.print("[dim]  --dry-run         Preview without updating watchlist[/dim]")
        console.print()
        return

    min_oi = DEFAULT_MIN_OI
    max_spread_pct = DEFAULT_MAX_SPREAD_PCT
    min_volume = DEFAULT_MIN_VOLUME
    workers = DEFAULT_WORKERS
    show_all = "--show-all" in args
    dry_run = "--dry-run" in args
    ticker_args = []

    i = 0
    while i < len(args):
        if args[i] == "--min-oi" and i+1 < len(args):       min_oi = int(args[i+1]); i += 2
        elif args[i] == "--max-spread" and i+1 < len(args): max_spread_pct = float(args[i+1]); i += 2
        elif args[i] == "--min-volume" and i+1 < len(args): min_volume = int(args[i+1]); i += 2
        elif args[i] == "--workers" and i+1 < len(args):    workers = int(args[i+1]); i += 2
        elif args[i] in ("--show-all", "--dry-run"):         i += 1
        else: ticker_args.append(args[i]); i += 1

    if ticker_args:
        tickers = [t.strip().upper() for raw in ticker_args for t in raw.replace(","," ").split() if t.strip()]
        items = {t: WatchlistItem.get(t) for t in tickers}
    else:
        items_list = WatchlistItem.all()
        if not items_list:
            console.print("[yellow]Watchlist is empty. Run helm watchlist add AAPL,NVDA first.[/yellow]")
            return
        tickers = [item.ticker for item in items_list]
        items = {item.ticker: item for item in items_list}

    if not get_active_account():
        console.print("[red]No active account. Run helm setup first.[/red]")
        return

    console.print()
    console.print(Panel.fit(
        f"[bold cyan]HELM Screen[/bold cyan] -- Optionability Filter\n"
        f"[dim]OI >= {min_oi:,} | IV required | {workers} concurrent workers[/dim]",
        border_style="cyan"
    ))
    console.print()

    results = []
    passed = []
    failed = []
    completed_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"Screening {len(tickers)} tickers...", total=len(tickers))

        import time
        # Process in batches to avoid yfinance rate limiting
        # 50 tickers per batch, 90s pause between batches
        # 332 tickers = 7 batches ~ 12 minutes total
        BATCH_SIZE = 50
        BATCH_PAUSE = 90

        batches = [tickers[i:i+BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
        n_batches = len(batches)

        for batch_num, batch in enumerate(batches, 1):
            if batch_num > 1:
                for remaining in range(BATCH_PAUSE, 0, -5):
                    progress.update(task,
                        description=f"[yellow]Rate limit pause: {remaining}s (batch {batch_num}/{n_batches})[/yellow]")
                    time.sleep(5)

            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(screen_ticker, ticker, min_oi, max_spread_pct, min_volume): ticker
                    for ticker in batch
                }
                for future in as_completed(futures):
                    ticker = futures[future]
                    try:
                        res = future.result()
                    except Exception as e:
                        res = {"ticker": ticker, "passed": False, "fail_reason": str(e)[:50],
                               "total_oi": 0, "avg_volume": 0, "atm_spread_pct": None,
                               "atm_mid": None, "iv_current": None, "spot_price": None,
                               "tier_label": "[red]error[/red]", "fundamentals": {}, "error": str(e)}

                    # Retry once on rate limit
                    if res.get("error") and "Rate" in str(res.get("error", "")):
                        time.sleep(10)
                        res = screen_ticker(ticker, min_oi, max_spread_pct, min_volume)

                    results.append(res)
                    if res["passed"]: passed.append(res)
                    else: failed.append(res)

                    if not dry_run:
                        item = items.get(ticker)
                        if item:
                            item.mark_screened(res["passed"])
                            if res.get("fundamentals"):
                                item.update_fundamentals(**res["fundamentals"])

                    completed_count += 1
                    status = "[green]PASS[/green]" if res["passed"] else "[red]fail[/red]"
                    progress.update(task, advance=1,
                        description=f"Batch {batch_num}/{n_batches} [dim]{ticker}[/dim] {status} ({completed_count}/{len(tickers)})")

    console.print()

    # Results table
    t = Table(box=box.SIMPLE_HEAD, show_header=True, padding=(0,1))
    t.add_column("Ticker",   style="bold cyan", width=7)
    t.add_column("Status",   width=7)
    t.add_column("Tier",     width=10)
    t.add_column("Total OI", justify="right", width=11)
    t.add_column("Volume",   justify="right", width=8)
    t.add_column("IV%",      justify="right", width=6)
    t.add_column("Mkt Cap",  justify="right", width=9)
    t.add_column("Beta",     justify="right", width=5)

    def add_row(res, status_str):
        iv = f"{res['iv_current']:.0f}%" if res["iv_current"] else "--"
        f = res.get("fundamentals", {})
        cap = f"${f['market_cap']:.0f}B" if f.get("market_cap") else "--"
        hi = f.get("week_52_high"); lo = f.get("week_52_low")
        rng = f"${lo:.0f}-${hi:.0f}" if hi and lo else "--"
        beta = f"{f['beta']:.1f}" if f.get("beta") else "--"
        earn = f.get("next_earnings", "--") or "--"
        if earn and earn != "--": earn = earn[5:]  # show MM-DD only
        t.add_row(
            res["ticker"], status_str, res.get("tier_label","--"),
            f"{res['total_oi']:,}", f"{res['avg_volume']:,}",
            iv, cap, beta)

    display = (passed + failed) if show_all else passed
    if display:
        label = "All Results" if show_all else f"Passed ({len(passed)})"
        console.print(f"[bold]{label}[/bold]")
        console.print()
        for res in sorted(display, key=lambda x: (-x["passed"], -x["total_oi"])):
            add_row(res, "[green]PASS[/green]" if res["passed"] else "[red]FAIL[/red]")
        console.print(t)

    if failed and not show_all:
        console.print()
        console.print(f"[bold]Failed ({len(failed)}):[/bold]")
        for res in sorted(failed, key=lambda x: -x["total_oi"]):
            reason = res.get("fail_reason") or "unknown"
            oi_str = f"OI {res['total_oi']:,}" if res["total_oi"] > 0 else "no data"
            console.print(f"  [dim]{res['ticker']:<8}[/dim] [red]{reason}[/red]  [dim]({oi_str})[/dim]")

    update_str = "" if dry_run else " Watchlist updated."
    console.print()
    console.print(Panel.fit(
        f"Screened [bold]{len(results)}[/bold] tickers  "
        f"[green]{len(passed)} passed[/green]  "
        f"[red]{len(failed)} failed[/red]{update_str}\n\n"
        f"[dim]Run [bold]helm scan[/bold] to evaluate opportunities on optionable tickers.[/dim]",
        border_style="green" if passed else "red",
        title="Screen Complete"
    ))
    console.print()

    try:
        _log_event("WATCHLIST_BUILT")
    except Exception:
        pass


if __name__ == "__main__":
    run()
