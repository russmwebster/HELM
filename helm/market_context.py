"""W175 -- record the tape, one row per session day.

RECORDS ONLY.  Nothing in HELM reads `market_context`: both repos were swept
2026-09-19 across .py/.sql/.html and produced twelve hits, all of them
`helm/schema.sql` or session-checkpoint prose.  The table has no model class,
so W155's `cls(**dict(row))` trap cannot apply to it.  `long_exit
.current_context` (W65) reads `signals`, not this table.

DO NOT wire any of this into a gate, a screen, a verdict or a size.
HELM-193: on the real book HELM informs, it does not act.

Spec: claude/HELM-W175-market-context-spec.md

Two rules the writer exists to honour:

  1. A row is written for every session day, even when a source fails.  A
     failed field is NULL and `data_source` says which field failed and why.
     A MISSING row therefore means the agent did not run -- which the audit
     reports -- rather than "the fetch was quiet about it".

  2. The bar's date is asserted against the date being described.  yfinance
     returns an EMPTY frame and raises nothing for a symbol it cannot serve
     (proven 2026-09-19 with a junk symbol), and the worse case is a
     non-empty frame whose last bar is not the day asked for.  A mismatch is
     a recorded failure, never a value.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

# --- stated constants -------------------------------------------------------

# Tier C -- convention, nobody's measured rule.  The band set is NAMED on
# every row so that changing it later cannot make old rows silently
# incomparable.  Bump the name when the numbers move.
VIX_BANDS = ((15.0, "CALM"), (20.0, "NORMAL"), (30.0, "ELEVATED"))
VIX_BAND_SET = "vixbands-v1-15/20/30"

# Data-sufficiency floor, not a market threshold: `helm scan GE,BAC` writes
# two `signals` rows, and a breadth reading over two names is closer to a
# guess than to a measurement.  Below this the cross-sectional columns are
# NULL and the count is recorded anyway.
MIN_CROSS_SECTION = 20

SYMBOLS = {"spx": "^GSPC", "vix": "^VIX", "vix3m": "^VIX3M"}

SMA_FAST = 50
SMA_SLOW = 200

# Enough bars to carry a 200-day SMA plus holidays and a long weekend.
LOOKBACK_DAYS = 420


# --- session days -----------------------------------------------------------

def _calendar():
    """XNYS when importable, else None.  Mirrors helm.market_calendar."""
    try:
        from helm import market_calendar as mc
        return mc._calendar()
    except Exception:
        return None


def is_session_day(d, cal=None):
    cal = cal if cal is not None else _calendar()
    if cal is None:
        return d.weekday() < 5
    try:
        return bool(cal.is_session(d.isoformat()))
    except Exception:
        return d.weekday() < 5


def calendar_name(cal=None):
    cal = cal if cal is not None else _calendar()
    return "XNYS" if cal is not None else "weekday"


def previous_session_day(d=None, cal=None):
    """The session day before `d` (default today, ET-naive)."""
    d = d or date.today()
    cal = cal if cal is not None else _calendar()
    probe = d - timedelta(days=1)
    for _ in range(14):
        if is_session_day(probe, cal):
            return probe
        probe -= timedelta(days=1)
    return probe


def session_days_between(start, end, cal=None):
    cal = cal if cal is not None else _calendar()
    out, probe = [], start
    while probe <= end:
        if is_session_day(probe, cal):
            out.append(probe)
        probe += timedelta(days=1)
    return out


# --- the index fetch --------------------------------------------------------

def index_frame(end_date, fetcher=None, lookback_days=LOOKBACK_DAYS):
    """{symbol_key: {date: close}} for one network trip, reused by backfill.

    `fetcher(yahoo_symbol, start, end) -> {date: close}` is injectable so the
    harness can fake every quote and a difference is the code, not the tape.
    Returns (series, failures) where failures is a list of short reasons.
    """
    start = end_date - timedelta(days=lookback_days)
    end = end_date + timedelta(days=1)
    fetcher = fetcher or _yf_fetch
    series, failures = {}, []
    for key, sym in SYMBOLS.items():
        try:
            got = fetcher(sym, start, end)
        except Exception as e:                      # reported, never swallowed
            series[key] = {}
            failures.append("%s=fetch raised %s" % (key, type(e).__name__))
            continue
        if not got:
            # The silent one: yfinance hands back an empty frame and no
            # exception at all for a symbol it cannot serve.
            series[key] = {}
            failures.append("%s=empty frame, no exception" % key)
        else:
            series[key] = got
    return series, failures


def _yf_fetch(symbol, start, end):
    import warnings
    warnings.filterwarnings("ignore")
    import yfinance as yf
    h = yf.Ticker(symbol).history(start=start.isoformat(), end=end.isoformat())
    if h is None or len(h) == 0:
        return {}
    out = {}
    for ts, close in h["Close"].items():
        try:
            out[ts.date()] = float(close)
        except Exception:
            continue
    return out


def _band(vix):
    if vix is None:
        return None
    for ceiling, name in VIX_BANDS:
        if vix < ceiling:
            return name
    return "STRESSED"


def _sma(closes_by_date, target, n):
    """Mean of the last `n` bars up to AND INCLUDING target, else None."""
    dates = sorted(d for d in closes_by_date if d <= target)
    if len(dates) < n or not dates or dates[-1] != target:
        return None
    window = dates[-n:]
    return sum(closes_by_date[d] for d in window) / float(n)


def _change(closes_by_date, target, back):
    dates = sorted(d for d in closes_by_date if d <= target)
    if len(dates) < back + 1 or dates[-1] != target:
        return None
    return closes_by_date[dates[-1]] - closes_by_date[dates[-1 - back]]


def index_reading(series, target):
    """The index half of one row.  Asserts each bar IS the target's bar."""
    r = {"vix": None, "spx_price": None, "term_structure_value": None,
         "vix_regime": None, "term_structure": None,
         "spx_vs_sma50": None, "spx_vs_sma200": None, "index_trend": None,
         "sma50": None, "sma200": None,
         "vix_chg_1d": None, "vix_chg_5d": None}
    failures = []

    for key in ("spx", "vix", "vix3m"):
        got = series.get(key) or {}
        if not got:
            failures.append("%s=no data" % key)
        elif target not in got:
            # The dangerous case: a non-empty frame whose last bar is not the
            # day we are describing.  Never take it as the day's close.
            last = max(got) if got else None
            failures.append("%s=no bar for %s (last %s)" % (key, target, last))

    spx = (series.get("spx") or {}).get(target)
    vix = (series.get("vix") or {}).get(target)
    v3m = (series.get("vix3m") or {}).get(target)

    if vix is not None:
        r["vix"] = round(vix, 2)
        r["vix_regime"] = _band(vix)
        c1 = _change(series.get("vix") or {}, target, 1)
        c5 = _change(series.get("vix") or {}, target, 5)
        r["vix_chg_1d"] = None if c1 is None else round(c1, 2)
        r["vix_chg_5d"] = None if c5 is None else round(c5, 2)

    if vix is not None and v3m is not None:
        ts = v3m - vix
        r["term_structure_value"] = round(ts, 2)
        r["term_structure"] = "CONTANGO" if ts >= 0 else "BACKWARDATION"

    if spx is not None:
        r["spx_price"] = round(spx, 2)
        s50 = _sma(series.get("spx") or {}, target, SMA_FAST)
        s200 = _sma(series.get("spx") or {}, target, SMA_SLOW)
        r["sma50"] = None if s50 is None else round(s50, 2)
        r["sma200"] = None if s200 is None else round(s200, 2)
        if s50 is None:
            failures.append("sma50=fewer than %d bars to %s" % (SMA_FAST, target))
        if s200 is None:
            failures.append("sma200=fewer than %d bars to %s" % (SMA_SLOW, target))
        if s50 is not None:
            r["spx_vs_sma50"] = "ABOVE" if spx > s50 else "BELOW"
        if s200 is not None:
            r["spx_vs_sma200"] = "ABOVE" if spx > s200 else "BELOW"
        if s50 is not None and s200 is not None:
            # Decision 2 (Russ, 2026-09-19): two signs, no chosen number.
            # Re-derivable from spx_price + the SMAs stored in notes.
            above50, above200 = spx > s50, spx > s200
            r["index_trend"] = ("UPTREND" if (above50 and above200) else
                                "RECOVERING" if (above50 and not above200) else
                                "PULLBACK" if (above200 and not above50) else
                                "DOWNTREND")
    return r, failures


# --- the cross-section, from our own record ---------------------------------

def cross_section(conn, target):
    """Breadth and the IV cross-section for `target`, from OUR rows.

    Breadth is computed from `spot_price` vs `sma_50` directly:
    `signals.price_vs_sma50` is NULL on every row swept (0 of 107 on
    2026-09-18, 0 of 326 on 2026-06-10) -- W54's census, in a column.

    Deduped to the latest row per ticker per day: 2026-09-16 and 2026-09-11
    carry 212 rows, not 106, because the scan ran twice.
    """
    r = {"breadth": None, "above200": None, "n_scanned": 0,
         "iv_rank_median": None, "iv_rank_ge50": None, "n_iv": 0}
    failures = []
    d = target.isoformat()

    rows = conn.execute(
        "SELECT spot_price, sma_50, sma_200 FROM signals s "
        "WHERE date(s.generated_at) = ? AND s.generated_at = ("
        "  SELECT MAX(s2.generated_at) FROM signals s2 "
        "  WHERE s2.ticker = s.ticker AND date(s2.generated_at) = ?)",
        (d, d)).fetchall()
    r["n_scanned"] = len(rows)
    if not rows:
        failures.append("breadth=no scan that day")
    elif len(rows) < MIN_CROSS_SECTION:
        failures.append("breadth=only %d names scanned (floor %d)"
                        % (len(rows), MIN_CROSS_SECTION))
    else:
        ok50 = [x for x in rows if x[0] is not None and x[1] is not None]
        ok200 = [x for x in rows if x[0] is not None and x[2] is not None]
        if ok50:
            r["breadth"] = round(
                100.0 * sum(1 for x in ok50 if x[0] > x[1]) / len(ok50), 1)
        if ok200:
            r["above200"] = round(
                100.0 * sum(1 for x in ok200 if x[0] > x[2]) / len(ok200), 1)

    ivs = [x[0] for x in conn.execute(
        "SELECT iv_rank FROM iv_history WHERE date = ? AND iv_rank IS NOT NULL",
        (d,)).fetchall()]
    r["n_iv"] = len(ivs)
    if not ivs:
        failures.append("iv_rank=no iv_history that day")
    elif len(ivs) < MIN_CROSS_SECTION:
        failures.append("iv_rank=only %d names (floor %d)"
                        % (len(ivs), MIN_CROSS_SECTION))
    else:
        s = sorted(ivs)
        mid = len(s) // 2
        med = s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0
        r["iv_rank_median"] = round(med, 1)
        r["iv_rank_ge50"] = sum(1 for x in ivs if x >= 50)

    return r, failures


# --- the row ----------------------------------------------------------------

def build_row(conn, target, series=None, fetcher=None, backfill=False,
              cal=None, fetch_failures=None):
    """One market_context row for `target`.  Never raises for a bad source.

    `series` is injectable so ONE network trip can serve many days (the
    backfill walks 100+), and so the harness can fake every quote.  When it
    is passed, `fetch_failures` carries forward what that single fetch
    reported -- otherwise the "empty frame, no exception" wording, which is
    the whole point of the silent-failure case, would be lost.
    """
    if series is None:
        series, fetch_failures = index_frame(target, fetcher=fetcher)
    else:
        fetch_failures = list(fetch_failures or [])

    idx, idx_failures = index_reading(series, target)
    xs, xs_failures = cross_section(conn, target)
    failures = fetch_failures + idx_failures + xs_failures

    notes = {
        "sma50": idx["sma50"], "sma200": idx["sma200"],
        "above_sma200_pct": xs["above200"],
        "vix_chg_1d": idx["vix_chg_1d"], "vix_chg_5d": idx["vix_chg_5d"],
        "n_scanned": xs["n_scanned"], "n_iv": xs["n_iv"],
        "calendar": calendar_name(cal),
    }
    if failures:
        notes["failed"] = failures

    src = ["yfinance:^GSPC,^VIX,^VIX3M(close)", "signals", "iv_history",
           "bands=" + VIX_BAND_SET]
    if failures:
        src.append("FAILED[" + " | ".join(failures) + "]")
    data_source = ("BACKFILL: " if backfill else "") + " + ".join(src)

    return {
        "id": target.isoformat(),
        "as_of_date": target.isoformat(),
        "vix": idx["vix"],
        "vix_regime": idx["vix_regime"],
        "spx_price": idx["spx_price"],
        "spx_vs_sma50": idx["spx_vs_sma50"],
        "spx_vs_sma200": idx["spx_vs_sma200"],
        "index_trend": idx["index_trend"],
        "term_structure": idx["term_structure"],
        "term_structure_value": idx["term_structure_value"],
        "breadth": xs["breadth"],
        "iv_rank_median": xs["iv_rank_median"],
        "iv_rank_ge50": xs["iv_rank_ge50"],
        "notes": json.dumps(notes, sort_keys=True),
        "data_source": data_source,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }, failures


COLUMNS = ("id", "as_of_date", "vix", "vix_regime", "spx_price",
           "spx_vs_sma50", "spx_vs_sma200", "index_trend", "term_structure",
           "term_structure_value", "breadth", "iv_rank_median",
           "iv_rank_ge50", "notes", "data_source", "created_at")


def write_row(conn, row, replace=False):
    """`id` IS the session date, and it is the PRIMARY KEY, so one row per
    day is enforced by the schema as it stands -- no migration, no index.

    The daily writer may REPLACE (its own day, re-run).  The backfill must
    never overwrite a contemporaneous row, so it INSERTs OR IGNOREs.
    """
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    sql = "%s INTO market_context (%s) VALUES (%s)" % (
        verb, ", ".join(COLUMNS), ", ".join("?" * len(COLUMNS)))
    cur = conn.execute(sql, tuple(row[c] for c in COLUMNS))
    conn.commit()
    return cur.rowcount


def has_row(conn, target):
    d = target if isinstance(target, str) else target.isoformat()
    return conn.execute(
        "SELECT 1 FROM market_context WHERE as_of_date = ?", (d,)
    ).fetchone() is not None


def record_for_previous_session(conn, now=None, fetcher=None, fill_back=3):
    """The 09:35 entrypoint.  Writes the previous session day, and fills any
    still-missing session day in the last `fill_back` as BACKFILL -- so a
    Monday the Mac was off does not lose Friday for good.

    Returns a short one-line status for the log and the ledger note.
    """
    cal = _calendar()
    today = (now or datetime.now()).date()
    target = previous_session_day(today, cal)

    # ONE network trip serves the target day and any gap behind it.
    series, fetch_failures = index_frame(target, fetcher=fetcher)

    written, notes = [], []
    row, failures = build_row(conn, target, series=series, cal=cal,
                              fetch_failures=fetch_failures)
    write_row(conn, row, replace=True)
    written.append(target.isoformat())
    if failures:
        notes.append("%s: %d source failure(s)" % (target, len(failures)))

    earlier = session_days_between(target - timedelta(days=fill_back * 3),
                                   target - timedelta(days=1), cal)
    for d in earlier[-fill_back:]:
        if has_row(conn, d):
            continue
        r2, f2 = build_row(conn, d, series=series, backfill=True, cal=cal,
                           fetch_failures=fetch_failures)
        if write_row(conn, r2, replace=False):
            written.append(d.isoformat() + "(backfill)")

    status = "market_context: wrote " + ", ".join(written)
    if notes:
        status += " -- " + "; ".join(notes)
    return status
