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


def refresh_watchlist_earnings(conn, tickers=None, force=False, stale_days=7, max_fetch=12, fetch=None):
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
        ed = (fetch or fetch_earnings_date)(tk)
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
def classify_earnings(next_earnings, ref=None):
    """Pure classifier: (days_to, severity) from a date string. No DB / network.

    severity: 'warn' (within EARNINGS_WARN_DAYS), 'ok' (beyond the window),
    'past' (already elapsed), 'unknown' (missing / unparseable). Shared by the
    open banner (via earnings_state) and the scan cell so both agree on state.
    """
    if not next_earnings:
        return (None, "unknown")
    d = days_until(next_earnings, ref)
    if d is None:
        return (None, "unknown")
    if d < 0:
        return (d, "past")
    if d <= EARNINGS_WARN_DAYS:
        return (d, "warn")
    return (d, "ok")


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
    d, sev = classify_earnings(ne)
    return (ne, d, sev)


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


def _refresh_earnings(conn):
    """Refresh positions.earnings_date for OPEN positions with null/stale dates.
    Relocated from helm/health.py during the HELM-033 /health retire.
    Used by helm ivr (ivr_cmd) before computing IVR."""
    try:
        import yfinance as yf
        from datetime import date
        today = date.today().isoformat()
        rows = conn.execute(
            "SELECT id, ticker FROM positions WHERE status='OPEN'"
            " AND (earnings_date IS NULL OR earnings_date < ?)"
            , (today,)
        ).fetchall()
        for pos_id, ticker in rows:
            try:
                cal = yf.Ticker(ticker).calendar
                ed = None
                if isinstance(cal, dict) and 'Earnings Date' in cal:
                    dates = cal['Earnings Date']
                    if dates:
                        ed = str(dates[0])[:10]
                if ed and ed not in ('NaT', 'None', ''):
                    conn.execute('UPDATE positions SET earnings_date=? WHERE id=?', (ed, pos_id))
                else:
                    conn.execute('UPDATE positions SET earnings_date=NULL WHERE id=?', (pos_id,))
            except Exception:
                pass
        conn.commit()
    except Exception:
        pass


# s160 (Russ, 2026-11-17): the watchlist's report dates were refreshed only inside the old entry
# scan, which no longer runs -- on 11-17, 89 of 135 active names carried a date already passed and
# 24 had none, and DELL's showed 11-27 (an October estimate) while Dell had announced 11-24. The
# put list, covered calls, the PMCC search and Today's earnings marks all read watchlist.next_earnings.
# The 09:35 pre-market run (helm ivr refresh) now calls refresh_morning: every name HELM holds is
# re-read each day -- the watchlist and that name's open positions get the same date -- then the
# stocks checked longest ago, MORNING_BATCH a day, each re-read after MORNING_STALE_DAYS (the same
# gate as refresh_watchlist_earnings, which does the fetching). Approved funds have no earnings.
MORNING_BATCH = 35
MORNING_STALE_DAYS = 7


def _funds(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='approved_funds'").fetchone():
        return set()
    return {r[0] for r in conn.execute("SELECT ticker FROM approved_funds")}


def held_names(conn):
    """Tickers with an open position or HELM-held shares, A-Z, approved funds left out."""
    names = {r[0] for r in conn.execute("SELECT DISTINCT ticker FROM positions WHERE status='OPEN'")}
    try:
        from helm.stockreads import held_tickers
        names |= set(held_tickers(conn))
    except Exception:
        pass
    return sorted(names - _funds(conn))


def refresh_morning(conn, fetch=None):
    """Refresh report dates for the held names (every day) and the oldest-checked stocks.
    Returns a summary dict; writes watchlist.next_earnings / last_fundamentals_at and, for held
    names, positions.earnings_date of their OPEN positions. Never touches anything else.
    `fetch` (ticker -> 'YYYY-MM-DD' or None) is for tests; the default is fetch_earnings_date."""
    from datetime import datetime
    fetch = fetch or fetch_earnings_date
    before = {r[0]: r[1] for r in conn.execute("SELECT ticker, next_earnings FROM watchlist")}
    now = datetime.now().isoformat()
    held = held_names(conn)
    held_ok, held_failed = 0, []
    for tk in held:
        ed = fetch(tk)
        if ed is None:
            held_failed.append(tk)
            continue
        conn.execute("UPDATE watchlist SET next_earnings = ?, last_fundamentals_at = ? WHERE ticker = ?",
                     (ed, now, tk))
        conn.execute("UPDATE positions SET earnings_date = ? WHERE ticker = ? AND status = 'OPEN'", (ed, tk))
        held_ok += 1
    conn.commit()
    funds = _funds(conn)
    rest = [r[0] for r in conn.execute("SELECT ticker FROM watchlist WHERE active = 1 ORDER BY ticker")
            if r[0] not in funds and r[0] not in set(held)]
    summ = refresh_watchlist_earnings(conn, tickers=rest, stale_days=MORNING_STALE_DAYS,
                                      max_fetch=MORNING_BATCH, fetch=fetch) if rest else {}
    after = {r[0]: r[1] for r in conn.execute("SELECT ticker, next_earnings FROM watchlist")}
    changed = [(t, before.get(t), after[t]) for t in sorted(after) if after[t] != before.get(t)]
    return {"held": len(held), "held_ok": held_ok, "held_failed": held_failed,
            "fetched": summ.get("updated", 0), "cached": summ.get("cached", 0),
            "failed": summ.get("failed", 0), "deferred": summ.get("deferred", 0), "changed": changed}


def morning_line(s):
    """One line for the ivr log."""
    ch = ", ".join("%s %s -> %s" % (t, (o or "none")[:10], (n or "none")[:10]) for t, o, n in s["changed"][:12])
    more = " (+%d more)" % (len(s["changed"]) - 12) if len(s["changed"]) > 12 else ""
    return ("earnings dates: held %d read, %d failed%s; watchlist %d fetched, %d cached, %d failed, %d left for "
            "later days; changed %d%s%s" % (
                s["held_ok"], len(s["held_failed"]),
                (" (" + ", ".join(s["held_failed"]) + ")") if s["held_failed"] else "",
                s["fetched"], s["cached"], s["failed"], s["deferred"], len(s["changed"]),
                (": " + ch) if ch else "", more))
