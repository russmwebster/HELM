
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
        "delta_min": 0.30,
        "delta_max": 0.70,
        "delta_sweet": (0.40, 0.60),
        "dte_min": 30,
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
        "delta_min": 0.20,
        "delta_max": 0.40,
        "delta_sweet": (0.25, 0.35),
        "dte_min": 21,
        "dte_max": 45,
        "label": "Bull Put Spread",
    },
    "BEAR_CALL_SPREAD": {
        "option_type": "CALL",
        "direction": "SHORT",
        "delta_min": 0.20,
        "delta_max": 0.40,
        "delta_sweet": (0.25, 0.35),
        "dte_min": 21,
        "dte_max": 45,
        "label": "Bear Call Spread",
    },
}

# ── Contract scoring (adapted from COTS ladder.py) ────────────────────────────

def fetch_ibkr_greeks(contracts: list) -> dict:
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
                      account_id: str) -> int:
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

        # Long options: fixed dollar target (~$5,000)
        # This will be user-configurable in setup in a future version
        LONG_OPTION_TARGET = 5000.0

        if strategy in ("LONG_CALL", "LONG_PUT"):
            max_contracts = int(LONG_OPTION_TARGET / (mid * 100)) if mid > 0 else 1
        elif strategy in ("CSP", "SHORT_STRANGLE"):
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

    today = date.today()
    expirations = tk.options
    target_exps = []
    for exp in expirations:
        d = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
        if dte_min <= d <= dte_max:
            target_exps.append((exp, d))

    if not target_exps:
        raise ValueError(f"No expiries found in {dte_min}-{dte_max} DTE range")

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
                if oi < 100:
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
        f"[dim]Delta {config['delta_min']:.2f}-{config['delta_max']:.2f} | "
        f"DTE {dte_target or config['dte_min']}-{dte_target or config['dte_max']} | "
        f"Spread threshold: 15% | Data: {data_source}[/dim]",
        border_style="cyan"
    ))
    console.print()

    console.print(f"Fetching options chain for [bold]{ticker}[/bold]...")

    try:
        contracts = evaluate_contracts(ticker, strategy, config, dte_target, top_n)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        return

    if not contracts:
        console.print(f"[yellow]No contracts found matching criteria.[/yellow]")
        console.print(f"[dim]Try --dte with a different target, or check helm screen output.[/dim]")
        return

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
        suggested = suggest_contracts(strategy, c["strike"], c["mid"], account_id)

        spread_str = spread_flag(c.get("spread_pct"))
        delta_str  = delta_flag(c.get("delta"), config["delta_min"],
                                config["delta_max"], config["delta_sweet"])
        theta_str  = f"${c['theta']:.3f}" if c.get("theta") else "--"
        iv_str     = f"{c['iv']:.0f}%" if c.get("iv") else "--"
        premium_str = f"${c['premium_total']:.0f}/contract"
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
    suggested = suggest_contracts(strategy, best["strike"], best["mid"], account_id)
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
