# HELM-EARN-HELPER-v1
"""helm/earnings.py -- shared earnings-date utilities (additive).

Extracted from health._refresh_earnings so the scan and entry paths can reuse
the same proven yfinance fetch. health._refresh_earnings is left untouched.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

# Warn if an earnings print falls within this many days (the max DTE target).
EARNINGS_WARN_DAYS = 45


def fetch_earnings_date(ticker: str) -> Optional[str]:
    """Next earnings date as 'YYYY-MM-DD', or None. Network call (yfinance)."""
    try:
        import yfinance as yf
        cal = yf.Ticker(ticker).calendar
        ed = None
        if isinstance(cal, dict) and "Earnings Date" in cal:
            dates = cal["Earnings Date"]
            if dates:
                ed = str(dates[0])[:10]
        if ed and ed not in ("NaT", "None", ""):
            return ed
    except Exception:
        pass
    return None


def days_until(earnings_date: Optional[str], ref: Optional[str] = None) -> Optional[int]:
    """Whole days from ref (default today) to earnings_date; None if unparseable."""
    if not earnings_date:
        return None
    try:
        ed = date.fromisoformat(str(earnings_date)[:10])
        rd = date.fromisoformat(str(ref)[:10]) if ref else date.today()
        return (ed - rd).days
    except Exception:
        return None


def earnings_warning(days: Optional[int], threshold: int = EARNINGS_WARN_DAYS) -> int:
    """1 if earnings is upcoming within `threshold` days (not past), else 0."""
    if days is None:
        return 0
    return 1 if 0 <= days <= threshold else 0


# HELM-EARN-REFRESH-v1
def _fundamentals_fresh(last_fundamentals_at, stale_days):
    """True if last_fundamentals_at is within stale_days of now."""
    if not last_fundamentals_at:
        return False
    try:
        from datetime import datetime
        d = datetime.fromisoformat(str(last_fundamentals_at)[:19])
        return (datetime.now() - d).days < stale_days
    except Exception:
        return False


def refresh_watchlist_earnings(conn, tickers=None, force=False, stale_days=7, max_fetch=12):
    """Refresh watchlist.next_earnings for the active universe (or given tickers).

    Gated by last_fundamentals_at staleness. Fetches at most max_fetch stale names per
    call (never-fetched / oldest-stamped first) to avoid bursting yfinance, which the
    scan also uses. last_fundamentals_at is stamped ONLY on a successful fetch, so a
    throttled or failed lookup retries on a later scan rather than caching a NULL as
    fresh. Returns a summary dict.

    Reference data only -- writes next_earnings + last_fundamentals_at, never the book.
    """
    from datetime import datetime
    if tickers:
        tks = [t.upper() for t in tickers]
        ph = ",".join("?" for _ in tks)
        rows = conn.execute(
            "SELECT ticker, last_fundamentals_at, next_earnings FROM watchlist WHERE ticker IN (" + ph + ") "
            "ORDER BY last_fundamentals_at ASC",
            tks,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT ticker, last_fundamentals_at, next_earnings FROM watchlist WHERE active = 1 "
            "ORDER BY last_fundamentals_at ASC, ticker"
        ).fetchall()

    stale = []
    cached = 0
    for r in rows:
        # HELM-044-L2: eligible when there is no date yet, when the cached
        # date has already passed, or when the shared fundamentals timestamp
        # is stale. Gating on next_earnings (the target column) stops NULL /
        # passed rows hiding behind a timestamp other refresh paths keep fresh.
        _du = days_until(r["next_earnings"]) if r["next_earnings"] else None
        # HELM-044-L2b: skip any dated + fresh name, including an already-passed
        # date. A stale-source past date (e.g. FDX still showing last quarter's
        # print until yfinance rolls forward) otherwise stays eligible on every
        # pass and re-fetches forever, burning a scan fetch slot each time.
        # Passed dates still re-fetch -- throttled to the staleness cadence.
        _have_date = _du is not None
        if not force and _fundamentals_fresh(r["last_fundamentals_at"], stale_days) and _have_date:
            cached += 1
        else:
            stale.append(r["ticker"])

    batch = stale[:max_fetch]
    now = datetime.now().isoformat()
    updated = failed = 0
    for tk in batch:
        ed = fetch_earnings_date(tk)
        if ed is None:
            failed += 1
            continue  # do NOT stamp -- let it retry on a later scan
        try:
            conn.execute(
                "UPDATE watchlist SET next_earnings = ?, last_fundamentals_at = ? WHERE ticker = ?",
                (ed, now, tk),
            )
            updated += 1
        except Exception:
            failed += 1
    conn.commit()
    return {
        "checked": len(rows),
        "stale": len(stale),
        "updated": updated,
        "cached": cached,
        "failed": failed,
        "deferred": max(0, len(stale) - len(batch)),
    }


# HELM-044-L1: entry-surface earnings helpers (cache-sourced, no network).
def earnings_state(ticker, conn=None):
    """Return (next_earnings, days_to, severity) from the watchlist cache.

    No network. severity:
      'warn'    -- dated, upcoming, within EARNINGS_WARN_DAYS
      'ok'      -- dated, upcoming, beyond the window
      'past'    -- cached date already elapsed (source not yet rolled forward)
      'unknown' -- not cached / NULL / unparseable
    """
    own = conn is None
    if own:
        from helm.db import get_conn
        conn = get_conn()
    try:
        row = conn.execute(
            "SELECT next_earnings FROM watchlist WHERE ticker = ?", (str(ticker).upper(),)
        ).fetchone()
    finally:
        if own:
            conn.close()
    ne = row[0] if row else None
    if not ne:
        return (None, None, "unknown")
    d = days_until(ne)
    if d is None:
        return (ne, None, "unknown")
    if d < 0:
        return (ne, d, "past")
    if d <= EARNINGS_WARN_DAYS:
        return (ne, d, "warn")
    return (ne, d, "ok")


def earnings_banner_line(ticker, conn=None):
    """Rich-markup one-liner for the entry header, or None. Cache-sourced.

    Dates are unconfirmed yfinance estimates -- marked 'est'. A passed date is
    never shown as an upcoming warning; a missing date renders 'unknown', never
    blank (a blank would read as 'no earnings risk').
    """
    ne, d, sev = earnings_state(ticker, conn=conn)
    if sev == "warn":
        return f"  [yellow][!] Earnings: {ne} ({d}d, est) -- inside entry window[/yellow]"
    if sev == "ok":
        return f"  [dim]Earnings: {ne} ({d}d out, est)[/dim]"
    if sev == "past":
        return f"  [dim]Earnings: last known {ne} has passed -- no confirmed upcoming date[/dim]"
    return "  [dim yellow]Earnings: unknown (not in cache)[/dim yellow]"
