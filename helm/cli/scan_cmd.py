
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


def compute_conviction(score: int, ivr=None) -> str:
    """
    Compute conviction level for a strategy recommendation.

    Conviction emerges from two axes:
      - Score magnitude: how strong is the directional signal
      - IVR distance from 50: how clearly is IV in buy or sell territory

    High:     strong direction (abs >= 2) + IV clearly actionable (distance >= 15)
    Moderate: either moderate direction (abs=1) + clear IV, or strong direction + mild IV
    Low:      no direction (score=0), or both signal dimensions weak

    Returns: 'High', 'Moderate', or 'Low'
    """
    if score == 0:
        return 'Low'

    abs_score = abs(score)
    ivr_val = float(ivr) if ivr is not None else None
    ivr_distance = abs(ivr_val - 50) if ivr_val is not None else None

    if abs_score >= 2:
        if ivr_distance is None:
            return 'Moderate'   # strong score, unknown IV
        if ivr_distance >= 15:
            return 'High'       # strong score + clearly actionable IV
        return 'Moderate'       # strong score + mild IV advantage

    elif abs_score == 1:
        if ivr_distance is None:
            return 'Low'
        if ivr_distance >= 20:
            return 'Moderate'   # mild score but clear IV environment
        return 'Low'            # mild score + weak IV edge

    return 'Low'




def bias_to_strategy(score: int, iv_pct, rsi=None, ivr=None):
    """
    Map directional bias + IV environment to best strategy.
    IVR threshold for premium selling lowered to 35 (from 50).
    """
    iv_high      = iv_pct is not None and float(iv_pct) >= 40
    iv_moderate  = iv_pct is not None and 25 <= float(iv_pct) < 40
    iv_low       = iv_pct is not None and float(iv_pct) < 25
    ivr_val      = float(ivr) if ivr is not None else None
    ivr_rich     = ivr_val is not None and ivr_val >= 35   # sell premium (lowered from 50)
    ivr_moderate = ivr_val is not None and 15 <= ivr_val < 35
    ivr_cheap    = ivr_val is not None and ivr_val < 15
    ivr_buyable  = ivr_val is not None and ivr_val < 60
    ivr_unknown  = ivr_val is None  # No IBKR data — defer strategy, use score only
    rsi_val      = float(rsi) if rsi is not None else None
    rsi_oversold = rsi_val is not None and rsi_val < 30
    rsi_bullish  = rsi_val is not None and rsi_val < 60
    rsi_momentum = rsi_val is not None and 40 <= rsi_val <= 65
    rsi_overbought = rsi_val is not None and rsi_val > 65

    if score >= 2:  # Bullish
        if (ivr_buyable or ivr_unknown) and not ivr_rich:
            if ivr_val is not None and ivr_val < 60:
                return 'LONG_CALL', 'Strong bullish bias + IVR confirms reasonable premium'
            elif iv_pct is None or float(iv_pct) < 50:
                return 'LONG_CALL', 'Strong bullish bias + reasonable IV'
        if ivr_rich or (ivr_unknown and iv_high):
            return 'CSP', 'Bullish bias + elevated IVR — ideal for cash-secured put'
        return 'BULL_PUT_SPREAD', 'Bullish bias, moderate IV — defined risk spread'

    elif score == 1:  # Mildly bullish
        # CSP takes priority when IVR >= 35
        if ivr_rich or (ivr_unknown and iv_high):
            return 'CSP', 'Mildly bullish + elevated IVR — CSP with comfortable strike'
        # DIAGONAL when IVR is moderate and momentum present
        if (ivr_moderate or (ivr_unknown and iv_moderate)) and rsi_momentum:
            return 'DIAGONAL', 'Mildly bullish + moderate IVR + momentum — diagonal spread'
        if ivr_cheap or (ivr_unknown and iv_low):
            return 'LONG_CALL', 'Mildly bullish + low IVR — long call while options cheap'
        return 'BULL_PUT_SPREAD', 'Mildly bullish — defined risk spread preferred'

    elif score == 0:
        if ivr_rich or (ivr_unknown and iv_high):
            return 'IRON_CONDOR', 'Neutral + elevated IVR — iron condor (IRA-safe defined risk)'
        if ivr_cheap or (ivr_unknown and iv_low):
            return 'LONG_STRADDLE', 'Neutral + low IVR — buy cheap volatility on both sides'
        return 'IRON_CONDOR', 'Neutral, moderate IV — defined risk condor'

    elif score == -1:
        if ivr_rich or (ivr_unknown and iv_high):
            return 'BEAR_CALL_SPREAD', 'Mildly bearish + elevated IVR — bear call credit spread'
        if ivr_cheap or (ivr_unknown and iv_low):
            return 'BEAR_PUT_SPREAD', 'Mildly bearish + low IVR — buy cheap puts via debit spread'
        return 'IRON_CONDOR', 'Mildly bearish, moderate IV — iron condor for range-bound move'

    else:  # score <= -2, Bearish
        if ivr_rich or (ivr_unknown and iv_high):
            return 'BEAR_CALL_SPREAD', 'Bearish + elevated IVR — bear call credit spread'
        return 'BEAR_PUT_SPREAD', 'Bearish + low IVR — buy cheap puts via debit spread'


def score_label(score: int) -> str:
    if score >= 2:   return "[green]Bullish[/green]"
    elif score == 1: return "[cyan]Mildly bullish[/cyan]"
    elif score == 0: return "[yellow]Neutral[/yellow]"
    elif score == -1:return "[yellow]Mildly bearish[/yellow]"
    else:            return "[red]Bearish[/red]"


# ── Technical indicator fetch ─────────────────────────────────────────────────

def fetch_technicals(ticker: str, ivr_record=None) -> dict:
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
        "iv_rank": None,
        "iv_pct": None,
        "week_52_high": None,
        "week_52_low": None,
        "price_vs_52wk_pct": None,  # 0=at low, 100=at high
        "bias_score": 0,
        "bias_factors": [],
        "strategy": None,
        "strategy_rationale": None,
        "conviction": None,
        "atr_strikes": None,  # suggested strike range based on ATR
        "error": None,
    }

    # Load IVR from IBKR-sourced DB record (populated by helm ivr refresh)
    if ivr_record is not None:
        result["iv_rank"] = ivr_record.iv_rank
        result["iv_pct"]  = ivr_record.iv_percentile
        result["iv_current"] = ivr_record.iv_current

    try:
        tk = yf.Ticker(ticker)

        # Get 1 year of daily data for indicators
        hist = tk.history(period="1y", interval="1d")
        hist = hist.dropna(subset=["Open", "High", "Low", "Close"])
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
        high_52 = float(close.max())
        low_52  = float(close.min())
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
                    if iv and float(iv) > 0 and result["iv_current"] is None:
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
                factors.append(f"Near 52wk high ({pct_52:.0f}% of range) — momentum")  # neutral, not bearish

        # IV context (informational only — premium buy/sell is decided in
        # bias_to_strategy via IVR, not the directional score)
        iv = result["iv_current"]
        if iv is not None:
            if iv >= 50:
                factors.append(f"IV {iv:.0f}% — very elevated, premium selling favored")
            elif iv >= 35:
                factors.append(f"IV {iv:.0f}% — elevated")
            elif iv < 20 and iv > 1.0:
                factors.append(f"IV {iv:.0f}% — low, premium selling less attractive")

        result["bias_score"] = max(-3, min(3, score))
        result["bias_factors"] = factors

        strategy, rationale = bias_to_strategy(result["bias_score"], None, rsi=result.get("rsi_14"), ivr=result.get("iv_rank"))
        conviction = compute_conviction(result["bias_score"], result.get("iv_rank"))
        result["conviction"] = conviction
        result["strategy"] = strategy
        result["strategy_rationale"] = rationale

        # IVR signal injection
        _ivr = result.get("iv_rank")
        if _ivr is not None:
            if strategy in ("CSP", "IRON_CONDOR") and _ivr < 25:
                result["bias_factors"].insert(0, f"⚠ Low IVR {_ivr:.0f} — selling into cheap IV")
            elif strategy in ("CSP", "IRON_CONDOR") and _ivr >= 50:
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

from helm.cli._decision_capture import persist_scan_signals


def run():
    args = sys.argv[1:]

    if "--help" in args or "-h" in args:
        console.print()
        console.print("[bold]Usage:[/bold]  helm scan [tickers] [options]")
        console.print("[dim]  --strategy CSP    Show only specific strategy[/dim]")
        console.print("[dim]  --min-iv N        Minimum IV% threshold[/dim]")
        console.print("[dim]  --top N           Show top N candidates[/dim]")
        console.print("[dim]  --workers N       Concurrent workers (default: 5)[/dim]")
        console.print("[dim]  --blind           Capture only; suppress HELM read (for russ-scan)[/dim]")
        console.print("[dim]  --build TAG       Scan a build set (e.g. sector_v1) vs the active universe[/dim]")
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
    blind = False
    build_tag = None
    ticker_args = []

    i = 0
    while i < len(args):
        if args[i] == "--strategy" and i+1 < len(args): strategy_filter = args[i+1].upper(); i += 2
        elif args[i] == "--min-iv" and i+1 < len(args): min_iv = float(args[i+1]); i += 2
        elif args[i] == "--top" and i+1 < len(args):    top_n = int(args[i+1]); i += 2
        elif args[i] == "--workers" and i+1 < len(args): workers = int(args[i+1]); i += 2
        elif args[i] == "--blind": blind = True; i += 1
        elif args[i] == "--build" and i+1 < len(args): build_tag = args[i+1]; i += 2
        else: ticker_args.append(args[i]); i += 1

    # Determine tickers
    if ticker_args:
        tickers = [t.strip().upper() for raw in ticker_args
                   for t in raw.replace(",", " ").split() if t.strip()]
    else:
        if build_tag:
            items = WatchlistItem.for_build(build_tag)
            if not items:
                console.print(f"[yellow]No optionable tickers in build '{build_tag}'.[/yellow]")
                return
        else:
            items = WatchlistItem.active()
            if not items:
                console.print("[yellow]No active tickers found.[/yellow]")
                console.print("[dim]Set active tickers via the master cull (active flag).[/dim]")
                return
        tickers = [item.ticker for item in items]

    console.print()
    console.print(Panel.fit(
        f"[bold cyan]HELM Scan[/bold cyan] — Technical Signal Scanner\n"
        f"[dim]{len(tickers)} tickers | {workers} concurrent workers[/dim]",
        border_style="cyan"
    ))
    console.print()

    # Pre-load IVR data from DB for all tickers (populated by helm ivr refresh)
    try:
        from helm.models.iv_history import IVHistory
        _ivr_preload = IVHistory.for_tickers(tickers)
    except Exception:
        _ivr_preload = {}

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
            futures = {executor.submit(fetch_technicals, t, _ivr_preload.get(t)): t for t in tickers}
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
    # decision-capture (policy v0): persist every scanned candidate
    try:
        persist_scan_signals(results)
    except Exception:
        pass
    if blind:
        console.print(f"[green]Scan complete.[/green] [bold]{len(results)}[/bold] candidates captured to signals (HELM read suppressed).")
        console.print("[dim]Open russ-scan for your independent picks: http://helm.local:8766/russ-scan[/dim]")
        return

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
    t.add_column("Conviction",  width=10, no_wrap=True)
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
        "IRON_CONDOR": "blue",
        "BEAR_CALL_SPREAD": "red", "LONG_CALL": "yellow", "DIAGONAL": "cyan", "LONG_STRADDLE": "magenta", "BEAR_PUT_SPREAD": "red",
    }

    try:
        from helm.db import get_conn as _gc
        _open_tickers = set(r[0] for r in _gc().execute(
            "SELECT ticker FROM positions WHERE status='OPEN'"
        ).fetchall())
    except Exception:
        _open_tickers = set()
    # Build ivr display data from results (already loaded per-ticker in fetch_technicals)
    # Keep IVHistory for label formatting
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
        _conv = res.get("conviction", "Low")
        _cc = {"High": "green", "Moderate": "yellow", "Low": "dim"}.get(_conv, "dim")
        conv_str = f"[{_cc}]{_conv}[/{_cc}]"
        _tk = res["ticker"]
        _tk_str = _tk
        if _tk in _open_tickers:
            _tk_str = f"{_tk}*"
            strat_str = strat_str + "[dim] open[/dim]"

        t.add_row(_tk_str, price, bias_str, strat_str, conv_str,
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

    console.print("[dim]  * = position already open on this ticker[/dim]")
    console.print(Panel.fit(
        "[dim]Next step: [bold]helm open <ticker>[/bold] to evaluate a specific contract[/dim]  ·  [dim]New? Run [bold]helm guide[/bold] to understand strategy assignments[/dim]",
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
