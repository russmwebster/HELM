# helm/hv_earnings.py
# HELM-101 G3 / G5 support: ex-earnings realized vol + the earnings-date cache
# it needs. Read-only at scan time; the cache is refreshed by an explicit call.
#
# Why this module exists: G3 gates the buy side on IV <= 0.90 * HV90_ex-earnings.
# A raw HV90 is contaminated by 1-2 earnings prints per 90-day window, which
# inflates realized vol and makes cheap-looking IV look cheaper than it is.
#
# Honesty rule (addendum s6/s7): when earnings dates are unavailable we return
# the PLAIN HV90 tagged source='plain'. We never present an untrimmed number as
# ex-earnings. G3 decides what to do with a 'plain' read; it is not this
# module's job to fake one.

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional

# Trading days excluded on each side of a print (the move itself + the drift
# day after it). 1 => a 3-day exclusion window per print.
EARNINGS_EXCL_DAYS = 1

# How many past prints to keep per name. 8 quarters covers a 2y lookback,
# comfortably more than the 90d window ever needs.
EARNINGS_LIMIT = 8

# Cache staleness before a refresh is attempted.
EARNINGS_STALE_DAYS = 7


# -- schema -----------------------------------------------------------------

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS earnings_history (
    ticker      TEXT NOT NULL,
    earn_date   TEXT NOT NULL,           -- YYYY-MM-DD
    source      TEXT DEFAULT 'yfinance',
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (ticker, earn_date)
);
"""


def ensure_table(conn):
    conn.executescript(CREATE_SQL)


# -- cache read/write -------------------------------------------------------

def cached_dates(conn, ticker: str) -> list[str]:
    """Past earnings dates for a ticker, newest first. Empty list if none."""
    try:
        rows = conn.execute(
            'SELECT earn_date FROM earnings_history WHERE ticker = ? '
            'ORDER BY earn_date DESC', (ticker.upper(),)).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []


def load_earnings_map(conn) -> dict:
    """Whole cache as {TICKER: [dates]} -- one read for a full scan."""
    out = {}
    try:
        for r in conn.execute(
                'SELECT ticker, earn_date FROM earnings_history '
                'ORDER BY ticker, earn_date DESC'):
            out.setdefault(r[0], []).append(r[1])
    except Exception:
        pass
    return out


def _last_fetch(conn, ticker: str) -> Optional[str]:
    try:
        row = conn.execute(
            'SELECT MAX(fetched_at) FROM earnings_history WHERE ticker = ?',
            (ticker.upper(),)).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def refresh_earnings_history(conn, tickers, force: bool = False,
                             stale_days: int = EARNINGS_STALE_DAYS,
                             limit: int = EARNINGS_LIMIT) -> dict:
    """Fetch past earnings dates from yfinance into the cache.

    Network call per eligible ticker. Returns a per-ticker report dict:
    {'TICKER': ('ok', n) | ('skipped', 'fresh') | ('error', msg)}.
    Requires lxml (yfinance's get_earnings_dates parses HTML) -- absence is
    reported per ticker, not raised.
    """
    ensure_table(conn)
    report = {}
    cutoff = (datetime.now() - timedelta(days=stale_days)).isoformat()
    for t in [str(x).upper() for x in tickers]:
        if not force:
            last = _last_fetch(conn, t)
            if last and last > cutoff:
                report[t] = ('skipped', 'fresh')
                continue
        try:
            import yfinance as yf
            df = yf.Ticker(t).get_earnings_dates(limit=limit)
            if df is None or len(df) == 0:
                report[t] = ('ok', 0)
                continue
            now = datetime.now().isoformat()
            n = 0
            for idx in df.index:
                d = str(idx)[:10]
                if len(d) != 10 or not d[:4].isdigit():
                    continue
                conn.execute(
                    'INSERT OR REPLACE INTO earnings_history '
                    '(ticker, earn_date, source, fetched_at) VALUES (?,?,?,?)',
                    (t, d, 'yfinance', now))
                n += 1
            conn.commit()
            report[t] = ('ok', n)
        except Exception as e:
            report[t] = ('error', type(e).__name__ + ': ' + str(e)[:80])
    return report


# -- realized vol -----------------------------------------------------------

def _annualize(rets) -> Optional[float]:
    try:
        if len(rets) < 20:
            return None
        return round(float(rets.std(ddof=1)) * math.sqrt(252) * 100, 1)
    except Exception:
        return None


def hv_from_closes(close, window: int = 90) -> Optional[float]:
    """Plain annualized HV (%) over the last <window> trading days."""
    try:
        c = close.dropna()
        if len(c) - 1 < max(20, int(window * 0.8)):
            return None
        rets = (c / c.shift(1)).apply(math.log).dropna().iloc[-window:]
        return _annualize(rets)
    except Exception:
        return None


def hv_ex_earnings(close, earn_dates, window: int = 90):
    """(hv, source) -- HV over <window> days with earnings moves removed.

    source is 'dates' when at least one print was excluded, 'plain' when no
    usable earnings dates were available (the value is then an untrimmed HV --
    caller must not label it ex-earnings), or 'dates-none' when dates exist but
    none fell inside the window (the trimmed and plain numbers are identical
    and both are honest).
    """
    try:
        c = close.dropna()
        if len(c) - 1 < max(20, int(window * 0.8)):
            return (None, None)
        rets = (c / c.shift(1)).apply(math.log).dropna().iloc[-window:]
        if not earn_dates:
            return (_annualize(rets), 'plain')
        # index may be tz-aware; compare on the date string only
        idx_dates = [str(x)[:10] for x in rets.index]
        excl = set()
        for d in earn_dates:
            d = str(d)[:10]
            if d not in idx_dates:
                # print may fall outside the window, or on a non-trading day;
                # nearest-trading-day handling is covered by the +/- span below
                pass
            for i, cur in enumerate(idx_dates):
                if abs((datetime.fromisoformat(cur)
                        - datetime.fromisoformat(d)).days) <= EARNINGS_EXCL_DAYS:
                    excl.add(i)
        if not excl:
            return (_annualize(rets), 'dates-none')
        keep = [r for i, r in enumerate(rets) if i not in excl]
        if len(keep) < 20:
            return (_annualize(rets), 'plain')
        import statistics
        sd = statistics.stdev(keep)
        return (round(sd * math.sqrt(252) * 100, 1), 'dates')
    except Exception:
        return (None, None)
