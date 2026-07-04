
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

# HELM-006: IVR staleness tolerance in calendar days. iv_history updates daily
# on trading days; >3 tolerates a normal weekend but flags a missed refresh.
IVR_STALE_DAYS = 3
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


def compute_conviction(score: int, ivr=None, strategy=None) -> float:
    """
    Strategy-aware conviction, 0-100. Higher = stronger setup FOR THIS strategy.
    IVR is used directionally (sellers want it high, buyers low), not as |IVR-50|.
    """
    abs_score   = abs(int(score)) if score is not None else 0
    directional = min(abs_score / 2.0, 1.0)
    range_conf  = 1.0 - directional

    ivr_val = float(ivr) if ivr is not None else None
    if ivr_val is not None:
        richness  = max(0.0, min(1.0, (ivr_val - 35.0) / 50.0))
        cheapness = max(0.0, min(1.0, (60.0 - ivr_val) / 45.0))
    else:
        richness = cheapness = None

    fam = _strategy_family(strategy)

    if fam == 'range':
        if richness is None:
            return round(100.0 * 0.35 * range_conf, 1)
        return round(100.0 * (0.65 * richness + 0.35 * range_conf), 1)

    if fam == 'buy':
        if cheapness is None:
            return round(100.0 * 0.60 * directional, 1)
        return round(100.0 * (0.60 * directional + 0.40 * cheapness), 1)

    if richness is None:
        return round(100.0 * 0.55 * directional, 1)
    return round(100.0 * (0.55 * directional + 0.45 * richness), 1)


_FAMILY = {
    'buy':   {'LONG_CALL', 'LONG_PUT', 'DIAGONAL', 'PMCC'},
    'range': {'IRON_CONDOR'},
}

def _strategy_family(strategy) -> str:
    if not strategy:
        return 'sell'
    s = str(strategy).upper()
    for fam, names in _FAMILY.items():
        if any(n in s for n in names):
            return fam
    return 'sell'


def conviction_label(score: float) -> str:
    if score is None:
        return 'Low'
    if score >= 65:
        return 'High'
    if score >= 40:
        return 'Moderate'
    return 'Low'




def momentum_bias(price, sma_50, sma_200, ema_20, macd_hist, obv_trend, adx):
    """HELM-042 v2 shadow scorer: four-category momentum read, ADX-gated.

    Sums three directional votes — MA stack (structure), MACD histogram (momentum),
    OBV trend (conviction) — then an ADX regime gate scales the result: a weak or
    absent trend (low ADX) is pulled toward neutral so range-bound names route to
    condors on purpose, not as a leftover. Same [-3, 3] clamp as the legacy score
    for direct comparison. Still shadow: NOT routed. Each vote is skipped when its
    input is None. Thresholds seeded from a s58 real-name sample; tunable.
    Returns (score, factors). Pure.
    """
    score = 0
    factors = []

    # (1) Direction -- MA stack (structure)
    if None not in (price, sma_50, sma_200):
        if price > sma_50 > sma_200:
            score += 2; factors.append("Price > SMA50 > SMA200 -- bullish stack")
        elif price < sma_50 < sma_200:
            score -= 2; factors.append("Price < SMA50 < SMA200 -- bearish stack")
        elif price > sma_50:
            score += 1; factors.append("Price > SMA50 -- above mid-term trend")
        elif price < sma_50:
            score -= 1; factors.append("Price < SMA50 -- below mid-term trend")
    elif None not in (price, ema_20, sma_50):  # SMA200 unavailable (short history)
        if price > ema_20 > sma_50:
            score += 1; factors.append("Price > EMA20 > SMA50 -- uptrend (no SMA200)")
        elif price < ema_20 < sma_50:
            score -= 1; factors.append("Price < EMA20 < SMA50 -- downtrend (no SMA200)")

    # (2) Momentum -- MACD histogram sign
    if macd_hist is not None:
        if macd_hist > 0:
            score += 1; factors.append(f"MACD hist +{macd_hist:.2f} -- momentum up")
        elif macd_hist < 0:
            score -= 1; factors.append(f"MACD hist {macd_hist:.2f} -- momentum down")

    # (3) Conviction -- OBV trend
    if obv_trend:  # +1 / -1 (0 or None -> no vote)
        score += 1 if obv_trend > 0 else -1
        factors.append("OBV rising -- accumulation" if obv_trend > 0
                       else "OBV falling -- distribution")

    # (4) Regime gate -- ADX scales the directional read (range -> neutral)
    if adx is not None:
        if adx < 20:
            if score != 0:
                factors.append(f"ADX {adx:.0f} (<20) -- range-bound, gated to neutral")
            score = 0
        elif adx < 25:
            _pre = score
            score = int(score / 2)  # halve toward zero (weak trend strength)
            if _pre != score:
                factors.append(f"ADX {adx:.0f} (20-25) -- weak trend, directional read halved")
        else:
            factors.append(f"ADX {adx:.0f} -- trend confirmed")
    else:
        factors.append("ADX n/a -- gate skipped")

    return max(-3, min(3, score)), factors


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
        "ivr_date": None,
        "ivr_stale": False,
        "iv_pct": None,
        "week_52_high": None,
        "week_52_low": None,
        "price_vs_52wk_pct": None,  # 0=at low, 100=at high
        "bias_score": 0,
        "bias_factors": [],
        "strategy": None,
        "strategy_rationale": None,
        "conviction": None,
        "conviction_score": None,
        "atr_strikes": None,  # suggested strike range based on ATR
        "macd": None,
        "macd_signal": None,
        "macd_hist": None,
        "obv": None,
        "obv_trend": None,   # +1 rising / -1 falling / 0 flat vs own 20d mean
        "adx": None,
        "plus_di": None,
        "minus_di": None,
        "error": None,
    }

    # Load IVR from IBKR-sourced DB record (populated by helm ivr refresh)
    if ivr_record is not None:
        result["iv_rank"] = ivr_record.iv_rank
        result["iv_pct"]  = ivr_record.iv_percentile
        result["iv_current"] = ivr_record.iv_current
        result["ivr_date"] = ivr_record.date
        if ivr_record.date:
            try:
                _asof = datetime.strptime(ivr_record.date, "%Y-%m-%d").date()
                result["ivr_stale"] = (date.today() - _asof).days > IVR_STALE_DAYS
            except (ValueError, TypeError):
                pass

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

        # ── HELM-042 capability: MACD/OBV/ADX (additive, shadow — no scorer reads these yet) ──
        _ema12 = close.ewm(span=12, adjust=False).mean()
        _ema26 = close.ewm(span=26, adjust=False).mean()
        _macd = _ema12 - _ema26
        _macd_sig = _macd.ewm(span=9, adjust=False).mean()
        result["macd"] = round(float(_macd.iloc[-1]), 3)
        result["macd_signal"] = round(float(_macd_sig.iloc[-1]), 3)
        result["macd_hist"] = round(float((_macd - _macd_sig).iloc[-1]), 3)

        _vol = hist["Volume"]
        _obv = (np.sign(close.diff().fillna(0)) * _vol).cumsum()
        _obv_now = float(_obv.iloc[-1])
        _obv_ref = _obv.rolling(20).mean().iloc[-1]
        result["obv"] = round(_obv_now, 0)
        result["obv_trend"] = (0 if _obv_ref != _obv_ref
                               else 1 if _obv_now > _obv_ref
                               else -1 if _obv_now < _obv_ref else 0)

        _up = high.diff()
        _down = -low.diff()
        _plus_dm = _up.where((_up > _down) & (_up > 0), 0.0)
        _minus_dm = _down.where((_down > _up) & (_down > 0), 0.0)
        _atr_n = tr.rolling(14).mean()
        _plus_di = 100 * _plus_dm.rolling(14).mean() / _atr_n
        _minus_di = 100 * _minus_dm.rolling(14).mean() / _atr_n
        _dx = 100 * (_plus_di - _minus_di).abs() / (_plus_di + _minus_di).replace(0, float("nan"))
        _adx = _dx.rolling(14).mean()
        _pdi, _mdi, _adxv = _plus_di.iloc[-1], _minus_di.iloc[-1], _adx.iloc[-1]
        result["plus_di"]  = round(float(_pdi), 1) if _pdi == _pdi else None
        result["minus_di"] = round(float(_mdi), 1) if _mdi == _mdi else None
        result["adx"]      = round(float(_adxv), 1) if _adxv == _adxv else None

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
        # HELM-042: momentum score computed here; the flip block just below
        # promotes v2 to the operative bias_score (legacy kept as display shadow).
        _mo_score, _mo_factors = momentum_bias(
            result.get("price"), result.get("sma_50"), result.get("sma_200"),
            result.get("ema_20"), result.get("macd_hist"),
            result.get("obv_trend"), result.get("adx"))
        result["momentum_bias_score"] = _mo_score
        result["momentum_bias_factors"] = _mo_factors
        # HELM-042 flip (s58): v2 momentum is now the OPERATIVE bias for the scan.
        # Legacy demoted to a display-only shadow (not recorded). Every downstream
        # read of result["bias_score"] -- routing, conviction, sort, the Bias column,
        # and the persisted auto_bias_score -- follows this automatically.
        # Revert = delete these three assignments.
        result["legacy_bias_score"] = result["bias_score"]
        result["bias_score"] = result["momentum_bias_score"]
        result["bias_factors"] = result["momentum_bias_factors"]

        strategy, rationale = bias_to_strategy(result["bias_score"], None, rsi=result.get("rsi_14"), ivr=result.get("iv_rank"))
        conv_score = compute_conviction(result["bias_score"], result.get("iv_rank"), strategy)
        result["conviction_score"] = conv_score
        result["conviction"] = conviction_label(conv_score)
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

        if result.get("ivr_stale"):
            result["bias_factors"].insert(0, f"⚠ IVR stale (as-of {result.get('ivr_date')}) — run helm ivr refresh")
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
            items = WatchlistItem.active_universe()
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

    # --- earnings refresh (HELM-EARN-REFRESH-v1) ---
    # Populate watchlist.next_earnings for the scanned names (7-day staleness gate)
    # so each signal can carry earnings proximity. yfinance; independent of IBKR.
    try:
        from helm.earnings import refresh_watchlist_earnings
        _esum = refresh_watchlist_earnings(get_conn(), tickers=tickers)
        console.print(f"  [dim]earnings: {_esum['updated']} fetched, {_esum['cached']} cached[/dim]")
        console.print()
    except Exception as _ee:
        console.print(f"  [yellow]![/yellow]  earnings refresh skipped: {_ee}")
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
    t.add_column("Legacy",   width=8, no_wrap=True)
    t.add_column("Strategy", width=16, no_wrap=True)
    t.add_column("Conviction",  width=10, no_wrap=True)
    t.add_column("Earnings", width=12, no_wrap=True)  # HELM-EARN-DISPLAY-v1
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
    # HELM-EARN-DISPLAY-v1: earnings proximity for the scan table
    from helm.earnings import classify_earnings
    try:
        _earn_map = {row["ticker"]: row["next_earnings"] for row in get_conn().execute(
            "SELECT ticker, next_earnings FROM watchlist").fetchall()}
    except Exception:
        _earn_map = {}

    for res in valid:
        score = res["bias_score"]
        strat = res.get("strategy", "--")
        color = strategy_colors.get(strat, "white")
        strat_str = f"[{color}]{strat}[/{color}]"
        bias_str = score_label(score)
        _lb = res.get("legacy_bias_score")
        legacy_str = "[dim]--[/dim]" if _lb is None else f"[dim]{_lb:+d}[/dim]"
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

        _ed = _earn_map.get(_tk)
        _d, _sev = classify_earnings(_ed)  # HELM-044-L1b: shared classifier; split past vs unknown
        if _sev == "warn":
            _earn_str = f"[yellow]{_ed[5:]} {_d}d[/yellow]"
        elif _sev == "ok":
            _earn_str = f"[dim]{_ed[5:]} {_d}d[/dim]"
        elif _sev == "past":
            _earn_str = "[dim]past[/dim]"
        else:
            _earn_str = "[dim]--[/dim]"
        t.add_row(_tk_str, price, bias_str, legacy_str, strat_str, conv_str, _earn_str,
                  rsi, iv, ivr_str, ivp_str, atr, s1, s2, top_factor)

    console.print(f"[bold]Scan Results — {len(valid)} candidates[/bold]")
    console.print()
    console.print(t)
    console.print()

    _stale_n = sum(1 for r in valid if r.get("ivr_stale"))
    if _stale_n:
        console.print(f"[yellow]⚠ {_stale_n} candidate(s) scored on IVR older than {IVR_STALE_DAYS}d — run [bold]helm ivr refresh[/bold] for current ranks.[/yellow]")
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
