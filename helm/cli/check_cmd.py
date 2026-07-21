
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
from helm.decision import evaluate as _core_evaluate, evaluate_shadow_debit_stop, DEFAULT_STOP_MULT
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

console = Console(no_color=True)  # HELM-074: monochrome -- focus on the data

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
            "current_bid, current_ask, current_price, delta, gamma, theta, vega, iv_current, greeks_source, data_quality, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'GOOD', ?)",
            ("LCHK-" + _uuid.uuid4().hex[:8].upper(),
             check_id, position_id, _lid, _now, _m.get("bid"), _m.get("ask"), _m["current_price"], _m.get("delta"), _m.get("gamma"), _m.get("theta"), _m.get("vega"), _m.get("iv"), ("ibkr-live" if _m.get("delta") is not None else None), _now),
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
        premium = pos.get("net_premium") or pos.get("premium_collected")
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
    # HELM-045 guard: a live mark implying pnl_unrealized > max_profit is an impossible
    # quote; it must not be persisted as GOOD even when the source is live + complete.
    _mp045 = pos.get("max_profit")
    _pnl045 = a.get("pnl_mtm")
    if dq == "GOOD" and _pnl045 is not None and _mp045 not in (None, 0) \
            and float(_pnl045) > float(_mp045) * 1.001:
        dq = "STALE"

    # HELM-037 live-only persistence gate: only GOOD (live + complete) marks are
    # written. Frozen / partial / yfinance reads are still computed and displayed
    # (the caller renders the returned assessment) but never persisted, keeping the
    # checks table a clean live record. Skipping the INSERT here has no display effect.
    if dq != "GOOD":
        return

    try:
        with _tx() as conn:
            _sh = assessment.get("shadow") or {}
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
                    created_at,
                    shadow_signal, shadow_would_fire, shadow_loss_pct
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                _sh.get("signal"),
                (1 if _sh.get("would_fire") else 0) if _sh else None,
                _sh.get("loss_pct"),
            ))
            _persist_real_leg_marks(conn, check_id, position_id, leg_marks_by_id)
    except Exception:
        import traceback; traceback.print_exc()



def fetch_ibkr_underlying(ticker: str) -> dict:
    """Fetch underlying price from IBKR. Returns close price outside hours.

    HELM-075: set frozen market-data type off-hours (mirroring
    fetch_ibkr_option) and prefer the frozen `last`. IBKR's `close` tick is the
    PRIOR session's close, so reading it after today's close returned
    yesterday's price (LRCX read 333.15 vs today's 353.17). Frozen `last` is the
    most recent session's close; `close` is kept only as a last-resort fallback.
    """
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
            ib.reqMarketDataType(2)  # HELM-076: frozen; IBKR upgrades to live (1) when a live session genuinely exists (pre/post-market)
            stock = Stock(ticker, "SMART", "USD")
            ib.qualifyContracts(stock)
            t = ib.reqMktData(stock, "", False, False)
            ib.sleep(2)

            def _ok(v):
                return v is not None and not math.isnan(v) and v > 0

            got_live = (getattr(t, "marketDataType", None) == 1)
            if _ok(t.last):
                # HELM-076: live extended-hours print when IBKR upgraded (pre/post-market), else frozen last
                result["price"] = round(t.last, 2)
                result["live"] = got_live
            elif _ok(t.close):
                # last resort: IBKR `close` tick is the PRIOR session's close
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
            ib.reqMarketDataType(2)  # HELM-076: frozen; IBKR upgrades to live (1) when a live session exists
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
        if key in leg_marks and leg_marks[key] is not None:
            marks[lg["id"]] = leg_marks[key]  # HELM-095: skip None-valued marks
    # HELM-095: a present-but-None mark (intermittent illiquid multi-leg quote)
    # must count as INCOMPLETE, not crash evaluate() on `open_price - None`.
    # The is-not-None guard above drops it from `marks`, so this check catches it
    # and we return None (hold) cleanly -- mirrors paper_manage's incomplete gate.
    if any(lg["id"] not in marks for lg in opt_legs):
        return None  # incomplete marks -> no verdict (mirrors paper_manage)
    _nspos = _ns_pos(pos)
    reason, total_pnl = _core_evaluate(
        _nspos, [_ns_leg(l) for l in opt_legs], marks
    )
    return {"reason": reason, "core_pnl": total_pnl,
            "shadow": evaluate_shadow_debit_stop(_nspos, total_pnl)}


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

        # Intrinsic buffer (distance to the SHORT strike). Meaningful when
        # there is exactly one short option leg -- CSP, covered call, or a
        # credit vertical (bull-put / bear-call). Two short strikes (condor /
        # strangle / jade) or none (long debit) leave it None; the deep view
        # shows the wings.
        _short_opt_legs = [_l for _l in opt_legs if _l.get("direction") == "SHORT"]
        if underlying_price and len(_short_opt_legs) == 1:
            _sl = _short_opt_legs[0]
            _s_strike = _sl.get("strike")
            _s_type = _sl.get("option_type")
            if _s_strike is not None and _s_type:
                if _s_type == "PUT":
                    intrinsic_buffer = round(underlying_price - _s_strike, 2)
                else:  # CALL
                    intrinsic_buffer = round(_s_strike - underlying_price, 2)

        # --- HELM quiet-flag rules (flags only at key decision points) ---
        # No RED anywhere. Orange = a decision is due; bold green = take-profit;
        # dim = holding (quiet). Orange wins over green when both apply.
        _strat_u = (strategy or "").upper()
        _is_csp = _strat_u in ("CSP", "CASH_SECURED_PUT")
        if pnl_pct is not None and pnl_pct >= 50:
            flags.append("GREEN")
            reasons.append(f"Take profit: kept {pnl_pct:+.0f}%")
        if days_left is not None and days_left <= 21:
            flags.append("YELLOW")
            reasons.append(f"Manage: {days_left} DTE")
        if _is_csp and pnl_pct is not None and pnl_pct < -100:
            flags.append("YELLOW")
            reasons.append(f"Stop watch: kept {pnl_pct:+.0f}% (below -100%)")

    # Final flag (no red): orange wins; else take-profit green; else quiet hold.
    if "YELLOW" in flags:
        final_flag = "YELLOW"
        flag_style = "bold yellow"
    elif "GREEN" in flags:
        final_flag = "GREEN"
        flag_style = "bold green"
    elif underlying_price is not None:
        final_flag = "GREEN"
        flag_style = "dim"
        reasons.append("Holding")
    else:
        final_flag = "UNKNOWN"
        flag_style = "dim"
        reasons.append("No market data available")

    # Resolve the profit target once (as a percent) so the deep panels read
    # the configured value instead of hardcoding 50%.
    _ptr = strategy_settings.get("profit_target_pct", 0.50) or 0.50
    _ptr = _ptr * 100 if _ptr <= 1 else _ptr
    return {
        "flag": final_flag,
        "flag_style": flag_style,
        "reasons": reasons,
        "pnl_mtm": pnl_mtm,
        "pnl_pct": pnl_pct,
        "intrinsic_buffer": intrinsic_buffer,
        "profit_target_pct": _ptr,
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

def _weakest_leg_confidence(mark_confidence, leg_marks_by_id):
    """HELM-019: a netted multi-leg P&L is only as fresh as its least-fresh leg.
    Per-leg liveness is captured in leg_marks_by_id (HELM-041). A \"live\" reading
    with any non-live (or unstamped) leg is contaminated, so it is downgraded to
    \"stale\"; non-live base readings (\"frozen\"/\"stale\") are returned unchanged.
    Fail-closed: a leg missing its is_live flag counts as not live."""
    if mark_confidence == "live" and any(
        not _m.get("is_live") for _m in (leg_marks_by_id or [])
    ):
        return "stale"
    return mark_confidence


def check_one(pos: dict, legs: list, deep: bool = False, persist: bool = False) -> dict:
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
            "delta": opt_data.get("delta"), "gamma": opt_data.get("gamma"),
            "theta": opt_data.get("theta"), "vega": opt_data.get("vega"),
            "iv": opt_data.get("iv"), "bid": opt_data.get("bid"), "ask": opt_data.get("ask"),
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
                    "delta": _q.get("delta"), "gamma": _q.get("gamma"),
                    "theta": _q.get("theta"), "vega": _q.get("vega"),
                    "iv": _q.get("iv"), "bid": _q.get("bid"), "ask": _q.get("ask"),
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

    # HELM-019: base mark freshness from the primary leg's opt_source, then apply
    # the weakest-leg downgrade below so a stale wing can't read as "live".
    if opt_source == "ibkr-live":
        mark_confidence = "live"
    elif opt_source == "ibkr-frozen":
        mark_confidence = "frozen"
    else:
        mark_confidence = "stale"
    mark_confidence = _weakest_leg_confidence(mark_confidence, leg_marks_by_id)

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
        "leg_greeks": {m["leg_id"]: m for m in leg_marks_by_id if m.get("leg_id")},
    })

    # WS4: decision-core verdict (additive; legacy flag retained for diff).
    # Guarded: a core failure must never break the legacy check (zero-regression).
    try:
        _cv = core_verdict(pos, legs, opt_legs, primary, opt_data, leg_marks)
        if _cv is not None:
            assessment["core_reason"] = _cv["reason"]
            assessment["core_pnl"] = _cv["core_pnl"]
            assessment["shadow"] = _cv.get("shadow")
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

    # Save check to DB silently -- only when the caller is a sanctioned writer
    # (helm snapshot / scheduled agent -> persist=True; ad-hoc helm check -> False). HELM-037.
    if persist:
        save_check(pos["id"], assessment, pos, leg_marks_by_id)

    return assessment


# ============================================================================
# HELM-074 -- `helm check` per-strategy redesign (display-only rewrite of the
# no-flags summary path). Instrument panel over stoplight: group by strategy,
# each group its own gauges; danger axis is delta-led with buffer alongside;
# a portfolio-pulse header on top. `check_one` stays persist=False (no writes).
#
# Drop-in: replaces the body of `cmd_check_all` and adds the helpers below.
# Deep views (cmd_check_deep_*) and the router (run/cmd_check_one) are untouched.
#
# Data contract (from check_one's returned assessment `a`, verified s69):
#   a: flag, primary_leg, underlying_price, underlying_source, intrinsic_buffer,
#      pnl_mtm, pnl_pct, opt_data{mid,delta,iv,bid,ask}, opt_source,
#      mark_confidence, reasons, profit_target_pct
#   pos: net_premium, breakeven_low/high, max_profit/max_loss, total_contracts,
#        strategy, ticker, company_name
#   legs: strike, direction, option_type, leg_role, contracts, open_price, expiration
#
# Delta note: check_one fetches greeks live for the PRIMARY leg only. So Δ is
# live for single-leg families (CSP short put, LONG_CALL) at RTH; for spreads &
# condors per-leg tested Δ is dashed ("-- *") pending the greek-write (HELM-073).
# Off-hours all greeks are frozen/None -> Δ dashes; derived columns still render.
# ============================================================================

from rich.columns import Columns

# ---- group taxonomy --------------------------------------------------------
_FAMILY_ORDER = ["CSP", "CREDIT_SPREAD", "IC", "LONG_CALL", "OTHER"]
_FAMILY_META = {
    "CSP":           ("Cash-secured puts", "CSP"),
    "CREDIT_SPREAD": ("Credit spreads",    "BCS"),   # suffix refined per members
    "IC":            ("Iron condors",      "IC"),
    "LONG_CALL":     ("Long calls",        "LC"),
    "OTHER":         ("Other",             "--"),
}

def _family(strat):
    s = (strat or "").upper()
    if s in ("CSP", "CASH_SECURED_PUT"):
        return "CSP"
    if s in ("BEAR_CALL_SPREAD", "BULL_PUT_SPREAD"):
        return "CREDIT_SPREAD"
    if s == "IRON_CONDOR":
        return "IC"
    if s == "LONG_CALL":
        return "LONG_CALL"
    return "OTHER"


# ---- small format helpers --------------------------------------------------
def _pct_s(v, dec=1):
    """Signed percent, plain (no color). None -> em dash."""
    return f"{v:+.{dec}f}%" if v is not None else "—"

def _dte_cell(days):
    """DTE with the 21-day gamma dot; color bands mirror the legacy renderer."""
    if days is None:
        return "—"
    dot = " •" if days <= 21 else ""      # inside 21-day gamma zone
    color = "yellow" if days <= 7 else "yellow" if days <= 21 else "green"
    return f"[{color}]{days}[/{color}]{dot}"

def _delta_cell(delta, pending=False):
    """Delta as the danger gauge: <.30 green / .30-.60 yellow / >=.60 red.
    `pending` renders the dashed placeholder for per-leg deltas we don't yet
    persist (spreads & condors -> HELM-073)."""
    if pending:
        return "[dim]— *[/dim]"
    if delta is None:
        return "[dim]— *[/dim]"
    d = abs(delta)
    color = "red" if d >= 0.60 else "yellow" if d >= 0.30 else "green"
    return f"[{color}]{d:.2f}[/{color}]"

def _buf_stack(strike_pct, be_pct):
    """Two-line stacked buffer cell: strike buffer on top, breakeven muted below.
    Positive = safe side; color the top line by proximity."""
    if strike_pct is None:
        top = "—"
    else:
        c = "green" if strike_pct > 10 else "yellow" if strike_pct > 0 else "red"
        top = f"[{c}]{strike_pct:+.1f}%[/{c}]"
    be = "—" if be_pct is None else f"be {be_pct:+.1f}%"
    return f"{top}\n[dim]{be}[/dim]"

def _kept_cell(pct):
    """kept% = share of credit banked. >=profit-target reads take-profit-ready."""
    if pct is None:
        return "—"
    c = "green" if pct >= 0 else "red"
    return f"[{c}]{pct:+.0f}%[/{c}]"

def _money(v, dec=0):
    if v is None:
        return "—"
    return (f"[green]+${v:,.{dec}f}[/green]" if v >= 0
            else f"[red]-${abs(v):,.{dec}f}[/red]")


# ---- per-position derivations ---------------------------------------------
def _single_short_leg(legs):
    shorts = [l for l in legs if l.get("option_type") not in (None, "STOCK")
              and l.get("direction") == "SHORT"]
    return shorts[0] if len(shorts) == 1 else None

def _credit_per_share(pos, primary):
    npx = abs(pos.get("net_premium") or 0)
    ctr = (primary or {}).get("contracts") or pos.get("total_contracts") or 1
    return (npx / ctr / 100) if ctr else 0

def _buffers_single_short(a, pos, legs):
    """(strike_pct, be_pct) for CSP / credit vertical -- positive = safe side.
    Uses the short leg's option_type to orient sign and picks the stored
    breakeven when present, else derives K -/+ credit-per-share."""
    spot = a.get("underlying_price")
    sl = _single_short_leg(legs)
    if not (spot and sl):
        return (None, None)
    K = sl.get("strike"); otype = sl.get("option_type")
    cps = _credit_per_share(pos, a.get("primary_leg"))
    if K is None or not otype:
        return (None, None)
    if otype == "PUT":                                   # CSP / bull put
        buf_strike = spot - K
        be = pos.get("breakeven_low") or (K - cps)
        buf_be = spot - be
    else:                                                # bear call
        buf_strike = K - spot
        be = pos.get("breakeven_high") or (K + cps)
        buf_be = be - spot
    return (buf_strike / spot * 100, buf_be / spot * 100)

def _extrinsic(a):
    """mark - intrinsic for the primary leg (the premium still bleeding)."""
    opt = a.get("opt_data") or {}
    mid = opt.get("mid"); spot = a.get("underlying_price")
    prim = a.get("primary_leg") or {}
    K = prim.get("strike"); otype = prim.get("option_type")
    if mid is None or spot is None or K is None or not otype:
        return None
    intrinsic = max(spot - K, 0) if otype == "CALL" else max(K - spot, 0)
    return mid - intrinsic

def _ic_tested(a, pos, legs):
    """(tested_side, buf_pct_to_tested, be_pct_to_tested, sp_K, sc_K) for a
    condor. Off-hours / greek-free: tested side = the short strike spot is
    nearest to (or has breached); sign positive = safe."""
    spot = a.get("underlying_price")
    sp = sc = None
    for l in legs:
        r = (l.get("leg_role") or "").upper()
        if r == "SHORT_PUT":  sp = l
        elif r == "SHORT_CALL": sc = l
    if not (spot and sp and sc):
        return (None, None, None, sp and sp.get("strike"), sc and sc.get("strike"))
    sp_K = sp.get("strike"); sc_K = sc.get("strike")
    dist_put = spot - sp_K            # + when spot above short put (safe)
    dist_call = sc_K - spot           # + when spot below short call (safe)
    if dist_put <= dist_call:
        side, buf = "put", dist_put
        be = pos.get("breakeven_low") or sp_K
        be_buf = spot - be
    else:
        side, buf = "call", dist_call
        be = pos.get("breakeven_high") or sc_K
        be_buf = be - spot
    return (side, buf / spot * 100, be_buf / spot * 100, sp_K, sc_K)


# ---- portfolio pulse header ------------------------------------------------
def _pulse_header(rows):
    """rows: list of dicts with keys ticker, family, pnl (a['pnl_mtm'])."""
    pnls = [r["pnl"] for r in rows if r["pnl"] is not None]
    total = sum(pnls) if pnls else 0
    up = sum(1 for p in pnls if p > 0)
    down = sum(1 for p in pnls if p <= 0)

    # concentration: largest strategy-family share of total drawdown.
    # NOTE: this is a strategy-group proxy for the hand-identified "correlated
    # cluster" (e.g. spec-name CSPs). A thematic-ticker map would sharpen it --
    # flagged for Russ (see accompanying notes).
    draw = {}
    for r in rows:
        if r["pnl"] is not None and r["pnl"] < 0:
            draw[r["family"]] = draw.get(r["family"], 0) + r["pnl"]
    conc_pct = conc_lbl = None
    total_draw = sum(draw.values())
    if total_draw < 0:
        fam, worst = min(draw.items(), key=lambda kv: kv[1])
        conc_pct = round(worst / total_draw * 100)
        n = sum(1 for r in rows if r["family"] == fam and (r["pnl"] or 0) < 0)
        conc_lbl = f"{n} {_FAMILY_META[fam][1]} ({_money(worst)})"

    pnl_col = "green" if total >= 0 else "red"
    card_pnl = (f"[dim]open p&l[/dim]\n[bold {pnl_col}]"
                f"{'+' if total>=0 else '-'}${abs(total):,.0f}[/bold {pnl_col}]\n"
                f"[dim]{len(rows)} open positions[/dim]")
    card_pos = (f"[dim]positions[/dim]\n[bold]{up} up · {down} down[/bold]\n"
                f"[dim]by open p&l[/dim]")
    if conc_pct is not None:
        card_con = (f"[dim]concentration[/dim]\n[bold]{conc_pct}%[/bold]\n"
                    f"[dim]largest {_FAMILY_META[fam][0].lower()} cluster[/dim]")
    else:
        card_con = "[dim]concentration[/dim]\n[bold]--[/bold]\n[dim]no drawdown[/dim]"

    _nth = _nvg = _bwd = 0.0
    _has_g = False
    for _r in rows:
        _a = _r["a"]; _lg = _r.get("legs") or []; _p = _r["pos"]
        _ct = _p.get("total_contracts") or 1
        _th = _pos_greek(_a, _lg, "theta"); _vg = _pos_greek(_a, _lg, "vega"); _nd = _net_delta(_a, _lg)
        if _th is not None:
            _nth += _th * 100 * _ct; _has_g = True
        if _vg is not None:
            _nvg += _vg * 100 * _ct; _has_g = True
        _sp = _a.get("underlying_price")
        if _nd is not None and _sp:
            _bwd += _nd * 100 * _ct * _sp * (_r.get("beta") or 1.0)
    def _gs(v):
        return ("+" if v >= 0 else "-") + "$" + format(abs(v), ",.0f")
    def _gk(v):
        return ("+" if v >= 0 else "-") + "$" + format(abs(v) / 1000, ",.0f") + "k"
    if _has_g:
        card_grk = ("[dim]net greeks (β-wtd)[/dim]\n[bold]Δ " + _gk(_bwd) + "[/bold]\n[dim]θ " + _gs(_nth) + "/day · ν " + _gs(_nvg) + "[/dim]")
    else:
        card_grk = "[dim]net greeks[/dim]\n[bold]— *[/bold]\n[dim]no live greeks[/dim]"
    console.print(Columns(
        [Panel(card_pnl, border_style=pnl_col, width=26),
         Panel(card_pos, border_style="cyan", width=26),
         Panel(card_con, border_style="yellow", width=30),
         Panel(card_grk, border_style="magenta", width=34)],
        equal=False, expand=False,
    ))
    console.print()


# ---- group renderers -------------------------------------------------------
def _grp_header(family, subtotal, n):
    label, code = _FAMILY_META[family]
    console.print(f"[bold]{label}[/bold]  [dim]{code} · {n}[/dim]   {_money(subtotal)}")

def _tbl(cols):
    t = Table(box=box.SIMPLE_HEAD, show_header=True, padding=(0, 2))
    for name, kw in cols:
        t.add_column(name, **kw)
    return t

_R = dict(justify="right", no_wrap=True)

def _spot_cell(spot):
    return f"{spot:,.2f}" if spot is not None else "—"


def _vert_width(legs):
    sh = [l for l in legs if l.get("direction") == "SHORT" and l.get("option_type") not in (None, "STOCK")]
    lo = [l for l in legs if l.get("direction") == "LONG"  and l.get("option_type") not in (None, "STOCK")]
    if len(sh) == 1 and len(lo) == 1 and sh[0].get("strike") is not None and lo[0].get("strike") is not None:
        return abs(sh[0]["strike"] - lo[0]["strike"])
    return None

def _ic_width(legs):
    by = {}
    for l in legs:
        by[(l.get("leg_role") or "").upper()] = l
    def _w(x, y):
        A, B = by.get(x), by.get(y)
        if A and B and A.get("strike") is not None and B.get("strike") is not None:
            return abs(A["strike"] - B["strike"])
        return None
    cands = [w for w in (_w("SHORT_PUT", "LONG_PUT"), _w("SHORT_CALL", "LONG_CALL")) if w is not None]
    return max(cands) if cands else None

def _risk_cell(width, pos):
    npx = abs(pos.get("net_premium") or 0)
    ctr = pos.get("total_contracts") or 1
    if not width:
        return "—"
    ml = width * 100 * ctr - npx
    if ml <= 0:
        return "—"
    ror = npx / ml * 100
    return f"${ml:,.0f}\n[dim]{ror:.0f}% RoR · {width:g}w[/dim]"


def _pos_greek(a, legs, key):
    lg = a.get("leg_greeks") or {}
    tot = 0.0
    have = False
    for l in legs:
        g = lg.get(l.get("id"))
        v = g.get(key) if g else None
        if v is None:
            continue
        have = True
        tot += v * (1 if l.get("direction") == "LONG" else -1)
    return tot if have else None

def _greek_cell(a, legs, pos):
    ctr = pos.get("total_contracts") or 1
    th = _pos_greek(a, legs, "theta")
    vg = _pos_greek(a, legs, "vega")
    if th is None and vg is None:
        return "[dim]— *[/dim]"
    ths = f"{th * 100 * ctr:+,.0f}" if th is not None else "—"
    vgs = f"{vg * 100 * ctr:+,.0f}" if vg is not None else "—"
    return f"{ths}\n[dim]{vgs}[/dim]"

def _ivr_cell(v):
    return f"{v:.0f}" if v is not None else "—"

def _own_cell(ticker):
    """Ownership Quality grade (A-F) from the cache; dash if not yet graded.
    Read-only lookup -- run `helm quality` to populate/refresh the cache."""
    try:
        from helm.ownership import read_cached_grade
        r = read_cached_grade(ticker)
    except Exception:
        r = None
    if not r or not r.get("grade"):
        return "[dim]\u2014[/dim]"
    g = r["grade"]
    color = {"A": "bold green", "B": "green", "C": "yellow",
             "D": "red", "F": "bold red"}.get(g, "white")
    return f"[{color}]{g}[/{color}]"


def _render_csp(rows):
    t = _tbl([("ticker", dict(style="bold cyan", no_wrap=True)), ("dte", _R),
              ("spot", _R), ("strike", _R), ("Δ put", _R), ("θ/ν", _R), ("buf% s/be", _R), ("kept%", _R),
              ("extr", _R), ("p&l", _R), ("credit", _R), ("IVR", _R), ("b/e", _R), ("own", dict(justify="center", no_wrap=True))])
    # sort by |delta| desc (danger first); None deltas sink
    rows.sort(key=lambda r: (r["_delta"] is None, -abs(r["_delta"] or 0)))
    for r in rows:
        a, pos, prim = r["a"], r["pos"], r["a"].get("primary_leg") or {}
        sp, be = _buffers_single_short(a, pos, r["legs"])
        cps = _credit_per_share(pos, prim)
        be_val = (prim.get("strike") - cps) if prim.get("strike") else None
        extr = _extrinsic(a)
        t.add_row(
            r["ticker"], _dte_cell(r["_dte"]), _spot_cell(a.get("underlying_price")), _spot_cell(prim.get("strike")), _delta_cell(r["_delta"]), _greek_cell(a, r["legs"], pos),
            _buf_stack(sp, be), _kept_cell(a.get("pnl_pct")),
            f"{extr:.2f}" if extr is not None else "—",
            _money(a.get("pnl_mtm")),
            f"${abs(pos.get('net_premium') or 0):,.0f}",
            _ivr_cell(r.get("ivr")),
            f"{be_val:.2f}" if be_val is not None else "—",
            _own_cell(r["ticker"]),
        )
    console.print(t)

def _leg_delta(a, leg):
    """Raw model delta for a specific leg from the live per-leg capture."""
    if not leg:
        return None
    g = (a.get("leg_greeks") or {}).get(leg.get("id"))
    return g.get("delta") if g else None


def _net_delta(a, legs):
    """Direction-adjusted net position delta across the structure (per contract):
    long legs +model_delta, short legs -model_delta. None if no leg has greeks."""
    lg = a.get("leg_greeks") or {}
    net = 0.0
    have = False
    for l in legs:
        g = lg.get(l.get("id"))
        d = g.get("delta") if g else None
        if d is None:
            continue
        have = True
        net += d * (1 if l.get("direction") == "LONG" else -1)
    return net if have else None


def _net_delta_cell(v):
    return f"{v:+.2f}" if v is not None else "[dim]— *[/dim]"


def _render_credit(rows):
    t = _tbl([("ticker", dict(style="bold cyan", no_wrap=True)), ("dte", _R),
              ("spot", _R), ("strike", _R), ("Δ short", _R), ("θ/ν", _R), ("buf% s/be", _R), ("kept%", _R),
              ("p&l", _R), ("credit", _R), ("max loss", _R), ("IVR", _R), ("b/e", _R)])
    rows.sort(key=lambda r: (r["a"].get("pnl_pct") is None, r["a"].get("pnl_pct") or 0))
    for r in rows:
        a, pos = r["a"], r["pos"]
        sp, be = _buffers_single_short(a, pos, r["legs"])
        sl = _single_short_leg(r["legs"])
        _w = _vert_width(r["legs"])
        be_val = pos.get("breakeven_high") or pos.get("breakeven_low")
        t.add_row(
            r["ticker"], _dte_cell(r["_dte"]), _spot_cell(a.get("underlying_price")), _spot_cell(sl.get("strike") if sl else None), _delta_cell(_leg_delta(a, sl)), _greek_cell(a, r["legs"], pos),
            _buf_stack(sp, be), _kept_cell(a.get("pnl_pct")),
            _money(a.get("pnl_mtm")),
            f"${abs(pos.get('net_premium') or 0):,.0f}",
            _risk_cell(_w, pos),
            _ivr_cell(r.get("ivr")),
            f"{be_val:.2f}" if be_val else "—",
        )
    console.print(t)

# ---- HELM-085: iron-condor spot-vs-legs strip -----------------------------
def _ic_strip_line(spot, legs, width=48):
    """Compact Rich-markup number line: spot vs the four condor strikes.

    Zones mirror render_csp_position_diagram: red outside the long wings,
    yellow in the wing buffers, green inside the short-strike profit tent.
    Markers: | long wing, ┃ short strike, ● spot. Returns a markup
    string, or None if the data isn't a well-formed 4-leg condor with a spot.
    """
    try:
        puts = sorted(l.get("strike") for l in legs
                      if l.get("option_type") == "PUT" and l.get("strike") is not None)
        calls = sorted(l.get("strike") for l in legs
                       if l.get("option_type") == "CALL" and l.get("strike") is not None)
    except Exception:
        return None
    if spot is None or len(puts) < 2 or len(calls) < 2:
        return None
    lp, sp = puts[0], puts[-1]      # long put (low), short put (high)
    sc, lc = calls[0], calls[-1]    # short call (low), long call (high)
    lo, hi = min(lp, spot), max(lc, spot)
    pad = (hi - lo) * 0.08 or 1.0
    lo -= pad
    hi += pad
    rng = hi - lo
    if rng <= 0:
        return None
    W = int(width)

    def px(p):
        return max(0, min(W - 1, int((p - lo) / rng * (W - 1))))

    # spot inserted last so it wins any column collision with a strike
    marks = {}
    for k, glyph, style in ((lp, "|", "bold"), (lc, "|", "bold"),
                            (sp, "┃", "bold"), (sc, "┃", "bold"),
                            (spot, "●", "bold green")):
        marks[px(k)] = (glyph, style)
    out = []
    for i in range(W):
        if i in marks:
            g, st = marks[i]
            out.append("[%s]%s[/%s]" % (st, g, st))
        else:
            pr = lo + (i / (W - 1)) * rng
            if pr < lp or pr > lc:
                out.append("[red]─[/red]")
            elif pr < sp or pr > sc:
                out.append("[yellow]─[/yellow]")
            else:
                out.append("[green]─[/green]")
    return "".join(out)


def _render_ic_strips(rows):
    """Print a compact spot-vs-legs strip for each iron-condor position,
    directly beneath the IC table (one full-width line per condor)."""
    printed = False
    for r in rows:
        spot = r["a"].get("underlying_price")
        strip = _ic_strip_line(spot, r["legs"])
        if strip is None:
            continue
        if not printed:
            console.print()
            console.print("  [bold dim]spot vs legs[/bold dim]   "
                          "[dim]| wing   ┃ short   ● spot[/dim]")
            printed = True
        console.print("  [bold cyan]%-5s[/bold cyan] %s" % (r["ticker"], strip))


def _render_ic(rows):
    t = _tbl([("ticker", dict(style="bold cyan", no_wrap=True)), ("dte", _R),
              ("spot", _R), ("tested", _R), ("buf% s/be", _R), ("net Δ", _R), ("θ/ν", _R), ("kept%", _R),
              ("p&l", _R), ("short p/c", _R), ("max loss", _R), ("IVR", _R), ("b/e lo–hi", _R)])
    keyed = []
    for r in rows:
        side, buf, be, sp_K, sc_K = _ic_tested(r["a"], r["pos"], r["legs"])
        r["_ic"] = (side, buf, be, sp_K, sc_K)
        keyed.append(r)
    keyed.sort(key=lambda r: (r["_ic"][1] is None, r["_ic"][1] if r["_ic"][1] is not None else 0))
    for r in keyed:
        a, pos = r["a"], r["pos"]
        side, buf, be, sp_K, sc_K = r["_ic"]
        blo, bhi = pos.get("breakeven_low"), pos.get("breakeven_high")
        _w = _ic_width(r["legs"])
        t.add_row(
            r["ticker"], _dte_cell(r["_dte"]), _spot_cell(a.get("underlying_price")), side or "—",
            _buf_stack(buf, be), _net_delta_cell(_net_delta(a, r["legs"])), _greek_cell(a, r["legs"], pos),
            _kept_cell(a.get("pnl_pct")), _money(a.get("pnl_mtm")),
            (f"{sp_K:.0f}p / {sc_K:.0f}c" if sp_K and sc_K else "—"),
            _risk_cell(_w, pos),
            _ivr_cell(r.get("ivr")),
            (f"{blo:.0f}–{bhi:.0f}" if blo and bhi else "—"),
        )
    console.print(t)
    _render_ic_strips(keyed)

def _render_longcall(rows):
    t = _tbl([("ticker", dict(style="bold cyan", no_wrap=True)), ("dte", _R),
              ("spot", _R), ("strike", _R), ("Δ", _R), ("θ/ν", _R), ("vs strike / be", _R), ("extr", _R),
              ("p&l", _R), ("debit", _R), ("b/e", _R), ("iv", _R), ("IVR", _R)])
    rows.sort(key=lambda r: (r["_delta"] is None, abs(r["_delta"] or 0)))
    for r in rows:
        a, pos, prim = r["a"], r["pos"], r["a"].get("primary_leg") or {}
        spot = a.get("underlying_price"); K = prim.get("strike")
        cps = _credit_per_share(pos, prim)
        be_val = (K + cps) if K else None
        if spot and K:
            moneyness = (spot - K) / K * 100
            tag = f"itm {moneyness:.1f}%" if moneyness >= 0 else f"otm {abs(moneyness):.1f}%"
            be_pct = ((spot - be_val) / spot * 100) if be_val and spot else None
            vs = f"{tag}\n[dim]be {be_pct:+.1f}%[/dim]" if be_pct is not None else tag
        else:
            vs = "—"
        extr = _extrinsic(a)
        iv = (a.get("opt_data") or {}).get("iv")
        t.add_row(
            r["ticker"], _dte_cell(r["_dte"]), _spot_cell(a.get("underlying_price")), _spot_cell(prim.get("strike")), _delta_cell(r["_delta"]), _greek_cell(a, r["legs"], pos),
            vs, f"{extr:.2f}" if extr is not None else "—",
            _money(a.get("pnl_mtm")),
            f"-${abs(pos.get('net_premium') or 0):,.0f}",
            f"{be_val:.2f}" if be_val is not None else "—",
            f"{iv:.0f}" if iv is not None else "—",
            _ivr_cell(r.get("ivr")),
        )
    console.print(t)

def _render_other(rows):
    t = _tbl([("ticker", dict(style="bold cyan", no_wrap=True)), ("strategy", dict(no_wrap=True)),
              ("dte", _R), ("spot", _R), ("kept%", _R), ("p&l", _R), ("credit/debit", _R)])
    rows.sort(key=lambda r: (r["a"].get("pnl_mtm") is None, r["a"].get("pnl_mtm") or 0))
    for r in rows:
        a, pos = r["a"], r["pos"]
        t.add_row(r["ticker"], pos.get("strategy", ""), _dte_cell(r["_dte"]), _spot_cell(a.get("underlying_price")),
                  _kept_cell(a.get("pnl_pct")), _money(a.get("pnl_mtm")),
                  f"${abs(pos.get('net_premium') or 0):,.0f}")
    console.print(t)

_GROUP_RENDER = {
    "CSP": _render_csp, "CREDIT_SPREAD": _render_credit, "IC": _render_ic,
    "LONG_CALL": _render_longcall, "OTHER": _render_other,
}


# ---- the new no-flags entry point -----------------------------------------
def cmd_check_all(args):
    """Check all open positions -- per-strategy instrument panel (HELM-074).
    Display-only: check_one runs persist=False, writes nothing."""
    conn = get_conn()
    account_id = get_active_account()
    bc, bp = book_filter(args)
    positions = [dict(r) for r in conn.execute(
        "SELECT * FROM positions WHERE account_id = ? AND status = 'OPEN'" + bc
        + " ORDER BY strategy, ticker", (account_id, *bp)).fetchall()]
    _tks = sorted({p["ticker"] for p in positions})
    try:
        _betas = {r["ticker"]: r["beta"] for r in conn.execute("SELECT ticker, beta FROM watchlist WHERE ticker IN (%s)" % ",".join(["?"] * len(_tks)), _tks)} if _tks else {}
    except Exception:
        _betas = {}
    conn.close()
    try:
        from helm.models.iv_history import IVHistory
        _ivr = {k: (v.iv_rank if v else None) for k, v in IVHistory.for_tickers(_tks).items()}
    except Exception:
        _ivr = {}

    if not positions:
        console.print("[yellow]No open positions.[/yellow]")
        return

    # assess each position (live fetch happens inside check_one; no writes)
    rows = []
    for pos in positions:
        conn = get_conn()
        legs = [dict(r) for r in conn.execute(
            "SELECT * FROM legs WHERE position_id = ?", (pos["id"],)).fetchall()]
        conn.close()
        console.print(f"[dim]Checking {pos['ticker']}...[/dim]", end="\r")
        a = check_one(pos, legs)
        prim = a.get("primary_leg") or {}
        rows.append({
            "ticker": pos["ticker"], "pos": pos, "legs": legs, "a": a,
            "family": _family(pos.get("strategy")),
            "pnl": a.get("pnl_mtm"),
            "_dte": dte(prim["expiration"]) if prim.get("expiration") else None,
            "_delta": (a.get("opt_data") or {}).get("delta"),
            "ivr": _ivr.get(pos["ticker"]), "beta": _betas.get(pos["ticker"]),
        })

    console.print(" " * 40, end="\r")   # clear the progress line
    console.print()
    console.print(Panel.fit(
        f"[bold cyan]helm check[/bold cyan]  [dim]book: {book_label(args)} · "
        f"{len(rows)} open · {market_status_label()}[/dim]",
        border_style="cyan"))
    console.print()

    _pulse_header(rows)

    # exit highlights: compute once (persists flags/history), colour flagged rows
    import io as _io, shutil as _shutil
    import helm.cli.check_cmd as _selfmod
    from rich.console import Console as _CapConsole
    from helm import exit_monitor as _xm
    _hl_items = _xm.evaluate(rows, is_market_open())
    _tcolors = _xm.row_colors(_hl_items, rows)
    _W = _shutil.get_terminal_size((200, 50)).columns

    # render each non-empty group in canonical order (capture plain, colour rows)
    for fam in _FAMILY_ORDER:
        grp = [r for r in rows if r["family"] == fam]
        if not grp:
            continue
        subtotal = sum(r["pnl"] for r in grp if r["pnl"] is not None)
        _cap = _CapConsole(no_color=True, width=_W, file=_io.StringIO())
        _orig = _selfmod.console
        _selfmod.console = _cap
        try:
            _grp_header(fam, subtotal, len(grp))
            _GROUP_RENDER[fam](grp)
        finally:
            _selfmod.console = _orig
        sys.stdout.write(_xm.colorize_group(_cap.file.getvalue(),
                                            {r["ticker"] for r in grp}, _tcolors))
        console.print()

    # _xm.render_panel(_hl_items)  # HELM-069: exit-highlights box removed (row colours kept)

    _check_footer()


# ---- helper the header needs (book pill) -----------------------------------
def book_label(args):
    """REAL / PAPER / ALL pill for the header, mirroring /health (HELM-033)."""
    a = args or []
    if "--all" in a:
        return "ALL"
    if "--paper" in a:
        return "PAPER"
    return "REAL"


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

    _net_pc = round(abs(net_premium) / contracts / 100, 2) if contracts else 0
    prem_str = f"${abs(net_premium):.0f}  ({contracts} x ${_net_pc:.2f})"
    prem_label = "Paid:" if direction == "LONG" else "Collected:"
    console.print(f"  {prem_label:<12} {prem_str}")

    if pnl_mtm is not None:
        pnl_color = "green" if pnl_mtm > 0 else "red"
        pct_str = f"  ({pnl_pct:+.1f}% of premium)" if pnl_pct else ""
        sign = "+" if pnl_mtm >= 0 else "-"
        console.print(f"  Current P&L: [{pnl_color}]{sign}${abs(pnl_mtm):.0f}{pct_str}[/{pnl_color}]")

        # Profit target progress
        target_pct = a.get("profit_target_pct") or 50.0
        if pnl_pct is not None and direction == "SHORT":
            target_dollar = abs(net_premium) * (target_pct / 100)
            console.print(f"  Target:      ${target_dollar:.0f} ({target_pct:.0f}% of premium)  —  {'[green]REACHED[/green]' if pnl_pct >= target_pct else f'[dim]{target_pct - pnl_pct:.0f}% remaining[/dim]'}")
    else:
        console.print(f"  Current P&L: [dim]-- (no option price data)[/dim]")

    # ── Buffer ────────────────────────────────────────────────────────────────
    buf = None
    buf_pct = None
    if spot and strike:
        console.print()
        console.print(f"  [bold]Buffer to Strike[/bold]")

        buf = a.get("intrinsic_buffer")
        if buf is None:
            # Multi-strike position (condor / strangle / jade / straddle /
            # diagonal): a single buffer-to-strike is ambiguous. Show n/a --
            # per-wing detail lives in the legs view.
            _nshort = len([_l for _l in legs if _l.get("option_type") not in (None, "STOCK") and _l.get("direction") == "SHORT"])
            if _nshort >= 2:
                console.print(f"  Strike:      [dim]multi-strike — see legs[/dim]  |  Spot: ${spot:.2f}")
                console.print(f"  Buffer:      [dim]n/a (multi-strike position)[/dim]")
            else:
                console.print(f"  Strike:      [dim]long option — no short strike[/dim]  |  Spot: ${spot:.2f}")
                console.print(f"  Buffer:      [dim]n/a (long debit — no short strike)[/dim]")
        else:
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
    target_pct = assessment.get("profit_target_pct") or 50.0
    target = round(net_premium * (target_pct / 100), 0)
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
    dte_color = 'yellow' if (days_left or 99) <= 7 else 'yellow' if (days_left or 99) <= 21 else 'green'
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
    if profit_pct >= target_pct:
        _rem = '[green]REACHED[/green]'
    else:
        _rem = f'{round(target_pct - profit_pct, 0):.0f}% remaining'
    console.print(f'  Target:      ${target:,.0f} ({target_pct:.0f}% of premium)  —  {_rem}')
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

    _net_pc = round(abs(net_premium) / contracts / 100, 2) if contracts else 0
    prem_str = f"${abs(net_premium):.0f}  ({contracts} x ${_net_pc:.2f})"
    prem_label = "Paid:" if direction == "LONG" else "Collected:"
    console.print(f"  {prem_label:<12} {prem_str}")

    if pnl_mtm is not None:
        pnl_color = "green" if pnl_mtm > 0 else "red"
        pct_str = f"  ({pnl_pct:+.1f}% of premium)" if pnl_pct else ""
        sign = "+" if pnl_mtm >= 0 else "-"
        console.print(f"  Current P&L: [{pnl_color}]{sign}${abs(pnl_mtm):.0f}{pct_str}[/{pnl_color}]")

        # Profit target progress
        target_pct = a.get("profit_target_pct") or 50.0
        if pnl_pct is not None and direction == "SHORT":
            target_dollar = abs(net_premium) * (target_pct / 100)
            console.print(f"  Target:      ${target_dollar:.0f} ({target_pct:.0f}% of premium)  —  {'[green]REACHED[/green]' if pnl_pct >= target_pct else f'[dim]{target_pct - pnl_pct:.0f}% remaining[/dim]'}")
    else:
        console.print(f"  Current P&L: [dim]-- (no option price data)[/dim]")

    # ── Buffer ────────────────────────────────────────────────────────────────
    buf = None
    buf_pct = None
    if spot and strike:
        console.print()
        console.print(f"  [bold]Buffer to Strike[/bold]")

        buf = a.get("intrinsic_buffer")
        if buf is None:
            # Multi-strike position (condor / strangle / jade / straddle /
            # diagonal): a single buffer-to-strike is ambiguous. Show n/a --
            # per-wing detail lives in the legs view.
            _nshort = len([_l for _l in legs if _l.get("option_type") not in (None, "STOCK") and _l.get("direction") == "SHORT"])
            if _nshort >= 2:
                console.print(f"  Strike:      [dim]multi-strike — see legs[/dim]  |  Spot: ${spot:.2f}")
                console.print(f"  Buffer:      [dim]n/a (multi-strike position)[/dim]")
            else:
                console.print(f"  Strike:      [dim]long option — no short strike[/dim]  |  Spot: ${spot:.2f}")
                console.print(f"  Buffer:      [dim]n/a (long debit — no short strike)[/dim]")
        else:
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


# ---- shared footer legend (used by cmd_check_all and cmd_check_one) --------
def _check_footer():
    console.print("[dim][green]Δ<.30[/green] [yellow].30–.60[/yellow] [red]≥.60[/red]  danger gauge = short-strike delta · • = inside 21-day gamma zone · * = stale/off-hours greeks[/dim]")
    console.print("[dim]strike = option strike (short leg on spreads) · buf% = spot→short strike (top) / →breakeven (below) · extr = extrinsic value[/dim]")
    console.print("[dim]θ/ν = position theta $/day (top) / vega $/vol-pt (below), direction-adjusted · IVR = current 52-wk IV rank (0–100)[/dim]")
    console.print("[dim]kept% = credit banked (≥50% = take-profit; neg = giving back) · max loss / RoR·Nw = max risk $, credit÷max-loss, width in pts[/dim]")
    console.print("[dim]net greeks card = β-wtd $delta · net θ/day · net ν · Δ short = tested short-leg delta · net Δ = position delta[/dim]")


def cmd_check_one(ticker: str, deep: bool = False):
    """Check a single position. --deep -> narrative view; otherwise the
    HELM-074 per-strategy panel scoped to this one position."""
    conn = get_conn()
    account_id = get_active_account()
    bc, bp = book_filter(sys.argv)
    pos = conn.execute(
        "SELECT * FROM positions WHERE account_id = ? AND ticker = ? AND status = 'OPEN'" + bc
        + " ORDER BY opened_at DESC LIMIT 1",
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
    snap = conn.execute(
        "SELECT * FROM entry_snapshots WHERE position_id=? ORDER BY created_at DESC LIMIT 1",
        (pos["id"],)
    ).fetchone()
    snap = dict(snap) if snap else {}
    conn.close()

    console.print(f"[dim]Checking {ticker}...[/dim]", end="\r")
    a = check_one(pos, legs, deep=deep)

    if deep:
        strat = pos.get('strategy', '')
        if strat in ('CSP', 'CASH_SECURED_PUT'):
            cmd_check_deep_csp(pos, legs, a, snap)
        elif strat == 'IRON_CONDOR':
            cmd_check_deep_iron_condor(pos, legs, a, snap)
        else:
            cmd_check_deep(pos, legs, a, snap)
        return

    # HELM-074: single-position view reuses the grouped panel renderers.
    prim = a.get("primary_leg") or {}
    row = {
        "ticker": pos["ticker"], "pos": pos, "legs": legs, "a": a,
        "family": _family(pos.get("strategy")),
        "pnl": a.get("pnl_mtm"),
        "_dte": dte(prim["expiration"]) if prim.get("expiration") else None,
        "_delta": (a.get("opt_data") or {}).get("delta"),
    }
    console.print(" " * 40, end="\r")
    console.print()
    console.print(Panel.fit(
        f"[bold cyan]helm check[/bold cyan]  [dim]{ticker.upper()} · "
        f"book: {book_label(sys.argv)} · {market_status_label()}[/dim]",
        border_style="cyan"))
    console.print()
    fam = row["family"]
    _grp_header(fam, row["pnl"], 1)
    _GROUP_RENDER[fam]([row])
    console.print()
    _check_footer()


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


def cmd_snapshot(args):
    """helm snapshot -- sanctioned scheduled writer (HELM-037).

    Computes + persists one live check row per open position, reusing the same
    compute (check_one) and the live-only persistence gate inside save_check.
    Persists the SAME rows the scheduled `helm check --silent` writes today:
    identical open-position set + identical check_one(persist=True) -> save_check
    path. The only difference is that the write lives in this dedicated command
    rather than the overloaded ad-hoc check path. No display table -- this is a
    writer, not a view.
    """
    conn = get_conn()
    account_id = get_active_account()
    bc, bp = book_filter(args)
    positions = [dict(r) for r in conn.execute(
        "SELECT * FROM positions WHERE account_id = ? AND status = 'OPEN'" + bc + " ORDER BY strategy, ticker",
        (account_id, *bp)
    ).fetchall()]
    conn.close()
    if not positions:
        return
    written = 0
    for pos in positions:
        conn = get_conn()
        legs = [dict(r) for r in conn.execute(
            "SELECT * FROM legs WHERE position_id = ?", (pos["id"],)
        ).fetchall()]
        conn.close()
        check_one(pos, legs, persist=True)
        written += 1
    if "--silent" not in args:
        console.print(f"[dim]snapshot: {written} position(s) processed (live-only persist)[/dim]")


def run_snapshot():
    args = sys.argv[1:]
    if not get_active_account():
        console.print("[red]No active account. Run helm setup first.[/red]")
        return
    cmd_snapshot(args)


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
