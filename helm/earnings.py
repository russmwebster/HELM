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
        _valid_future = _du is not None and _du >= 0
        if not force and _fundamentals_fresh(r["last_fundamentals_at"], stale_days) and _valid_future:
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
