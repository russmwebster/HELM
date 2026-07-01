
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
try:
    from helm.models.theme import log_event as _log_event, check_nudges as _check_nudges
except Exception:
    _log_event = lambda *a, **k: None
    _check_nudges = lambda: []

import logging
from pathlib import Path
from datetime import date, datetime, time
from typing import Optional
import math
from types import SimpleNamespace
from helm.decision import evaluate as _core_evaluate, DEFAULT_STOP_MULT
from helm.models.settings import StrategySettings

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
from helm.db import get_conn, book_filter
from helm.dates import dte

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





def _persist_real_leg_marks(conn, check_id, position_id, leg_marks_by_id):
    # HELM-041: one GOOD leg_checks row per leg on the REAL book. Sibling to
    # paper_manage._persist_leg_marks; here check_id is populated (the paper
    # writer writes NULL). All-or-nothing live gate mirrors the paper writer's
    # book_live contract: rows are written only when every leg has a live mark.
    # Runs inside save_check's _tx() and does NOT commit -- the parent
    # transaction owns the commit, so parent + child rows stay atomic.
    import uuid as _uuid
    from datetime import datetime
    if not leg_marks_by_id:
        return
    for _m in leg_marks_by_id:
        if (not _m.get("leg_id") or _m.get("current_price") is None
                or not _m.get("is_live")):
            return
    _now = datetime.now().isoformat()
    _seen = set()
    for _m in leg_marks_by_id:
        _lid = _m["leg_id"]
        if _lid in _seen:
            continue
        _seen.add(_lid)
        conn.execute(
            "INSERT INTO leg_checks "
            "(id, check_id, position_id, leg_id, checked_at, "
            "current_price, data_quality, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'GOOD', ?)",
            ("LCHK-" + _uuid.uuid4().hex[:8].upper(),
             check_id, position_id, _lid, _now, _m["current_price"], _now),
        )


def save_check(position_id: str, assessment: dict, pos: dict, leg_marks_by_id: Optional[list] = None) -> None:
    """Save a check run to the checks table with full IVR, RTH flag, and narrative."""
    import uuid as _uuid
    from helm.db import transaction as _tx
    from datetime import time as _time

    a = assessment
    primary = a.get("primary_leg") or {}
    opt = a.get("opt_data") or {}
    spot = a.get("underlying_price")
    source = a.get("opt_source", "unknown")

    # RTH detection
    _now_time = datetime.now().time()
    rth_flag = "RTH" if _time(9, 30) <= _now_time <= _time(16, 0) else "OUTSIDE_RTH"

    # Entry snapshot for comparison
    entry_delta = entry_iv = entry_spot = None
    try:
        from helm.db import get_conn as _gc
        _c = _gc()
        snap = _c.execute(
            "SELECT * FROM entry_snapshots WHERE position_id=? ORDER BY created_at DESC LIMIT 1",
            (position_id,)
        ).fetchone()
        if snap:
            entry_delta = snap["delta"]
            entry_iv    = snap["iv_current"]
            entry_spot  = snap["spot_price"]
        _c.close()
    except Exception:
        pass

    # IVR from iv_history
    iv_rank = iv_percentile = None
    try:
        from helm.models.iv_history import IVHistory as _IVH
        _ivr = _IVH.latest(pos.get("ticker", ""))
        if _ivr:
            iv_rank      = _ivr.iv_rank
            iv_percentile = _ivr.iv_percentile
    except Exception:
        pass

    # Deltas
    delta_vs_entry = iv_vs_entry = spot_pct_change = None
    try:
        d_now = opt.get("delta")
        if d_now is not None and entry_delta is not None:
            delta_vs_entry = round(float(d_now) - float(entry_delta), 4)
        iv_now = opt.get("iv")
        if iv_now is not None and entry_iv is not None:
            iv_vs_entry = round(float(iv_now) - float(entry_iv), 2)
        if spot is not None and entry_spot is not None:
            spot_pct_change = round((float(spot) - float(entry_spot)) / float(entry_spot) * 100, 2)
    except Exception:
        pass

    # Days open
    days_open = None
    try:
        opened = pos.get("opened_at", "")[:10]
        if opened:
            days_open = (date.today() - date.fromisoformat(opened)).days
    except Exception:
        pass

    # P&L pct
    pnl_pct = buffer_pct = None
    try:
        pnl = a.get("pnl_mtm")
        premium = pos.get("net_premium") or pos.get("premium_collected") or (primary.get("open_price", 0) * 100)
        if pnl is not None and premium:
            pnl_pct = round(float(pnl) / abs(float(premium)) * 100, 1)
    except Exception:
        pass

    # Buffer
    buf = a.get("intrinsic_buffer")
    try:
        buf_pct_val = round(buf / spot * 100, 2) if (buf is not None and spot) else buffer_pct
    except Exception:
        buf_pct_val = a.get("buffer_pct")

    # Health flag
    # Use flag directly from assessment (health_score key is not populated)
    flag = a.get("flag") or a.get("health_flag") or a.get("health_score")
    if flag in ("GREEN", "YELLOW", "RED"):
        pass  # already a valid flag string
    elif isinstance(flag, (int, float)):
        flag = "GREEN" if flag >= 70 else ("RED" if flag < 40 else "YELLOW")
    else:
        flag = "YELLOW"  # default to monitor, not RED
    action_signal = "HOLD" if flag == "GREEN" else ("CLOSE" if flag == "RED" else "WATCH")

    # DTE and narrative
    days_left = dte(primary.get("expiration")) if primary.get("expiration") else None
    days_to_earnings = a.get("days_to_earnings") or primary.get("days_to_earnings")
    narrative = a.get("narrative") or a.get("claude_narrative")

    check_id = "CHK-" + _uuid.uuid4().hex[:8].upper()
    now = datetime.now().isoformat()
    has_option_data = opt.get("bid") is not None or opt.get("mid") is not None
    dq = "GOOD" if ("live" in source and has_option_data) else "PARTIAL"
    # HELM-037 live-only persistence gate: only GOOD (live + complete) marks are
    # written. Frozen / partial / yfinance reads are still computed and displayed
    # (the caller renders the returned assessment) but never persisted, keeping the
    # checks table a clean live record. Skipping the INSERT here has no display effect.
    if dq != "GOOD":
        return

    try:
        with _tx() as conn:
            conn.execute("""
                INSERT INTO checks (
                    id, position_id, checked_at,
                    spot_price, dte_now, days_open, days_to_earnings,
                    current_bid, current_ask, current_price,
                    delta, gamma, theta, vega, iv_current,
                    iv_rank, iv_percentile,
                    delta_vs_entry, iv_vs_entry, spot_pct_change,
                    pnl_unrealized, pnl_pct,
                    health_flag, action_signal,
                    greeks_source, data_quality, rth_flag,
                    buffer_dollars, buffer_pct,
                    narrative,
                    created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                check_id, position_id, now,
                spot, days_left, days_open, days_to_earnings,
                opt.get("bid"), opt.get("ask"), opt.get("mid"),
                opt.get("delta"), opt.get("gamma"), opt.get("theta"), opt.get("vega"),
                opt.get("iv"),
                iv_rank, iv_percentile,
                delta_vs_entry, iv_vs_entry, spot_pct_change,
                a.get("pnl_mtm"), pnl_pct,
                flag, action_signal,
                source, dq, rth_flag,
                buf, buf_pct_val,
                narrative,
                now,
            ))
            _persist_real_leg_marks(conn, check_id, position_id, leg_marks_by_id)
    except Exception:
        import traceback; traceback.print_exc()



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

        from helm.ibkr import check_connection
        if not check_connection()["connected"]:
            result["error"] = "IBKR not connected"
            return result

        from ib_insync import IB, Option
        ib = IB()
        ib.connect("127.0.0.1", 4002, clientId=13, readonly=True)
        try:
            exp_fmt = expiration.replace("-", "")  # YYYYMMDD
            opt = Option(ticker, exp_fmt, strike,
                         option_type[0].upper(), "SMART", multiplier="100")
            ib.qualifyContracts(opt)
            ib.reqMarketDataType(1 if is_market_open() else 2)  # 2=frozen — last close data, requires subscription
            t = ib.reqMktData(opt, "106", False, False)
            for _ in range(8):
                ib.sleep(1)
                _g = t.modelGreeks
                if _g and _g.delta is not None and not math.isnan(_g.delta):
                    break

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
            if bid and ask and float(bid) > 0 and float(ask) > 0:
                result["bid"] = round(float(bid), 2)
                result["ask"] = round(float(ask), 2)
                result["mid"] = round((float(bid) + float(ask)) / 2, 2)
            else:
                # Outside market hours bid/ask may be 0 — use lastPrice as mid
                last = r.get("lastPrice") or r.get("last")
                if last and float(last) > 0:
                    result["mid"] = round(float(last), 2)
            iv = r.get("impliedVolatility")
            if iv and float(iv) > 0:
                result["iv"] = round(float(iv) * 100, 1)
            result["volume"] = int(r.get("volume", 0) or 0)
            result["oi"]     = int(r.get("openInterest", 0) or 0)
    except Exception as e:
        result["error"] = str(e)[:60]
    return result


# ── Health assessment logic ───────────────────────────────────────────────────


from helm.verdict import band_for, _ns_pos, _ns_leg


def core_verdict(pos, legs, opt_legs, primary, opt_data, leg_marks):
    """Decision-core verdict for a check-side position, or None if the core
    can't be applied (stock leg present, or any option leg unmarked) -- mirrors
    paper_manage's gate so we never emit a verdict off incomplete marks.
    Marks are derived from prices check_one already fetched (opt_data['mid'] for
    the primary, leg_marks for the rest), so core P&L agrees with displayed P&L.
    Returns {flag, reason, core_pnl}."""
    if not opt_legs:
        return None
    if any(l.get("option_type") in (None, "STOCK") for l in legs):
        return None  # covered/PMCC stock leg: deferred (COVERED_CALL gradeability)
    marks = {}
    if primary is not None and opt_data.get("mid") is not None:
        marks[primary["id"]] = opt_data["mid"]
    for lg in opt_legs:
        if lg["id"] in marks:
            continue
        key = (lg.get("option_type"), lg.get("strike"))
        if key in leg_marks:
            marks[lg["id"]] = leg_marks[key]
    if any(lg["id"] not in marks for lg in opt_legs):
        return None  # incomplete marks -> no verdict (mirrors paper_manage)
    reason, total_pnl = _core_evaluate(
        _ns_pos(pos), [_ns_leg(l) for l in opt_legs], marks
    )
    return {"reason": reason, "core_pnl": total_pnl}


def assess_position(pos: dict, legs: list, underlying_price: Optional[float],
                    opt_data: dict, strategy_settings: dict,
                    leg_marks: Optional[dict] = None,
                    mark_confidence: str = "live") -> dict:
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
    is_multileg = len(opt_legs) > 1

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

        # For multi-leg credit structures the net position behaves like a short-
        # premium trade (profit as value decays). Derive the signal's direction
        # from net_premium so it does not depend on which leg is opt_legs[0].
        if is_multileg:
            flag_direction = "SHORT" if net_premium > 0 else "LONG"
        else:
            flag_direction = direction

        # Mark-to-market P&L
        if is_multileg:
            # HELM-018: mark EVERY leg and sum per-leg P&L. Pricing only the
            # primary leg counts one leg's decay as the whole position's P&L.
            marks = leg_marks or {}
            _total = 0.0
            _marked_all = True
            for _l in opt_legs:
                _m = marks.get((_l["option_type"], _l["strike"]))
                if _m is None:
                    _marked_all = False
                    break
                if _l["direction"] == "SHORT":
                    _total += (_l["open_price"] - _m) * _l["contracts"] * 100
                else:
                    _total += (_m - _l["open_price"]) * _l["contracts"] * 100
            if _marked_all:
                pnl_mtm = round(_total, 2)
                if net_premium != 0:
                    pnl_pct = round((pnl_mtm / abs(net_premium)) * 100, 1)
        else:
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

        # Intrinsic buffer (distance from strike) -- single-leg only; a condor's
        # one-wing buffer is misleading (deep view shows both wings).
        if underlying_price and not is_multileg:
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

        if pnl_pct is not None and mark_confidence != "live":
            # HELM-019: frozen/stale marks must not drive an actionable
            # profit-target/stop signal. Show the number, cap at YELLOW,
            # tell the trader to confirm at RTH.
            flags.append("YELLOW")
            reasons.append(f"Frozen/stale marks ({mark_confidence}) — P&L {pnl_pct:+.0f}% unverified, confirm at RTH")
        elif pnl_pct is not None:
            if flag_direction == "SHORT" and pnl_pct >= profit_target:
                flags.append("GREEN")
                reasons.append(f"Profit target reached ({pnl_pct:.0f}% of {profit_target:.0f}%)")
            elif flag_direction == "SHORT" and pnl_pct >= 25:
                flags.append("GREEN")
                reasons.append(f"Healthy gain ({pnl_pct:.0f}% of premium)")
            elif flag_direction == "SHORT" and pnl_pct < -50:
                flags.append("RED")
                reasons.append(f"Significantly underwater ({pnl_pct:.0f}%) — consider closing or rolling")
            elif flag_direction == "SHORT" and pnl_pct < -15:
                flags.append("YELLOW")
                reasons.append(f"Position losing ({pnl_pct:.0f}%) — monitor closely")
            elif flag_direction == "LONG" and pnl_pct > 5:
                flags.append("GREEN")
                reasons.append(f"Long position profitable (+{pnl_pct:.0f}%)")
            elif flag_direction == "LONG" and pnl_pct < -50:
                flags.append("RED")
                reasons.append(f"Long position down {pnl_pct:.0f}%")
            elif flag_direction == "LONG" and pnl_pct < -25:
                flags.append("YELLOW")
                reasons.append(f"Long position down {pnl_pct:.0f}%")

        if intrinsic_buffer is not None and flag_direction == "SHORT" and not is_multileg:
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

        # Try IBKR first (frozen greeks available even when market closed)
        ibkr_opt = fetch_ibkr_option(ticker, expiration, strike, option_type)
        if ibkr_opt.get("mid") or ibkr_opt.get("delta") is not None:
            opt_data = ibkr_opt
            opt_source = "ibkr-live" if is_market_open() else "ibkr-frozen"

        # Fall back to yfinance for price; preserve IBKR greeks if present
        if not opt_data.get("mid"):
            yf_data = fetch_yf_data(ticker, expiration, strike, option_type)
            if yf_data.get("mid"):
                if opt_data.get("delta") is not None:
                    for _k in ("mid", "bid", "ask", "last"):
                        if yf_data.get(_k) is not None:
                            opt_data[_k] = yf_data[_k]
                    opt_source = "ibkr-greeks+yf-price"
                else:
                    opt_data = yf_data
                    opt_source = "yfinance"

    # HELM-018: mark every leg for multi-leg P&L. The single opt_data above only
    # prices the primary leg; netting all legs needs a quote per leg.
    leg_marks = {}
    # HELM-041: sibling per-leg store keyed by leg_id, carrying liveness, for
    # the real-book leg_checks writer. Does NOT alter the (option_type, strike)
    # -> mid netting dict above. Primary is seeded from opt_data/opt_source;
    # non-primary legs are captured inside the existing fetch loop (no second
    # IBKR round-trip). Liveness == a market-open live IBKR mark, matching the
    # opt_source == "ibkr-live" semantics used for the primary leg.
    leg_marks_by_id = []
    if primary is not None:
        leg_marks_by_id.append({
            "leg_id": primary.get("id"),
            "current_price": opt_data.get("mid"),
            "is_live": (opt_source == "ibkr-live"),
        })
    if len(opt_legs) > 1:
        if primary is not None and opt_data.get("mid") is not None:
            leg_marks[(primary["option_type"], primary["strike"])] = opt_data.get("mid")
        for _lg in opt_legs:
            _key = (_lg["option_type"], _lg["strike"])
            if _key in leg_marks:
                continue
            _q = fetch_ibkr_option(ticker, _lg["expiration"], _lg["strike"], _lg["option_type"])
            _mid = _q.get("mid")
            _leg_live = bool(is_market_open() and _mid is not None)
            if _mid is None:
                _yq = fetch_yf_data(ticker, _lg["expiration"], _lg["strike"], _lg["option_type"])
                _mid = _yq.get("mid")
            leg_marks[_key] = _mid
            if _lg.get("id") != (primary.get("id") if primary is not None else None):
                leg_marks_by_id.append({
                    "leg_id": _lg.get("id"),
                    "current_price": _mid,
                    "is_live": _leg_live,
                })

    # Get strategy settings
    conn = get_conn()
    account_id = get_active_account()
    settings_row = conn.execute(
        "SELECT * FROM strategy_settings WHERE account_id = ? AND strategy = ?",
        (account_id, strategy)
    ).fetchone()
    conn.close()
    strategy_settings = dict(settings_row) if settings_row else {}

    # HELM-019: classify mark freshness from the primary leg's opt_source as a
    # market-state proxy (per-leg weakest-link is a logged deferral).
    if opt_source == "ibkr-live":
        mark_confidence = "live"
    elif opt_source == "ibkr-frozen":
        mark_confidence = "frozen"
    else:
        mark_confidence = "stale"

    # Run assessment
    assessment = assess_position(pos, legs, underlying_price, opt_data, strategy_settings, leg_marks=leg_marks, mark_confidence=mark_confidence)
    assessment.update({
        "ticker": ticker,
        "strategy": strategy,
        "underlying_price": underlying_price,
        "underlying_source": underlying_source,
        "opt_data": opt_data,
        "opt_source": opt_source,
        "mark_confidence": mark_confidence,
        "primary_leg": primary,
        "pos": pos,
        "legs": legs,
    })

    # WS4: decision-core verdict (additive; legacy flag retained for diff).
    # Guarded: a core failure must never break the legacy check (zero-regression).
    try:
        _cv = core_verdict(pos, legs, opt_legs, primary, opt_data, leg_marks)
        if _cv is not None:
            assessment["core_reason"] = _cv["reason"]
            assessment["core_pnl"] = _cv["core_pnl"]
            _ib = assessment.get("intrinsic_buffer")
            _ev = {
                "pnl_pct": assessment.get("pnl_pct"),
                "intrinsic_buffer": _ib,
                "pct_buffer": (_ib / underlying_price * 100)
                              if _ib is not None and underlying_price else None,
                "mark_confidence": mark_confidence,
                "direction": (primary or {}).get("direction"),
                "is_multileg": len(opt_legs) > 1,
            }
            _b = band_for(_cv["reason"], _ev)
            assessment["flag"] = _b["flag"]
            assessment["flag_style"] = _b["flag_style"]
            assessment["reasons"] = [_b["headline"]] + assessment.get("reasons", [])
    except Exception as _e:
        logging.getLogger("helm.check").warning(
            "core_verdict failed for %s: %s", pos.get("ticker"), _e
        )

    # Save check to DB silently
    save_check(pos["id"], assessment, pos, leg_marks_by_id)

    return assessment


def cmd_check_all(args):
    """Check all open positions — summary table."""
    conn = get_conn()
    account_id = get_active_account()
    bc, bp = book_filter(args)
    positions = [dict(r) for r in conn.execute(
        "SELECT * FROM positions WHERE account_id = ? AND status = 'OPEN'" + bc + " ORDER BY strategy, ticker",
        (account_id, *bp)
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
    t.add_column("Buf%",       justify="right", width=6, no_wrap=True)
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
        buf_pct_str = "--"  # default, overwritten below if buffer exists
        if a["intrinsic_buffer"] is not None:
            buf_color = "red" if a["intrinsic_buffer"] < 0 else                         "yellow" if a["intrinsic_buffer"] < a["underlying_price"] * 0.03 else "green"
            buffer_str = f"[{buf_color}]{buffer_str}[/{buf_color}]"
            buf_pct_v = a["intrinsic_buffer"] / a["underlying_price"] * 100 if a["underlying_price"] else None
            _bpc = "green" if (buf_pct_v or 0) > 10 else ("yellow" if (buf_pct_v or 0) > 5 else "red")
            buf_pct_str = f"[{_bpc}]{buf_pct_v:.1f}%[/{_bpc}]"

        pnl_str = fmt_pnl(a["pnl_mtm"])
        pct_str = fmt_pct(a["pnl_pct"])
        reason = a["reasons"][0] if a["reasons"] else "--"

        _co = (pos.get("company_name") or "")[:14]
        _tk_display = (f"{pos['ticker']}  [dim]{_co}[/dim]") if _co else pos["ticker"]
        t.add_row(
            flag_str, _tk_display, pos["strategy"],
            strike_str, dte_fmt,
            underlying_str, buffer_str, buf_pct_str,
            pnl_str, pct_str, reason
        )

    console.print(t)
    console.print()

    # Summary
    summary = f"[green]● {counts['GREEN']} GREEN[/green]  "               f"[yellow]● {counts['YELLOW']} YELLOW[/yellow]  "               f"[red]● {counts['RED']} RED[/red]"
    console.print(f"  {summary}")
    console.print()




def render_csp_position_diagram(spot, strike, open_price, atr, net_premium):
    if not all([spot, strike, open_price]): return
    breakeven = round(strike - open_price, 2)
    atr1 = round(spot - atr, 2) if atr else None
    low  = min(breakeven * 0.96, spot * 0.91)
    high = max(spot * 1.05, strike * 1.10)
    rng  = high - low
    if rng <= 0: return
    W = 54
    def px(p): return max(0, min(W-1, int((p-low)/rng*(W-1))))
    be_p=px(breakeven); st_p=px(strike); sp_p=px(spot)
    a1_p=px(atr1) if atr1 else None
    line = '  '
    for i in range(W):
        pr = low+(i/(W-1))*rng
        if i==be_p: line += '[red bold]▲[/red bold]'
        elif i==st_p: line += '[bold]|[/bold]'
        elif i==sp_p: line += '[green bold]●[/green bold]'
        elif a1_p and i==a1_p: line += '[dim].[/dim]'
        elif pr < breakeven: line += '[red]─[/red]'
        elif pr < strike:    line += '[yellow]─[/yellow]'
        else:                line += '[green]─[/green]'
    console.print()
    console.print('  [bold dim]Position map[/bold dim]')
    console.print()
    console.print(line)
    console.print()
    # Label row — sorted, non-overlapping
    items = [(be_p,'b/e'),(st_p,'strike'),(sp_p,'now')]
    if a1_p is not None: items.append((a1_p,'1-ATR'))
    items = sorted(items, key=lambda x:x[0])
    lbs = list(' '*(W+12))
    cursor = 0
    for p,t in items:
        start = max(cursor, p - len(t)//2)
        start = min(start, W+12-len(t))
        for j,c in enumerate(t):
            if start+j < len(lbs): lbs[start+j]=c
        cursor = start + len(t) + 2
    console.print('  '+''.join(lbs))
    console.print()
    bv=round(spot-breakeven,2); bvp=round(bv/spot*100,1) if spot else 0
    sv=round(spot-strike,2);   svp=round(sv/spot*100,1) if spot else 0
    bc='green' if bvp>10 else 'yellow' if bvp>5 else 'red'
    console.print(f'  Buffer to b/e  [{bc}]${bv:.2f} ({bvp:.1f}%)[/{bc}]   Buffer to strike ${sv:.2f} ({svp:.1f}%)')
    if atr1:
        note='[green]above 1-ATR ✓[/green]' if spot>atr1 else '[yellow]approaching 1-ATR[/yellow]'
        console.print(f'  1-ATR: ${atr1:.2f}   Spot is {note}')
    console.print()


def cmd_check_deep_csp(pos: dict, legs: list, assessment: dict, snap: dict):
    """
    Deep narrative check for a single position.
    Shows full context: entry vs now, Greeks comparison, guidance.
    """
    ticker   = pos["ticker"]
    strategy = pos["strategy"]
    a        = assessment
    primary  = a.get("primary_leg") or {}
    opt      = a.get("opt_data") or {}
    spot     = a.get("underlying_price")
    flag     = a.get("flag", "UNKNOWN")
    pnl_mtm  = a.get("pnl_mtm")
    pnl_pct  = a.get("pnl_pct")

    flag_colors = {"GREEN": "green", "YELLOW": "yellow", "RED": "red", "UNKNOWN": "dim"}
    flag_color  = flag_colors.get(flag, "dim")

    # Position basics
    strike     = primary.get("strike") or 0
    expiration = primary.get("expiration") or ""
    direction  = primary.get("direction") or ""
    opt_type   = primary.get("option_type") or ""
    contracts  = primary.get("contracts") or 0
    open_price = primary.get("open_price") or 0
    net_premium = pos.get("net_premium") or 0

    days_left  = dte(expiration) if expiration else None
    opened_at  = pos.get("opened_at", "")[:10]
    try:
        days_held = (date.today() - date.fromisoformat(opened_at)).days
    except Exception:
        days_held = None

    # Entry snapshot comparisons
    entry_spot  = snap.get("spot_price")
    entry_iv    = snap.get("iv_current")
    entry_delta = snap.get("delta")
    entry_rsi   = snap.get("rsi")
    entry_bias  = snap.get("bias_score")

    # Current Greeks
    iv_now    = opt.get("iv")
    delta_now = opt.get("delta")
    theta_now = opt.get("theta")

    # ATR for context
    atr = None
    try:
        import yfinance as yf, warnings
        warnings.filterwarnings("ignore")
        import pandas as pd
        hist = yf.Ticker(ticker).history(period="30d")
        if not hist.empty:
            high_low = hist["High"] - hist["Low"]
            atr = round(float(high_low.rolling(14).mean().iloc[-1]), 2)
    except Exception:
        pass

    # ── Header ────────────────────────────────────────────────────────────────
    leg_str = f"{opt_type[0] if opt_type else '?'}{strike:.0f} {expiration[5:] if expiration else ''}"
    held_str = f"{days_held}d held" if days_held is not None else ""
    dte_str  = f"{days_left}d remaining" if days_left is not None else ""

    console.print()
    console.print(Panel.fit(
        f"[bold cyan]{ticker}[/bold cyan]  "
        f"[dim]{strategy}  {direction} {leg_str}  x{contracts}[/dim]\n"
        f"[dim]Opened {opened_at}  |  {held_str}  |  {dte_str}[/dim]",
        title=f"{ticker} Deep Check",
        border_style=flag_color
    ))

    # ── Market Data ───────────────────────────────────────────────────────────
    console.print()
    console.print(f"  [bold]Market Data[/bold]")

    spot_str = f"${spot:.2f}  ({a.get('underlying_source', '')})" if spot else "--"
    console.print(f"  Underlying:  {spot_str}")

    if opt.get("mid"):
        bid = opt.get("bid") or 0
        ask = opt.get("ask") or 0
        console.print(f"  Option mid:  ${opt['mid']:.2f}   bid ${bid:.2f}  ask ${ask:.2f}  ({a.get('opt_source', '')})")
    else:
        console.print(f"  Option:      [dim]no data[/dim]")

    # IV comparison
    if iv_now is not None:
        if entry_iv:
            iv_chg = round(iv_now - entry_iv, 1)
            iv_dir = "[green]↓[/green]" if iv_chg < 0 else "[red]↑[/red]" if iv_chg > 0 else "→"
            iv_str = f"{iv_now:.1f}%   (was {entry_iv:.1f}% at entry  {iv_dir}  {iv_chg:+.1f}%)"
        else:
            iv_str = f"{iv_now:.1f}%"
        console.print(f"  IV:          {iv_str}")
    try:
        from helm.models.iv_history import IVHistory as _IVH
        _ivr = _IVH.for_tickers([ticker]).get(ticker)
    except Exception:
        _ivr = None
    if _ivr and _ivr.iv_rank is not None:
        console.print(f"  IVR/IVP:     {_ivr.rank_label}[dim] rank[/dim]  /  {_ivr.percentile_label}[dim] pct  (52wk: {_ivr.iv_52wk_low:.0f}%-{_ivr.iv_52wk_high:.0f}%)[/dim]")

    # Delta comparison
    if delta_now is not None:
        if entry_delta:
            delta_chg = round(abs(delta_now) - abs(entry_delta), 3)
            if direction == "SHORT":
                delta_dir = "[green]↓[/green]" if delta_chg < 0 else "[red]↑[/red]" if delta_chg > 0 else "→"
                delta_note = "improved" if delta_chg < 0 else "deteriorated"
            else:
                delta_dir = "[green]↑[/green]" if delta_chg > 0 else "[red]↓[/red]" if delta_chg < 0 else "→"
                delta_note = "improved" if delta_chg > 0 else "deteriorated"
            delta_str = f"{delta_now:.3f}   (was {entry_delta:.3f} at entry  {delta_dir}  {delta_note})"
        else:
            delta_str = f"{delta_now:.3f}"
        console.print(f"  Delta:       {delta_str}")

    if theta_now is not None:
        theta_daily = abs(theta_now) * contracts * 100
        theta_color = "green" if direction == "SHORT" else "red"
        console.print(f"  Theta/day:   [{theta_color}]+${theta_daily:.0f}[/{theta_color}] decaying {'in your favor' if direction == 'SHORT' else 'against you'}")

    # ── P&L ──────────────────────────────────────────────────────────────────
    console.print()
    console.print(f"  [bold]P&L[/bold]")

    prem_str = f"${abs(net_premium):.0f}  ({contracts} x ${open_price:.2f})"
    prem_label = "Paid:" if direction == "LONG" else "Collected:"
    console.print(f"  {prem_label:<12} {prem_str}")

    if pnl_mtm is not None:
        pnl_color = "green" if pnl_mtm > 0 else "red"
        pct_str = f"  ({pnl_pct:+.1f}% of premium)" if pnl_pct else ""
        sign = "+" if pnl_mtm >= 0 else "-"
        console.print(f"  Current P&L: [{pnl_color}]{sign}${abs(pnl_mtm):.0f}{pct_str}[/{pnl_color}]")

        # Profit target progress
        target_pct = 50.0
        if pnl_pct is not None and direction == "SHORT":
            target_dollar = abs(net_premium) * (target_pct / 100)
            console.print(f"  Target:      ${target_dollar:.0f} ({target_pct:.0f}% of premium)  —  {'[green]REACHED[/green]' if pnl_pct >= target_pct else f'[dim]{target_pct - pnl_pct:.0f}% remaining[/dim]'}")
    else:
        console.print(f"  Current P&L: [dim]-- (no option price data)[/dim]")

    # ── Buffer ────────────────────────────────────────────────────────────────
    if spot and strike:
        console.print()
        console.print(f"  [bold]Buffer to Strike[/bold]")

        buf = a.get("intrinsic_buffer", 0) or 0
        buf_pct = round(buf / spot * 100, 1) if spot else 0
        otm_itm = "OTM" if buf > 0 else "ITM"
        buf_color = "green" if buf_pct > 10 else "yellow" if buf_pct > 5 else "red"

        console.print(f"  Strike:      ${strike:.0f}  |  Spot: ${spot:.2f}")
        console.print(f"  Buffer:      [{buf_color}]${abs(buf):.2f}  ({abs(buf_pct):.1f}% {otm_itm})[/{buf_color}]")

        if atr:
            atr1 = round(spot - atr, 2) if opt_type == "PUT" else round(spot + atr, 2)
            atr2 = round(spot - 2*atr, 2) if opt_type == "PUT" else round(spot + 2*atr, 2)
            console.print(f"  1-ATR:       ${atr1:.2f}  |  2-ATR: ${atr2:.2f}")
            if opt_type == "PUT":
                if spot > atr1:
                    console.print(f"  [dim]Spot is above 1-ATR — well positioned[/dim]")
                elif spot > atr2:
                    console.print(f"  [yellow]Spot between 1-ATR and 2-ATR — monitor[/yellow]")
                else:
                    console.print(f"  [red]Spot below 2-ATR — elevated risk[/red]")
            else:  # CALL
                gap_to_strike = abs(buf)
                gap_pct = round(gap_to_strike / spot * 100, 1) if spot else 0
                if gap_pct > 20:
                    console.print(f"  [red]Stock needs to rally {gap_pct:.1f}% to reach strike[/red]")
                elif gap_pct > 10:
                    console.print(f"  [yellow]Stock needs to rally {gap_pct:.1f}% to reach strike[/yellow]")
                else:
                    console.print(f"  [dim]Stock needs to rally {gap_pct:.1f}% to reach strike[/dim]")

    # ── Break-even ─────────────────────────────────────────────────────────────────
    if spot and strike and open_price:
        _be = round(strike - open_price, 2)
        _buf_be = round(spot - _be, 2)
        _buf_be_pct = round(_buf_be / spot * 100, 1) if spot else 0
        _be_clr = "green" if _buf_be_pct > 10 else "yellow" if _buf_be_pct > 0 else "bold red"
        console.print()
        console.print(f"  [bold]Break-even & Stop[/bold]")
        console.print(f"  Break-even:       [cyan]${_be:.2f}[/cyan]   (strike ${strike:.0f} − premium ${open_price:.2f}/share)")
        console.print(f"  Buffer to b/e:    [{_be_clr}]${_buf_be:.2f}  ({_buf_be_pct:.1f}%)[/{_be_clr}]")
        if _buf_be < 0:
            console.print(f"  [bold red]⚠  Stock ${abs(_buf_be):.2f} BELOW break-even — real loss at expiry if held[/bold red]")
        _ss = StrategySettings.get(pos["account_id"], pos["strategy"])
        _stop_mult = (_ss.stop_loss_multiplier if _ss else None) or DEFAULT_STOP_MULT
        _stop_level = (net_premium or 0) * _stop_mult
        _pnl = pnl_mtm if pnl_mtm is not None else 0
        _stop_pct = round(abs(min(_pnl, 0)) / _stop_level * 100, 0) if _stop_level else 0
        _stop_rem = round(_stop_level - abs(min(_pnl, 0)), 0) if _stop_level else 0
        _sc = "green" if _stop_pct < 50 else "yellow" if _stop_pct < 80 else "red"
        console.print(f"  {_stop_mult:g}x Stop loss:     ${_stop_level:,.0f}   [{_sc}]{_stop_pct:.0f}% used[/{_sc}]  (${_stop_rem:,.0f} remaining)")

    # ── Entry Context ─────────────────────────────────────────────────────────
    if entry_spot or entry_rsi or entry_bias is not None:
        console.print()
        console.print(f"  [bold]At Entry[/bold]")
        if entry_spot:
            spot_chg = round((spot - entry_spot) / entry_spot * 100, 1) if spot else None
            chg_str = f"  ({spot_chg:+.1f}% since entry)" if spot_chg is not None else ""
            console.print(f"  Spot:        ${entry_spot:.2f}{chg_str}")
        if entry_rsi:
            console.print(f"  RSI:         {entry_rsi:.0f}")
        if entry_bias is not None:
            bias_label = "Bullish" if entry_bias > 0 else "Bearish" if entry_bias < 0 else "Neutral"
            console.print(f"  Bias:        {bias_label} ({entry_bias:+d})")

    # ── Guidance ──────────────────────────────────────────────────────────────
    console.print()
    console.print(f"  [bold]Guidance[/bold]")
    console.print(f"  [{flag_color}]● {flag}[/{flag_color}]")
    console.print()

    guidance = generate_guidance(pos, primary, a, snap, days_left, pnl_pct, buf if spot and strike else None, buf_pct if spot and strike else None)
    for line in guidance:
        console.print(f"  {line}")

    if spot and strike and open_price:
        render_csp_position_diagram(spot, strike, open_price, atr or (spot * 0.03), net_premium)
    console.print()


def cmd_check_deep_iron_condor(pos, legs, assessment, snap):
    a = assessment
    tkr = pos.get('ticker', '')  # HELM-020
    spot = a.get('underlying_price')
    net_premium = pos.get('net_premium') or 0
    contracts = pos.get('total_contracts') or 1
    per_contract = round(net_premium / contracts / 100, 2) if contracts else 0
    pnl_mtm = a.get('pnl_mtm') or 0
    profit_pct = round(pnl_mtm / net_premium * 100, 1) if net_premium else 0
    mark_confidence = a.get('mark_confidence', 'live')  # HELM-019 v1.1
    target = round(net_premium * 0.50, 0)
    expiration = None
    short_put = long_put = short_call = long_call = None
    for l in legs:
        expiration = expiration or l.get('expiration','')
        r = l.get('leg_role','').upper()
        if r == 'SHORT_PUT':  short_put  = l
        elif r == 'LONG_PUT': long_put   = l
        elif r == 'SHORT_CALL': short_call = l
        elif r == 'LONG_CALL': long_call  = l
    if not all([short_put, short_call]):
        console.print('  [red]Missing legs — cannot display Iron Condor deep check[/red]')
        return
    sp_str = short_put.get('strike',0)
    sc_str = short_call.get('strike',0)
    lp_str = long_put.get('strike',0) if long_put else None
    lc_str = long_call.get('strike',0) if long_call else None
    spread_w = round(float(sc_str) - float(sp_str), 0) if sp_str and sc_str else None
    be_low  = pos.get('breakeven_low')  or (round(float(sp_str) - per_contract, 2) if sp_str else None)
    be_high = pos.get('breakeven_high') or (round(float(sc_str) + per_contract, 2) if sc_str else None)
    days_left = dte(expiration) if expiration else None
    dte_color = 'red' if (days_left or 99) <= 7 else 'yellow' if (days_left or 99) <= 21 else 'green'
    # Distance to short strikes
    dist_put  = round(float(spot) - float(sp_str), 2) if (spot and sp_str) else None
    dist_call = round(float(sc_str) - float(spot), 2) if (spot and sc_str) else None
    pct_put   = round(dist_put  / float(spot) * 100, 1) if (dist_put  and spot) else None
    pct_call  = round(dist_call / float(spot) * 100, 1) if (dist_call and spot) else None
    put_color  = 'red' if (pct_put  or 99) < 3 else 'yellow' if (pct_put  or 99) < 7 else 'green'
    call_color = 'red' if (pct_call or 99) < 3 else 'yellow' if (pct_call or 99) < 7 else 'green'
    # Is stock in profit zone?
    in_zone = sp_str and sc_str and float(sp_str) < float(spot) < float(sc_str)
    zone_str = '[green]centered in profit zone ✓[/green]' if in_zone else '[red]outside profit zone ✗[/red]'
    console.print()
    console.print(Panel.fit(
        f'[bold cyan]{pos[chr(116)+chr(105)+chr(99)+chr(107)+chr(101)+chr(114)]}[/bold cyan]  IRON CONDOR  {expiration}  [{dte_color}]{days_left}d remaining[/{dte_color}]  x{contracts}',
        border_style='cyan'
    ))
    console.print()
    console.print('  [bold]Structure[/bold]')
    lp_s = f'Long ${lp_str:.0f} / ' if lp_str else ''
    lc_s = f' / Long ${lc_str:.0f}' if lc_str else ''
    console.print(f'  Put spread:   {lp_s}[bold]Short ${sp_str:.0f}[/bold]   (protect below ${sp_str:.0f})')
    console.print(f'  Call spread:  [bold]Short ${sc_str:.0f}[/bold]{lc_s}   (protect above ${sc_str:.0f})')
    console.print(f'  Profit zone:  [green]${sp_str:.0f} → ${sc_str:.0f}[/green]   (${spread_w:.0f} wide)')
    if be_low and be_high:
        console.print(f'  Break-evens:  [cyan]${be_low:.2f}[/cyan] (put side)  /  [cyan]${be_high:.2f}[/cyan] (call side)')
    console.print()
    console.print('  [bold]Current Position[/bold]')
    console.print(f'  {tkr} now:  ${spot:.2f}  —  {zone_str}')
    console.print(f'  Put side:  [{put_color}]${dist_put:.2f} ({pct_put:.1f}%)[/{put_color}] above short put ${sp_str:.0f}')
    console.print(f'  Call side: [{call_color}]${dist_call:.2f} ({pct_call:.1f}%)[/{call_color}] below short call ${sc_str:.0f}')
    pct_used = min(abs(pct_put or 99), abs(pct_call or 99))
    if pct_used < 3:
        console.print(f'  [red bold]⚠  Short strike within 3% — adjustment may be needed[/red bold]')
    console.print()
    console.print('  [bold]P&L[/bold]')
    console.print(f'  Collected:   ${net_premium:,.0f}  ({contracts} x ${per_contract:.2f}/contract)')
    pnl_color = 'green' if pnl_mtm >= 0 else 'red'
    _fz = '' if mark_confidence == 'live' else f'  [yellow]({mark_confidence})[/yellow]'
    console.print(f'  Current P&L: [{pnl_color}]{pnl_mtm:+,.0f}  ({profit_pct:.1f}% of max profit)[/{pnl_color}]{_fz}')
    console.print(f'  Target:      ${target:,.0f} (50% of premium)  —  {round(100 - profit_pct, 0):.0f}% remaining')
    console.print()
    # Position map
    if spot and sp_str and sc_str:
        low  = float(lp_str or sp_str) * 0.97
        high = float(lc_str or sc_str) * 1.03
        rng  = high - low
        W = 54
        def px(p): return max(0, min(W-1, int((float(p)-low)/rng*(W-1))))
        sp_p = px(sp_str); sc_p = px(sc_str); spot_p = px(spot)
        lp_p = px(lp_str) if lp_str else None
        lc_p = px(lc_str) if lc_str else None
        line = '  '
        for i in range(W):
            pr = low + (i/(W-1))*rng
            if i == sp_p:   line += '[bold]|[/bold]'
            elif i == sc_p: line += '[bold]|[/bold]'
            elif i == spot_p: line += '[green bold]●[/green bold]'
            elif lp_p and i == lp_p: line += '[dim][[/dim]'
            elif lc_p and i == lc_p: line += '[dim]][/dim]'
            elif float(sp_str) <= pr <= float(sc_str): line += '[green]─[/green]'
            else: line += '[red]─[/red]'
        console.print('  [bold dim]Position map[/bold dim]')
        console.print()
        console.print(line)
        console.print()
        lbs = list(' '*(W+12))
        cursor = 0
        items = sorted([(sp_p, f'${sp_str:.0f}'), (spot_p, f'now ${spot:.2f}'), (sc_p, f'${sc_str:.0f}')], key=lambda x:x[0])
        for p,t in items:
            start = max(cursor, p - len(t)//2)
            start = min(start, W+12-len(t))
            for j,c in enumerate(t):
                if start+j < len(lbs): lbs[start+j]=c
            cursor = start + len(t) + 2
        console.print('  '+''.join(lbs))
        console.print()
    # Guidance
    console.print('  [bold]Guidance[/bold]')
    if (days_left or 99) <= 7:
        console.print('  [bold red]⚠  7 DTE — close immediately, gamma risk extreme[/bold red]')
    elif (days_left or 99) <= 21:
        console.print('  [yellow]⚠  21 DTE — consider closing regardless of P&L[/yellow]')
    elif profit_pct >= 50 and mark_confidence == "live":
        console.print('  [green]🎯 50% profit target reached — close and redeploy[/green]')
    elif in_zone:
        console.print('  [green]✓ Centered in profit zone — hold, let theta work[/green]')
        alert_put  = round(float(sp_str) * 1.03, 2)
        alert_call = round(float(sc_str) * 0.97, 2)
        console.print(f'  Alert if {tkr} moves below [yellow]${alert_put:.2f}[/yellow] or above [yellow]${alert_call:.2f}[/yellow] (within 3% of short strikes)')
    else:
        console.print('  [red]⚠  Outside profit zone — evaluate adjustment or close[/red]')
    if mark_confidence != "live":
        console.print('  [yellow]⚠  P&L is frozen/stale — confirm at RTH before acting on profit/stop levels[/yellow]')
    console.print()


def cmd_check_deep(pos: dict, legs: list, assessment: dict, snap: dict):
    """
    Deep narrative check for a single position.
    Shows full context: entry vs now, Greeks comparison, guidance.
    """
    ticker   = pos["ticker"]
    strategy = pos["strategy"]
    a        = assessment
    primary  = a.get("primary_leg") or {}
    opt      = a.get("opt_data") or {}
    spot     = a.get("underlying_price")
    flag     = a.get("flag", "UNKNOWN")
    pnl_mtm  = a.get("pnl_mtm")
    pnl_pct  = a.get("pnl_pct")

    flag_colors = {"GREEN": "green", "YELLOW": "yellow", "RED": "red", "UNKNOWN": "dim"}
    flag_color  = flag_colors.get(flag, "dim")

    # Position basics
    strike     = primary.get("strike") or 0
    expiration = primary.get("expiration") or ""
    direction  = primary.get("direction") or ""
    opt_type   = primary.get("option_type") or ""
    contracts  = primary.get("contracts") or 0
    open_price = primary.get("open_price") or 0
    net_premium = pos.get("net_premium") or 0

    days_left  = dte(expiration) if expiration else None
    opened_at  = pos.get("opened_at", "")[:10]
    try:
        days_held = (date.today() - date.fromisoformat(opened_at)).days
    except Exception:
        days_held = None

    # Entry snapshot comparisons
    entry_spot  = snap.get("spot_price")
    entry_iv    = snap.get("iv_current")
    entry_delta = snap.get("delta")
    entry_rsi   = snap.get("rsi")
    entry_bias  = snap.get("bias_score")

    # Current Greeks
    iv_now    = opt.get("iv")
    delta_now = opt.get("delta")
    theta_now = opt.get("theta")

    # ATR for context
    atr = None
    try:
        import yfinance as yf, warnings
        warnings.filterwarnings("ignore")
        import pandas as pd
        hist = yf.Ticker(ticker).history(period="30d")
        if not hist.empty:
            high_low = hist["High"] - hist["Low"]
            atr = round(float(high_low.rolling(14).mean().iloc[-1]), 2)
    except Exception:
        pass

    # ── Header ────────────────────────────────────────────────────────────────
    leg_str = f"{opt_type[0] if opt_type else '?'}{strike:.0f} {expiration[5:] if expiration else ''}"
    held_str = f"{days_held}d held" if days_held is not None else ""
    dte_str  = f"{days_left}d remaining" if days_left is not None else ""

    console.print()
    console.print(Panel.fit(
        f"[bold cyan]{ticker}[/bold cyan]  "
        f"[dim]{strategy}  {direction} {leg_str}  x{contracts}[/dim]\n"
        f"[dim]Opened {opened_at}  |  {held_str}  |  {dte_str}[/dim]",
        title=f"{ticker} Deep Check",
        border_style=flag_color
    ))

    # ── Market Data ───────────────────────────────────────────────────────────
    console.print()
    console.print(f"  [bold]Market Data[/bold]")

    spot_str = f"${spot:.2f}  ({a.get('underlying_source', '')})" if spot else "--"
    console.print(f"  Underlying:  {spot_str}")

    if opt.get("mid"):
        bid = opt.get("bid") or 0
        ask = opt.get("ask") or 0
        console.print(f"  Option mid:  ${opt['mid']:.2f}   bid ${bid:.2f}  ask ${ask:.2f}  ({a.get('opt_source', '')})")
    else:
        console.print(f"  Option:      [dim]no data[/dim]")

    # IV comparison
    if iv_now is not None:
        if entry_iv:
            iv_chg = round(iv_now - entry_iv, 1)
            iv_dir = "[green]↓[/green]" if iv_chg < 0 else "[red]↑[/red]" if iv_chg > 0 else "→"
            iv_str = f"{iv_now:.1f}%   (was {entry_iv:.1f}% at entry  {iv_dir}  {iv_chg:+.1f}%)"
        else:
            iv_str = f"{iv_now:.1f}%"
        console.print(f"  IV:          {iv_str}")

    # Delta comparison
    if delta_now is not None:
        if entry_delta:
            delta_chg = round(abs(delta_now) - abs(entry_delta), 3)
            if direction == "SHORT":
                delta_dir = "[green]↓[/green]" if delta_chg < 0 else "[red]↑[/red]" if delta_chg > 0 else "→"
                delta_note = "improved" if delta_chg < 0 else "deteriorated"
            else:
                delta_dir = "[green]↑[/green]" if delta_chg > 0 else "[red]↓[/red]" if delta_chg < 0 else "→"
                delta_note = "improved" if delta_chg > 0 else "deteriorated"
            delta_str = f"{delta_now:.3f}   (was {entry_delta:.3f} at entry  {delta_dir}  {delta_note})"
        else:
            delta_str = f"{delta_now:.3f}"
        console.print(f"  Delta:       {delta_str}")

    if theta_now is not None:
        theta_daily = abs(theta_now) * contracts * 100
        theta_color = "green" if direction == "SHORT" else "red"
        console.print(f"  Theta/day:   [{theta_color}]+${theta_daily:.0f}[/{theta_color}] decaying {'in your favor' if direction == 'SHORT' else 'against you'}")

    # ── P&L ──────────────────────────────────────────────────────────────────
    console.print()
    console.print(f"  [bold]P&L[/bold]")

    prem_str = f"${abs(net_premium):.0f}  ({contracts} x ${open_price:.2f})"
    prem_label = "Paid:" if direction == "LONG" else "Collected:"
    console.print(f"  {prem_label:<12} {prem_str}")

    if pnl_mtm is not None:
        pnl_color = "green" if pnl_mtm > 0 else "red"
        pct_str = f"  ({pnl_pct:+.1f}% of premium)" if pnl_pct else ""
        sign = "+" if pnl_mtm >= 0 else "-"
        console.print(f"  Current P&L: [{pnl_color}]{sign}${abs(pnl_mtm):.0f}{pct_str}[/{pnl_color}]")

        # Profit target progress
        target_pct = 50.0
        if pnl_pct is not None and direction == "SHORT":
            target_dollar = abs(net_premium) * (target_pct / 100)
            console.print(f"  Target:      ${target_dollar:.0f} ({target_pct:.0f}% of premium)  —  {'[green]REACHED[/green]' if pnl_pct >= target_pct else f'[dim]{target_pct - pnl_pct:.0f}% remaining[/dim]'}")
    else:
        console.print(f"  Current P&L: [dim]-- (no option price data)[/dim]")

    # ── Buffer ────────────────────────────────────────────────────────────────
    if spot and strike:
        console.print()
        console.print(f"  [bold]Buffer to Strike[/bold]")

        buf = a.get("intrinsic_buffer", 0) or 0
        buf_pct = round(buf / spot * 100, 1) if spot else 0
        otm_itm = "OTM" if buf > 0 else "ITM"
        buf_color = "green" if buf_pct > 10 else "yellow" if buf_pct > 5 else "red"

        console.print(f"  Strike:      ${strike:.0f}  |  Spot: ${spot:.2f}")
        console.print(f"  Buffer:      [{buf_color}]${abs(buf):.2f}  ({abs(buf_pct):.1f}% {otm_itm})[/{buf_color}]")

        if atr:
            atr1 = round(spot - atr, 2) if opt_type == "PUT" else round(spot + atr, 2)
            atr2 = round(spot - 2*atr, 2) if opt_type == "PUT" else round(spot + 2*atr, 2)
            console.print(f"  1-ATR:       ${atr1:.2f}  |  2-ATR: ${atr2:.2f}")
            if opt_type == "PUT":
                if spot > atr1:
                    console.print(f"  [dim]Spot is above 1-ATR — well positioned[/dim]")
                elif spot > atr2:
                    console.print(f"  [yellow]Spot between 1-ATR and 2-ATR — monitor[/yellow]")
                else:
                    console.print(f"  [red]Spot below 2-ATR — elevated risk[/red]")
            else:  # CALL
                gap_to_strike = abs(buf)
                gap_pct = round(gap_to_strike / spot * 100, 1) if spot else 0
                if gap_pct > 20:
                    console.print(f"  [red]Stock needs to rally {gap_pct:.1f}% to reach strike[/red]")
                elif gap_pct > 10:
                    console.print(f"  [yellow]Stock needs to rally {gap_pct:.1f}% to reach strike[/yellow]")
                else:
                    console.print(f"  [dim]Stock needs to rally {gap_pct:.1f}% to reach strike[/dim]")

    # ── Entry Context ─────────────────────────────────────────────────────────
    if entry_spot or entry_rsi or entry_bias is not None:
        console.print()
        console.print(f"  [bold]At Entry[/bold]")
        if entry_spot:
            spot_chg = round((spot - entry_spot) / entry_spot * 100, 1) if spot else None
            chg_str = f"  ({spot_chg:+.1f}% since entry)" if spot_chg is not None else ""
            console.print(f"  Spot:        ${entry_spot:.2f}{chg_str}")
        if entry_rsi:
            console.print(f"  RSI:         {entry_rsi:.0f}")
        if entry_bias is not None:
            bias_label = "Bullish" if entry_bias > 0 else "Bearish" if entry_bias < 0 else "Neutral"
            console.print(f"  Bias:        {bias_label} ({entry_bias:+d})")

    # ── Guidance ──────────────────────────────────────────────────────────────
    console.print()
    console.print(f"  [bold]Guidance[/bold]")
    console.print(f"  [{flag_color}]● {flag}[/{flag_color}]")
    console.print()

    guidance = generate_guidance(pos, primary, a, snap, days_left, pnl_pct, buf if spot and strike else None, buf_pct if spot and strike else None)
    for line in guidance:
        console.print(f"  {line}")

    console.print()


_GUIDANCE = {
    "PROFIT_TARGET": "Profit target hit — consider closing to bank the gain.",
    "STOP":          "Stop breached — close or roll to cap the loss.",
    "DTE_MANAGE":    "In the management window — close or roll before expiry week.",
    "EXPIRY":        "At or past expiry — act now to avoid assignment.",
}


def generate_guidance(pos: dict, primary: dict, assessment: dict, snap: dict,
                      days_left, pnl_pct, buffer_dollars, buffer_pct) -> list[str]:
    """Table-driven guidance: the core verdict decides, this only renders.

    Headline is the band_for headline (assessment["reasons"][0]); the action line
    is keyed off the core reason; the body is evidence facts. No thresholds here --
    threshold logic was the drift that let this prose contradict the core verdict.
    Gated positions (no core_reason) show their legacy headline + facts only.
    """
    flag = assessment.get("flag", "UNKNOWN")
    reason = assessment.get("core_reason")
    headline = (assessment.get("reasons") or [""])[0]
    color = {"GREEN": "green", "YELLOW": "yellow", "RED": "red"}.get(flag, "dim")

    lines = []
    if headline:
        lines.append(f"[{color}]{headline}[/{color}]")
    action = _GUIDANCE.get(reason)
    if action:
        lines.append(action)

    facts = []
    if pnl_pct is not None:
        facts.append(f"P&L {pnl_pct:+.0f}%")
    if buffer_pct is not None:
        facts.append(f"buffer {buffer_pct:+.1f}%" + (" (ITM)" if buffer_pct < 0 else ""))
    if days_left is not None:
        facts.append(f"{days_left} DTE")
    if facts:
        lines.append("[dim]" + " · ".join(facts) + "[/dim]")

    return lines


def cmd_check_one(ticker: str, deep: bool = False):
    """Check a single position, with optional deep narrative."""
    conn = get_conn()
    account_id = get_active_account()
    bc, bp = book_filter(sys.argv)
    pos = conn.execute(
        "SELECT * FROM positions WHERE account_id = ? AND ticker = ? AND status = 'OPEN'" + bc + " ORDER BY opened_at DESC LIMIT 1",
        (account_id, ticker.upper(), *bp)
    ).fetchone()
    if not pos:
        console.print(f"[yellow]No open position found for {ticker}[/yellow]")
        conn.close()
        return
    pos = dict(pos)
    legs = [dict(r) for r in conn.execute(
        "SELECT * FROM legs WHERE position_id = ?", (pos["id"],)
    ).fetchall()]
    # Get entry snapshot
    snap = conn.execute(
        "SELECT * FROM entry_snapshots WHERE position_id=? ORDER BY created_at DESC LIMIT 1",
        (pos["id"],)
    ).fetchone()
    snap = dict(snap) if snap else {}
    conn.close()

    console.print()
    console.print(f"[dim]Checking {ticker}...[/dim]")
    a = check_one(pos, legs, deep=deep)
    primary = a["primary_leg"]

    if deep:
        strat = pos.get('strategy', '')
        if strat in ('CSP', 'CASH_SECURED_PUT'):
            cmd_check_deep_csp(pos, legs, a, snap)
        elif strat == 'IRON_CONDOR':
            cmd_check_deep_iron_condor(pos, legs, a, snap)
        else:
            cmd_check_deep(pos, legs, a, snap)
        return

    flag_colors = {"GREEN": "green", "YELLOW": "yellow", "RED": "red", "UNKNOWN": "dim"}
    flag_color = flag_colors.get(a["flag"], "dim")

    lines = [
        f"[bold cyan]{ticker}[/bold cyan]  [dim]{(pos.get('company_name') or '')[:30]}[/dim]  {pos['strategy']}",
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



# ============================================================================
# helm check --integrity : exhaustive data-integrity invariant sweep
# The "ratchet" -- each s23 post-mortem becomes a standing assertion so a bug
# found once can never silently recur. Read-only. Fail-closed: strategies with
# no explicit rule are NAMED, never silently passed. No silent drops: every
# family reports its inputs and itemizes exclusions (missing != inconsistent).
# ============================================================================

_INTEG_SIGN_CREDIT = frozenset({'CSP', 'COVERED_CALL', 'BULL_PUT_SPREAD',
    'BEAR_CALL_SPREAD', 'IRON_CONDOR', 'SHORT_STRANGLE', 'JADE_LIZARD'})
_INTEG_SIGN_DEBIT = frozenset({'LONG_CALL', 'BEAR_PUT_SPREAD', 'BULL_CALL_SPREAD',
    'DIAGONAL', 'DIAGONAL_PUT', 'PMCC', 'LONG_CONDOR'})
_INTEG_LEG_COUNT = {'CSP': 1, 'LONG_CALL': 1, 'COVERED_CALL': 2, 'BULL_PUT_SPREAD': 2,
    'BEAR_CALL_SPREAD': 2, 'BEAR_PUT_SPREAD': 2, 'BULL_CALL_SPREAD': 2, 'DIAGONAL': 2,
    'DIAGONAL_PUT': 2, 'PMCC': 2, 'SHORT_STRANGLE': 2, 'IRON_CONDOR': 4,
    'LONG_CONDOR': 4, 'JADE_LIZARD': 3}
# PERM intentionally unmapped in both -> surfaces as "no rule defined".
_INTEG_DUP_PRICE_TOL = 0.05
_INTEG_DUP_DAYS_TOL = 1


def _integ_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], '%Y-%m-%d')
    except Exception:
        return None


def _integrity_findings(conn):
    """Return family findings: {key,title,status,summary,items}. Read-only."""
    F = []

    bad, unmapped = [], set()
    for pid, strat, npx in conn.execute(
            "SELECT id, strategy, net_premium FROM positions WHERE net_premium IS NOT NULL"):
        if strat in _INTEG_SIGN_CREDIT:
            if not npx > 0:
                bad.append((pid, strat, npx, 'expected credit (+)'))
        elif strat in _INTEG_SIGN_DEBIT:
            if not npx < 0:
                bad.append((pid, strat, npx, 'expected debit (-)'))
        else:
            unmapped.add(strat)
    F.append({'key': 'sign', 'title': 'Sign vs strategy (HELM-017)',
              'status': 'FAIL' if bad else 'PASS',
              'summary': '%d sign mismatch(es)%s' % (len(bad),
                  ('; no sign rule for: ' + ', '.join(sorted(unmapped))) if unmapped else ''),
              'items': ['%s  %s  net_premium=%s  (%s)' % (p, s, n, w) for p, s, n, w in bad]})

    rb = list(conn.execute(
        "SELECT id, position_id, leg_role, direction FROM legs "
        "WHERE NOT ((leg_role LIKE 'LONG%' AND direction='LONG') "
        "       OR  (leg_role LIKE 'SHORT%' AND direction='SHORT'))"))
    F.append({'key': 'role', 'title': 'Leg role vs direction (HELM-017)',
              'status': 'FAIL' if rb else 'PASS',
              'summary': '%d role/direction mismatch(es)' % len(rb),
              'items': ['%s (pos %s)  role=%s  direction=%s' % (lid, pid, role, dr)
                        for lid, pid, role, dr in rb]})

    lc_bad, lc_unmapped = [], set()
    for pid, strat, n in conn.execute(
            "SELECT p.id, p.strategy, COUNT(l.id) FROM positions p "
            "LEFT JOIN legs l ON l.position_id=p.id GROUP BY p.id, p.strategy"):
        exp = _INTEG_LEG_COUNT.get(strat)
        if exp is None:
            lc_unmapped.add(strat)
        elif n != exp:
            lc_bad.append((pid, strat, n, exp))
    F.append({'key': 'legcount', 'title': 'Leg count per strategy',
              'status': 'FAIL' if lc_bad else 'PASS',
              'summary': '%d leg-count mismatch(es)%s' % (len(lc_bad),
                  ('; no leg rule for: ' + ', '.join(sorted(lc_unmapped))) if lc_unmapped else ''),
              'items': ['%s  %s  has %d leg(s), expected %d' % (p, s, n, e) for p, s, n, e in lc_bad]})

    fk = list(conn.execute("PRAGMA foreign_key_check"))
    bytab = {}
    for row in fk:
        bytab[row[0]] = bytab.get(row[0], 0) + 1
    F.append({'key': 'orphans', 'title': 'FK orphans',
              'status': 'FAIL' if fk else 'PASS',
              'summary': '%d orphan row(s)%s' % (len(fk),
                  (': ' + ', '.join('%s=%d' % (t, c) for t, c in sorted(bytab.items()))) if bytab else ''),
              'items': ['%s rowid=%s -> missing %s' % (t, rid, parent) for t, rid, parent, fkid in fk[:40]]})

    a_items = []
    dupsnap = list(conn.execute(
        "SELECT position_id, COUNT(*) FROM entry_snapshots GROUP BY position_id HAVING COUNT(*)>1"))
    for pid, n in dupsnap:
        a_items.append('%s has %d snapshots (UNIQUE breach)' % (pid, n))
    mis = list(conn.execute(
        "SELECT e.position_id, e.leg_id, l.direction FROM entry_snapshots e "
        "JOIN legs l ON e.leg_id=l.id "
        "WHERE (SELECT COUNT(*) FROM legs lg WHERE lg.position_id=e.position_id)>1 "
        "  AND l.direction<>'SHORT'"))
    for pid, lid, dr in mis:
        a_items.append('%s multileg snapshot anchored to %s leg %s (want SHORT)' % (pid, dr, lid))
    F.append({'key': 'anchor', 'title': 'Snapshot anchoring',
              'status': 'FAIL' if a_items else 'PASS',
              'summary': '%d UNIQUE breach(es), %d mis-anchored multileg' % (len(dupsnap), len(mis)),
              'items': a_items})

    posrows = {}
    for pid, tkr, strat, opened, role, strike, exp, ctr, price in conn.execute(
            "SELECT p.id,p.ticker,p.strategy,p.opened_at,l.leg_role,l.strike,l.expiration,"
            "l.contracts,l.open_price FROM positions p JOIN legs l ON l.position_id=p.id "
            "WHERE p.book='REAL'"):
        d = posrows.setdefault(pid, {'tkr': tkr, 'strat': strat, 'opened': opened, 'legs': []})
        d['legs'].append((role, strike, exp, ctr, price))
    buckets = {}
    for pid, d in posrows.items():
        ls = sorted(d['legs'], key=lambda x: (str(x[0]), x[1] if x[1] is not None else 0,
                                              str(x[2]), x[3] if x[3] is not None else 0))
        struct = tuple((role, strike, exp, ctr) for role, strike, exp, ctr, _ in ls)
        buckets.setdefault((d['tkr'], d['strat'], struct), []).append(
            (pid, d['opened'], [p for *_, p in ls]))
    dups = []
    for key, members in buckets.items():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pa, oa, pra = members[i]
                pb, ob, prb = members[j]
                da, db = _integ_date(oa), _integ_date(ob)
                day_ok = da is not None and db is not None and abs((da - db).days) <= _INTEG_DUP_DAYS_TOL
                price_ok = all(abs((x or 0) - (y or 0)) <= _INTEG_DUP_PRICE_TOL for x, y in zip(pra, prb))
                if day_ok and price_ok:
                    dups.append('%s ~ %s  (%s %s, opened %s/%s)' % (pa, pb, key[0], key[1], oa, ob))
    F.append({'key': 'dupfill', 'title': 'Duplicate fills (price+time+size)',
              'status': 'FAIL' if dups else 'PASS',
              'summary': '%d suspected double-book(s)' % len(dups), 'items': dups})

    npos = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
    cov = conn.execute("SELECT COUNT(DISTINCT position_id) FROM entry_snapshots").fetchone()[0]
    F.append({'key': 'coverage', 'title': 'Snapshot coverage (info)', 'status': 'INFO',
              'summary': '%d/%d positions have an entry snapshot (missing = backfillable, not a bug)' % (cov, npos),
              'items': []})
    return F


def cmd_check_integrity(verbose=False):
    """helm check --integrity : exhaustive data-integrity invariant sweep."""
    conn = get_conn()
    try:
        findings = _integrity_findings(conn)
    finally:
        conn.close()
    n_fail = sum(1 for f in findings if f['status'] == 'FAIL')
    hs = 'red' if n_fail else 'green'
    head = ('%d FAILING' % n_fail) if n_fail else 'ALL CLEAR'
    console.print()
    console.print(Panel.fit(
        "[bold %s]HELM Integrity — %s[/bold %s]\n[dim]%d invariant families · exhaustive sweep[/dim]"
        % (hs, head, hs, len(findings)), border_style=hs))
    t = Table(box=box.SIMPLE_HEAD, show_header=True, padding=(0, 1))
    t.add_column("", width=3, no_wrap=True)
    t.add_column("Invariant")
    t.add_column("Result")
    marks = {'PASS': '[green]ok[/green]', 'FAIL': '[red]X[/red]', 'INFO': '[cyan]i[/cyan]'}
    styles = {'PASS': 'green', 'FAIL': 'red', 'INFO': 'cyan'}
    for f in findings:
        t.add_row(marks[f['status']], f['title'],
                  "[%s]%s[/%s]" % (styles[f['status']], f['summary'], styles[f['status']]))
    console.print(t)
    for f in findings:
        if f['items'] and (f['status'] == 'FAIL' or verbose):
            console.print("\n[bold]%s[/bold]" % f['title'])
            for it in f['items']:
                console.print("  [dim]-[/dim] %s" % it)
    console.print()
    if n_fail:
        console.print("[red]Integrity: %d family(ies) failing.[/red] Use [bold]--verbose[/bold] for all items." % n_fail)
    else:
        console.print("[green]Integrity: all invariants hold.[/green]")


def run():
    args = sys.argv[1:]

    if "--integrity" in args:
        cmd_check_integrity(verbose=("--verbose" in args or "-v" in args))
        return

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
    elif deep:
        # --deep with no ticker: run deep check on all open positions
        from helm.db import get_conn as _gc
        _conn = _gc()
        _bc, _bp = book_filter(args)
        _open = _conn.execute(
            "SELECT DISTINCT ticker FROM positions WHERE status='OPEN'" + _bc + " ORDER BY ticker", _bp
        ).fetchall()
        for _row in _open:
            cmd_check_one(_row['ticker'], deep=True)
    else:
        cmd_check_all(args)

    if "--manage" in args:
        from helm.cli.paper_manage import manage_paper_book
        manage_paper_book()

    # Log event and show nudges
    try:
        _log_event("FULL_CHECK_RUN")
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
