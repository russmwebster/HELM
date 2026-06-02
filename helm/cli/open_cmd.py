
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
    "SHORT_STRANGLE": {
        "option_type": "BOTH",
        "direction": "SHORT",
        "delta_min": 0.15,
        "delta_max": 0.35,
        "delta_sweet": (0.20, 0.30),
        "dte_min": 21,
        "dte_max": 56,
        "label": "Short Strangle",
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
    "DIAGONAL": {
        "option_type": "CALL",
        "label": "Diagonal Spread",
        "is_diagonal": True,
        "short_dte_min": 21,  "short_dte_max": 45,  "short_dte_sweet": 30,
        "short_delta_min": 0.30, "short_delta_max": 0.55, "short_delta_sweet": (0.38, 0.45),
        "long_dte_min": 60,   "long_dte_max": 120, "long_dte_sweet": 75,
        "long_delta_min": 0.55, "long_delta_max": 0.85, "long_delta_sweet": (0.65, 0.75),
        "max_debit_pct": 0.75,
    },
    "DIAGONAL_PUT": {
        "option_type": "PUT",
        "label": "Diagonal Spread (Put)",
        "is_diagonal_put": True,
        "short_dte_min": 21,  "short_dte_max": 45,  "short_dte_sweet": 30,
        "short_delta_min": 0.30, "short_delta_max": 0.55, "short_delta_sweet": (0.38, 0.45),
        "long_dte_min": 60,   "long_dte_max": 120, "long_dte_sweet": 75,
        "long_delta_min": 0.55, "long_delta_max": 0.85, "long_delta_sweet": (0.65, 0.75),
        "max_debit_pct": 0.75,
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
                if oi < 50:
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


def evaluate_strangles(ticker: str, strategy: str, config: dict,
                       dte_target: int = None, top_n: int = 6) -> list:
    """
    Evaluate short strangle contracts.
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
                    if oi < 50:
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
    """Display short strangle evaluation results."""

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

    console.print(f"[bold]Top {len(strangles)} strangles — {ticker} Short Strangle[/bold]")
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
        f"[bold green]Top pick:[/bold green] {ticker} Short Strangle "
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
        f"[dim]To open: [bold]helm open {ticker} SHORT_STRANGLE --confirm[/bold][/dim]",
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
                if oi < 50:
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
        f"[dim]Delta {config.get("delta_min", config.get("short_delta_min",0)):.2f}-{config.get("delta_max", config.get("short_delta_max",1)):.2f} | "
        f"DTE {dte_target or config.get("dte_min", config.get("short_dte_min",0))}-{dte_target or config.get("dte_max", config.get("short_dte_max",90))} | "
        f"Spread threshold: 25% | Data: {data_source}[/dim]",
        border_style="cyan"
    ))
    console.print()

    console.print(f"Fetching options chain for [bold]{ticker}[/bold]...")

    if strategy == "SHORT_STRANGLE":
        console.print()
        console.print("  [yellow]⚠  SHORT_STRANGLE requires naked options approval (Level 3+) and margin account.[/yellow]")
        console.print()
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

    is_spread   = config.get("is_spread", False)
    is_strangle = config.get("is_strangle", False)
    is_condor   = config.get("is_condor", False)
    is_diagonal = config.get("is_diagonal", False)
    is_diagonal_put = config.get("is_diagonal_put", False)

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
