
# helm/cli/scan_cmd.py
# helm scan -- scan optionable watchlist tickers for opportunities
#
# Stage 3 of the HELM workflow:
#   watchlist -> screen -> SCAN -> open
#
# For each optionable ticker, fetches technical indicators and IV context,
# computes a directional bias score, and suggests the best strategy.
# Output is a ranked table of actionable candidates.
#
# Usage:
#   helm scan                    Scan all optionable tickers
#   helm scan NVDA,AAPL          Scan specific tickers
#   helm scan --strategy CSP     Show only CSP candidates
#   helm scan --min-iv 30        Only tickers with IV >= 30%
#   helm scan --top 10           Show top N candidates

import sys
try:
    from helm.models.theme import log_event, check_nudges as _check_nudges
except Exception:
    log_event = lambda *a, **k: None
    _check_nudges = lambda: []

import time
import logging
import warnings
from pathlib import Path
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("asyncio").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore")

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich import box

from helm.config import get_active_account
from helm.models.watchlist import WatchlistItem
from helm.db import get_conn

console = Console()

# ── Strategy mapping ──────────────────────────────────────────────────────────

def bias_to_strategy(score: int, iv_pct, rsi=None, ivr=None):
    """
    Map bias score + IV + RSI + IVR to best strategy.
    LONG_CALL: bullish + low IV + oversold RSI — cheap premium, momentum entry
    DIAGONAL:  bullish + low IV + RSI 35-50 — steady uptrend, harvest time decay
    CSP:       bullish + high IV — sell premium into elevated volatility
    """
    # Industry-standard thresholds (tastytrade/TradeStation research)
    iv_high      = iv_pct is not None and float(iv_pct) >= 40  # raw IV% fallback for selling
    iv_low       = iv_pct is not None and float(iv_pct) < 30   # raw IV% fallback for buying
    rsi_oversold = rsi is not None and float(rsi) < 30         # Wilder standard oversold
    rsi_rising   = rsi is not None and 30 <= float(rsi) < 50   # recovering from oversold
    # IVR thresholds (preferred over raw IV% when available)
    ivr_low      = ivr is not None and float(ivr) <= 25        # cheap options: IVR bottom quartile
    ivr_high     = ivr is not None and float(ivr) >= 50        # rich options: IVR upper half
    ivr_neutral  = ivr is not None and 25 < float(ivr) < 50   # neutral: let directional bias decide
    # Prefer IVR over raw IV% when available
    cheap_options = (ivr_low) or (ivr is None and iv_low)
    rich_premium  = (ivr_high) or (ivr is None and iv_high)

    if score >= 2:  # Bullish
        if cheap_options and rsi_oversold:
            return "LONG_CALL", "Bullish + low IVR + oversold RSI — cheap premium, mean reversion"
        elif cheap_options and rsi_rising:
            return "DIAGONAL", "Bullish + low IVR + rising RSI — diagonal to capture trend"
        elif rich_premium:
            return "CSP", "Bullish bias + elevated IV/IVR — ideal for cash-secured put"
        else:
            return "BULL_PUT_SPREAD", "Bullish bias, moderate IV — defined risk spread"
    elif score == 1:  # Mildly bullish
        if cheap_options and rsi_oversold:
            return "LONG_CALL", "Mildly bullish + low IVR + oversold — long call for directional move"
        elif rich_premium:
            return "CSP", "Mildly bullish + elevated IV/IVR — CSP with comfortable strike"
        else:
            return "BULL_PUT_SPREAD", "Mildly bullish — defined risk spread preferred"
    elif score == 0:
        if iv_high:
            return "SHORT_STRANGLE", "Neutral + elevated IV — collect premium both sides"
        else:
            return "IRON_CONDOR", "Neutral, moderate IV — defined risk condor"
    elif score == -1:
        if iv_high:
            return "BEAR_CALL_SPREAD", "Mildly bearish + elevated IV — defined risk spread"
        else:
            return "IRON_CONDOR", "Mildly bearish — defined risk condor"
    else:  # score <= -2, Bearish
        return "BEAR_CALL_SPREAD", "Bearish bias — defined risk bear call spread"


def score_label(score: int) -> str:
    if score >= 2:   return "[green]Bullish[/green]"
    elif score == 1: return "[cyan]Mildly bullish[/cyan]"
    elif score == 0: return "[yellow]Neutral[/yellow]"
    elif score == -1:return "[yellow]Mildly bearish[/yellow]"
    else:            return "[red]Bearish[/red]"


# ── Technical indicator fetch ─────────────────────────────────────────────────

def fetch_technicals(ticker: str) -> dict:
    """
    Fetch technical indicators for a ticker using yfinance.
    Returns dict with RSI, EMAs, SMAs, ATR, IV, price context.
    """
    import yfinance as yf
    import numpy as np

    result = {
        "ticker": ticker,
        "price": None,
        "rsi_14": None,
        "ema_20": None,
        "sma_50": None,
        "sma_200": None,
        "atr_14": None,
        "iv_current": None,
        "week_52_high": None,
        "week_52_low": None,
        "price_vs_52wk_pct": None,  # 0=at low, 100=at high
        "bias_score": 0,
        "bias_factors": [],
        "strategy": None,
        "strategy_rationale": None,
        "atr_strikes": None,  # suggested strike range based on ATR
        "error": None,
    }

    try:
        tk = yf.Ticker(ticker)

        # Get 1 year of daily data for indicators
        hist = tk.history(period="1y", interval="1d")
        if hist.empty or len(hist) < 50:
            result["error"] = "Insufficient price history"
            return result

        close = hist["Close"]
        high  = hist["High"]
        low   = hist["Low"]

        price = float(close.iloc[-1])
        result["price"] = round(price, 2)

        # ── RSI(14) ───────────────────────────────────────────────────────────
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = 100 - (100 / (1 + rs))
        result["rsi_14"] = round(float(rsi.iloc[-1]), 1)

        # ── EMAs and SMAs ─────────────────────────────────────────────────────
        result["ema_20"]  = round(float(close.ewm(span=20).mean().iloc[-1]), 2)
        result["sma_50"]  = round(float(close.rolling(50).mean().iloc[-1]), 2)
        if len(close) >= 200:
            result["sma_200"] = round(float(close.rolling(200).mean().iloc[-1]), 2)

        # ── ATR(14) ───────────────────────────────────────────────────────────
        prev_close = close.shift(1)
        tr = np.maximum(
            high - low,
            np.maximum(abs(high - prev_close), abs(low - prev_close))
        )
        atr = float(tr.rolling(14).mean().iloc[-1])
        result["atr_14"] = round(atr, 2)

        # ATR-based strike suggestions for CSP (1 and 2 ATRs below spot)
        result["atr_strikes"] = {
            "1atr": round(price - atr, 2),
            "2atr": round(price - (2 * atr), 2),
        }

        # ── 52-week range ─────────────────────────────────────────────────────
        high_52 = float(close.rolling(252).max().iloc[-1])
        low_52  = float(close.rolling(252).min().iloc[-1])
        result["week_52_high"] = round(high_52, 2)
        result["week_52_low"]  = round(low_52, 2)
        if high_52 > low_52:
            pct = (price - low_52) / (high_52 - low_52) * 100
            result["price_vs_52wk_pct"] = round(pct, 1)

        # ── IV from options chain ─────────────────────────────────────────────
        try:
            exps = tk.options
            today = date.today()
            for exp in exps:
                dte = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
                if dte >= 20:
                    chain = tk.option_chain(exp)
                    puts = chain.puts
                    puts["dist"] = (puts["strike"] - price).abs()
                    row = puts.nsmallest(1, "dist").iloc[0]
                    iv = row.get("impliedVolatility")
                    if iv and float(iv) > 0:
                        result["iv_current"] = round(float(iv) * 100, 1)
                    break
        except Exception:
            pass

        # ── Bias scoring ──────────────────────────────────────────────────────
        score = 0
        factors = []

        # RSI
        rsi_val = result["rsi_14"]
        if rsi_val is not None:
            if rsi_val < 30:
                score += 2; factors.append(f"RSI {rsi_val:.0f} — oversold")
            elif rsi_val < 45:
                score += 1; factors.append(f"RSI {rsi_val:.0f} — below midpoint")
            elif rsi_val > 70:
                score -= 2; factors.append(f"RSI {rsi_val:.0f} — overbought")
            elif rsi_val > 55:
                score -= 1; factors.append(f"RSI {rsi_val:.0f} — above midpoint")

        # Trend (price vs EMAs)
        ema20 = result["ema_20"]
        sma50 = result["sma_50"]
        if ema20 and sma50:
            if price > ema20 > sma50:
                score += 1; factors.append("Price > EMA20 > SMA50 — uptrend")
            elif price < ema20 < sma50:
                score -= 1; factors.append("Price < EMA20 < SMA50 — downtrend")

        # 52-week position
        pct_52 = result["price_vs_52wk_pct"]
        if pct_52 is not None:
            if pct_52 <= 25:
                score += 1; factors.append(f"Near 52wk low ({pct_52:.0f}% of range) — mean reversion")
            elif pct_52 >= 75:
                score -= 1; factors.append(f"Near 52wk high ({pct_52:.0f}% of range)")

        # IV context
        iv = result["iv_current"]
        if iv is not None:
            if iv >= 50:
                score += 1; factors.append(f"IV {iv:.0f}% — very elevated, premium selling favored")
            elif iv >= 35:
                factors.append(f"IV {iv:.0f}% — elevated")
            elif iv < 20:
                score -= 1; factors.append(f"IV {iv:.0f}% — low, premium selling less attractive")

        result["bias_score"] = max(-3, min(3, score))
        result["bias_factors"] = factors

        strategy, rationale = bias_to_strategy(result["bias_score"], iv, rsi=result.get("rsi"), ivr=result.get("iv_rank"))
        result["strategy"] = strategy
        result["strategy_rationale"] = rationale

        # IVR signal injection
        _ivr = result.get("iv_rank")
        if _ivr is not None:
            if strategy in ("CSP", "SHORT_STRANGLE", "IRON_CONDOR") and _ivr < 25:
                result["bias_factors"].insert(0, f"⚠ Low IVR {_ivr:.0f} — selling into cheap IV")
            elif strategy in ("CSP", "SHORT_STRANGLE", "IRON_CONDOR") and _ivr >= 50:
                result["bias_factors"].insert(0, f"✓ IVR {_ivr:.0f} — elevated, good premium")
            elif strategy == "LONG_CALL" and _ivr <= 25:
                result["bias_factors"].insert(0, f"✓ IVR {_ivr:.0f} — low IV, cheap options")
            elif strategy == "LONG_CALL" and _ivr > 50:  # IV crush risk
                result["bias_factors"].insert(0, f"⚠ IVR {_ivr:.0f} — buying expensive options")

        return result

    except Exception as e:
        result["error"] = str(e)[:60]
        return result


# ── Main command ──────────────────────────────────────────────────────────────

def run():
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        console.print()
        console.print("[bold]Usage:[/bold]  helm scan [tickers] [options]")
        console.print("[dim]  --strategy CSP    Show only specific strategy[/dim]")
        console.print("[dim]  --min-iv N        Minimum IV% threshold[/dim]")
        console.print("[dim]  --top N           Show top N candidates[/dim]")
        console.print("[dim]  --workers N       Concurrent workers (default: 5)[/dim]")
        console.print()
        return

    if not get_active_account():
        console.print("[red]No active account. Run helm setup first.[/red]")
        return

    # Parse args
    strategy_filter = None
    min_iv = None
    top_n = None
    workers = 5
    ticker_args = []

    i = 0
    while i < len(args):
        if args[i] == "--strategy" and i+1 < len(args): strategy_filter = args[i+1].upper(); i += 2
        elif args[i] == "--min-iv" and i+1 < len(args): min_iv = float(args[i+1]); i += 2
        elif args[i] == "--top" and i+1 < len(args):    top_n = int(args[i+1]); i += 2
        elif args[i] == "--workers" and i+1 < len(args): workers = int(args[i+1]); i += 2
        else: ticker_args.append(args[i]); i += 1

    # Determine tickers
    if ticker_args:
        tickers = [t.strip().upper() for raw in ticker_args
                   for t in raw.replace(",", " ").split() if t.strip()]
    else:
        items = WatchlistItem.optionable()
        if not items:
            console.print("[yellow]No optionable tickers found.[/yellow]")
            console.print("[dim]Run [bold]helm screen[/bold] first to mark tickers as optionable.[/dim]")
            return
        tickers = [item.ticker for item in items]

    console.print()
    console.print(Panel.fit(
        f"[bold cyan]HELM Scan[/bold cyan] — Technical Signal Scanner\n"
        f"[dim]{len(tickers)} tickers | {workers} concurrent workers[/dim]",
        border_style="cyan"
    ))
    console.print()

    results = []
    completed = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Scanning...", total=len(tickers))

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(fetch_technicals, t): t for t in tickers}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    res = future.result()
                except Exception as e:
                    res = {"ticker": ticker, "error": str(e), "bias_score": 0,
                           "strategy": None, "price": None, "rsi_14": None,
                           "iv_current": None, "atr_14": None, "atr_strikes": None,
                           "bias_factors": [], "strategy_rationale": None,
                           "price_vs_52wk_pct": None}
                results.append(res)
                completed += 1
                score = res.get("bias_score", 0)
                strat = res.get("strategy", "--")
                progress.update(task, advance=1,
                    description=f"[dim]{ticker}[/dim] score={score:+d} {strat or ''} ({completed}/{len(tickers)})")

    console.print()

    # Filter results
    valid = [r for r in results if not r.get("error") and r.get("strategy")]
    errors = [r for r in results if r.get("error")]

    if strategy_filter:
        valid = [r for r in valid if r.get("strategy") == strategy_filter]
    if min_iv is not None:
        valid = [r for r in valid if r.get("iv_current") and r["iv_current"] >= min_iv]

    # Sort by bias score (absolute value first, then bullish bias for CSP focus)
    valid.sort(key=lambda r: (-abs(r["bias_score"]), -r["bias_score"]))

    if top_n:
        valid = valid[:top_n]

    if not valid:
        console.print("[yellow]No candidates found matching criteria.[/yellow]")
        return

    # Results table
    t = Table(box=box.SIMPLE_HEAD, show_header=True, padding=(0,1), width=170)
    t.add_column("Ticker",   style="bold cyan", width=7, no_wrap=True)
    t.add_column("Price",    justify="right", width=8, no_wrap=True)
    t.add_column("Bias",     width=16, no_wrap=True)
    t.add_column("Strategy", width=16, no_wrap=True)
    t.add_column("RSI",      justify="right", width=5, no_wrap=True)
    t.add_column("IV%",      justify="right", width=5, no_wrap=True)
    t.add_column("IVR",      justify="right", width=5, no_wrap=True)
    t.add_column("IVP",      justify="right", width=5, no_wrap=True)
    t.add_column("ATR",      justify="right", width=7, no_wrap=True)
    t.add_column("1-ATR Strike", justify="right", width=12, no_wrap=True)
    t.add_column("2-ATR Strike", justify="right", width=12, no_wrap=True)
    t.add_column("Top Signal", width=45, no_wrap=True)

    strategy_colors = {
        "CSP": "green", "BULL_PUT_SPREAD": "cyan",
        "SHORT_STRANGLE": "magenta", "IRON_CONDOR": "blue",
        "BEAR_CALL_SPREAD": "red", "LONG_CALL": "yellow", "DIAGONAL": "cyan",
    }

    from helm.models.iv_history import IVHistory
    _ivr_data = IVHistory.for_tickers([r["ticker"] for r in valid])

    for res in valid:
        score = res["bias_score"]
        strat = res.get("strategy", "--")
        color = strategy_colors.get(strat, "white")
        strat_str = f"[{color}]{strat}[/{color}]"
        bias_str = score_label(score)
        rsi = f"{res['rsi_14']:.0f}" if res.get("rsi_14") else "--"
        iv  = f"{res['iv_current']:.0f}%" if res.get("iv_current") else "--"
        atr = f"${res['atr_14']:.2f}" if res.get("atr_14") else "--"
        strikes = res.get("atr_strikes") or {}
        s1 = f"${strikes.get('1atr', 0):.2f}" if strikes.get("1atr") else "--"
        s2 = f"${strikes.get('2atr', 0):.2f}" if strikes.get("2atr") else "--"
        _ivr = _ivr_data.get(res["ticker"])
        ivr_str = _ivr.rank_label if _ivr else "[dim]--[/dim]"
        ivp_str = _ivr.percentile_label if _ivr else "[dim]--[/dim]"
        top_factor = res["bias_factors"][0] if res.get("bias_factors") else "--"
        price = f"${res['price']:.2f}" if res.get("price") else "--"

        t.add_row(res["ticker"], price, bias_str, strat_str,
                  rsi, iv, ivr_str, ivp_str, atr, s1, s2, top_factor)

    console.print(f"[bold]Scan Results — {len(valid)} candidates[/bold]")
    console.print()
    console.print(t)
    console.print()

    # Strategy summary
    from collections import Counter
    counts = Counter(r.get("strategy") for r in valid if r.get("strategy"))
    summary = "  ".join(f"[{strategy_colors.get(s,'white')}]{s}[/{strategy_colors.get(s,'white')}]: {n}"
                        for s, n in counts.most_common())
    console.print(f"[dim]{summary}[/dim]")
    console.print()

    if errors:
        console.print(f"[dim]{len(errors)} ticker(s) had errors: {', '.join(r['ticker'] for r in errors[:5])}[/dim]")
        console.print()

    console.print(Panel.fit(
        "[dim]Next step: [bold]helm open <ticker>[/bold] to evaluate a specific contract[/dim]",
        border_style="dim"
    ))
    console.print()

    # Log scan event and show nudges
    try:
        log_event("SCREEN_RUN")
        nudges = _check_nudges()
        if nudges:
            console.print()
            for n in nudges:
                console.print(n)
            console.print()
    except Exception:
        pass


if __name__ == "__main__":
    run()
