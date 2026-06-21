
# helm/cli/open_cmd.py
# helm open -- evaluate specific contracts for a new position
#
# Stage 4 of the HELM workflow:
#   watchlist -> screen -> scan -> OPEN
#
# Given a ticker and strategy, pulls the options chain for the target DTE range,
# scores each contract on delta, OI, spread%, and theta, and presents a ranked
# table of the best contracts to open.
#
# Spread % is evaluated HERE at the specific strike level -- not in helm screen.
#
# Usage:
#   helm open ANET CSP              Evaluate CSP contracts for ANET
#   helm open ANET CSP --dte 45     Target 45 DTE (default: 30-45)
#   helm open ANET LONG_CALL        Evaluate long call contracts
#   helm open ANET CSP --top 5      Show top 5 contracts

import sys
import math
import logging
import warnings
from pathlib import Path
from datetime import date, datetime
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logging.getLogger("ib_insync").setLevel(logging.CRITICAL)
logging.getLogger("asyncio").setLevel(logging.CRITICAL)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore")

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Confirm
from rich import box

from helm.config import get_active_account
from helm.db import get_conn

console = Console()

# ── Strategy configuration ────────────────────────────────────────────────────

STRATEGY_CONFIG = {
    "CSP": {
        "option_type": "PUT",
        "direction": "SHORT",
        "delta_min": 0.15,
        "delta_max": 0.40,
        "delta_sweet": (0.25, 0.35),
        "dte_min": 21,
        "dte_max": 50,
        "label": "Cash-Secured Put",
    },
    "COVERED_CALL": {
        "option_type": "CALL",
        "direction": "SHORT",
        "delta_min": 0.20,
        "delta_max": 0.45,
        "delta_sweet": (0.25, 0.35),
        "dte_min": 21,
        "dte_max": 50,
        "label": "Covered Call",
    },
    "LONG_CALL": {
        "option_type": "CALL",
        "direction": "LONG",
        "delta_min": 0.40,   # industry standard: ATM/slightly ITM for better R/R
        "delta_max": 0.70,
        "delta_sweet": (0.45, 0.60),
        "dte_min": 60,       # minimum 60 DTE to give move time to develop
        "dte_max": 90,
        "label": "Long Call",
    },
    "LONG_PUT": {
        "option_type": "PUT",
        "direction": "LONG",
        "delta_min": 0.30,
        "delta_max": 0.70,
        "delta_sweet": (0.40, 0.60),
        "dte_min": 30,
        "dte_max": 90,
        "label": "Long Put",
    },
    "BULL_PUT_SPREAD": {
        "option_type": "PUT",
        "direction": "SHORT",
        "delta_min": 0.15,
        "delta_max": 0.40,
        "delta_sweet": (0.20, 0.35),
        "dte_min": 21,
        "dte_max": 56,
        "label": "Bull Put Spread",
        "is_spread": True,
        "spread_widths": [5, 10, 15, 20, 25],  # $ widths to evaluate
    },
    "BEAR_CALL_SPREAD": {
        "option_type": "CALL",
        "direction": "SHORT",
        "delta_min": 0.15,
        "delta_max": 0.40,
        "delta_sweet": (0.20, 0.35),
        "dte_min": 21,
        "dte_max": 56,
        "label": "Bear Call Spread",
        "is_spread": True,
        "spread_widths": [5, 10, 15, 20, 25],
    },
    "BEAR_PUT_SPREAD": {
        "option_type": "PUT",
        "direction": "LONG",       # buy the higher strike put (debit)
        "delta_min": 0.30,
        "delta_max": 0.60,
        "delta_sweet": (0.40, 0.55),
        "dte_min": 30,
        "dte_max": 90,
        "dte_sweet": 60,
        "label": "Bear Put Spread",
        "is_debit_spread": True,
        "spread_widths": [5, 10, 15, 20, 25],
    },
    "LONG_STRADDLE": {
        "option_type": "BOTH",     # buy ATM call + ATM put
        "direction": "LONG",
        "delta_min": 0.40,
        "delta_max": 0.60,
        "delta_sweet": 0.50,
        "dte_min": 30,
        "dte_max": 90,
        "dte_sweet": 45,
        "label": "Long Straddle",
        "is_straddle": True,
    },
    "BULL_CALL_SPREAD": {
        "option_type": "CALL",
        "direction": "LONG",       # buy the lower strike call (debit)
        "delta_min": 0.30,
        "delta_max": 0.60,
        "delta_sweet": (0.40, 0.55),
        "dte_min": 30,
        "dte_max": 90,
        "dte_sweet": 60,
        "label": "Bull Call Spread",
        "is_debit_spread": True,
        "spread_widths": [5, 10, 15, 20, 25],
    },
    "IRON_CONDOR": {
        "option_type": "BOTH",
        "direction": "SHORT",
        "delta_min": 0.15,
        "delta_max": 0.35,
        "delta_sweet": (0.20, 0.30),
        "dte_min": 21,
        "dte_max": 56,
        "label": "Iron Condor",
        "is_strangle": True,
    },
    "IRON_CONDOR": {
        "option_type": "BOTH",
        "direction": "SHORT",
        "delta_min": 0.15,
        "delta_max": 0.35,
        "delta_sweet": (0.20, 0.30),
        "dte_min": 21,
        "dte_max": 56,
        "label": "Iron Condor",
        "is_condor": True,
        "spread_widths": [5, 10, 15, 20],
    },
    "PMCC": {
        "option_type": "CALL",
        "label": "Poor Man's Covered Call (PMCC)",
        "is_pmcc": True,
        "short_dte_min": 21, "short_dte_max": 45, "short_dte_sweet": 30,
        "short_delta_min": 0.20, "short_delta_max": 0.35, "short_delta_sweet": (0.25, 0.30),
        "long_dte_min": 150, "long_dte_max": 730, "long_dte_sweet": 365,
        "long_delta_min": 0.70, "long_delta_max": 0.90, "long_delta_sweet": (0.75, 0.85),
        "max_debit_pct": 1.0,
        # Header display values (actual filters live in PMCC_CONFIG in diagonal.py)
        "delta_min": 0.20,   "delta_max": 0.90,
        "delta_sweet": (0.75, 0.85),
        "dte_min": 21,       "dte_max": 730,
    },
    "PERM": {
        "option_type": "CALL",
        "label": "Pre-Earnings Run-up (PERM)",
        "is_perm": True,
        "delta_min": 0.35,   "delta_max": 0.65,
        "delta_sweet": (0.45, 0.55),
        "dte_min": 14,       "dte_max": 60,
    },
    "DIAGONAL": {
        "option_type": "CALL",
        "label": "Diagonal Spread",
        "is_diagonal": True,
        "short_dte_min": 21,  "short_dte_max": 45,  "short_dte_sweet": 30,
        "short_delta_min": 0.30, "short_delta_max": 0.55, "short_delta_sweet": (0.38, 0.45),
        "long_dte_min": 60,   "long_dte_max": 120, "long_dte_sweet": 75,
        "long_delta_min": 0.55, "long_delta_max": 0.85, "long_delta_sweet": (0.65, 0.75),
        "max_debit_pct": 1.0,
    },
    "DIAGONAL_PUT": {
        "option_type": "PUT",
        "label": "Diagonal Spread (Put)",
        "is_diagonal_put": True,
        "short_dte_min": 21,  "short_dte_max": 45,  "short_dte_sweet": 30,
        "short_delta_min": 0.30, "short_delta_max": 0.55, "short_delta_sweet": (0.38, 0.45),
        "long_dte_min": 60,   "long_dte_max": 120, "long_dte_sweet": 75,
        "long_delta_min": 0.55, "long_delta_max": 0.85, "long_delta_sweet": (0.65, 0.75),
        "max_debit_pct": 1.0,
    },
}

# ── Contract scoring (adapted from COTS ladder.py) ────────────────────────────

def fetch_ibkr_greeks(contracts: list) -> dict:  # DEPRECATED
    """
    Fetch live Greeks from IBKR for a list of contracts.
    Returns dict keyed by (expiration, strike, opt_type) -> greeks dict.
    Only called when IBKR is connected and market is open.
    """
    results = {}
    try:
        from helm.ibkr import check_connection
        from helm.cli.check_cmd import is_market_open
        import math

        if not check_connection()["connected"]:
            return results
        if not is_market_open():
            return results

        from ib_insync import IB, Option as IBOption
        ib = IB()
        ib.connect("127.0.0.1", 4002, clientId=14, readonly=True)

        try:
            ib_contracts = []
            for c in contracts:
                exp_fmt = c["expiration"].replace("-", "")
                opt = IBOption(
                    c["ticker"], exp_fmt, c["strike"],
                    c["opt_type"][0].upper(), "SMART", multiplier="100"
                )
                ib_contracts.append((c, opt))

            valid_opts = [o for _, o in ib_contracts]
            ib.qualifyContracts(*valid_opts)

            ticker_map = []
            for (c, opt) in ib_contracts:
                t = ib.reqMktData(opt, "106", False, False)
                ticker_map.append((c, opt, t))

            ib.sleep(3)

            def vld(v):
                return v is not None and not math.isnan(float(v)) and float(v) not in (-1.0, 0.0)

            for (c, opt, t) in ticker_map:
                key = (c["expiration"], c["strike"], c["opt_type"])
                greeks = {}
                if vld(t.bid):  greeks["bid"] = round(float(t.bid), 2)
                if vld(t.ask):  greeks["ask"] = round(float(t.ask), 2)
                if greeks.get("bid") and greeks.get("ask"):
                    greeks["mid"] = round((greeks["bid"] + greeks["ask"]) / 2, 2)
                if t.modelGreeks:
                    g = t.modelGreeks
                    if g.delta is not None:      greeks["delta"] = round(abs(float(g.delta)), 3)
                    if g.theta is not None:      greeks["theta"] = round(float(g.theta), 4)
                    if g.gamma is not None:      greeks["gamma"] = round(float(g.gamma), 4)
                    if g.vega is not None:       greeks["vega"]  = round(float(g.vega), 4)
                    if g.impliedVol is not None: greeks["iv"]    = round(float(g.impliedVol) * 100, 1)
                if greeks:
                    results[key] = greeks
        finally:
            ib.disconnect()
    except Exception:
        pass
    return results


def score_contract(row: dict, direction: str, delta_sweet: tuple) -> float:
    score = 0.0
    delta   = abs(row.get("delta", 0) or 0)
    theta   = abs(row.get("theta", 0) or 0)
    premium = row.get("mid", 0) or 0
    oi      = row.get("oi", 0) or 0
    spread_pct = row.get("spread_pct") or None
    is_long = direction == "LONG"

    # Delta sweet spot
    d_lo, d_hi = delta_sweet
    if d_lo <= delta <= d_hi:
        score += 30
    elif (d_lo - 0.10) <= delta < d_lo or d_hi < delta <= (d_hi + 0.10):
        score += 15

    # OI liquidity
    if oi >= 5000:   score += 25
    elif oi >= 1000: score += 18
    elif oi >= 500:  score += 10
    elif oi >= 100:  score += 5

    # Spread tightness (as % of mid)
    if spread_pct is not None:
        if spread_pct <= 5:    score += 20
        elif spread_pct <= 10: score += 14
        elif spread_pct <= 15: score += 8
        elif spread_pct <= 20: score += 3
        # > 20%: no points, but not penalized here (flagged in display)

    # Theta (for short positions, higher theta = better)
    if not is_long and theta > 0:
        if theta >= 0.05:   score += 15
        elif theta >= 0.02: score += 8
        elif theta >= 0.01: score += 3

    # Premium sanity (not too cheap, not too wide)
    if premium >= 0.50: score += 5

    return round(score, 1)


def spread_flag(spread_pct: Optional[float]) -> str:
    if spread_pct is None:
        return "[dim]--[/dim]"
    if spread_pct <= 10:
        return f"[green]{spread_pct:.1f}%[/green]"
    elif spread_pct <= 15:
        return f"[yellow]{spread_pct:.1f}%[/yellow]"
    else:
        return f"[red]{spread_pct:.1f}%[/red]"


def delta_flag(delta: Optional[float], delta_min: float, delta_max: float,
               delta_sweet: tuple) -> str:
    if delta is None:
        return "[dim]--[/dim]"
    d_lo, d_hi = delta_sweet
    if d_lo <= delta <= d_hi:
        return f"[green]{delta:.2f}[/green]"
    elif delta_min <= delta <= delta_max:
        return f"[yellow]{delta:.2f}[/yellow]"
    else:
        return f"[red]{delta:.2f}[/red]"


# ── Position sizing ───────────────────────────────────────────────────────────

def suggest_contracts(strategy: str, strike: float, mid: float,
                      account_id: str, ticker: str = "") -> int:
    """
    Suggest number of contracts based on risk_pct_per_trade and buying power.
    """
    try:
        conn = get_conn()
        settings = conn.execute(
            "SELECT * FROM strategy_settings WHERE account_id = ? AND strategy = ?",
            (account_id, strategy)
        ).fetchone()
        account = conn.execute(
            "SELECT * FROM accounts WHERE id = ?", (account_id,)
        ).fetchone()
        conn.close()

        if not settings or not account:
            return 1

        risk_pct = settings["risk_pct_per_trade"] or 0.05
        portfolio_value = account["portfolio_value"] or account["buying_power"] or 0

        if portfolio_value <= 0:
            return 1

        # Covered call: cap contracts at shares owned / 100
        if strategy == "COVERED_CALL" and ticker:
            sp = conn.execute(
                "SELECT shares FROM stock_positions WHERE ticker=? AND account_id=?",
                (ticker.upper(), account_id)
            ).fetchone()
            if sp:
                return max(1, sp["shares"] // 100)
            else:
                return 1  # no stock position found

        # Long options: fixed dollar target (~$5,000)
        # This will be user-configurable in setup in a future version
        LONG_OPTION_TARGET = 5000.0

        if strategy in ("LONG_CALL", "LONG_PUT"):
            max_contracts = int(LONG_OPTION_TARGET / (mid * 100)) if mid > 0 else 1
        elif strategy in ("CSP", "IRON_CONDOR"):
            # CSP: max collateral = strike * 100 * contracts
            max_risk = portfolio_value * risk_pct
            max_contracts = int(max_risk / (strike * 100))
        else:
            # Defined risk: use risk_pct of portfolio
            max_risk = portfolio_value * risk_pct
            max_contracts = int(max_risk / (strike * 100))

        return max(1, min(max_contracts, 20))  # cap at 20 for sanity
    except Exception:
        return 1


# ── Main fetch and evaluation ─────────────────────────────────────────────────


def fetch_chain_from_ibkr(ticker, opt_type, target_exps, spot, atr,
                           delta_min, delta_max, delta_sweet,
                           spread_threshold=0.25):
    # Fetch live/frozen options chain from IBKR.
    # Live when market open; frozen (2) when closed (type 1 returns -1 quotes outside RTH).
    # Returns list of contract dicts. Empty list = fallback to yfinance.
    import math
    from ib_insync import IB, Option as IBOption

    results = []
    ib = IB()
    try:
        ib.connect("127.0.0.1", 4002, clientId=15, readonly=True, timeout=15)
        from helm.cli.check_cmd import is_market_open
        ib.reqMarketDataType(1 if is_market_open() else 2)
        import atexit; atexit.register(lambda: ib.disconnect() if ib.isConnected() else None)

        for exp, days in target_exps:
            exp_fmt = exp.replace("-", "")

            # Qualify stock to get conId (required for reqSecDefOptParams)
            from ib_insync import Stock as IBStock
            stk = IBStock(ticker, "SMART", "USD")
            try:
                ib.qualifyContracts(stk)
            except Exception:
                pass
            con_id = getattr(stk, "conId", 0) or 0

            # Discover strikes via reqSecDefOptParams
            params = ib.reqSecDefOptParams(ticker, "", "STK", con_id)
            ib.sleep(1)

            all_strikes = set()
            for p in params:
                if exp_fmt in p.expirations:
                    all_strikes.update(p.strikes)
            if not all_strikes:
                continue

            # Filter to spot +/- 3xATR
            atr_buf = atr * 3
            filtered = sorted(s for s in all_strikes
                               if (spot - atr_buf) <= s <= (spot + atr_buf))
            if not filtered:
                filtered = sorted(all_strikes, key=lambda s: abs(s - spot))[:20]

            opt_right = "P" if opt_type == "PUT" else "C"
            raw_opts = [(s, IBOption(ticker, exp_fmt, s, opt_right, "SMART", "", "USD"))
                        for s in filtered]

            try:
                ib.qualifyContracts(*[o for _, o in raw_opts])
            except Exception:
                pass

            tmap = []
            for (strike, opt) in raw_opts:
                if not getattr(opt, "conId", 0):
                    continue
                t = ib.reqMktData(opt, "106,101", False, False)
                tmap.append((strike, opt, t))

            ib.sleep(3)

            def vld(v):
                try:
                    f = float(v)
                    return not math.isnan(f) and f not in (-1.0, 0.0)
                except Exception:
                    return False

            for (strike, opt, t) in tmap:
                bid = float(t.bid) if vld(t.bid) else None
                ask = float(t.ask) if vld(t.ask) else None
                if not bid or not ask or bid <= 0 or ask <= 0:
                    continue
                mid = round((bid + ask) / 2, 2)
                spread_pct = round((ask - bid) / mid * 100, 1) if mid > 0 else 99
                if spread_pct > spread_threshold * 100:
                    continue

                delta = theta = iv = None
                for gattr in ("modelGreeks", "lastGreeks"):
                    g = getattr(t, gattr, None)
                    if g:
                        if vld(getattr(g, "delta", None)):
                            delta = abs(float(g.delta))
                        if vld(getattr(g, "theta", None)):
                            theta = abs(float(g.theta))
                        if vld(getattr(g, "impliedVol", None)):
                            iv = round(float(g.impliedVol) * 100, 1)
                        break

                if delta is None and iv is not None:
                    try:
                        from scipy.stats import norm as _n
                        iv_d = iv / 100.0
                        T = days / 365.0
                        d1 = (math.log(spot / strike) + (0.045 + 0.5 * iv_d**2) * T) / (iv_d * math.sqrt(T))
                        delta = abs(_n.cdf(d1) - 1) if opt_type == "PUT" else _n.cdf(d1)
                    except Exception:
                        pass

                if delta is not None and not (delta_min <= delta <= delta_max):
                    continue

                oi = 0
                try:
                    v = getattr(t, "openInterest", None)
                    if vld(v):
                        oi = int(float(v))
                except Exception:
                    pass

                results.append({
                    "ticker": ticker, "expiration": exp, "dte": days,
                    "strike": strike, "opt_type": opt_type, "direction": "SHORT",
                    "bid": round(bid, 2), "ask": round(ask, 2), "mid": mid,
                    "spread": round(ask - bid, 2), "spread_pct": spread_pct,
                    "delta": round(delta, 3) if delta else None,
                    "theta": round(theta, 3) if theta else None,
                    "iv": iv, "oi": oi, "volume": 0,
                    "source": "ibkr",
                })

    except Exception:
        pass
    finally:
        try: ib.disconnect()
        except Exception: pass

    return results


def evaluate_contracts(ticker: str, strategy: str, config: dict,
                       dte_target: Optional[int] = None,
                       top_n: int = 8) -> list:
    """
    Fetch options chain and score contracts for the given strategy.
    Returns list of scored contract dicts, sorted by score desc.
    """
    import yfinance as yf
    import numpy as np

    opt_type  = config["option_type"]
    direction = config["direction"]
    delta_min = config["delta_min"]
    delta_max = config["delta_max"]
    delta_sweet = config["delta_sweet"]
    dte_min   = config["dte_min"]
    dte_max   = config["dte_max"]

    if dte_target:
        dte_min = max(7, dte_target - 7)
        dte_max = dte_target + 7

    tk = yf.Ticker(ticker)
    info = tk.fast_info
    spot = getattr(info, "last_price", None)
    if not spot:
        hist = tk.history(period="5d")
        spot = float(hist["Close"].iloc[-1]) if not hist.empty else None
    if not spot:
        raise ValueError(f"Cannot fetch price for {ticker}")

    # Quick ATR(14) for strike range filtering
    atr = spot * 0.05  # default: 5% of spot
    try:
        _hist = tk.history(period="30d", interval="1d")
        if not _hist.empty and len(_hist) >= 14:
            _tr = (_hist["High"] - _hist["Low"]).rolling(14).mean()
            _atr = _tr.iloc[-1]
            if _atr and not (_atr != _atr):  # not NaN
                atr = round(float(_atr), 2)
    except Exception:
        pass

    today = date.today()
    expirations = tk.options
    target_exps = []
    for exp in expirations:
        d = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
        if dte_min <= d <= dte_max:
            target_exps.append((exp, d))

    if not target_exps:
        raise ValueError(f"No expiries found in {dte_min}-{dte_max} DTE range")

    # ── Try IBKR primary chain ────────────────────────────────────────────────
    contracts = fetch_chain_from_ibkr(
        ticker, opt_type, target_exps, spot, atr or (spot * 0.05),
        delta_min, delta_max, delta_sweet,
        spread_threshold=config.get("spread_threshold", 0.25),
    )
    _ibkr_source = len(contracts) > 0
    if not _ibkr_source:
        # ── yfinance fallback ─────────────────────────────────────────────────
        console.print("  [yellow]⚠  IBKR chain unavailable — using yfinance data (may be stale)[/yellow]")
        console.print()
    if _ibkr_source:
        # Enrich OI from yfinance (IBKR OI unreliable in snapshot)
        try:
            _oi_map = {}
            for exp, _ in target_exps:
                try:
                    _chain = tk.option_chain(exp)
                    _df = _chain.puts if opt_type == "PUT" else _chain.calls
                    for _, _row in _df.iterrows():
                        _k = (exp, float(_row["strike"]))
                        _oi_map[_k] = int(_row.get("openInterest", 0) or 0)
                except Exception:
                    pass
            for c in contracts:
                _k = (c["expiration"], c["strike"])
                if _k in _oi_map and _oi_map[_k] > 0:
                    c["oi"] = _oi_map[_k]
        except Exception:
            pass

        # Score IBKR contracts
        for c in contracts:
            c["score"] = score_contract(c, direction, delta_sweet)
        contracts.sort(key=lambda x: -x["score"])
        return contracts[:top_n]

    # yfinance fallback path
    contracts = []
    for exp, days in target_exps:
        try:
            chain = tk.option_chain(exp)
            df = chain.puts if opt_type == "PUT" else chain.calls

            for _, row in df.iterrows():
                strike = float(row["strike"])
                bid = row.get("bid", None)
                ask = row.get("ask", None)
                oi = int(row.get("openInterest", 0) or 0)
                vol = int(row.get("volume", 0) or 0)
                iv = row.get("impliedVolatility", None)

                if bid is None or ask is None or bid <= 0 or ask <= 0:
                    continue
                if oi < 500:
                    continue

                mid = (float(bid) + float(ask)) / 2
                spread = float(ask) - float(bid)
                spread_pct = (spread / mid * 100) if mid > 0 else None

                # Estimate delta using Black-Scholes if no Greeks available
                delta = row.get("delta", None)
                theta = row.get("theta", None)

                if delta is None and iv is not None and float(iv) > 0:
                    try:
                        iv_val = float(iv)
                        T = days / 365.0
                        S, K, r = spot, strike, 0.045
                        d1 = (math.log(S/K) + (r + 0.5*iv_val**2)*T) / (iv_val*math.sqrt(T))
                        from scipy.stats import norm
                        if opt_type == "PUT":
                            delta = norm.cdf(d1) - 1
                        else:
                            delta = norm.cdf(d1)
                    except Exception:
                        pass

                if delta is not None:
                    delta = abs(float(delta))

                # Filter by delta range
                if delta is not None and not (delta_min <= delta <= delta_max):
                    continue

                contract = {
                    "ticker": ticker,
                    "expiration": exp,
                    "dte": days,
                    "strike": strike,
                    "opt_type": opt_type,
                    "direction": direction,
                    "bid": round(float(bid), 2),
                    "ask": round(float(ask), 2),
                    "mid": round(mid, 2),
                    "spread": round(spread, 2),
                    "spread_pct": round(spread_pct, 1) if spread_pct else None,
                    "oi": oi,
                    "volume": vol,
                    "delta": round(delta, 3) if delta else None,
                    "theta": round(float(theta), 3) if theta else None,
                    "iv": round(float(iv) * 100, 1) if iv else None,
                    "premium_total": round(mid * 100, 2),
                }

                contract["score"] = score_contract(contract, direction, delta_sweet)
                contracts.append(contract)

        except Exception:
            continue

    # Enrich top contracts with live IBKR Greeks
    contracts.sort(key=lambda c: -c["score"])
    top_contracts = contracts[:top_n]
    
    ibkr_data = fetch_ibkr_greeks(top_contracts)
    for c in top_contracts:
        key = (c["expiration"], c["strike"], c["opt_type"])
        if key in ibkr_data:
            g = ibkr_data[key]
            # Update with live IBKR data (more accurate than yfinance)
            if "bid" in g:    c["bid"]   = g["bid"]
            if "ask" in g:    c["ask"]   = g["ask"]
            if "mid" in g:    c["mid"]   = g["mid"]
            if "delta" in g:  c["delta"] = g["delta"]
            if "theta" in g:  c["theta"] = g["theta"]
            if "gamma" in g:  c["gamma"] = g["gamma"]
            if "iv" in g:     c["iv"]    = g["iv"]
            # Recalculate spread with live bid/ask
            if "bid" in g and "ask" in g and g["mid"] > 0:
                c["spread"] = round(g["ask"] - g["bid"], 2)
                c["spread_pct"] = round((c["spread"] / g["mid"]) * 100, 1)
            # Recalculate premium total with live mid
            if "mid" in g:
                c["premium_total"] = round(g["mid"] * 100, 2)
            # Rescore with live data
            c["score"] = score_contract(c, c["direction"], 
                                         STRATEGY_CONFIG[top_contracts[0].get("strategy", "CSP")]["delta_sweet"]
                                         if top_contracts else (0.25, 0.35))
            c["source"] = "ibkr-live"
        else:
            c["source"] = "yfinance"
    
    # Re-sort after IBKR enrichment
    top_contracts.sort(key=lambda c: -c["score"])
    return top_contracts


# ── Command ───────────────────────────────────────────────────────────────────


def confirm_and_log(ticker: str, strategy: str, contracts: list, config: dict,
                    spot: Optional[float], scan_data: Optional[dict] = None):
    """
    Interactive confirm flow — user selects a contract and confirms fill price.
    Creates position + leg + entry snapshot in the database.
    """
    from rich.prompt import Prompt, Confirm
    from helm.cli.entry_snapshot import open_position_with_snapshot

    console.print()
    console.print("[bold]Open a position?[/bold]")
    console.print("[dim]Enter rank number to select a contract, or 'n' to exit.[/dim]")
    console.print()

    while True:
        choice = Prompt.ask(
            f"Select contract",
            default="1",
            choices=[str(i+1) for i in range(len(contracts))] + ["n"],
            show_choices=False,
        )
        if choice.lower() == "n":
            console.print("[dim]No position opened.[/dim]")
            console.print()
            return

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(contracts):
                selected = contracts[idx]
                break
        except ValueError:
            pass
        console.print("[yellow]Invalid choice. Enter a rank number or 'n'.[/yellow]")

    # Show selected contract summary
    console.print()
    console.print(Panel.fit(
        f"[bold]Selected:[/bold] {ticker} {selected['opt_type']} "
        f"${selected['strike']:.1f} {selected['expiration']} ({selected['dte']}d)\n"
        f"  Bid: ${selected['bid']:.2f}  Ask: ${selected['ask']:.2f}  "
        f"Mid: ${selected['mid']:.2f}  Delta: {selected.get('delta', '--')}  "
        f"Theta: {selected.get('theta', '--')}",
        border_style="cyan",
        title="Contract Selected"
    ))
    console.print()

    # Get actual fill price
    default_price = str(selected['mid'])
    fill_str = Prompt.ask(
        f"  Actual fill price",
        default=f"{selected['mid']:.2f}"
    )
    try:
        fill_price = float(fill_str.replace("$", "").strip())
    except ValueError:
        console.print("[red]Invalid price. Aborting.[/red]")
        return

    # Get number of contracts
    suggested = suggest_contracts(strategy, selected["strike"], fill_price,
                                  get_active_account())
    contracts_str = Prompt.ask(
        f"  Number of contracts",
        default=str(suggested)
    )
    try:
        num_contracts = int(contracts_str)
    except ValueError:
        num_contracts = suggested

    # Final confirmation
    total_premium = round(fill_price * 100 * num_contracts, 2)
    direction = config["direction"]
    premium_label = f"collect ${total_premium:.0f}" if direction == "SHORT" else f"pay ${total_premium:.0f}"

    console.print()
    if not Confirm.ask(
        f"  Open [bold]{num_contracts}x {ticker} {selected['opt_type']} "
        f"${selected['strike']:.1f} {selected['expiration']}[/bold] "
        f"@ ${fill_price:.2f} ({premium_label})?",
        default=True
    ):
        console.print("[dim]Cancelled.[/dim]")
        console.print()
        return

    # Add spot to contract for snapshot
    selected["spot"] = spot
    # HELM-017: stamp config-authoritative direction onto the selected contract so single-leg longs are not persisted with the fetch_chain_from_ibkr SHORT placeholder (entry_snapshot reads contract['direction'])
    selected["direction"] = config["direction"]

    # Open position with full entry snapshot
    console.print()
    console.print("[dim]Recording position...[/dim]")
    try:
        pos_id, leg_id, snap_id = open_position_with_snapshot(
            ticker=ticker,
            strategy=strategy,
            contract=selected,
            fill_price=fill_price,
            contracts=num_contracts,
            scan_data=scan_data,
        )

        net_premium = fill_price * 100 * num_contracts
        if direction == "LONG":
            net_premium = -net_premium

        console.print()
        console.print(Panel(
            f"[bold green]Position Opened[/bold green]\n\n"
            f"  Ticker:     [bold cyan]{ticker}[/bold cyan]  {strategy}\n"
            f"  Contract:   {selected['opt_type']} ${selected['strike']:.1f} "
            f"{selected['expiration']} ({selected['dte']}d)\n"
            f"  Contracts:  {num_contracts}\n"
            f"  Fill price: ${fill_price:.2f}\n"
            f"  Premium:    [green]${abs(net_premium):.0f} {'collected' if direction == 'SHORT' else 'paid'}[/green]\n\n"
            f"  Position ID: [dim]{pos_id}[/dim]\n"
            f"  Snapshot:    [dim]{snap_id}[/dim]\n\n"
            f"[dim]Entry context captured. Run [bold]helm check {ticker}[/bold] to monitor.[/dim]",
            title="✓ Trade Logged",
            border_style="green"
        ))
        console.print()

    except Exception as e:
        import traceback
        console.print(f"[red]Error opening position:[/red] {e}")
        traceback.print_exc()





def confirm_spread(ticker: str, strategy: str, spreads: list, config: dict,
                   spot: float, args: list):
    """Interactive confirm flow for spread positions."""
    from rich.prompt import Prompt, Confirm
    # For now, spreads log as a single position with notes about both legs
    # Full multi-leg logging will be built in a future session
    console.print()
    console.print("[yellow]Note:[/yellow] Spread --confirm logging is coming soon.")
    console.print("[dim]For now, log via helm activity after executing in Fidelity.[/dim]")
    console.print()


def display_spreads(ticker: str, strategy: str, config: dict, spreads: list,
                    spot: float, atr: float, account_id: str, args: list):
    """Display two-leg spread evaluation results."""
    label = config["label"]
    opt_type = config["option_type"]
    is_bull = strategy == "BULL_PUT_SPREAD"

    console.print()
    if spot:
        atr_str = f"  ATR(14): ${atr:.2f}  →  1-ATR: ${spot-atr:.2f}  2-ATR: ${spot-2*atr:.2f}" if atr else ""
        console.print(f"  Spot: [bold]${spot:.2f}[/bold]{atr_str}")
        console.print()

    t = Table(box=box.SIMPLE_HEAD, show_header=True, padding=(0,1), width=170)
    t.add_column("Rank",    width=5, no_wrap=True)
    t.add_column("Exp",     width=6, no_wrap=True)
    t.add_column("DTE",     justify="right", width=5, no_wrap=True)
    t.add_column("Short",   justify="right", width=7, no_wrap=True)
    t.add_column("Long",    justify="right", width=7, no_wrap=True)
    t.add_column("Width",   justify="right", width=6, no_wrap=True)
    t.add_column("Credit",  justify="right", width=8, no_wrap=True)
    t.add_column("MaxLoss", justify="right", width=8, no_wrap=True)
    t.add_column("MaxGain", justify="right", width=8, no_wrap=True)
    t.add_column("C/W%",    justify="right", width=6, no_wrap=True)
    t.add_column("R/R",     justify="right", width=5, no_wrap=True)
    t.add_column("Delta",   justify="right", width=7, no_wrap=True)
    t.add_column("IV%",     justify="right", width=5, no_wrap=True)
    t.add_column("OI",      justify="right", width=7, no_wrap=True)
    t.add_column("Score",   justify="right", width=6, no_wrap=True)
    t.add_column("Contracts", justify="right", width=9, no_wrap=True)

    for rank, s in enumerate(spreads, 1):
        rank_str = "[green]#1[/green]" if rank==1 else "[cyan]#2[/cyan]" if rank==2 else f"#{rank}"
        cw_color = "green" if s["credit_to_width_pct"] >= 25 else "yellow" if s["credit_to_width_pct"] >= 15 else "red"
        rr_color = "green" if s["rr_ratio"] >= 0.40 else "yellow" if s["rr_ratio"] >= 0.25 else "red"

        # Sizing: max risk = max_loss * 100 * contracts
        suggested = 1
        try:
            from helm.db import get_conn as _gc
            _c = _gc()
            settings = _c.execute("SELECT risk_pct_per_trade FROM strategy_settings WHERE account_id=? AND strategy=?",
                                  (account_id, strategy)).fetchone()
            acct = _c.execute("SELECT portfolio_value FROM accounts WHERE id=?", (account_id,)).fetchone()
            _c.close()
            if settings and acct:
                risk_pct = settings[0] or 0.05
                max_risk = (acct[0] or 0) * risk_pct
                suggested = max(1, min(20, int(max_risk / (s["max_loss"] * 100))))
        except Exception:
            pass

        t.add_row(
            rank_str,
            s["expiration"][5:],
            str(s["dte"]),
            f"${s['short_strike']:.0f}",
            f"${s['long_strike']:.0f}",
            f"${s['width']:.0f}",
            f"${s['net_credit']:.2f}",
            f"[red]${s['max_loss']:.2f}[/red]",
            f"[green]${s['max_gain']:.2f}[/green]",
            f"[{cw_color}]{s['credit_to_width_pct']:.0f}%[/{cw_color}]",
            f"[{rr_color}]{s['rr_ratio']:.2f}[/{rr_color}]",
            f"{s['delta']:.3f}" if s.get("delta") else "--",
            f"{s['iv']:.0f}%" if s.get("iv") else "--",
            f"{s['oi']:,}",
            f"{s['score']:.0f}",
            f"[bold]{suggested}[/bold]",
        )

    console.print(f"[bold]Top {len(spreads)} spreads — {ticker} {label}[/bold]")
    console.print()
    console.print(t)
    console.print()

    # Best spread summary
    best = spreads[0]
    suggested_best = 1
    try:
        from helm.db import get_conn as _gc2
        _c2 = _gc2()
        settings2 = _c2.execute("SELECT risk_pct_per_trade FROM strategy_settings WHERE account_id=? AND strategy=?",
                                (account_id, strategy)).fetchone()
        acct2 = _c2.execute("SELECT portfolio_value FROM accounts WHERE id=?", (account_id,)).fetchone()
        _c2.close()
        if settings2 and acct2:
            suggested_best = max(1, min(20, int((acct2[0]*settings2[0]) / (best["max_loss"]*100))))
    except Exception:
        pass

    total_credit = round(best["net_credit"] * 100 * suggested_best, 0)
    total_risk = round(best["max_loss"] * 100 * suggested_best, 0)

    console.print(Panel(
        f"[bold green]Top pick:[/bold green] {ticker} {opt_type} "
        f"${best['short_strike']:.0f}/{best['long_strike']:.0f} spread "
        f"{best['expiration']} ({best['dte']}d)\n"
        f"  Sell ${best['short_strike']:.0f} {opt_type} @ ${best['short_mid']:.2f}  |  "
        f"Buy ${best['long_strike']:.0f} {opt_type} @ ${best['long_mid']:.2f}\n"
        f"  Net credit: [green]${best['net_credit']:.2f}/contract[/green]  |  "
        f"Max loss: [red]${best['max_loss']:.2f}/contract[/red]  |  "
        f"Width: ${best['width']:.0f}\n"
        f"  Credit/width: {best['credit_to_width_pct']:.0f}%  |  "
        f"R/R: {best['rr_ratio']:.2f}  |  Delta: {best.get('delta', '--')}\n\n"
        f"  Suggested: [bold]{suggested_best} spread(s)[/bold]  |  "
        f"Collect: [green]${total_credit:.0f}[/green]  |  "
        f"Max risk: [red]${total_risk:.0f}[/red]\n\n"
        f"[dim]To open: [bold]helm open {ticker} {strategy} --confirm[/bold][/dim]",
        title="Recommendation",
        border_style="green"
    ))
    console.print()

    # --confirm flow for spreads
    if "--confirm" in args:
        confirm_spread(ticker, strategy, spreads, config, spot, args)




def evaluate_condors(ticker: str, strategy: str, config: dict,
                     dte_target: int = None, top_n: int = 6) -> list:
    """
    Evaluate iron condor contracts.
    Combines a bull put spread (below) + bear call spread (above).
    Reuses evaluate_spreads logic for each wing.
    """
    import yfinance as yf
    import math

    delta_min   = config["delta_min"]
    delta_max   = config["delta_max"]
    delta_sweet = config["delta_sweet"]
    dte_min     = config["dte_min"]
    dte_max     = config["dte_max"]
    widths      = config.get("spread_widths", [5, 10, 15, 20])

    if dte_target:
        dte_min = max(7, dte_target - 7)
        dte_max = dte_target + 7

    tk = yf.Ticker(ticker)
    info = tk.fast_info
    spot = getattr(info, "last_price", None)
    if not spot:
        hist = tk.history(period="5d")
        spot = float(hist["Close"].iloc[-1]) if not hist.empty else None
    if not spot:
        raise ValueError(f"Cannot fetch price for {ticker}")

    today = __import__("datetime").date.today()
    expirations = tk.options
    target_exps = []
    for exp in expirations:
        d = (__import__("datetime").datetime.strptime(exp, "%Y-%m-%d").date() - today).days
        if dte_min <= d <= dte_max:
            target_exps.append((exp, d))

    if not target_exps:
        raise ValueError(f"No expiries in {dte_min}-{dte_max} DTE range")

    condors = []

    for exp, days in target_exps:
        try:
            chain = tk.option_chain(exp)
            puts  = chain.puts
            calls = chain.calls

            def build_strike_data(df):
                data = {}
                for _, row in df.iterrows():
                    s = float(row["strike"])
                    bid = row.get("bid", 0) or 0
                    ask = row.get("ask", 0) or 0
                    if float(bid) > 0 and float(ask) > 0:
                        data[s] = {
                            "bid": round(float(bid), 2),
                            "ask": round(float(ask), 2),
                            "mid": round((float(bid)+float(ask))/2, 2),
                            "iv":  round(float(row.get("impliedVolatility",0) or 0)*100, 1),
                            "oi":  int(row.get("openInterest", 0) or 0),
                        }
                return data

            put_data  = build_strike_data(puts)
            call_data = build_strike_data(calls)

            def compute_delta(strike, opt_type, iv_pct):
                try:
                    iv = iv_pct / 100
                    T = days / 365.0
                    S, K, r = spot, strike, 0.045
                    d1 = (math.log(S/K) + (r + 0.5*iv**2)*T) / (iv*math.sqrt(T))
                    from scipy.stats import norm
                    return abs(norm.cdf(d1) - 1) if opt_type == "PUT" else norm.cdf(d1)
                except Exception:
                    return None

            # Find short put candidates (OTM puts below spot)
            put_shorts = []
            for strike, data in sorted(put_data.items()):
                if strike >= spot: continue
                if data["oi"] < 100: continue
                delta = compute_delta(strike, "PUT", data["iv"])
                if delta and delta_min <= delta <= delta_max:
                    put_shorts.append({"strike": strike, "delta": delta, **data})

            # Find short call candidates (OTM calls above spot)
            call_shorts = []
            for strike, data in sorted(call_data.items()):
                if strike <= spot: continue
                if data["oi"] < 100: continue
                delta = compute_delta(strike, "CALL", data["iv"])
                if delta and delta_min <= delta <= delta_max:
                    call_shorts.append({"strike": strike, "delta": delta, **data})

            if not put_shorts or not call_shorts:
                continue

            # Sort by delta proximity to sweet spot
            d_mid = sum(delta_sweet) / 2
            put_shorts.sort(key=lambda x: abs(x["delta"] - d_mid))
            call_shorts.sort(key=lambda x: abs(x["delta"] - d_mid))

            # Pair top 2 puts x top 2 calls x each width
            for ps in put_shorts[:2]:
                for cs in call_shorts[:2]:
                    for width in widths:
                        # Put spread: short put at ps["strike"], long put at ps["strike"] - width
                        long_put_strike = round(ps["strike"] - width, 0)
                        if long_put_strike not in put_data:
                            available = [s for s in put_data if s < ps["strike"]]
                            if not available: continue
                            long_put_strike = min(available, key=lambda s: abs(s-(ps["strike"]-width)))

                        # Call spread: short call at cs["strike"], long call at cs["strike"] + width
                        long_call_strike = round(cs["strike"] + width, 0)
                        if long_call_strike not in call_data:
                            available = [s for s in call_data if s > cs["strike"]]
                            if not available: continue
                            long_call_strike = min(available, key=lambda s: abs(s-(cs["strike"]+width)))

                        if long_put_strike not in put_data or long_call_strike not in call_data:
                            continue

                        lp = put_data[long_put_strike]
                        lc = call_data[long_call_strike]

                        put_credit  = round(ps["mid"] - lp["mid"], 2)
                        call_credit = round(cs["mid"] - lc["mid"], 2)
                        if put_credit <= 0 or call_credit <= 0:
                            continue

                        total_credit = round(put_credit + call_credit, 2)
                        put_width    = round(ps["strike"] - long_put_strike, 2)
                        call_width   = round(long_call_strike - cs["strike"], 2)
                        max_loss     = round(max(put_width, call_width) - total_credit, 2)
                        if max_loss <= 0: continue

                        rr_ratio = round(total_credit / max_loss, 2)
                        cw_pct   = round(total_credit / max(put_width, call_width) * 100, 1)

                        # Score
                        score = 0.0
                        for leg_delta in [ps["delta"], cs["delta"]]:
                            if delta_sweet[0] <= leg_delta <= delta_sweet[1]: score += 20
                            elif (delta_sweet[0]-0.05) <= leg_delta <= (delta_sweet[1]+0.05): score += 10
                        if cw_pct >= 25: score += 20
                        elif cw_pct >= 15: score += 10
                        if rr_ratio >= 0.40: score += 15
                        elif rr_ratio >= 0.25: score += 8
                        for oi in [ps["oi"], cs["oi"]]:
                            if oi >= 1000: score += 8
                            elif oi >= 500: score += 4

                        condors.append({
                            "ticker": ticker,
                            "strategy": strategy,
                            "expiration": exp,
                            "dte": days,
                            # Put spread
                            "short_put": ps["strike"],
                            "long_put": long_put_strike,
                            "put_width": put_width,
                            "put_credit": put_credit,
                            "put_delta": ps["delta"],
                            "put_iv": ps["iv"],
                            "put_oi": ps["oi"],
                            # Call spread
                            "short_call": cs["strike"],
                            "long_call": long_call_strike,
                            "call_width": call_width,
                            "call_credit": call_credit,
                            "call_delta": cs["delta"],
                            "call_iv": cs["iv"],
                            "call_oi": cs["oi"],
                            # Conservative per-leg fills (short -> bid, long -> ask)
                            "short_put_bid": ps["bid"],
                            "long_put_ask": lp["ask"],
                            "short_call_bid": cs["bid"],
                            "long_call_ask": lc["ask"],
                            # Combined
                            "total_credit": total_credit,
                            "max_loss": max_loss,
                            "rr_ratio": rr_ratio,
                            "cw_pct": cw_pct,
                            "score": round(score, 1),
                        })

        except Exception:
            continue

    # Deduplicate and sort
    seen = set()
    unique = []
    for c in sorted(condors, key=lambda x: -x["score"]):
        key = (c["expiration"], c["short_put"], c["short_call"])
        if key not in seen:
            seen.add(key)
            unique.append(c)

    return unique[:top_n]


def confirm_condor(ticker: str, strategy: str, condors: list, config: dict,
                   spot: float, args: list):
    """
    Interactive confirm flow for iron condors (HELM-013).

    Captures the actual NET credit, assembles the four legs, and writes one
    Position + 4 legs + one short-leg-anchored snapshot + OPENED event in a
    single transaction via open_multileg_with_snapshot (atomic -- a partial
    failure rolls the whole open back). Per-leg fills default to the conservative
    values modeled by evaluate_condors (short -> bid, long -> ask); on a net
    override the delta is absorbed into the two short legs so the derived net
    matches the actual fill while the long legs stay at ask.
    """
    from rich.prompt import Prompt, Confirm
    from helm.cli.entry_snapshot import open_multileg_with_snapshot

    if not condors:
        console.print("[yellow]No condors to open.[/yellow]")
        console.print()
        return

    console.print()
    console.print("[bold]Open an iron condor?[/bold]")
    console.print("[dim]Enter rank number to select, or 'n' to exit.[/dim]")
    console.print()

    while True:
        choice = Prompt.ask(
            "Select condor",
            default="1",
            choices=[str(i + 1) for i in range(len(condors))] + ["n"],
            show_choices=False,
        )
        if choice.lower() == "n":
            console.print("[dim]No position opened.[/dim]")
            console.print()
            return
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(condors):
                c = condors[idx]
                break
        except ValueError:
            pass
        console.print("[yellow]Invalid choice. Enter a rank number or 'n'.[/yellow]")

    width = max(c["put_width"], c["call_width"])

    console.print()
    console.print(Panel.fit(
        f"[bold]Selected:[/bold] {ticker} Iron Condor {c['expiration']} ({c['dte']}d)\n"
        f"  Put spread:  Long ${c['long_put']:.0f} / Short ${c['short_put']:.0f}  "
        f"(delta {c['put_delta']:.3f})\n"
        f"  Call spread: Short ${c['short_call']:.0f} / Long ${c['long_call']:.0f}  "
        f"(delta {c['call_delta']:.3f})\n"
        f"  Modeled net credit: ${c['total_credit']:.2f}/contract  |  "
        f"Max loss: ${c['max_loss']:.2f}/contract\n"
        f"  Width: ${width:.0f}  |  Credit/width: {c['cw_pct']:.0f}%  |  R/R: {c['rr_ratio']:.2f}",
        border_style="cyan", title="Confirm Iron Condor",
    ))
    console.print()

    # Number of contracts (portfolio-sized via max_loss, mirroring display_condors).
    suggested = 1
    try:
        from helm.db import get_conn as _gc
        _c = _gc()
        acct = _c.execute(
            "SELECT portfolio_value FROM accounts WHERE id = ?", (get_active_account(),)
        ).fetchone()
        _c.close()
        if acct and acct[0]:
            suggested = max(1, min(20, int((acct[0] * 0.05) / (c["max_loss"] * 100))))
    except Exception:
        pass
    contracts_str = Prompt.ask("  Number of contracts", default=str(suggested))
    try:
        num_contracts = int(contracts_str)
    except ValueError:
        num_contracts = suggested

    # Conservative per-leg fills (short -> bid, long -> ask); modeled net = signed sum.
    sp_bid = float(c["short_put_bid"]); lp_ask = float(c["long_put_ask"])
    sc_bid = float(c["short_call_bid"]); lc_ask = float(c["long_call_ask"])
    modeled_net = round((sp_bid + sc_bid) - (lp_ask + lc_ask), 2)

    fill_str = Prompt.ask("  Actual NET credit received", default=f"{modeled_net:.2f}")
    try:
        net_credit = float(fill_str.replace("$", "").strip())
    except ValueError:
        console.print("[red]Invalid net credit. Aborting.[/red]")
        console.print()
        return

    # Absorb any net override into the two short legs (longs stay at ask) so the
    # writer-derived net_premium equals the actual fill.
    base_short_sum = sp_bid + sc_bid
    target_short_sum = net_credit + lp_ask + lc_ask
    if base_short_sum > 0:
        scale = target_short_sum / base_short_sum
        sp_fill = max(0.0, round(sp_bid * scale, 2))
        sc_fill = max(0.0, round(sc_bid * scale, 2))
    else:
        sp_fill, sc_fill = sp_bid, sc_bid

    total_credit_amt = round(net_credit * 100 * num_contracts, 2)
    console.print()
    if not Confirm.ask(
        f"  Open [bold]{num_contracts}x {ticker} Iron Condor "
        f"{c['short_put']:.0f}/{c['long_put']:.0f}P {c['short_call']:.0f}/{c['long_call']:.0f}C "
        f"{c['expiration']}[/bold] @ net ${net_credit:.2f} (collect ${total_credit_amt:.0f})?",
        default=True,
    ):
        console.print("[dim]Cancelled.[/dim]")
        console.print()
        return

    legs = [
        {"direction": "SHORT", "opt_type": "PUT", "strike": c["short_put"],
         "expiration": c["expiration"], "fill_price": sp_fill, "delta": c.get("put_delta"),
         "iv": c.get("put_iv"), "dte": c["dte"], "spot": spot, "oi": c.get("put_oi")},
        {"direction": "LONG", "opt_type": "PUT", "strike": c["long_put"],
         "expiration": c["expiration"], "fill_price": lp_ask, "dte": c["dte"], "spot": spot},
        {"direction": "SHORT", "opt_type": "CALL", "strike": c["short_call"],
         "expiration": c["expiration"], "fill_price": sc_fill, "delta": c.get("call_delta"),
         "iv": c.get("call_iv"), "dte": c["dte"], "spot": spot, "oi": c.get("call_oi")},
        {"direction": "LONG", "opt_type": "CALL", "strike": c["long_call"],
         "expiration": c["expiration"], "fill_price": lc_ask, "dte": c["dte"], "spot": spot},
    ]

    position_fields = {
        "spread_width": width,
        "max_profit": round(net_credit * 100 * num_contracts, 2),
        "max_loss": round((width - net_credit) * 100 * num_contracts, 2),
        "credit_to_width_ratio": round(net_credit / width, 4) if width else None,
        "breakeven_low": round(c["short_put"] - net_credit, 2),
        "breakeven_high": round(c["short_call"] + net_credit, 2),
    }

    # Live `helm open` sources its chain from IBKR (see "Data: IBKR live" header).
    pricing_source = "ibkr"

    console.print()
    console.print("[dim]Recording position...[/dim]")
    try:
        pos_id, leg_ids, snap_ids = open_multileg_with_snapshot(
            ticker=ticker,
            strategy=strategy,
            legs=legs,
            contracts=num_contracts,
            spot=spot,
            scan_data=None,
            book="REAL",
            position_fields=position_fields,
            pricing_source=pricing_source,
        )
    except Exception as e:
        console.print(f"[red]Failed to record position: {e}[/red]")
        console.print()
        return

    console.print()
    console.print(Panel(
        f"[bold green]Position Opened[/bold green]\n\n"
        f"  Ticker:      [bold cyan]{ticker}[/bold cyan]  {strategy}\n"
        f"  Structure:   {c['short_put']:.0f}/{c['long_put']:.0f}P  "
        f"{c['short_call']:.0f}/{c['long_call']:.0f}C  {c['expiration']}\n"
        f"  Contracts:   {num_contracts}\n"
        f"  Net credit:  [green]${net_credit:.2f}/contract[/green]  (collected ${total_credit_amt:.0f})\n"
        f"  Max loss:    [red]${position_fields['max_loss']:.0f}[/red]\n"
        f"  Break-evens: ${position_fields['breakeven_low']:.2f} / ${position_fields['breakeven_high']:.2f}\n"
        f"  Position:    [dim]{pos_id}[/dim]",
        border_style="green", title="Opened",
    ))
    console.print()


def display_condors(ticker: str, strategy: str, config: dict, condors: list,
                    spot: float, atr: float, account_id: str, args: list):
    """Display iron condor evaluation results."""

    console.print()
    if spot:
        atr_str = f"  ATR(14): ${atr:.2f}  →  Put wing: ${spot-atr:.2f}  Call wing: ${spot+atr:.2f}" if atr else ""
        console.print(f"  Spot: [bold]${spot:.2f}[/bold]{atr_str}")
        console.print()

    t = Table(box=box.SIMPLE_HEAD, show_header=True, padding=(0,1), width=190)
    t.add_column("Rank",      width=5,  no_wrap=True)
    t.add_column("Exp",       width=6,  no_wrap=True)
    t.add_column("DTE",       justify="right", width=4)
    t.add_column("Long Put",  justify="right", width=9)
    t.add_column("Short Put", justify="right", width=10)
    t.add_column("Short Call",justify="right", width=11)
    t.add_column("Long Call", justify="right", width=10)
    t.add_column("Width",     justify="right", width=6)
    t.add_column("Credit",    justify="right", width=8)
    t.add_column("MaxLoss",   justify="right", width=8)
    t.add_column("C/W%",      justify="right", width=6)
    t.add_column("R/R",       justify="right", width=5)
    t.add_column("Put Δ",     justify="right", width=7)
    t.add_column("Call Δ",    justify="right", width=7)
    t.add_column("Score",     justify="right", width=6)
    t.add_column("Contracts", justify="right", width=10)

    for rank, c in enumerate(condors, 1):
        rank_str = "[green]#1[/green]" if rank==1 else "[cyan]#2[/cyan]" if rank==2 else f"#{rank}"
        cw_color = "green" if c["cw_pct"] >= 25 else "yellow" if c["cw_pct"] >= 15 else "red"
        rr_color = "green" if c["rr_ratio"] >= 0.40 else "yellow" if c["rr_ratio"] >= 0.25 else "red"

        suggested = 1
        try:
            from helm.db import get_conn as _gc
            _c = _gc()
            acct = _c.execute("SELECT portfolio_value FROM accounts WHERE id=?",
                              (account_id,)).fetchone()
            _c.close()
            if acct and acct[0]:
                max_risk = acct[0] * 0.05
                suggested = max(1, min(20, int(max_risk / (c["max_loss"] * 100))))
        except Exception:
            pass

        t.add_row(
            rank_str,
            c["expiration"][5:],
            str(c["dte"]),
            f"${c['long_put']:.0f}",
            f"${c['short_put']:.0f}",
            f"${c['short_call']:.0f}",
            f"${c['long_call']:.0f}",
            f"${max(c['put_width'],c['call_width']):.0f}",
            f"[green]${c['total_credit']:.2f}[/green]",
            f"[red]${c['max_loss']:.2f}[/red]",
            f"[{cw_color}]{c['cw_pct']:.0f}%[/{cw_color}]",
            f"[{rr_color}]{c['rr_ratio']:.2f}[/{rr_color}]",
            f"{c['put_delta']:.3f}",
            f"{c['call_delta']:.3f}",
            f"{c['score']:.0f}",
            f"[bold]{suggested}[/bold]",
        )

    console.print(f"[bold]Top {len(condors)} iron condors — {ticker}[/bold]")
    console.print()
    console.print(t)
    console.print()

    best = condors[0]
    suggested_best = 1
    try:
        from helm.db import get_conn as _gc2
        _c2 = _gc2()
        acct2 = _c2.execute("SELECT portfolio_value FROM accounts WHERE id=?",
                            (account_id,)).fetchone()
        _c2.close()
        if acct2 and acct2[0]:
            suggested_best = max(1, min(20, int((acct2[0]*0.05) / (best["max_loss"]*100))))
    except Exception:
        pass

    total_credit = round(best["total_credit"] * 100 * suggested_best, 0)
    total_risk   = round(best["max_loss"] * 100 * suggested_best, 0)
    put_be = round(best["short_put"] - best["total_credit"], 2)
    call_be = round(best["short_call"] + best["total_credit"], 2)

    console.print(Panel(
        f"[bold green]Top pick:[/bold green] {ticker} Iron Condor "
        f"{best['expiration']} ({best['dte']}d)\n\n"
        f"  [dim]─── Put Spread ───[/dim]\n"
        f"  Long  PUT  ${best['long_put']:.0f}  |  "
        f"Short PUT  ${best['short_put']:.0f}  →  Credit ${best['put_credit']:.2f}  (Δ {best['put_delta']:.3f})\n\n"
        f"  [dim]─── Call Spread ───[/dim]\n"
        f"  Short CALL ${best['short_call']:.0f}  |  "
        f"Long  CALL ${best['long_call']:.0f}  →  Credit ${best['call_credit']:.2f}  (Δ {best['call_delta']:.3f})\n\n"
        f"  Total credit: [green]${best['total_credit']:.2f}/contract[/green]  |  "
        f"Max loss: [red]${best['max_loss']:.2f}/contract[/red]\n"
        f"  Credit/width: {best['cw_pct']:.0f}%  |  R/R: {best['rr_ratio']:.2f}\n"
        f"  Break-evens: ${put_be:.2f} (put) / ${call_be:.2f} (call)\n\n"
        f"  Suggested: [bold]{suggested_best} contract(s)[/bold]  |  "
        f"Collect: [green]${total_credit:.0f}[/green]  |  "
        f"Max risk: [red]${total_risk:.0f}[/red]\n\n"
        f"[dim]To open: [bold]helm open {ticker} IRON_CONDOR --confirm[/bold][/dim]",
        title="Recommendation",
        border_style="green"
    ))
    console.print()

    # --confirm flow for iron condors (HELM-013)
    if "--confirm" in args:
        confirm_condor(ticker, strategy, condors, config, spot, args)


def evaluate_strangles(ticker: str, strategy: str, config: dict,
                       dte_target: int = None, top_n: int = 6) -> list:
    """
    Evaluate iron condor contracts.
    Finds best OTM put + OTM call pair for the same expiration.
    Returns list of strangle dicts sorted by score.
    """
    import yfinance as yf
    import math

    delta_min   = config["delta_min"]
    delta_max   = config["delta_max"]
    delta_sweet = config["delta_sweet"]
    dte_min     = config["dte_min"]
    dte_max     = config["dte_max"]

    if dte_target:
        dte_min = max(7, dte_target - 7)
        dte_max = dte_target + 7

    tk = yf.Ticker(ticker)
    info = tk.fast_info
    spot = getattr(info, "last_price", None)
    if not spot:
        hist = tk.history(period="5d")
        spot = float(hist["Close"].iloc[-1]) if not hist.empty else None
    if not spot:
        raise ValueError(f"Cannot fetch price for {ticker}")

    today = __import__("datetime").date.today()
    expirations = tk.options
    target_exps = []
    for exp in expirations:
        d = (__import__("datetime").datetime.strptime(exp, "%Y-%m-%d").date() - today).days
        if dte_min <= d <= dte_max:
            target_exps.append((exp, d))

    if not target_exps:
        raise ValueError(f"No expiries in {dte_min}-{dte_max} DTE range")

    strangles = []

    for exp, days in target_exps:
        try:
            chain = tk.option_chain(exp)
            puts  = chain.puts
            calls = chain.calls

            def get_candidates(df, opt_type):
                candidates = []
                for _, row in df.iterrows():
                    strike = float(row["strike"])
                    bid = row.get("bid", 0) or 0
                    ask = row.get("ask", 0) or 0
                    if float(bid) <= 0 or float(ask) <= 0:
                        continue
                    oi = int(row.get("openInterest", 0) or 0)
                    if oi < 500:
                        continue
                    mid = (float(bid) + float(ask)) / 2
                    iv  = row.get("impliedVolatility", None)

                    # Compute delta
                    delta = None
                    if iv and float(iv) > 0:
                        try:
                            iv_val = float(iv)
                            T = days / 365.0
                            S, K, r = spot, strike, 0.045
                            d1 = (math.log(S/K) + (r + 0.5*iv_val**2)*T) / (iv_val*math.sqrt(T))
                            from scipy.stats import norm
                            if opt_type == "PUT":
                                delta = abs(norm.cdf(d1) - 1)
                            else:
                                delta = norm.cdf(d1)
                        except Exception:
                            pass

                    if delta is None or not (delta_min <= delta <= delta_max):
                        continue

                    # For puts: strike must be below spot; for calls: above spot
                    if opt_type == "PUT" and strike >= spot:
                        continue
                    if opt_type == "CALL" and strike <= spot:
                        continue

                    candidates.append({
                        "strike": strike,
                        "bid": round(float(bid), 2),
                        "ask": round(float(ask), 2),
                        "mid": round(mid, 2),
                        "iv": round(float(iv)*100, 1) if iv else None,
                        "oi": oi,
                        "delta": round(delta, 3),
                        "opt_type": opt_type,
                    })
                return candidates

            put_candidates  = get_candidates(puts, "PUT")
            call_candidates = get_candidates(calls, "CALL")

            if not put_candidates or not call_candidates:
                continue

            # Pair best put with best call (closest to delta sweet spot)
            d_lo, d_hi = delta_sweet
            d_mid = (d_lo + d_hi) / 2

            def delta_score(c):
                return abs(c["delta"] - d_mid)

            put_candidates.sort(key=delta_score)
            call_candidates.sort(key=delta_score)

            # Evaluate top 3 puts x top 3 calls
            for put in put_candidates[:3]:
                for call in call_candidates[:3]:
                    net_credit = round(put["mid"] + call["mid"], 2)
                    put_pct    = round((put["ask"]-put["bid"])/put["mid"]*100, 1) if put["mid"] > 0 else None
                    call_pct   = round((call["ask"]-call["bid"])/call["mid"]*100, 1) if call["mid"] > 0 else None

                    # Width between strikes (max loss zone)
                    width = round(call["strike"] - put["strike"], 2)

                    # Score
                    score = 0.0
                    for leg in [put, call]:
                        if d_lo <= leg["delta"] <= d_hi: score += 20
                        elif (d_lo-0.05) <= leg["delta"] <= (d_hi+0.05): score += 10
                        if leg["oi"] >= 1000: score += 10
                        elif leg["oi"] >= 500: score += 5
                    if put_pct and put_pct <= 5: score += 10
                    if call_pct and call_pct <= 5: score += 10
                    if net_credit >= 2.0: score += 10
                    elif net_credit >= 1.0: score += 5

                    strangles.append({
                        "ticker": ticker,
                        "strategy": strategy,
                        "expiration": exp,
                        "dte": days,
                        "put_strike": put["strike"],
                        "call_strike": call["strike"],
                        "width": width,
                        "put_bid": put["bid"],
                        "put_ask": put["ask"],
                        "put_mid": put["mid"],
                        "put_delta": put["delta"],
                        "put_iv": put["iv"],
                        "put_oi": put["oi"],
                        "call_bid": call["bid"],
                        "call_ask": call["ask"],
                        "call_mid": call["mid"],
                        "call_delta": call["delta"],
                        "call_iv": call["iv"],
                        "call_oi": call["oi"],
                        "net_credit": net_credit,
                        "score": round(score, 1),
                    })

        except Exception:
            continue

    # Deduplicate and sort
    seen = set()
    unique = []
    for s in sorted(strangles, key=lambda x: -x["score"]):
        key = (s["expiration"], s["put_strike"], s["call_strike"])
        if key not in seen:
            seen.add(key)
            unique.append(s)

    return unique[:top_n]


def display_strangles(ticker: str, strategy: str, config: dict, strangles: list,
                      spot: float, atr: float, account_id: str, args: list):
    """Display iron condor evaluation results."""

    console.print()
    if spot:
        atr_str = f"  ATR(14): ${atr:.2f}  →  1-ATR put: ${spot-atr:.2f}  1-ATR call: ${spot+atr:.2f}" if atr else ""
        console.print(f"  Spot: [bold]${spot:.2f}[/bold]{atr_str}")
        console.print()

    t = Table(box=box.SIMPLE_HEAD, show_header=True, padding=(0,1), width=180)
    t.add_column("Rank",      width=5,  no_wrap=True)
    t.add_column("Exp",       width=6,  no_wrap=True)
    t.add_column("DTE",       justify="right", width=4)
    t.add_column("Put Strike", justify="right", width=10)
    t.add_column("Call Strike", justify="right", width=11)
    t.add_column("Width",     justify="right", width=7)
    t.add_column("Put Mid",   justify="right", width=8)
    t.add_column("Call Mid",  justify="right", width=9)
    t.add_column("Credit",    justify="right", width=8)
    t.add_column("Put Δ",     justify="right", width=7)
    t.add_column("Call Δ",    justify="right", width=7)
    t.add_column("Put IV",    justify="right", width=7)
    t.add_column("Put OI",    justify="right", width=8)
    t.add_column("Score",     justify="right", width=6)
    t.add_column("Contracts", justify="right", width=10)

    for rank, s in enumerate(strangles, 1):
        rank_str = "[green]#1[/green]" if rank==1 else "[cyan]#2[/cyan]" if rank==2 else f"#{rank}"

        # Sizing: max loss is theoretically unlimited but use width as proxy
        suggested = 1
        try:
            from helm.db import get_conn as _gc
            _c = _gc()
            acct = _c.execute("SELECT portfolio_value FROM accounts WHERE id=?",
                              (account_id,)).fetchone()
            _c.close()
            if acct and acct[0]:
                max_risk = acct[0] * 0.05
                # Use 2x width as proxy for max loss per contract
                loss_proxy = s["width"] * 2 * 100
                suggested = max(1, min(20, int(max_risk / loss_proxy)))
        except Exception:
            pass

        t.add_row(
            rank_str,
            s["expiration"][5:],
            str(s["dte"]),
            f"${s['put_strike']:.0f}",
            f"${s['call_strike']:.0f}",
            f"${s['width']:.0f}",
            f"${s['put_mid']:.2f}",
            f"${s['call_mid']:.2f}",
            f"[green]${s['net_credit']:.2f}[/green]",
            f"{s['put_delta']:.3f}",
            f"{s['call_delta']:.3f}",
            f"{s['put_iv']:.0f}%" if s.get("put_iv") else "--",
            f"{s['put_oi']:,}",
            f"{s['score']:.0f}",
            f"[bold]{suggested}[/bold]",
        )

    console.print(f"[bold]Top {len(strangles)} strangles — {ticker} Iron Condor[/bold]")
    console.print()
    console.print(t)
    console.print()

    # Best strangle summary
    best = strangles[0]
    suggested_best = 1
    try:
        from helm.db import get_conn as _gc2
        _c2 = _gc2()
        acct2 = _c2.execute("SELECT portfolio_value FROM accounts WHERE id=?",
                            (account_id,)).fetchone()
        _c2.close()
        if acct2 and acct2[0]:
            loss_proxy = best["width"] * 2 * 100
            suggested_best = max(1, min(20, int((acct2[0] * 0.05) / loss_proxy)))
    except Exception:
        pass

    total_credit = round(best["net_credit"] * 100 * suggested_best, 0)

    console.print(Panel(
        f"[bold green]Top pick:[/bold green] {ticker} Iron Condor "
        f"{best['expiration']} ({best['dte']}d)\n"
        f"  Sell PUT  ${best['put_strike']:.0f} @ ${best['put_mid']:.2f}  "
        f"(Δ {best['put_delta']:.3f})\n"
        f"  Sell CALL ${best['call_strike']:.0f} @ ${best['call_mid']:.2f}  "
        f"(Δ {best['call_delta']:.3f})\n"
        f"  Net credit: [green]${best['net_credit']:.2f}/contract[/green]  |  "
        f"Width: ${best['width']:.0f}  |  "
        f"Break-evens: ${best['put_strike']-best['net_credit']:.2f} / "
        f"${best['call_strike']+best['net_credit']:.2f}\n\n"
        f"  Suggested: [bold]{suggested_best} contract(s)[/bold]  |  "
        f"Collect: [green]${total_credit:.0f}[/green]\n\n"
        f"[dim]To open: [bold]helm open {ticker} IRON_CONDOR --confirm[/bold][/dim]",
        title="Recommendation",
        border_style="green"
    ))
    console.print()


def evaluate_spreads(ticker: str, strategy: str, config: dict,
                     dte_target: int = None, top_n: int = 6) -> list:
    """
    Evaluate two-leg spread contracts (Bull Put Spread or Bear Call Spread).
    For each short leg candidate, pairs with multiple long leg widths.
    Returns list of spread dicts sorted by score.
    """
    import yfinance as yf
    import math

    opt_type = config["option_type"]
    direction = config["direction"]  # SHORT = selling the spread
    delta_min = config["delta_min"]
    delta_max = config["delta_max"]
    delta_sweet = config["delta_sweet"]
    dte_min = config["dte_min"]
    dte_max = config["dte_max"]
    spread_widths = config.get("spread_widths", [10, 20])

    if dte_target:
        dte_min = max(7, dte_target - 7)
        dte_max = dte_target + 7

    tk = yf.Ticker(ticker)
    info = tk.fast_info
    spot = getattr(info, "last_price", None)
    if not spot:
        hist = tk.history(period="5d")
        spot = float(hist["Close"].iloc[-1]) if not hist.empty else None
    if not spot:
        raise ValueError(f"Cannot fetch price for {ticker}")

    today = __import__("datetime").date.today()
    expirations = tk.options
    target_exps = []
    for exp in expirations:
        d = (__import__("datetime").datetime.strptime(exp, "%Y-%m-%d").date() - today).days
        if dte_min <= d <= dte_max:
            target_exps.append((exp, d))

    if not target_exps:
        raise ValueError(f"No expiries in {dte_min}-{dte_max} DTE range")

    spreads = []
    for exp, days in target_exps:
        try:
            chain = tk.option_chain(exp)
            df = chain.puts if opt_type == "PUT" else chain.calls

            # Build strike -> row lookup
            strike_data = {}
            for _, row in df.iterrows():
                s = float(row["strike"])
                bid = row.get("bid", 0) or 0
                ask = row.get("ask", 0) or 0
                if bid > 0 and ask > 0:
                    mid = (float(bid) + float(ask)) / 2
                    iv = row.get("impliedVolatility", None)
                    oi = int(row.get("openInterest", 0) or 0)
                    strike_data[s] = {
                        "bid": round(float(bid), 2),
                        "ask": round(float(ask), 2),
                        "mid": round(mid, 2),
                        "iv": round(float(iv)*100, 1) if iv else None,
                        "oi": oi,
                    }

            # Find short leg candidates in delta range
            for _, row in df.iterrows():
                strike = float(row["strike"])
                bid = row.get("bid", 0) or 0
                ask = row.get("ask", 0) or 0
                if bid <= 0 or ask <= 0:
                    continue
                oi = int(row.get("openInterest", 0) or 0)
                if oi < 500:
                    continue

                mid_short = (float(bid) + float(ask)) / 2
                iv = row.get("impliedVolatility", None)

                # Compute delta via BS
                delta = None
                if iv and float(iv) > 0:
                    try:
                        iv_val = float(iv)
                        T = days / 365.0
                        S, K, r = spot, strike, 0.045
                        d1 = (math.log(S/K) + (r + 0.5*iv_val**2)*T) / (iv_val*math.sqrt(T))
                        from scipy.stats import norm
                        delta = abs(norm.cdf(d1) - 1) if opt_type == "PUT" else norm.cdf(d1)
                    except Exception:
                        pass

                if delta is None or not (delta_min <= delta <= delta_max):
                    continue

                # For each spread width, find the long leg
                for width in spread_widths:
                    if opt_type == "PUT":
                        long_strike = round(strike - width, 0)
                    else:
                        long_strike = round(strike + width, 0)

                    if long_strike not in strike_data:
                        # Try nearest available
                        available = sorted(strike_data.keys())
                        if opt_type == "PUT":
                            candidates = [s for s in available if s < strike]
                            long_strike = min(candidates, key=lambda s: abs(s-(strike-width))) if candidates else None
                        else:
                            candidates = [s for s in available if s > strike]
                            long_strike = min(candidates, key=lambda s: abs(s-(strike+width))) if candidates else None

                    if long_strike is None or long_strike not in strike_data:
                        continue

                    long_data = strike_data[long_strike]
                    mid_long = long_data["mid"]

                    net_credit = round(mid_short - mid_long, 2)
                    if net_credit <= 0:
                        continue

                    actual_width = abs(strike - long_strike)
                    max_loss = round(actual_width - net_credit, 2)
                    max_gain = net_credit
                    if max_loss <= 0:
                        continue

                    rr_ratio = round(max_gain / max_loss, 2)
                    credit_to_width = round(net_credit / actual_width * 100, 1)

                    spread_pct_short = round((float(ask)-float(bid)) / mid_short * 100, 1) if mid_short > 0 else None
                    spread_pct_long = round((long_data["ask"]-long_data["bid"]) / mid_long * 100, 1) if mid_long > 0 else None

                    # Score: favor good R/R, tight spreads, adequate OI
                    if credit_to_width < 20: continue  # min 20% credit/width
                    score = 0.0
                    d_lo, d_hi = delta_sweet
                    if d_lo <= delta <= d_hi: score += 30
                    elif (d_lo-0.10) <= delta <= (d_hi+0.10): score += 15
                    if credit_to_width >= 30: score += 25
                    elif credit_to_width >= 20: score += 18
                    elif credit_to_width >= 15: score += 10
                    if oi >= 1000: score += 15
                    elif oi >= 500: score += 10
                    elif oi >= 100: score += 5
                    if spread_pct_short and spread_pct_short <= 5: score += 15
                    elif spread_pct_short and spread_pct_short <= 10: score += 8
                    if rr_ratio >= 0.50: score += 15
                    elif rr_ratio >= 0.33: score += 8

                    spreads.append({
                        "ticker": ticker,
                        "strategy": strategy,
                        "expiration": exp,
                        "dte": days,
                        "short_strike": strike,
                        "long_strike": long_strike,
                        "width": actual_width,
                        "opt_type": opt_type,
                        "short_bid": float(bid),
                        "short_ask": float(ask),
                        "short_mid": round(mid_short, 2),
                        "long_bid": long_data["bid"],
                        "long_ask": long_data["ask"],
                        "long_mid": mid_long,
                        "net_credit": net_credit,
                        "max_loss": max_loss,
                        "max_gain": max_gain,
                        "rr_ratio": rr_ratio,
                        "credit_to_width_pct": credit_to_width,
                        "delta": round(delta, 3),
                        "iv": round(float(iv)*100, 1) if iv else None,
                        "oi": oi,
                        "spread_pct": spread_pct_short,
                        "score": round(score, 1),
                    })

        except Exception:
            continue

    spreads.sort(key=lambda s: -s["score"])
    return spreads[:top_n]



def evaluate_straddles(ticker, strategy, config, dte_target=None, top_n=5):
    import yfinance as yf
    from datetime import date, datetime
    tk = yf.Ticker(ticker)
    spot = tk.fast_info.get('last_price') or tk.fast_info.get('previous_close', 0)
    if not spot: return []
    dte_min = config.get('dte_min', 30)
    dte_max = config.get('dte_max', 90)
    dte_sweet = config.get('dte_sweet', 45)
    today = date.today()
    results = []
    for exp in tk.options:
        d = datetime.strptime(exp, '%Y-%m-%d').date()
        dte = (d - today).days
        if not (dte_min <= dte <= dte_max): continue
        if dte_target and abs(dte - dte_target) > 10: continue
        try:
            chain = tk.option_chain(exp)
        except Exception:
            continue
        calls = chain.calls[chain.calls['bid'] > 0].copy()
        puts  = chain.puts[chain.puts['bid'] > 0].copy()
        if calls.empty or puts.empty: continue
        calls['spot_dist'] = abs(calls['strike'] - spot)
        for _, call_row in calls.nsmallest(3, 'spot_dist').iterrows():
            strike = call_row['strike']
            pm = puts[puts['strike'] == strike]
            if pm.empty: continue
            put_row = pm.iloc[0]
            call_mid = (call_row['bid'] + call_row['ask']) / 2
            put_mid  = (put_row['bid']  + put_row['ask'])  / 2
            total_debit = round(call_mid + put_mid, 2)
            call_oi = int(call_row.get('openInterest', 0) or 0)
            put_oi  = int(put_row.get('openInterest', 0)  or 0)
            if min(call_oi, put_oi) < 500: continue
            call_sp = (call_row['ask'] - call_row['bid']) / call_mid * 100 if call_mid > 0 else 99
            put_sp  = (put_row['ask']  - put_row['bid'])  / put_mid  * 100 if put_mid  > 0 else 99
            if max(call_sp, put_sp) > 25: continue
            score = 50.0 - abs(strike - spot) / spot * 50
            score += max(0, 10 - max(call_sp, put_sp)) - abs(dte - dte_sweet) * 0.2
            score += min(10, min(call_oi, put_oi) / 500)
            results.append({'exp': exp, 'dte': dte, 'strike': strike,
                'call_mid': call_mid, 'put_mid': put_mid, 'total_debit': total_debit,
                'call_ask': round(float(call_row['ask']), 2), 'put_ask': round(float(put_row['ask']), 2),
                'call_oi': call_oi, 'put_oi': put_oi,
                'be_down': round(strike - total_debit, 2),
                'be_up':   round(strike + total_debit, 2),
                'pct_move_needed': round(total_debit / spot * 100, 1),
                'score': round(score, 1)})
    return sorted(results, key=lambda x: -x['score'])[:top_n]

def display_straddles(ticker, strategy, config, straddles, spot, atr, account_id, args):
    from rich.table import Table
    from rich import box as _box
    console.print()
    console.print(Panel.fit(
        f'[bold]HELM Open -- {ticker} Long Straddle[/bold]\n'
        f'[dim]Buy ATM call + put | DTE {config["dte_min"]}-{config["dte_max"]} | Data: IBKR live[/dim]',
        border_style='cyan'))
    console.print()
    try:
        from helm.models.iv_history import IVHistory
        ivr_data = IVHistory.for_tickers([ticker]).get(ticker, {})
        ivr_val = ivr_data.get('iv_rank') if ivr_data else None
        if ivr_val is not None:
            if ivr_val > 40:
                console.print(f'  [yellow]Warning IVR {ivr_val:.0f} -- elevated. Best at IVR < 35.[/yellow]')
            else:
                console.print(f'  [green]IVR {ivr_val:.0f} -- cheap. Good straddle entry.[/green]')
            console.print()
    except Exception:
        pass
    if atr:
        console.print(f'  Spot: ${spot:,.2f}  ATR(14): ${atr:.2f}')
        console.print()
    tbl = Table(box=_box.SIMPLE, show_header=True, header_style='bold dim')
    for col, w in [('Rank',5),('Exp',10),('DTE',5),('Strike',8),('Call Mid',9),('Put Mid',9),('Total Cost',11),('Min OI',8),('Break-evens',22),('Move Needed',12),('Score',7)]:
        tbl.add_column(col, justify='right' if col not in ('Rank','Exp','Break-evens') else 'left', width=w)
    for i, s in enumerate(straddles, 1):
        tbl.add_row(f'#{i}', s['exp'], str(s['dte']), f'${s["strike"]:.1f}',
            f'${s["call_mid"]:.2f}', f'${s["put_mid"]:.2f}', f'${s["total_debit"]:.2f}',
            f'{min(s["call_oi"],s["put_oi"]):,}', f'${s["be_down"]:.2f} / ${s["be_up"]:.2f}',
            f'{s["pct_move_needed"]:.1f}%', str(s['score']))
    console.print(f'Top {len(straddles)} straddles -- {ticker} Long Straddle')
    console.print()
    console.print(tbl)
    console.print()
    best = straddles[0]
    contracts = suggest_contracts(strategy, best['strike'], best['total_debit'], account_id, ticker=ticker)
    total_cost = round(best['total_debit'] * contracts * 100, 2)
    console.print(Panel(
        f'[bold green]Top pick:[/bold green] {ticker} Straddle ${best["strike"]:.1f} {best["exp"]} ({best["dte"]}d)\n'
        f'  Buy CALL ${best["strike"]:.1f} @ ${best["call_mid"]:.2f}  |  Buy PUT ${best["strike"]:.1f} @ ${best["put_mid"]:.2f}\n'
        f'  Total debit: ${best["total_debit"]:.2f}/contract  |  Break-evens: ${best["be_down"]:.2f} / ${best["be_up"]:.2f}\n'
        f'  Move needed: {best["pct_move_needed"]:.1f}% in either direction\n\n'
        f'  Suggested: {contracts} contract(s)  |  Total cost: ${total_cost:,.0f}\n\n'
        f'[dim]To open: [bold]helm open {ticker} LONG_STRADDLE --confirm[/bold][/dim]',
        title='Recommendation', border_style='green'))
    console.print()



def evaluate_diagonals(ticker: str, strategy: str, config: dict,
                       dte_target: int = None, top_n: int = 6,
                       side: str = "CALL") -> list:
    """
    Evaluate CALL diagonals (DIAGONAL, PMCC): long deeper-ITM back-month call +
    short nearer-term higher-strike call, legs at DIFFERENT expiries. Delta-
    selected via a Black-Scholes delta from yfinance IV (chains carry no greeks).
    Silent (no console) for the paper-generate path; robust weekend-spot
    fallback. Returns a ranked list of flat dicts; the booker consumes ranked[0].
    max_profit is intentionally NOT computed -- a diagonal's upside is path-
    dependent because the legs don't co-expire.
    """
    import yfinance as yf
    import math
    from datetime import date, datetime
    from scipy.stats import norm

    s_dte_min, s_dte_max, s_dte_sweet = config["short_dte_min"], config["short_dte_max"], config["short_dte_sweet"]
    s_dmin, s_dmax, s_dsweet = config["short_delta_min"], config["short_delta_max"], config["short_delta_sweet"]
    l_dte_min, l_dte_max, l_dte_sweet = config["long_dte_min"], config["long_dte_max"], config["long_dte_sweet"]
    l_dmin, l_dmax, l_dsweet = config["long_delta_min"], config["long_delta_max"], config["long_delta_sweet"]
    max_debit_pct = config.get("max_debit_pct", 0.75)
    is_put = (str(side).upper() == "PUT")

    tk = yf.Ticker(ticker)
    spot = getattr(tk.fast_info, "last_price", None)
    if not spot:
        hist = tk.history(period="5d")
        spot = float(hist["Close"].iloc[-1]) if not hist.empty else None
    if not spot:
        return []

    def bs_delta(strike, iv_pct, dte_days):
        try:
            iv = (iv_pct or 0) / 100.0
            if iv <= 0 or dte_days <= 0 or strike <= 0:
                return None
            T = dte_days / 365.0
            d1 = (math.log(spot / strike) + (0.045 + 0.5 * iv**2) * T) / (iv * math.sqrt(T))
            # delta MAGNITUDE in [0,1]: call N(d1); put 1-N(d1)=N(-d1)
            return float(norm.cdf(-d1) if is_put else norm.cdf(d1))
        except Exception:
            return None

    def sweet_mid(s):
        return (s[0] + s[1]) / 2 if isinstance(s, (tuple, list)) else s

    today = date.today()
    short_exps, long_exps = [], []
    for exp in tk.options:
        try:
            d = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
        except Exception:
            continue
        if s_dte_min <= d <= s_dte_max:
            short_exps.append((d, exp))
        elif l_dte_min <= d <= l_dte_max:
            long_exps.append((d, exp))
    if not short_exps or not long_exps:
        return []
    short_exps.sort(key=lambda x: abs(x[0] - s_dte_sweet))
    long_exps.sort(key=lambda x: abs(x[0] - l_dte_sweet))

    def opt_rows(exp):
        try:
            df = tk.option_chain(exp).puts if is_put else tk.option_chain(exp).calls
        except Exception:
            return []
        out = []
        for _, r in df.iterrows():
            bid = float(r.get("bid", 0) or 0); ask = float(r.get("ask", 0) or 0)
            if bid <= 0 or ask <= 0:
                continue
            mid = round((bid + ask) / 2, 2)
            out.append({"strike": float(r["strike"]), "bid": round(bid, 2), "ask": round(ask, 2),
                        "mid": mid, "iv": round(float(r.get("impliedVolatility", 0) or 0) * 100, 1),
                        "oi": int(r.get("openInterest", 0) or 0),
                        "spread_pct": round((ask - bid) / mid, 3) if mid > 0 else 9.0})
        return out

    short_cands = []
    for dte, exp in short_exps[:3]:
        for c in opt_rows(exp):
            if c["oi"] < 100:
                continue
            delta = bs_delta(c["strike"], c["iv"], dte)
            if delta is None or not (s_dmin <= delta <= s_dmax):
                continue
            short_cands.append({**c, "exp": exp, "dte": dte, "delta": round(delta, 3)})
    if not short_cands:
        return []
    short_cands.sort(key=lambda x: abs(x["delta"] - sweet_mid(s_dsweet)))

    results = []
    for short in short_cands[:4]:
        best = None
        for dte, exp in long_exps[:3]:
            for c in opt_rows(exp):
                bad_strike = (c["strike"] < short["strike"]) if is_put else (c["strike"] > short["strike"])
                if c["oi"] < 100 or bad_strike:
                    continue
                delta = bs_delta(c["strike"], c["iv"], dte)
                if delta is None or not (l_dmin <= delta <= l_dmax):
                    continue
                prox = -abs(delta - sweet_mid(l_dsweet))
                if best is None or prox > best[0]:
                    best = (prox, {**c, "exp": exp, "dte": dte, "delta": round(delta, 3)})
        if best is None:
            continue
        long = best[1]

        net_debit = round(long["mid"] - short["mid"], 2)
        if net_debit <= 0:
            continue
        width = round(abs(short["strike"] - long["strike"]), 2)
        if width > 0 and (net_debit / width) > max_debit_pct:
            continue
        breakeven = round(short["strike"] + (-net_debit if is_put else net_debit), 2)

        score = 0.0
        if s_dsweet[0] <= short["delta"] <= s_dsweet[1]: score += 20
        elif (s_dsweet[0] - 0.05) <= short["delta"] <= (s_dsweet[1] + 0.05): score += 10
        if l_dsweet[0] <= long["delta"] <= l_dsweet[1]: score += 20
        elif (l_dsweet[0] - 0.05) <= long["delta"] <= (l_dsweet[1] + 0.05): score += 10
        for oi in (short["oi"], long["oi"]):
            if oi >= 1000: score += 8
            elif oi >= 500: score += 4
        score += max(0.0, 10 - 0.5 * short["spread_pct"] * 100)
        score += max(0.0, 10 - 0.5 * long["spread_pct"] * 100)
        score -= abs(short["dte"] - s_dte_sweet) * 0.1
        score -= abs(long["dte"] - l_dte_sweet) * 0.02

        results.append({
            "ticker": ticker, "strategy": strategy,
            "short_exp": short["exp"], "short_dte": short["dte"], "short_strike": short["strike"],
            "short_mid": short["mid"], "short_bid": short["bid"], "short_delta": short["delta"],
            "short_iv": short["iv"], "short_oi": short["oi"],
            "long_exp": long["exp"], "long_dte": long["dte"], "long_strike": long["strike"],
            "long_mid": long["mid"], "long_ask": long["ask"], "long_delta": long["delta"],
            "long_iv": long["iv"], "long_oi": long["oi"],
            "net_debit": net_debit, "width": width, "breakeven": breakeven, "score": round(score, 1),
        })

    results.sort(key=lambda x: -x["score"])
    return results[:top_n]


def evaluate_debit_spreads(ticker, strategy, config, dte_target=None, top_n=5):
    import yfinance as yf
    from datetime import date, datetime
    is_bear = strategy == 'BEAR_PUT_SPREAD'
    tk = yf.Ticker(ticker)
    spot = getattr(tk.fast_info, 'last_price', None)
    if not spot:
        hist = tk.history(period='5d')
        spot = float(hist['Close'].iloc[-1]) if not hist.empty else None
    if not spot:
        return []
    dte_min = config.get('dte_min', 30)
    dte_max = config.get('dte_max', 90)
    dte_sweet = config.get('dte_sweet', 60)
    widths = config.get('spread_widths', [5, 10, 15, 20, 25])
    today = date.today()
    results = []
    for exp in tk.options:
        d = datetime.strptime(exp, '%Y-%m-%d').date()
        dte = (d - today).days
        if not (dte_min <= dte <= dte_max): continue
        if dte_target and abs(dte - dte_target) > 10: continue
        try:
            chain = tk.option_chain(exp)
            opts = chain.puts if is_bear else chain.calls
        except Exception:
            continue
        opts = opts[opts['bid'] > 0].copy()
        if opts.empty: continue
        for _, long_row in opts.iterrows():
            long_strike = long_row['strike']
            sp = long_strike / spot
            if is_bear:
                if not (0.88 <= sp <= 1.02): continue
            else:
                if not (0.98 <= sp <= 1.12): continue
            long_mid = (long_row['bid'] + long_row['ask']) / 2
            long_oi = int(long_row.get('openInterest', 0) or 0)
            if long_oi < 500 or long_mid <= 0: continue
            for width in widths:
                short_strike = long_strike - width if is_bear else long_strike + width
                sm = opts[opts['strike'] == short_strike]
                if sm.empty: continue
                short_row = sm.iloc[0]
                short_mid = (short_row['bid'] + short_row['ask']) / 2
                short_oi = int(short_row.get('openInterest', 0) or 0)
                if short_oi < 500 or short_mid <= 0: continue
                net_debit = round(long_mid - short_mid, 2)
                if net_debit <= 0: continue
                max_profit = round(width - net_debit, 2)
                if max_profit <= 0: continue
                dtw = round(net_debit / width * 100, 1)
                rr = round(max_profit / net_debit, 2)
                lsp = (long_row['ask'] - long_row['bid']) / long_mid * 100
                ssp = (short_row['ask'] - short_row['bid']) / short_mid * 100
                if max(lsp, ssp) > 30: continue
                score = 0.0
                if dtw <= 40: score += 20
                elif dtw <= 50: score += 12
                else: score += 5
                if rr >= 1.5: score += 20
                elif rr >= 1.0: score += 12
                if min(long_oi, short_oi) >= 1000: score += 15
                elif min(long_oi, short_oi) >= 500: score += 8
                score -= abs(dte - dte_sweet) * 0.2
                results.append({'exp': exp, 'dte': dte, 'long_strike': long_strike,
                    'short_strike': short_strike, 'width': width, 'long_mid': long_mid,
                    'short_mid': short_mid, 'net_debit': net_debit, 'max_profit': max_profit,
                    'debit_to_width_pct': dtw, 'rr': rr,
                    'long_oi': long_oi, 'short_oi': short_oi,
                    'long_bid': float(long_row['bid']), 'long_ask': float(long_row['ask']),
                    'short_bid': float(short_row['bid']), 'short_ask': float(short_row['ask']),
                    'score': round(score, 1)})
    return sorted(results, key=lambda x: -x['score'])[:top_n]

def display_debit_spreads(ticker, strategy, config, spreads, spot, atr, account_id, args):
    from rich.table import Table
    from rich import box as _box
    is_bear  = strategy == 'BEAR_PUT_SPREAD'
    label    = config.get('label', strategy)
    leg_type = 'PUT' if is_bear else 'CALL'
    console.print()
    console.print(Panel.fit(
        f'[bold]HELM Open -- {ticker} {label}[/bold]\n'
        f'[dim]Debit spread | DTE {config["dte_min"]}-{config["dte_max"]} | Data: IBKR live[/dim]',
        border_style='cyan'))
    console.print()
    if atr:
        s1 = round(spot - atr, 2)
        s2 = round(spot - 2*atr, 2)
        console.print(f'  Spot: ${spot:,.2f}  ATR(14): ${atr:.2f}  -- 1-ATR: ${s1:,.2f}  2-ATR: ${s2:,.2f}')
        console.print()
    tbl = Table(box=_box.SIMPLE, show_header=True, header_style='bold dim')
    for col, w, just in [('Rank',5,'left'),('Exp',10,'left'),('DTE',5,'right'),
        ('Long',8,'right'),('Short',8,'right'),('Width',6,'right'),
        ('Debit',8,'right'),('Max Profit',10,'right'),('D/W%',6,'right'),
        ('R/R',6,'right'),('OI',7,'right'),('Score',7,'right')]:
        tbl.add_column(col, justify=just, width=w)
    for i, s in enumerate(spreads, 1):
        tbl.add_row(f'#{i}', s['exp'], str(s['dte']),
            f'${s["long_strike"]:.0f}', f'${s["short_strike"]:.0f}', f'${s["width"]}',
            f'${s["net_debit"]:.2f}', f'${s["max_profit"]:.2f}',
            f'{s["debit_to_width_pct"]:.0f}%', str(s['rr']),
            f'{min(s["long_oi"],s["short_oi"]):,}', str(s['score']))
    console.print(f'Top {len(spreads)} spreads -- {ticker} {label}')
    console.print()
    console.print(tbl)
    console.print()
    best = spreads[0]
    contracts = suggest_contracts(strategy, best['long_strike'], best['net_debit'], account_id, ticker=ticker)
    total_cost = round(best['net_debit'] * contracts * 100, 2)
    console.print(Panel(
        f'[bold green]Top pick:[/bold green] {ticker} {label} '
        f'${best["long_strike"]:.0f}/${best["short_strike"]:.0f} {best["exp"]} ({best["dte"]}d)\n'
        f'  Buy  {leg_type} ${best["long_strike"]:.0f} @ ${best["long_mid"]:.2f}  |  '
        f'Sell {leg_type} ${best["short_strike"]:.0f} @ ${best["short_mid"]:.2f}\n'
        f'  Net debit: ${best["net_debit"]:.2f}/contract  |  '
        f'Max profit: ${best["max_profit"]:.2f}/contract  |  Width: ${best["width"]}\n'
        f'  Debit/width: {best["debit_to_width_pct"]:.0f}%  |  R/R: {best["rr"]}\n\n'
        f'  Suggested: {contracts} contract(s)  |  Total cost: ${total_cost:,.0f}\n\n'
        f'[dim]To open: [bold]helm open {ticker} {strategy} --confirm[/bold][/dim]',
        title='Recommendation', border_style='green'))
    console.print()


# Strategies deliberately off-limits in this IRA (undefined-risk / not IRA-eligible).
# Tokens stay load-bearing for import, check, risk classification & paper code;
# they are refused at the open path only.
OFF_LIMITS = {"SHORT_STRANGLE", "JADE_LIZARD"}


def run():
    args = sys.argv[1:]

    if not args or args[0] in ("--help", "-h"):
        console.print()
        console.print("[bold]Usage:[/bold]  helm open <ticker> <strategy> [options]")
        console.print()
        console.print("[dim]Strategies:[/dim]  CSP  COVERED_CALL  LONG_CALL  LONG_PUT  BULL_PUT_SPREAD  BEAR_CALL_SPREAD")
        console.print("[dim]Options:[/dim]")
        console.print("  [cyan]--dte N[/cyan]      Target DTE (default: strategy default)")
        console.print("  [cyan]--top N[/cyan]      Show top N contracts (default: 8)")
        console.print()
        return

    if not get_active_account():
        console.print("[red]No active account. Run helm setup first.[/red]")
        return

    # Parse
    dte_target = None
    top_n = 8
    positional = []
    i = 0
    while i < len(args):
        if args[i] == "--dte" and i+1 < len(args):   dte_target = int(args[i+1]); i += 2
        elif args[i] == "--top" and i+1 < len(args):  top_n = int(args[i+1]); i += 2
        else: positional.append(args[i]); i += 1

    if len(positional) < 2:
        console.print("[red]Specify ticker and strategy.[/red]")
        console.print("[dim]Example: helm open ANET CSP[/dim]")
        return

    ticker   = positional[0].upper()
    strategy = positional[1].upper()

    if strategy in OFF_LIMITS:
        console.print(f"[red]Off-limits in this IRA:[/red] {strategy}")
        console.print("[dim]Undefined-risk structure, not IRA-eligible. "
                      "Token kept for import & risk classification only.[/dim]")
        return

    if strategy not in STRATEGY_CONFIG:
        console.print(f"[red]Unknown strategy:[/red] {strategy}")
        console.print(f"[dim]Supported: {', '.join(STRATEGY_CONFIG.keys())}[/dim]")
        return

    config = STRATEGY_CONFIG[strategy]
    account_id = get_active_account()

    # Check IBKR + market status for data source label
    try:
        from helm.ibkr import check_connection as _chk
        from helm.cli.check_cmd import is_market_open as _mkt
        _ibkr_ok = _chk()["connected"]
        _mkt_open = _mkt()
        if _ibkr_ok and _mkt_open:
            data_source = "[green]IBKR live[/green]"
        elif _ibkr_ok:
            data_source = "[yellow]IBKR + yfinance (market closed)[/yellow]"
        else:
            data_source = "[dim]yfinance only[/dim]"
    except Exception:
        data_source = "[dim]yfinance[/dim]"

    console.print()
    console.print(Panel.fit(
        f"[bold cyan]HELM Open[/bold cyan] — {ticker} {config['label']}\n"
        f"[dim]Delta {config.get("delta_min", config.get("short_delta_min",0)):.2f}-{config.get("delta_max", config.get("short_delta_max",1)):.2f} | "
        f"DTE {dte_target or config.get("dte_min", config.get("short_dte_min",0))}-{dte_target or config.get("dte_max", config.get("short_dte_max",90))} | "
        f"Spread threshold: 25% | Data: {data_source}[/dim]",
        border_style="cyan"
    ))
    console.print()

    console.print(f"Fetching options chain for [bold]{ticker}[/bold]...")

    # Show IVR context before fetching chain
    from helm.models.iv_history import IVHistory
    _ivr_open = IVHistory.latest(ticker)
    if _ivr_open and _ivr_open.iv_rank is not None:
        ivr_min = config.get("entry_iv_rank_min")
        ivr_max = config.get("entry_iv_rank_max")
        ivr_warn = ""
        if ivr_min and _ivr_open.iv_rank < ivr_min:
            ivr_warn = f"  [yellow]⚠ IVR {_ivr_open.iv_rank:.0f} below strategy min {ivr_min}[/yellow]"
        elif ivr_max and _ivr_open.iv_rank > ivr_max:
            ivr_warn = f"  [yellow]⚠ IVR {_ivr_open.iv_rank:.0f} above strategy max {ivr_max}[/yellow]"
        console.print(f"  IVR: {_ivr_open.rank_label}  IVP: {_ivr_open.percentile_label}  [dim]current IV {_ivr_open.iv_current:.1f}% | 52wk {_ivr_open.iv_52wk_low:.0f}%-{_ivr_open.iv_52wk_high:.0f}%[/dim]{ivr_warn}")

    # Get spot price for context
    spot = None
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).fast_info
        spot = getattr(info, "last_price", None)
    except Exception:
        pass

    # Get ATR for context
    atr = None
    try:
        import yfinance as yf
        import numpy as np
        hist = yf.Ticker(ticker).history(period="3mo", interval="1d")
        if not hist.empty:
            prev = hist["Close"].shift(1)
            tr = np.maximum(hist["High"]-hist["Low"],
                 np.maximum(abs(hist["High"]-prev), abs(hist["Low"]-prev)))
            atr = round(float(tr.rolling(14).mean().iloc[-1]), 2)
    except Exception:
        pass

    is_spread       = config.get("is_spread", False)
    is_strangle     = config.get("is_strangle", False)
    is_condor       = config.get("is_condor", False)
    is_diagonal     = config.get("is_diagonal", False)
    is_diagonal_put = config.get("is_diagonal_put", False)
    is_debit_spread = config.get("is_debit_spread", False)
    is_straddle     = config.get("is_straddle", False)
    is_pmcc         = config.get("is_pmcc", False)
    is_perm          = config.get("is_perm", False)

    if is_diagonal:
        try:
            from helm.cli.diagonal import evaluate_diagonal, display_diagonal
            spot_d, diagonals = evaluate_diagonal(ticker)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            return
        display_diagonal(ticker, spot_d, diagonals, args)
        return

    if is_diagonal_put:
        try:
            from helm.cli.diagonal import evaluate_diagonal_put, display_diagonal_put
            spot_dp, diagonals_p = evaluate_diagonal_put(ticker)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            return
        display_diagonal_put(ticker, spot_dp, diagonals_p, args)
        return

    if is_pmcc:
        try:
            from helm.cli.diagonal import evaluate_diagonal, display_diagonal, PMCC_CONFIG
            spot_pm, pmcc_d = evaluate_diagonal(ticker, PMCC_CONFIG)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            return
        display_diagonal(ticker, spot_pm, pmcc_d, args, label="Poor Man's Covered Call (PMCC)")
        return

    if is_perm:
        try:
            from helm.cli.perm import evaluate_perm, display_perm
            spot_p, earn_d, days_earn, exit_d, cands = evaluate_perm(ticker)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            return
        display_perm(ticker, spot_p, earn_d, days_earn, exit_d, cands, args)
        return

    if is_condor:
        try:
            condors = evaluate_condors(ticker, strategy, config, dte_target, top_n)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            return

        if not condors:
            console.print(f"[yellow]No iron condor contracts found matching criteria.[/yellow]")
            console.print(f"[dim]Try --dte with a different target.[/dim]")
            return

        display_condors(ticker, strategy, config, condors, spot, atr, account_id, args)
        return

    if is_strangle:
        try:
            strangles = evaluate_strangles(ticker, strategy, config, dte_target, top_n)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            return

        if not strangles:
            console.print(f"[yellow]No strangle contracts found matching criteria.[/yellow]")
            console.print(f"[dim]Try --dte with a different target.[/dim]")
            return

        display_strangles(ticker, strategy, config, strangles, spot, atr, account_id, args)
        return

    if is_straddle:
        try:
            straddles = evaluate_straddles(ticker, strategy, config, dte_target, top_n)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            return
        if not straddles:
            console.print(f"[yellow]No straddle contracts found matching criteria.[/yellow]")
            console.print(f"[dim]Try --dte with a different target, or check IVR (low IVR preferred).[/dim]")
            return
        display_straddles(ticker, strategy, config, straddles, spot, atr, account_id, args)
        return

    if is_debit_spread:
        try:
            debit_spreads = evaluate_debit_spreads(ticker, strategy, config, dte_target, top_n)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            return
        if not debit_spreads:
            console.print(f"[yellow]No contracts found matching criteria.[/yellow]")
            console.print(f"[dim]Try --dte with a different target, or check helm screen output.[/dim]")
            return
        display_debit_spreads(ticker, strategy, config, debit_spreads, spot, atr, account_id, args)
        return

    if is_spread:
        try:
            spreads = evaluate_spreads(ticker, strategy, config, dte_target, top_n)
        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            return

        if not spreads:
            console.print(f"[yellow]No spread contracts found matching criteria.[/yellow]")
            console.print(f"[dim]Try --dte with a different target.[/dim]")
            return

        display_spreads(ticker, strategy, config, spreads, spot, atr, account_id, args)
        return

    try:
        contracts = evaluate_contracts(ticker, strategy, config, dte_target, top_n)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        return

    if not contracts:
        console.print(f"[yellow]No contracts found matching criteria.[/yellow]")
        console.print(f"[dim]Try --dte with a different target, or check helm screen output.[/dim]")
        return

    console.print()
    if spot:
        spot_str = f"Spot: [bold]${spot:.2f}[/bold]"
        atr_str = f"  ATR(14): ${atr:.2f}  →  1-ATR: ${spot-atr:.2f}  2-ATR: ${spot-2*atr:.2f}" if atr else ""
        console.print(f"  {spot_str}{atr_str}")
        console.print()

    # Results table
    t = Table(box=box.SIMPLE_HEAD, show_header=True, padding=(0,1), width=170)
    t.add_column("Rank",     width=5, no_wrap=True)
    t.add_column("Exp",      width=8, no_wrap=True)
    t.add_column("DTE",      justify="right", width=5, no_wrap=True)
    t.add_column("Strike",   justify="right", width=8, no_wrap=True)
    t.add_column("Bid",      justify="right", width=6, no_wrap=True)
    t.add_column("Ask",      justify="right", width=6, no_wrap=True)
    t.add_column("Mid",      justify="right", width=6, no_wrap=True)
    t.add_column("Spread%",  justify="right", width=8, no_wrap=True)
    t.add_column("Delta",    justify="right", width=7, no_wrap=True)
    t.add_column("Theta",    justify="right", width=7, no_wrap=True)
    t.add_column("IV%",      justify="right", width=5, no_wrap=True)
    t.add_column("OI",       justify="right", width=7, no_wrap=True)
    t.add_column("Premium",  justify="right", width=9, no_wrap=True)
    t.add_column("Score",    justify="right", width=6, no_wrap=True)
    t.add_column("Contracts",justify="right", width=9, no_wrap=True)
    t.add_column("Source",   width=10, no_wrap=True)

    for rank, c in enumerate(contracts, 1):
        # Suggest contracts
        suggested = suggest_contracts(strategy, c["strike"], c["mid"], account_id, ticker=ticker)

        spread_str = spread_flag(c.get("spread_pct"))
        delta_str  = delta_flag(c.get("delta"), config["delta_min"],
                                config["delta_max"], config["delta_sweet"])
        theta_str  = f"${c['theta']:.3f}" if c.get("theta") else "--"
        iv_str     = f"{c['iv']:.0f}%" if c.get("iv") else "--"
        premium_str = f"${(c.get('premium_total') or c.get('mid',0)*100):.0f}/contract"
        score_str  = f"{c['score']:.0f}"

        # Rank indicator
        rank_str = "[green]#1[/green]" if rank == 1 else                    "[cyan]#2[/cyan]" if rank == 2 else                    "[yellow]#3[/yellow]" if rank == 3 else f"#{rank}"

        t.add_row(
            rank_str,
            c["expiration"][5:],  # MM-DD
            str(c["dte"]),
            f"${c['strike']:.1f}",
            f"${c['bid']:.2f}",
            f"${c['ask']:.2f}",
            f"${c['mid']:.2f}",
            spread_str,
            delta_str,
            theta_str,
            iv_str,
            f"{c['oi']:,}",
            premium_str,
            score_str,
            f"[bold]{suggested}[/bold]",
            f"[dim]{c.get('source', 'yf')}[/dim]",
        )

    console.print(f"[bold]Top {len(contracts)} contracts — {ticker} {strategy}[/bold]")
    console.print()
    console.print(t)
    console.print()

    # Best contract summary
    best = contracts[0]
    suggested = suggest_contracts(strategy, best["strike"], best["mid"], account_id, ticker=ticker)
    total_premium = round(best["mid"] * 100 * suggested, 2)

    console.print(Panel(
        f"[bold green]Top pick:[/bold green] {ticker} {best['opt_type']} "
        f"${best['strike']:.1f} {best['expiration']} ({best['dte']}d)\n"
        f"  Mid: ${best['mid']:.2f}  |  Delta: {best.get('delta', '--')}  |  "
        f"Spread: {best.get('spread_pct', '--')}%  |  OI: {best['oi']:,}\n"
        f"  Suggested: [bold]{suggested} contract(s)[/bold] @ ${best['mid']:.2f} = "
        f"[green]${total_premium:.0f} premium[/green]\n\n"
        f"[dim]To open: [bold]helm open {ticker} {strategy} --confirm[/bold][/dim]",
        title="Recommendation",
        border_style="green"
    ))
    console.print()

    # --confirm flow
    if "--confirm" in args:
        # Fetch scan data for entry snapshot context
        scan_data = None
        try:
            from helm.cli.scan_cmd import fetch_technicals
            console.print("[dim]Fetching technical context for entry snapshot...[/dim]")
            scan_data = fetch_technicals(ticker)
        except Exception:
            pass
        confirm_and_log(ticker, strategy, contracts, config, spot, scan_data)


if __name__ == "__main__":
    run()
