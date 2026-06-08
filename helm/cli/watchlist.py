# helm/cli/watchlist.py
# helm watchlist -- manage your ticker universe
#
# Commands:
#   helm watchlist build                           Guided universe builder
#   helm watchlist add AAPL,NVDA                  Fast add (instant, no API)
#   helm watchlist add AAPL,NVDA --evaluate       Add with full evaluation
#   helm watchlist suggest AAPL,NVDA              Evaluate without adding
#   helm watchlist list [--optionable] [--wto]
#   helm watchlist remove AAPL
#   helm watchlist screen AAPL
#   helm watchlist wto AAPL
#   helm watchlist show AAPL

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich import box

from helm.config import get_active_account
from helm.models.watchlist import WatchlistItem
from helm.models.position import Position

console = Console()

# ── OI / Volume benchmark levels ─────────────────────────────────────────────
# Reference points shown alongside each ticker so user gets context.

OI_LEVELS = [
    (500_000, "Mega liquid",  "green"),
    (100_000, "Very liquid",  "green"),
    (50_000,  "Liquid",       "cyan"),
    (5_000,   "Moderate",     "yellow"),
    (1_000,   "Thin",         "yellow"),
    (0,       "Illiquid",     "red"),
]

VOL_LEVELS = [
    (10_000, "Very active", "green"),
    (1_000,  "Active",      "cyan"),
    (500,    "Moderate",    "yellow"),
    (0,      "Thin",        "red"),
]

def oi_level(oi):
    for threshold, label, style in OI_LEVELS:
        if oi >= threshold:
            return label, style
    return "Illiquid", "red"

def vol_level(vol):
    for threshold, label, style in VOL_LEVELS:
        if vol >= threshold:
            return label, style
    return "Thin", "red"


# ── Quick evaluation ─────────────────────────────────────────────────────────

def quick_eval(ticker, include_options=True):
    """
    Evaluate a ticker for watchlist candidacy.
    include_options=True: fetch nearest expiry OI + volume (one chain call)
    include_options=False: fast_info only, no options data
    """
    import logging, warnings
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    warnings.filterwarnings("ignore")
    import yfinance as yf

    result = {
        "ticker": ticker,
        "company_name": None,
        "sector": None,
        "spot": None,
        "market_cap": None,
        "beta": None,
        "week_52_high": None,
        "week_52_low": None,
        "has_options": False,
        "total_oi": None,
        "avg_volume": None,
        "oi_level": None,
        "oi_style": "dim",
        "vol_level": None,
        "vol_style": "dim",
        "verdict": None,
        "verdict_style": "dim",
        "verdict_reason": None,
        "error": None,
    }

    try:
        tk = yf.Ticker(ticker)
        info = tk.fast_info

        spot = getattr(info, "last_price", None)
        if not spot:
            result["error"] = "No price data"
            result["verdict"] = "FLAG"
            result["verdict_style"] = "red"
            result["verdict_reason"] = "No price data — possibly delisted"
            return result

        market_cap = getattr(info, "market_cap", None)
        if market_cap:
            market_cap = round(market_cap / 1e9, 1)

        result.update({
            "spot": round(spot, 2),
            "market_cap": market_cap,
            "week_52_high": getattr(info, "fifty_two_week_high", None),
            "week_52_low":  getattr(info, "fifty_two_week_low", None),
        })

        try:
            full = tk.info
            result["company_name"] = full.get("longName") or full.get("shortName")
            result["sector"]       = full.get("sector")
            beta = full.get("beta")
            result["beta"] = round(beta, 2) if beta else None
        except Exception:
            pass

        # Options existence check
        try:
            expirations = tk.options
            result["has_options"] = bool(expirations and len(expirations) > 0)
        except Exception:
            result["has_options"] = False

        # OI: sum across ALL expiries (DTE >= 1) -- total market interest picture
        # Volume: today only, shown as informational, NOT a pass/fail gate
        # (BarChart 2-week avg volume needs paid API -- planned for IBKR integration)
        if include_options and result["has_options"]:
            try:
                from datetime import date, datetime
                today = date.today()
                total_oi  = 0
                today_vol = 0
                exps_scanned = 0
                for exp in tk.options:
                    dte = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
                    if dte < 1:
                        continue  # skip expiring today
                    try:
                        c = tk.option_chain(exp)
                        total_oi  += int(c.calls["openInterest"].fillna(0).sum() +
                                         c.puts["openInterest"].fillna(0).sum())
                        today_vol += int(c.calls["volume"].fillna(0).sum() +
                                         c.puts["volume"].fillna(0).sum())
                        exps_scanned += 1
                    except Exception:
                        pass
                if exps_scanned > 0:
                    result["total_oi"]   = total_oi
                    result["avg_volume"] = today_vol  # single day, informational only
                    oi_lbl, oi_sty = oi_level(total_oi)
                    result["oi_level"]  = oi_lbl
                    result["oi_style"]  = oi_sty
                    result["vol_level"] = "today only"
                    result["vol_style"] = "dim"
            except Exception:
                pass

        # Verdict
        oi = result.get("total_oi")
        if not result["has_options"]:
            result["verdict"] = "FLAG"
            result["verdict_style"] = "red"
            result["verdict_reason"] = "No options available"
        elif oi is not None and oi < 1_000:
            result["verdict"] = "FLAG"
            result["verdict_style"] = "red"
            result["verdict_reason"] = f"Total OI {oi:,} — very illiquid across all expiries"
        elif oi is not None and oi < 5_000:
            result["verdict"] = "MARGINAL"
            result["verdict_style"] = "yellow"
            result["verdict_reason"] = f"Total OI {oi:,} — thin, full screen recommended"
        elif market_cap and market_cap >= 10:
            result["verdict"] = "STRONG"
            result["verdict_style"] = "green"
            result["verdict_reason"] = f"${market_cap:.0f}B cap | total OI {oi:,}" if oi else f"${market_cap:.0f}B cap"
        elif market_cap and market_cap >= 2:
            result["verdict"] = "GOOD"
            result["verdict_style"] = "cyan"
            result["verdict_reason"] = f"${market_cap:.0f}B cap | total OI {oi:,}" if oi else f"${market_cap:.0f}B cap"
        else:
            result["verdict"] = "MARGINAL"
            result["verdict_style"] = "yellow"
            result["verdict_reason"] = "Small cap — full screen recommended"

        return result

    except Exception as e:
        result["error"] = str(e)[:60]
        result["verdict"] = "FLAG"
        result["verdict_style"] = "red"
        result["verdict_reason"] = f"Error: {str(e)[:50]}"
        return result


def run_evaluation(tickers, title="Evaluating", include_options=True):
    """Run quick_eval on a list with progress bar. Returns results list."""
    results = []
    completed = 0

    if len(tickers) > 20:
        est = len(tickers) * 3 / 60
        console.print(f"Evaluating [bold]{len(tickers)}[/bold] tickers (~{est:.0f} minutes)...")
        if not Confirm.ask("  Proceed?", default=True):
            return []
        console.print()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"{title}...", total=len(tickers))
        for ticker in tickers:
            progress.update(task, description=f"[dim]{title}:[/dim] [cyan]{ticker}[/cyan]")
            res = quick_eval(ticker, include_options=include_options)
            results.append(res)
            completed += 1
            v = f"[{res['verdict_style']}]{res['verdict']}[/{res['verdict_style']}]" if res["verdict"] else ""
            progress.update(task, advance=1,
                description=f"[dim]{ticker}[/dim] {v} ({completed}/{len(tickers)})")
            time.sleep(0.3)

    return results


def print_eval_table(results, title="Evaluation Results"):
    """Rich table with OI/volume benchmarks alongside raw numbers."""
    console.print()
    t = Table(box=box.SIMPLE_HEAD, title=title, show_header=True, padding=(0,1))
    t.add_column("Ticker",   style="bold cyan", width=7)
    t.add_column("Company",  width=20)
    t.add_column("Sector",   style="dim", width=13)
    t.add_column("Mkt Cap",  justify="right", width=8)
    t.add_column("Beta",     justify="right", width=5)
    t.add_column("Opts",     justify="center", width=5)
    t.add_column("OI",       justify="right", width=10)
    t.add_column("OI Level", width=12)
    t.add_column("Volume",   justify="right", width=8)
    t.add_column("Vol Level",width=11)
    t.add_column("Verdict",  width=9)

    for res in results:
        cap  = f"${res['market_cap']:.0f}B" if res["market_cap"] else "--"
        beta = f"{res['beta']:.1f}" if res["beta"] else "--"
        opts = "[green]YES[/green]" if res["has_options"] else "[red]NO[/red]"
        oi   = f"{res['total_oi']:,}" if res["total_oi"] is not None else "--"
        vol  = f"{res['avg_volume']:,}" if res["avg_volume"] is not None else "--"
        oi_lbl  = f"[{res['oi_style']}]{res['oi_level']}[/{res['oi_style']}]" if res["oi_level"] else "--"
        vol_lbl = f"[{res['vol_style']}]{res['vol_level']}[/{res['vol_style']}]" if res["vol_level"] else "--"
        verdict = f"[{res['verdict_style']}]{res['verdict']}[/{res['verdict_style']}]" if res["verdict"] else "--"
        t.add_row(
            res["ticker"],
            (res.get("company_name") or "")[:20],
            (res.get("sector") or "")[:13],
            cap, beta, opts, oi, oi_lbl, vol, vol_lbl, verdict,
        )

    # Benchmark legend
    console.print(t)
    console.print("[dim]  OI Benchmarks:  Mega liquid >500k  |  Very liquid >100k  |  Liquid >50k  |  Moderate >5k  |  Thin >1k  |  Illiquid <1k[/dim]")
    console.print("[dim]  Vol Benchmarks: Very active >10k   |  Active >1k         |  Moderate >500               |  Thin <500[/dim]")
    console.print()


# ── Guided builder ────────────────────────────────────────────────────────────

def cmd_build(args):
    """Guided watchlist universe builder."""
    console.print()
    console.print(Panel.fit(
        "[bold cyan]HELM Watchlist Builder[/bold cyan]\n"
        "[dim]Let's build your trading universe step by step.[/dim]",
        border_style="cyan"
    ))
    console.print()

    # Step 1: source
    console.print("[bold]Where are your tickers?[/bold]")
    console.print("  [cyan]1[/cyan]  I'll paste them now")
    console.print("  [cyan]2[/cyan]  They're in a file (CSV or text)")
    console.print("  [cyan]3[/cyan]  I'll add them one at a time")
    console.print()
    source = Prompt.ask("  Choice", choices=["1","2","3"], default="1")
    console.print()

    raw_tickers = []

    if source == "1":
        console.print("[dim]Paste your tickers — comma or space separated, any format:[/dim]")
        raw = Prompt.ask("  Tickers")
        raw_tickers = [t.strip().upper() for t in raw.replace(","," ").split() if t.strip()]

    elif source == "2":
        filepath = Prompt.ask("  File path")
        try:
            fp = Path(filepath).expanduser()
            text = fp.read_text()
            # Handle CSV (first column) or plain text
            lines = text.strip().splitlines()
            for line in lines:
                # Skip header-like lines
                parts = line.replace(","," ").split()
                if parts and not parts[0].isdigit():
                    candidate = parts[0].strip().upper().replace('"', '').replace("'", '')
                    if candidate.isalpha() or (candidate.replace(".","").isalpha()):
                        raw_tickers.append(candidate)
            raw_tickers = list(dict.fromkeys(raw_tickers))  # dedup preserve order
        except Exception as e:
            console.print(f"[red]Could not read file:[/red] {e}")
            return

    elif source == "3":
        console.print("[dim]Add tickers one at a time. Press Enter with no input to finish.[/dim]")
        console.print()
        while True:
            ticker = Prompt.ask("  Ticker (or Enter to finish)", default="")
            if not ticker:
                break
            raw_tickers.append(ticker.strip().upper())

    if not raw_tickers:
        console.print("[yellow]No tickers found.[/yellow]")
        return

    # Dedup and show count
    tickers = list(dict.fromkeys(raw_tickers))
    console.print(f"Found [bold]{len(tickers)}[/bold] ticker(s).")
    console.print()

    # Step 2: how to add
    console.print("[bold]How would you like to add them?[/bold]")
    console.print("  [cyan]1[/cyan]  Fast add — add all now, screen later [dim](instant)[/dim]")
    console.print("  [cyan]2[/cyan]  Evaluated add — quick optionability feedback with OI/volume data")
    console.print("  [cyan]3[/cyan]  Review each one — see data before deciding")
    est2 = len(tickers) * 3 / 60
    console.print(f"  [dim]Options 2 and 3 fetch live data (~{est2:.0f} min for {len(tickers)} tickers)[/dim]")
    console.print()
    mode = Prompt.ask("  Choice", choices=["1","2","3"], default="1")
    console.print()

    # Step 3: WTO default
    wto_default = Confirm.ask("  Set willing-to-own = YES for all?", default=True)
    console.print()

    # Step 4: Execute
    if mode == "1":
        # Fast add
        added = skipped = 0
        for ticker in tickers:
            if WatchlistItem.get(ticker):
                skipped += 1
                continue
            WatchlistItem.add(ticker, willing_to_own=1 if wto_default else 0)
            added += 1
        console.print(f"[green]Added {added} tickers.[/green]" + (f" ({skipped} already existed)" if skipped else ""))
        console.print()
        _build_summary(tickers, mode="fast")

    elif mode == "2":
        # Evaluated add with progress bar
        console.print(Panel.fit(
            "[bold]Evaluated Add[/bold]\n[dim]Fetching optionability data for each ticker...[/dim]",
            border_style="cyan"
        ))
        console.print()
        results = run_evaluation(tickers, title="Evaluating", include_options=True)
        if not results:
            return
        print_eval_table(results, title="Evaluation Results")
        _confirm_and_add(results, wto_default)

    elif mode == "3":
        # Review each one individually
        console.print(Panel.fit(
            "[bold]Review Mode[/bold]\n[dim]You'll see data for each ticker and decide individually.[/dim]",
            border_style="cyan"
        ))
        console.print()
        added_list = []
        skipped_list = []

        for i, ticker in enumerate(tickers, 1):
            console.print(f"[dim]({i}/{len(tickers)})[/dim] Fetching [cyan]{ticker}[/cyan]...")
            res = quick_eval(ticker, include_options=True)

            # Show mini summary
            cap  = f"${res['market_cap']:.0f}B" if res["market_cap"] else "N/A"
            name = res.get("company_name") or ticker
            sector = res.get("sector") or "--"
            oi   = f"{res['total_oi']:,}" if res["total_oi"] is not None else "--"
            vol  = f"{res['avg_volume']:,}" if res["avg_volume"] is not None else "--"
            oi_lbl  = f"[{res['oi_style']}]{res['oi_level']}[/{res['oi_style']}]" if res["oi_level"] else "--"
            verdict = f"[{res['verdict_style']}]{res['verdict']}[/{res['verdict_style']}]" if res["verdict"] else "--"

            console.print(
                f"  [bold cyan]{ticker}[/bold cyan]  {name}  |  {sector}  |  {cap}  |  "
                f"OI: {oi} ({oi_lbl})  |  Vol: {vol}  |  {verdict}"
            )
            if res.get("verdict_reason"):
                console.print(f"  [dim]  {res['verdict_reason']}[/dim]")

            already = WatchlistItem.get(ticker)
            if already:
                console.print(f"  [dim]  Already on watchlist.[/dim]")
                skipped_list.append(ticker)
                console.print()
                continue

            add = Confirm.ask(f"  Add {ticker}?", default=(res["verdict"] != "FLAG"))
            if add:
                WatchlistItem.add(
                    ticker,
                    company_name=res.get("company_name"),
                    sector=res.get("sector"),
                    willing_to_own=1 if wto_default else 0,
                    market_cap=res.get("market_cap"),
                    week_52_high=res.get("week_52_high"),
                    week_52_low=res.get("week_52_low"),
                    beta=res.get("beta"),
                )
                added_list.append(ticker)
                console.print(f"  [green]Added.[/green]")
            else:
                skipped_list.append(ticker)
                console.print(f"  [dim]Skipped.[/dim]")
            console.print()

        console.print(f"[green]Added {len(added_list)} tickers.[/green]")
        if skipped_list:
            console.print(f"[dim]Skipped {len(skipped_list)}: {', '.join(skipped_list[:10])}{'...' if len(skipped_list)>10 else ''}[/dim]")
        console.print()
        _build_summary(tickers, mode="review")


def _confirm_and_add(results, wto_default):
    """After evaluation, show flagged/marginal items and confirm before adding."""
    flagged  = [r for r in results if r["verdict"] == "FLAG"]
    marginal = [r for r in results if r["verdict"] == "MARGINAL"]
    good     = [r for r in results if r["verdict"] in ("STRONG","GOOD")]

    if flagged:
        console.print(f"[red]Flagged ({len(flagged)}) — no options or delisted:[/red]")
        for r in flagged:
            console.print(f"  [dim]{r['ticker']:<8}[/dim] {r.get('verdict_reason','')}")
        console.print()
        keep_flagged = Confirm.ask(f"  Add flagged tickers anyway?", default=False)
        if not keep_flagged:
            results = [r for r in results if r["verdict"] != "FLAG"]
        console.print()

    if marginal:
        console.print(f"[yellow]Marginal ({len(marginal)}) — thin options market:[/yellow]")
        for r in marginal:
            console.print(f"  [dim]{r['ticker']:<8}[/dim] {r.get('verdict_reason','')}")
        console.print()
        keep_marginal = Confirm.ask(f"  Add marginal tickers?", default=True)
        if not keep_marginal:
            results = [r for r in results if r["verdict"] != "MARGINAL"]
        console.print()

    # Auto-detect sectors
    auto_sector = Confirm.ask("  Auto-populate sectors from market data?", default=True)
    console.print()

    added = skipped = 0
    for res in results:
        if WatchlistItem.get(res["ticker"]):
            skipped += 1
            continue
        WatchlistItem.add(
            res["ticker"],
            company_name=res.get("company_name"),
            sector=res.get("sector") if auto_sector else None,
            willing_to_own=1 if wto_default else 0,
            market_cap=res.get("market_cap"),
            week_52_high=res.get("week_52_high"),
            week_52_low=res.get("week_52_low"),
            beta=res.get("beta"),
        )
        added += 1

    console.print(f"[green]Added {added} tickers.[/green]" + (f" ({skipped} already existed)" if skipped else ""))
    console.print()
    _build_summary([r["ticker"] for r in results], mode="evaluated")


def _build_summary(tickers, mode):
    """Show summary and suggested next steps after build."""
    total = len(WatchlistItem.all())
    opt   = len(WatchlistItem.optionable())
    est   = total * 12 / 60  # rough estimate for full screen

    lines = [
        f"Watchlist: [bold]{total}[/bold] tickers  |  [green]{opt} optionable[/green]",
        "",
    ]
    if mode == "fast":
        lines += [
            "[dim]Sectors and fundamentals not yet populated.[/dim]",
            "[dim]Suggested next steps:[/dim]",
            f"  [cyan]helm screen[/cyan]           Full optionability screen (~{est:.0f} min)",
        ]
    else:
        lines += [
            "[dim]Suggested next steps:[/dim]",
            f"  [cyan]helm screen[/cyan]           Full optionability screen (~{est:.0f} min)",
            f"  [cyan]helm watchlist list[/cyan]   Review your universe",
        ]

    console.print(Panel("\n".join(lines), title="Build Complete", border_style="green"))
    console.print()


# ── Other commands ────────────────────────────────────────────────────────────

def cmd_add(args):
    if not args:
        console.print("[red]Specify at least one ticker.[/red]")
        return

    evaluate = "--evaluate" in args
    no_wto   = "--no-wto" in args
    sector = thesis = None
    ticker_args = []

    i = 0
    while i < len(args):
        if args[i] == "--sector" and i+1 < len(args):   sector = args[i+1]; i += 2
        elif args[i] == "--thesis" and i+1 < len(args): thesis = args[i+1]; i += 2
        elif args[i] in ("--evaluate","--no-wto"):       i += 1
        else: ticker_args.append(args[i]); i += 1

    tickers = [t.strip().upper() for raw in ticker_args for t in raw.replace(","," ").split() if t.strip()]
    if not tickers:
        console.print("[red]No valid tickers found.[/red]")
        return

    console.print()

    new_tickers = []
    already = []
    for ticker in tickers:
        existing = WatchlistItem.get(ticker)
        if existing:
            already.append((ticker, existing))
        else:
            new_tickers.append(ticker)

    if already:
        console.print(f"[dim]Already on watchlist ({len(already)}):[/dim]")
        for ticker, item in already:
            opt = "[green]optionable[/green]" if item.is_optionable else "[dim]not screened[/dim]"
            screened = f"screened {item.last_screened_at[:10]}" if item.last_screened_at else "never screened"
            console.print(f"  [cyan]{ticker}[/cyan]  {opt}  {screened}")
        console.print()

    if not new_tickers:
        console.print("[dim]No new tickers to add.[/dim]")
        return

    if evaluate:
        console.print(Panel.fit(
            f"[bold cyan]Evaluated Add[/bold cyan] — {len(new_tickers)} ticker(s)\n"
            "[dim]Fetching optionability data...[/dim]",
            border_style="cyan"
        ))
        console.print()
        results = run_evaluation(new_tickers, title="Evaluating", include_options=True)
        if not results:
            return
        print_eval_table(results, title="Evaluation")
        _confirm_and_add(results, wto_default=True)
    else:
        added = []
        for ticker in new_tickers:
            WatchlistItem.add(ticker, sector=sector,
                              willing_to_own=1, thesis=thesis)
            added.append(ticker)
        console.print(f"[green]Added ({len(added)}):[/green] {', '.join(added)}")
        console.print(f"[dim]Watchlist: {len(WatchlistItem.all())} tickers total.[/dim]")
        console.print(f"[dim]Tip: use [bold]--evaluate[/bold] flag for optionability feedback.[/dim]")
        console.print()


def cmd_suggest(args):
    if not args:
        console.print("[red]Specify at least one ticker.[/red]")
        return
    tickers = [t.strip().upper() for raw in args for t in raw.replace(","," ").split() if t.strip()]
    if not tickers:
        return

    console.print()
    console.print(Panel.fit(
        f"[bold cyan]HELM Suggest[/bold cyan] — {len(tickers)} ticker(s)\n"
        "[dim]Quick evaluation — no changes made to watchlist[/dim]",
        border_style="cyan"
    ))
    console.print()

    results = run_evaluation(tickers, title="Evaluating", include_options=True)
    if not results:
        return
    print_eval_table(results, title="Suggestion Results")

    on_wl    = [r["ticker"] for r in results if WatchlistItem.get(r["ticker"])]
    not_on   = [r["ticker"] for r in results if not WatchlistItem.get(r["ticker"])]
    strong   = [r["ticker"] for r in results if r["verdict"] in ("STRONG","GOOD") and r["ticker"] in not_on]

    if on_wl:
        console.print(f"[dim]Already on watchlist:[/dim] {', '.join(on_wl)}")
    if strong:
        console.print(f"[dim]To add STRONG and GOOD candidates:[/dim] [cyan]helm watchlist add {chr(44).join(strong)}[/cyan]")
    console.print()


def cmd_list(args):
    optionable_only = "--optionable" in args
    wto_only = "--wto" in args

    if wto_only:
        items = WatchlistItem.willing_to_own_list()
        title = "Watchlist — WTO + Optionable"
    elif optionable_only:
        items = WatchlistItem.optionable()
        title = "Watchlist — Optionable"
    else:
        items = WatchlistItem.all()
        title = f"Watchlist ({len(WatchlistItem.all())} tickers)"

    if not items:
        console.print()
        console.print("[yellow]Watchlist is empty.[/yellow]")
        console.print("[dim]Run [bold]helm watchlist build[/bold] to get started.[/dim]")
        console.print()
        return

    console.print()
    t = Table(box=box.SIMPLE_HEAD, title=title, show_header=True, padding=(0,1))
    t.add_column("Ticker",     style="bold cyan", width=7)
    t.add_column("Company",    width=20)
    t.add_column("Sector",     style="dim", width=13)
    t.add_column("Mkt Cap",    justify="right", width=8)
    t.add_column("Optionable", justify="center", width=11)
    t.add_column("WTO",        justify="center", width=5)
    t.add_column("Screened",   style="dim", width=12)
    t.add_column("Open Pos",   justify="center", width=8)

    for item in items:
        opt     = "[green]YES[/green]" if item.is_optionable else "[dim]no[/dim]"
        wto     = "[green]Y[/green]" if item.willing_to_own else "[red]N[/red]"
        screened= item.last_screened_at[:10] if item.last_screened_at else "[dim]never[/dim]"
        cap     = f"${item.market_cap:.0f}B" if item.market_cap else "--"
        open_pos= Position.by_ticker(item.ticker, status="OPEN")
        pos_str = str(len(open_pos)) if open_pos else "[dim]—[/dim]"
        t.add_row(item.ticker, (item.company_name or "")[:20],
                  (item.sector or "")[:13], cap, opt, wto, screened, pos_str)

    console.print(t)
    total = len(WatchlistItem.all())
    opt_c = len(WatchlistItem.optionable())
    wto_c = len(WatchlistItem.willing_to_own_list())
    console.print(f"[dim]  {total} total  |  {opt_c} optionable  |  {wto_c} willing-to-own[/dim]")
    console.print()


def cmd_remove(args):
    if not args:
        console.print("[red]Specify a ticker.[/red]")
        return
    tickers = [t.strip().upper() for raw in args for t in raw.replace(","," ").split() if t.strip()]
    for ticker in tickers:
        item = WatchlistItem.get(ticker)
        if not item:
            console.print(f"[yellow]{ticker}[/yellow] not on watchlist.")
            continue
        open_pos = Position.by_ticker(ticker, status="OPEN")
        if open_pos:
            console.print(f"[yellow]Warning:[/yellow] {ticker} has {len(open_pos)} open position(s).")
            if not Confirm.ask(f"  Remove {ticker} anyway?", default=False):
                continue
        from helm.db import transaction
        with transaction() as conn:
            conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
        console.print(f"[green]Removed:[/green] {ticker}")


def cmd_screen_manual(args):
    if not args:
        console.print("[red]Specify a ticker.[/red]")
        return
    ticker = args[0].upper()
    item = WatchlistItem.get(ticker)
    if not item:
        console.print(f"[yellow]{ticker}[/yellow] not on watchlist.")
        return
    current = "optionable" if item.is_optionable else "NOT optionable"
    console.print(f"{ticker} is currently [bold]{current}[/bold].")
    mark = Confirm.ask(f"  Mark {ticker} as optionable?", default=not item.is_optionable)
    item.mark_screened(mark)
    status = "[green]optionable[/green]" if mark else "[dim]not optionable[/dim]"
    console.print(f"[green]Updated:[/green] {ticker} -> {status}")


def cmd_wto(args):
    if not args:
        console.print("[red]Specify a ticker.[/red]")
        return
    ticker = args[0].upper()
    item = WatchlistItem.get(ticker)
    if not item:
        console.print(f"[yellow]{ticker}[/yellow] not on watchlist.")
        return
    new_wto = 0 if item.willing_to_own else 1
    item.willing_to_own = new_wto
    item.save()
    status = "[green]willing to own[/green]" if new_wto else "[red]not willing to own[/red]"
    console.print(f"[green]Updated:[/green] {ticker} -> {status}")


def cmd_show(args):
    if not args:
        console.print("[red]Specify a ticker.[/red]")
        return
    ticker = args[0].upper()
    item = WatchlistItem.get(ticker)
    if not item:
        console.print(f"[yellow]{ticker}[/yellow] not on watchlist.")
        return

    open_pos = Position.by_ticker(ticker, status="OPEN")
    all_pos  = Position.by_ticker(ticker)
    cap  = f"${item.market_cap:.0f}B" if item.market_cap else "N/A"
    hi   = item.week_52_high; lo = item.week_52_low
    rng  = f"${lo:.0f} - ${hi:.0f}" if hi and lo else "N/A"

    lines = [
        f"[bold cyan]{ticker}[/bold cyan]  {item.company_name or ''}",
        f"Sector:      {item.sector or '--'}",
        f"Market Cap:  {cap}",
        f"52wk Range:  {rng}",
        f"Beta:        {item.beta or '--'}",
        f"Optionable:  {'YES' if item.is_optionable else 'no'}",
        f"WTO:         {'YES' if item.willing_to_own else 'no'}",
        f"Screened:    {item.last_screened_at[:10] if item.last_screened_at else 'never'}",
        f"Added:       {item.added_at[:10]}",
        f"Open pos:    {len(open_pos)}  |  All-time: {len(all_pos)}",
    ]
    if item.thesis:
        lines.append(f"Thesis:      {item.thesis}")
    if open_pos:
        lines.append("")
        lines.append("[bold]Open positions:[/bold]")
        for p in open_pos:
            prem = f"net {p.net_premium:+.2f}" if p.net_premium else ""
            lines.append(f"  {p.strategy:<16} {prem}  (opened {p.opened_at[:10]})")

    console.print()
    console.print(Panel("\n".join(lines), title=ticker, border_style="cyan"))
    console.print()


# ── Router ────────────────────────────────────────────────────────────────────



def _fetch_watchlist_fundamentals(ticker):
    import yfinance as yf
    try:
        tk = yf.Ticker(ticker)
        info = tk.fast_info
        full = {}
        try: full = tk.info
        except Exception: pass
        market_cap = getattr(info, 'market_cap', None)
        if market_cap: market_cap = round(market_cap / 1e9, 2)
        next_earn = None
        try:
            cal = tk.calendar
            if isinstance(cal, dict) and 'Earnings Date' in cal:
                dates = cal['Earnings Date']
                if dates: next_earn = str(dates[0])[:10]
        except Exception: pass
        if not next_earn:
            ed = full.get('earningsDate')
            if isinstance(ed, list) and ed: next_earn = str(ed[0])[:10]
            elif ed: next_earn = str(ed)[:10]
        return ticker, {
            'company_name':     full.get('longName') or full.get('shortName'),
            'sector':           full.get('sector'),
            'market_cap':       market_cap,
            'avg_daily_volume': getattr(info, 'three_month_average_volume', None),
            'week_52_high':     getattr(info, 'fifty_two_week_high', None),
            'week_52_low':      getattr(info, 'fifty_two_week_low', None),
            'beta':             round(full['beta'], 2) if full.get('beta') else None,
            'dividend_yield':   round(full['dividendYield'], 4) if full.get('dividendYield') else None,
            'next_earnings':    next_earn,
        }, None
    except Exception as e:
        return ticker, {}, str(e)


def cmd_refresh(args):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import datetime
    from helm.db import get_conn
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn
    force = '--force' in args
    conn = get_conn()
    if force:
        rows = conn.execute('SELECT ticker FROM watchlist ORDER BY ticker').fetchall()
    else:
        rows = conn.execute(
            'SELECT ticker FROM watchlist '
            'WHERE last_fundamentals_at IS NULL '
            "   OR last_fundamentals_at < date('now', '-30 days') "
            'ORDER BY ticker'
        ).fetchall()
    tickers = [r[0] for r in rows]
    if not tickers:
        console.print('  [green]All fundamentals are current.[/green] Use --force to refresh all.')
        return
    console.print()
    console.print(f"  Refreshing fundamentals for [bold]{len(tickers)}[/bold] tickers...")
    console.print()
    updated = failed = 0
    now = datetime.now().isoformat()
    with Progress(SpinnerColumn(), TextColumn('[progress.description]{task.description}'),
                  BarColumn(), MofNCompleteColumn(), console=console, transient=True) as progress:
        task = progress.add_task('Fetching...', total=len(tickers))
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_watchlist_fundamentals, t): t for t in tickers}
            for fut in as_completed(futures):
                ticker, data, err = fut.result()
                progress.advance(task)
                if err or not data:
                    failed += 1
                    continue
                fields = {k: v for k, v in data.items() if v is not None}
                fields['last_fundamentals_at'] = now
                set_clause = ', '.join(f'{k} = ?' for k in fields)
                vals = list(fields.values()) + [ticker]
                conn.execute(f'UPDATE watchlist SET {set_clause} WHERE ticker = ?', vals)
                conn.commit()
                updated += 1
    console.print(f"  [green]Updated:[/green] {updated}  [dim]Failed:[/dim] {failed}")
    console.print()
    sectors = conn.execute(
        'SELECT sector, COUNT(*) as n FROM watchlist WHERE sector IS NOT NULL GROUP BY sector ORDER BY n DESC'
    ).fetchall()
    if sectors:
        console.print('  [bold dim]Sector breakdown:[/bold dim]')
        t = Table(box=box.SIMPLE, show_header=False, padding=(0,2))
        t.add_column(style='cyan'); t.add_column(style='dim', justify='right')
        for s, n in sectors: t.add_row(s, str(n))
        console.print(t)
    console.print()

def run():
    args = sys.argv[1:]

    if not args or args[0] in ("--help", "-h", "help"):
        print_usage()
        return

    if not get_active_account():
        console.print("[red]No active account.[/red] Run [bold]helm setup[/bold] first.")
        return

    cmd  = args[0].lower()
    rest = args[1:]

    if   cmd == "build":   cmd_build(rest)
    elif cmd == "add":     cmd_add(rest)
    elif cmd == "suggest": cmd_suggest(rest)
    elif cmd == "list":    cmd_list(rest)
    elif cmd == "remove":  cmd_remove(rest)
    elif cmd == "screen":  cmd_screen_manual(rest)
    elif cmd == "wto":     cmd_wto(rest)
    elif cmd == "show":    cmd_show(rest)
    elif cmd == "refresh": cmd_refresh(rest)
    else:
        console.print(f"[red]Unknown watchlist command:[/red] {cmd}")
        print_usage()


def print_usage():
    console.print()
    console.print("[bold]Usage:[/bold]  helm watchlist <command> [args]")
    console.print()
    t = Table(box=box.SIMPLE, show_header=False, padding=(0,2))
    t.add_column(style="cyan bold")
    t.add_column(style="dim")
    t.add_row("build",                   "Guided universe builder")
    t.add_row("add AAPL,NVDA",           "Fast add (instant, no API)")
    t.add_row("add AAPL,NVDA --evaluate","Add with OI/volume feedback")
    t.add_row("suggest AAPL,NVDA",       "Evaluate without adding")
    t.add_row("refresh",             "Refresh fundamentals for all tickers")
    t.add_row("list [--optionable|--wto]","Show watchlist")
    t.add_row("remove AAPL",             "Remove a ticker")
    t.add_row("screen AAPL",             "Mark as optionable manually")
    t.add_row("wto AAPL",                "Toggle willing-to-own")
    t.add_row("show AAPL",               "Detail view")
    console.print(t)
    console.print()


if __name__ == "__main__":
    run()
