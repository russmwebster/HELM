
# helm/cli/check_cmd.py
# helm check -- health assessment for open positions
#
# Usage:
#   helm check              Check all open positions
#   helm check AMD          Check a specific ticker
#   helm check AMD --deep   Full detail including Greeks
#
# Data sources:
#   IBKR connected + market open  -> live prices + Greeks
#   IBKR connected + market closed -> last close (underlying only)
#   IBKR not connected             -> yfinance for all data
#
# Output:
#   GREEN  -- position healthy, on track
#   YELLOW -- attention needed, monitor closely
#   RED    -- action likely required

import sys
import logging
from pathlib import Path
from datetime import date, datetime, time
from typing import Optional
import math

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
logging.getLogger("ib_insync").setLevel(logging.CRITICAL)
logging.getLogger("asyncio").setLevel(logging.CRITICAL)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

import warnings
warnings.filterwarnings("ignore")

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from helm.config import get_active_account
from helm.db import get_conn

console = Console()

# ── Market hours ─────────────────────────────────────────────────────────────

def is_market_open() -> bool:
    """Check if US equity options market is currently open (handles holidays)."""
    try:
        import pandas_market_calendars as mcal
        import pandas as pd
        nyse = mcal.get_calendar("NYSE")
        today = pd.Timestamp.now(tz="America/New_York")
        schedule = nyse.schedule(
            start_date=today.strftime("%Y-%m-%d"),
            end_date=today.strftime("%Y-%m-%d")
        )
        if schedule.empty:
            return False  # holiday or weekend
        market_open  = schedule.iloc[0]["market_open"].tz_convert("America/New_York")
        market_close = schedule.iloc[0]["market_close"].tz_convert("America/New_York")
        return market_open <= today <= market_close
    except Exception:
        # Fallback: simple weekday + time check (no holiday awareness)
        try:
            import pytz
            now_et = datetime.now(pytz.timezone("America/New_York"))
            if now_et.weekday() >= 5:
                return False
            from datetime import time as dtime
            return dtime(9, 30) <= now_et.time() <= dtime(16, 0)
        except Exception:
            return False


def market_status_label() -> str:
    try:
        if is_market_open():
            return "[green]market open[/green]"
        else:
            return "[yellow]market closed — last session data[/yellow]"
    except ImportError:
        return "[dim]market status unknown[/dim]"


# ── DTE calculation ───────────────────────────────────────────────────────────

def dte(expiration: str) -> Optional[int]:
    if not expiration:
        return None
    try:
        exp = datetime.strptime(expiration, "%Y-%m-%d").date()
        return (exp - date.today()).days
    except Exception:
        return None


# ── IBKR data fetch ───────────────────────────────────────────────────────────

def fetch_ibkr_underlying(ticker: str) -> dict:
    """Fetch underlying price from IBKR. Returns close price outside hours."""
    result = {"price": None, "source": "ibkr", "live": False, "error": None}
    try:
        from helm.ibkr import check_connection
        status = check_connection()
        if not status["connected"]:
            result["error"] = "IBKR not connected"
            return result

        from ib_insync import IB, Stock
        ib = IB()
        ib.connect("127.0.0.1", 4002, clientId=12, readonly=True)
        try:
            stock = Stock(ticker, "SMART", "USD")
            ib.qualifyContracts(stock)
            t = ib.reqMktData(stock, "", False, False)
            ib.sleep(2)

            market_open = is_market_open()
            if market_open and t.last and not math.isnan(t.last) and t.last > 0:
                result["price"] = round(t.last, 2)
                result["live"] = True
            elif t.close and not math.isnan(t.close) and t.close > 0:
                result["price"] = round(t.close, 2)
                result["live"] = False
        finally:
            ib.disconnect()
    except Exception as e:
        result["error"] = str(e)[:60]
    return result


def fetch_ibkr_option(ticker: str, expiration: str, strike: float,
                       option_type: str) -> dict:
    """Fetch live option data from IBKR. Only useful during market hours."""
    result = {
        "bid": None, "ask": None, "mid": None, "last": None,
        "delta": None, "theta": None, "gamma": None, "vega": None,
        "iv": None, "source": "ibkr", "live": False, "error": None
    }
    try:
        if not is_market_open():
            result["error"] = "Market closed — no live option data from IBKR"
            return result

        from helm.ibkr import check_connection
        if not check_connection()["connected"]:
            result["error"] = "IBKR not connected"
            return result

        from ib_insync import IB, Option
        ib = IB()
        ib.connect("127.0.0.1", 4002, clientId=12, readonly=True)
        try:
            exp_fmt = expiration.replace("-", "")  # YYYYMMDD
            opt = Option(ticker, exp_fmt, strike,
                         option_type[0].upper(), "SMART", multiplier="100")
            ib.qualifyContracts(opt)
            t = ib.reqMktData(opt, "106", False, False)
            ib.sleep(2)

            def valid(v):
                return v is not None and not math.isnan(v) and v not in (-1, 0)

            if valid(t.bid):  result["bid"] = round(t.bid, 2)
            if valid(t.ask):  result["ask"] = round(t.ask, 2)
            if valid(t.last): result["last"] = round(t.last, 2)
            if result["bid"] and result["ask"]:
                result["mid"] = round((result["bid"] + result["ask"]) / 2, 2)

            if t.modelGreeks:
                g = t.modelGreeks
                if valid(g.delta):  result["delta"] = round(g.delta, 3)
                if valid(g.theta):  result["theta"] = round(g.theta, 2)
                if valid(g.gamma):  result["gamma"] = round(g.gamma, 4)
                if valid(g.vega):   result["vega"]  = round(g.vega, 2)
                if valid(g.impliedVol): result["iv"] = round(g.impliedVol * 100, 1)

            result["live"] = True
        finally:
            ib.disconnect()
    except Exception as e:
        result["error"] = str(e)[:60]
    return result


# ── yfinance data fetch ───────────────────────────────────────────────────────

def fetch_yf_data(ticker: str, expiration: str, strike: float,
                   option_type: str) -> dict:
    """Fetch option and underlying data from yfinance (last session)."""
    result = {
        "underlying": None, "bid": None, "ask": None, "mid": None,
        "iv": None, "volume": None, "oi": None,
        "source": "yfinance", "live": False, "error": None
    }
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        info = tk.fast_info
        spot = getattr(info, "last_price", None)
        if spot:
            result["underlying"] = round(spot, 2)

        # Find the option
        chain = tk.option_chain(expiration)
        df = chain.puts if option_type.upper() == "PUT" else chain.calls
        df["dist"] = (df["strike"] - strike).abs()
        row = df.nsmallest(1, "dist")
        if not row.empty:
            r = row.iloc[0]
            bid = r.get("bid")
            ask = r.get("ask")
            if bid and ask and bid > 0 and ask > 0:
                result["bid"] = round(float(bid), 2)
                result["ask"] = round(float(ask), 2)
                result["mid"] = round((float(bid) + float(ask)) / 2, 2)
            iv = r.get("impliedVolatility")
            if iv and float(iv) > 0:
                result["iv"] = round(float(iv) * 100, 1)
            result["volume"] = int(r.get("volume", 0) or 0)
            result["oi"]     = int(r.get("openInterest", 0) or 0)
    except Exception as e:
        result["error"] = str(e)[:60]
    return result


# ── Health assessment logic ───────────────────────────────────────────────────

def assess_position(pos: dict, legs: list, underlying_price: Optional[float],
                    opt_data: dict, strategy_settings: dict) -> dict:
    """
    Compute health assessment for a position.
    Returns: {flag, flag_style, reasons, pnl_mtm, pnl_pct, intrinsic_buffer}
    """
    strategy = pos["strategy"]
    net_premium = pos["net_premium"] or 0
    flags = []
    reasons = []

    # Find primary option leg
    opt_legs = [l for l in legs if l["option_type"] not in (None, "STOCK")]
    stock_legs = [l for l in legs if l["option_type"] == "STOCK"]
    primary = opt_legs[0] if opt_legs else None

    pnl_mtm = None
    pnl_pct = None
    intrinsic_buffer = None

    if primary:
        strike = primary["strike"]
        direction = primary["direction"]
        opt_type = primary["option_type"]
        contracts = primary["contracts"]
        open_price = primary["open_price"]
        expiration = primary["expiration"]
        days_left = dte(expiration)

        # Mark-to-market P&L
        current_mid = opt_data.get("mid")
        if current_mid is not None:
            if direction == "SHORT":
                # Sold option: P&L = (open_price - current_mid) * contracts * 100
                pnl_mtm = round((open_price - current_mid) * contracts * 100, 2)
            else:
                # Long option: P&L = (current_mid - open_price) * contracts * 100
                pnl_mtm = round((current_mid - open_price) * contracts * 100, 2)
            if net_premium != 0:
                pnl_pct = round((pnl_mtm / abs(net_premium)) * 100, 1)

        # Intrinsic buffer (distance from strike)
        if underlying_price:
            if opt_type == "PUT":
                intrinsic_buffer = round(underlying_price - strike, 2)
            else:  # CALL
                intrinsic_buffer = round(strike - underlying_price, 2)

        # ── GREEN/YELLOW/RED logic ────────────────────────────────────────────
        profit_target_raw = strategy_settings.get("profit_target_pct", 0.50) or 0.50
        profit_target = profit_target_raw * 100 if profit_target_raw <= 1 else profit_target_raw
        dte_exit = strategy_settings.get("dte_exit_threshold", 21) or 21

        if days_left is not None and days_left < 0:
            flags.append("RED")
            reasons.append(f"EXPIRED {abs(days_left)}d ago")
        elif days_left is not None and days_left <= 7:
            flags.append("RED")
            reasons.append(f"Only {days_left} DTE — expiration risk")
        elif days_left is not None and days_left <= dte_exit:
            flags.append("YELLOW")
            reasons.append(f"{days_left} DTE — approaching exit threshold ({dte_exit}d)")

        if pnl_pct is not None:
            if direction == "SHORT" and pnl_pct >= profit_target:
                flags.append("GREEN")
                reasons.append(f"Profit target reached ({pnl_pct:.0f}% of {profit_target:.0f}%)")
            elif direction == "SHORT" and pnl_pct >= 25:
                flags.append("GREEN")
                reasons.append(f"Healthy gain ({pnl_pct:.0f}% of premium)")
            elif direction == "SHORT" and pnl_pct < -50:
                flags.append("RED")
                reasons.append(f"Significantly underwater ({pnl_pct:.0f}%) — consider closing or rolling")
            elif direction == "SHORT" and pnl_pct < -15:
                flags.append("YELLOW")
                reasons.append(f"Position losing ({pnl_pct:.0f}%) — monitor closely")
            elif direction == "LONG" and pnl_pct > 5:
                flags.append("GREEN")
                reasons.append(f"Long position profitable (+{pnl_pct:.0f}%)")
            elif direction == "LONG" and pnl_pct < -50:
                flags.append("RED")
                reasons.append(f"Long position down {pnl_pct:.0f}%")
            elif direction == "LONG" and pnl_pct < -25:
                flags.append("YELLOW")
                reasons.append(f"Long position down {pnl_pct:.0f}%")

        if intrinsic_buffer is not None and direction == "SHORT":
            pct_buffer = (intrinsic_buffer / underlying_price * 100) if underlying_price else 0
            if intrinsic_buffer < 0:
                flags.append("RED")
                reasons.append(f"ITM by ${abs(intrinsic_buffer):.2f} ({abs(pct_buffer):.1f}%)")
            elif pct_buffer < 3:
                flags.append("YELLOW")
                reasons.append(f"Only {pct_buffer:.1f}% buffer to strike")
            elif pct_buffer >= 10:
                flags.append("GREEN")
                reasons.append(f"{pct_buffer:.1f}% buffer to strike — comfortable")

    # Determine final flag (worst case wins)
    if "RED" in flags:
        final_flag = "RED"
        flag_style = "bold red"
    elif "YELLOW" in flags:
        final_flag = "YELLOW"
        flag_style = "bold yellow"
    elif "GREEN" in flags:
        final_flag = "GREEN"
        flag_style = "bold green"
    elif underlying_price is not None:
        # Have data but no specific condition triggered -- position is neutral/monitoring
        final_flag = "YELLOW"
        flag_style = "bold yellow"
        reasons.append("Monitoring — no specific action needed")
    else:
        final_flag = "UNKNOWN"
        flag_style = "dim"
        reasons.append("No market data available")

    return {
        "flag": final_flag,
        "flag_style": flag_style,
        "reasons": reasons,
        "pnl_mtm": pnl_mtm,
        "pnl_pct": pnl_pct,
        "intrinsic_buffer": intrinsic_buffer,
    }


# ── Format helpers ────────────────────────────────────────────────────────────

def fmt_price(v, prefix="$"):
    return f"{prefix}{v:.2f}" if v is not None else "--"

def fmt_pnl(v):
    if v is None: return "--"
    return f"[green]+${v:.0f}[/green]" if v >= 0 else f"[red]-${abs(v):.0f}[/red]"

def fmt_pct(v):
    if v is None: return "--"
    return f"[green]+{v:.1f}%[/green]" if v >= 0 else f"[red]{v:.1f}%[/red]"


# ── Commands ──────────────────────────────────────────────────────────────────

def check_one(pos: dict, legs: list, deep: bool = False) -> dict:
    """Run a full check on a single position. Returns assessment dict."""
    ticker = pos["ticker"]
    strategy = pos["strategy"]
    opt_legs = [l for l in legs if l.get("option_type") not in (None, "STOCK")]
    primary = opt_legs[0] if opt_legs else None

    # Fetch underlying price (IBKR first, yfinance fallback)
    underlying_price = None
    underlying_source = "unknown"
    ibkr_result = fetch_ibkr_underlying(ticker)
    if ibkr_result["price"]:
        underlying_price = ibkr_result["price"]
        underlying_source = "ibkr-live" if ibkr_result["live"] else "ibkr-close"
    else:
        # yfinance fallback
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).fast_info
            spot = getattr(info, "last_price", None)
            if spot:
                underlying_price = round(spot, 2)
                underlying_source = "yfinance"
        except Exception:
            pass

    # Fetch option data
    opt_data = {}
    opt_source = "none"
    if primary:
        expiration = primary["expiration"]
        strike = primary["strike"]
        option_type = primary["option_type"]

        # Try IBKR live first (only useful during market hours)
        if is_market_open():
            ibkr_opt = fetch_ibkr_option(ticker, expiration, strike, option_type)
            if ibkr_opt.get("mid"):
                opt_data = ibkr_opt
                opt_source = "ibkr-live"

        # Fall back to yfinance
        if not opt_data.get("mid"):
            yf_data = fetch_yf_data(ticker, expiration, strike, option_type)
            if yf_data.get("mid"):
                opt_data = yf_data
                opt_source = "yfinance"

    # Get strategy settings
    conn = get_conn()
    account_id = get_active_account()
    settings_row = conn.execute(
        "SELECT * FROM strategy_settings WHERE account_id = ? AND strategy = ?",
        (account_id, strategy)
    ).fetchone()
    conn.close()
    strategy_settings = dict(settings_row) if settings_row else {}

    # Run assessment
    assessment = assess_position(pos, legs, underlying_price, opt_data, strategy_settings)
    assessment.update({
        "ticker": ticker,
        "strategy": strategy,
        "underlying_price": underlying_price,
        "underlying_source": underlying_source,
        "opt_data": opt_data,
        "opt_source": opt_source,
        "primary_leg": primary,
        "pos": pos,
        "legs": legs,
    })
    return assessment


def cmd_check_all(args):
    """Check all open positions — summary table."""
    conn = get_conn()
    account_id = get_active_account()
    positions = [dict(r) for r in conn.execute(
        "SELECT * FROM positions WHERE account_id = ? AND status = 'OPEN' ORDER BY strategy, ticker",
        (account_id,)
    ).fetchall()]
    conn.close()

    if not positions:
        console.print("[yellow]No open positions.[/yellow]")
        return

    console.print()
    console.print(Panel.fit(
        f"[bold cyan]HELM Check[/bold cyan] — {len(positions)} open position(s)\n"
        f"[dim]Data: {market_status_label()}[/dim]",
        border_style="cyan"
    ))
    console.print()

    t = Table(box=box.SIMPLE_HEAD, show_header=True, padding=(0,1),
              width=170)
    t.add_column("",           width=3,  no_wrap=True)
    t.add_column("Ticker",     style="bold cyan", width=7, no_wrap=True)
    t.add_column("Strategy",   width=14, no_wrap=True)
    t.add_column("Strike",     justify="right", width=8, no_wrap=True)
    t.add_column("DTE",        justify="right", width=5, no_wrap=True)
    t.add_column("Underlying", justify="right", width=11, no_wrap=True)
    t.add_column("Buffer",     justify="right", width=9, no_wrap=True)
    t.add_column("P&L",        justify="right", width=9, no_wrap=True)
    t.add_column("P&L%",       justify="right", width=8, no_wrap=True)
    t.add_column("Assessment", width=50, no_wrap=True)

    counts = {"GREEN": 0, "YELLOW": 0, "RED": 0, "UNKNOWN": 0}

    for pos in positions:
        conn = get_conn()
        legs = [dict(r) for r in conn.execute(
            "SELECT * FROM legs WHERE position_id = ?", (pos["id"],)
        ).fetchall()]
        conn.close()

        console.print(f"[dim]Checking {pos['ticker']}...[/dim]", end="\r")
        a = check_one(pos, legs)
        counts[a["flag"]] = counts.get(a["flag"], 0) + 1

        flag_icons = {"GREEN": "[green]●[/green]", "YELLOW": "[yellow]●[/yellow]",
                      "RED": "[red]●[/red]", "UNKNOWN": "[dim]?[/dim]"}
        flag_str = flag_icons.get(a["flag"], "?")

        primary = a["primary_leg"]
        strike_str = f"${primary['strike']:.0f}" if primary else "--"
        exp_str = primary["expiration"][5:] if primary else "--"
        days = dte(primary["expiration"]) if primary else None
        dte_str = f"{days}d" if days is not None else "--"
        dte_color = "red" if (days is not None and days <= 7) else                     "yellow" if (days is not None and days <= 21) else "green"
        dte_fmt = f"[{dte_color}]{dte_str}[/{dte_color}]"

        underlying_str = fmt_price(a["underlying_price"])
        buffer_str = f"${a['intrinsic_buffer']:.1f}" if a["intrinsic_buffer"] is not None else "--"
        if a["intrinsic_buffer"] is not None:
            buf_color = "red" if a["intrinsic_buffer"] < 0 else                         "yellow" if a["intrinsic_buffer"] < a["underlying_price"] * 0.03 else "green"
            buffer_str = f"[{buf_color}]{buffer_str}[/{buf_color}]"

        pnl_str = fmt_pnl(a["pnl_mtm"])
        pct_str = fmt_pct(a["pnl_pct"])
        reason = a["reasons"][0] if a["reasons"] else "--"

        t.add_row(
            flag_str, pos["ticker"], pos["strategy"],
            strike_str, dte_fmt,
            underlying_str, buffer_str,
            pnl_str, pct_str, reason
        )

    console.print(t)
    console.print()

    # Summary
    summary = f"[green]● {counts['GREEN']} GREEN[/green]  "               f"[yellow]● {counts['YELLOW']} YELLOW[/yellow]  "               f"[red]● {counts['RED']} RED[/red]"
    console.print(f"  {summary}")
    console.print()


def cmd_check_one(ticker: str, deep: bool = False):
    """Deep check on a single position."""
    conn = get_conn()
    account_id = get_active_account()
    pos = conn.execute(
        "SELECT * FROM positions WHERE account_id = ? AND ticker = ? AND status = 'OPEN' ORDER BY opened_at DESC LIMIT 1",
        (account_id, ticker.upper())
    ).fetchone()
    if not pos:
        console.print(f"[yellow]No open position found for {ticker}[/yellow]")
        conn.close()
        return
    pos = dict(pos)
    legs = [dict(r) for r in conn.execute(
        "SELECT * FROM legs WHERE position_id = ?", (pos["id"],)
    ).fetchall()]
    conn.close()

    console.print()
    console.print(f"[dim]Checking {ticker}...[/dim]")
    a = check_one(pos, legs, deep=deep)
    primary = a["primary_leg"]

    flag_colors = {"GREEN": "green", "YELLOW": "yellow", "RED": "red", "UNKNOWN": "dim"}
    flag_color = flag_colors.get(a["flag"], "dim")

    lines = [
        f"[bold cyan]{ticker}[/bold cyan]  {pos['strategy']}",
        "",
    ]

    if primary:
        days = dte(primary["expiration"])
        lines += [
            f"Strike:     ${primary['strike']:.0f}  {primary['expiration']}  ({days}d)",
            f"Direction:  {primary['direction']}  x{primary['contracts']} contracts",
            f"Opened at:  ${primary['open_price']:.2f}  (net premium: ${abs(pos['net_premium'] or 0):.0f})",
        ]

    lines += ["", "── Market Data " + "─"*40]
    lines.append(f"Underlying: ${a['underlying_price']:.2f}  [dim]({a['underlying_source']})[/dim]" if a["underlying_price"] else "Underlying: --")

    opt = a["opt_data"]
    if opt.get("bid") and opt.get("ask"):
        lines.append(f"Option:     bid ${opt['bid']:.2f}  ask ${opt['ask']:.2f}  mid ${opt['mid']:.2f}  [dim]({a['opt_source']})[/dim]")
    elif opt.get("mid"):
        lines.append(f"Option mid: ${opt['mid']:.2f}  [dim]({a['opt_source']})[/dim]")
    else:
        lines.append(f"Option:     no data  [dim]({a['opt_source']})[/dim]")

    if opt.get("iv"):
        lines.append(f"IV:         {opt['iv']:.1f}%")

    if deep and any(opt.get(g) for g in ["delta","theta","gamma","vega"]):
        lines.append(f"Delta:  {opt.get('delta','--')}   Theta: {opt.get('theta','--')}/day")
        lines.append(f"Gamma:  {opt.get('gamma','--')}   Vega:  {opt.get('vega','--')}")

    lines += ["", "── P&L " + "─"*46]
    if a["pnl_mtm"] is not None:
        sign = "+" if a["pnl_mtm"] >= 0 else ""
        lines.append(f"Mark-to-market: {sign}${a['pnl_mtm']:.0f}  ({sign}{a['pnl_pct']:.1f}% of premium)")
    else:
        lines.append("Mark-to-market: -- (no option price data)")

    if a["intrinsic_buffer"] is not None and a["underlying_price"]:
        pct_buf = a["intrinsic_buffer"] / a["underlying_price"] * 100
        itm_otm = "OTM" if a["intrinsic_buffer"] >= 0 else "ITM"
        buf_val = abs(a['intrinsic_buffer'])
        lines.append(f"Buffer to strike: ${buf_val:.2f} ({abs(pct_buf):.1f}%) {itm_otm}")

    lines += ["", "── Assessment " + "─"*40]
    lines.append(f"[{flag_color}]● {a['flag']}[/{flag_color}]")
    for reason in a["reasons"]:
        lines.append(f"  • {reason}")

    console.print(Panel(
        "\n".join(lines),
        title=f"{ticker} Health Check",
        border_style=flag_color
    ))
    console.print()


def run():
    args = sys.argv[1:]

    if not get_active_account():
        console.print("[red]No active account. Run helm setup first.[/red]")
        return

    if not args:
        cmd_check_all([])
        return

    if args[0] in ("--help", "-h"):
        console.print("[bold]Usage:[/bold]  helm check [ticker] [--deep]")
        return

    deep = "--deep" in args
    tickers = [a for a in args if not a.startswith("--")]

    if tickers:
        for ticker in tickers:
            cmd_check_one(ticker, deep=deep)
    else:
        cmd_check_all(args)


if __name__ == "__main__":
    run()
