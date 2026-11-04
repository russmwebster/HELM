"""helm/pmcctool.py -- new PMCC entries (s147, 2026-11-02): `helm pmcc`.

The rules (rulebook v0.2 section 6; Russ 2026-11-01 and 11-02; claude/HELM-pmcc-plan.md):
  * universe -- grade A, B or C, or an approved fund (FUND). Not a company that
    already has a PMCC (or run-off diagonal) open: one per name (GOOG + GOOGL are one).
  * long -- 270-730 days, 0.75-0.85 delta, spread < 10% of mid, a live IBKR quote.
  * short -- 30-60 days, 0.20-0.30 delta, spread < 10% of mid, expiring before the long.
  * the width test passes at entry, with the long priced at its ASK (the price you are
    likely to pay): the strike gap must exceed the long's cost a share. One function --
    helm.fillopens.width_test -- never a copy. A pair that passes only at the mid is
    left off and says so. (The short's price is not part of the test as HELM defines
    it: rent counts once a short is closed.)
  * per name, the passing pair with the highest RENT COVER: the short's monthly rent
    (mid / days x 30) over the long's monthly loss of time value (time value at mid /
    days x 30). Linear decay overstates a long-dated option's early decay, so the
    number is conservative. Flagged below 2x, never a gate.
  * the list -- one pair per company, ranked by rent cover, ties by the yearly return
    on the long's cost (short mid / long mid x 365 / short days). Top 5 shown; every
    name's best pair is recorded.
  * shown, never gates: trend (the 200-day average and the 50-day's slope over 10
    trading days, from a year of IBKR daily bars; DOWNTREND = below the 200-day with
    the 50-day falling), IV rank, earnings inside the short (allowed, flagged).
  * sizing -- one contract per name (the rule until there is a record), checked against
    the sleeve, name and sector room at the long's cost; 0 = "would be declined".

HELM informs, never acts. This module reads the database and IBKR and writes only its
own tables (pmcc_runs, pmcc_candidates; pmcc_entry_trend is written by the import).
"""
from __future__ import annotations

import json
import math
import time
import uuid
from datetime import date, datetime

from helm import puttool as P

RULE_VERSION = "pmcc-v2 (rulebook v0.2 s6; ranked by rent cover, Russ 2026-11-02; short 30-60 days, Russ 2026-11-03)"

PASSING = P.PASSING
LONG_DTE = (270, 730)
LONG_DELTA = (0.75, 0.85)
SHORT_DTE = (30, 60)             # s149 (Russ 2026-11-03): was 30 to 45
SHORT_DELTA = (0.20, 0.30)
SPREAD_MAX_PCT = 10.0             # strictly below, as everywhere
RENT_COVER_FLAG = 2.0
TOP_N = 5
PROFIT_TAKE = 0.50
LOSS_LIMIT = 0.50                 # whole trade at or below -half the long's cost
NET_LONG_DELTA = (0.65, 0.95)     # the net cast before the rules, so reasons are true
NET_SHORT_DELTA = (0.15, 0.40)
TREND_SLOPE_BARS = 10
RATE = 0.045
IBKR_CLIENT = 15                  # the chain reader's own number; used one connection at a time

TABLES_SQL = """
CREATE TABLE IF NOT EXISTS pmcc_runs (
    id            TEXT PRIMARY KEY,
    run_at        TEXT NOT NULL,              -- local time
    rule_version  TEXT NOT NULL,
    ivr_date      TEXT,
    market_open   INTEGER,                    -- 0 = frozen (last) quotes
    n_universe    INTEGER,                    -- graded A-C or FUND
    n_checked     INTEGER,                    -- had no PMCC open (chains read)
    n_found       INTEGER,                    -- had a pair inside every rule
    sleeve_free   REAL,
    elapsed_s     REAL,
    rejects_json  TEXT,                       -- {ticker: reason} for every name left off
    note          TEXT
);
CREATE TABLE IF NOT EXISTS pmcc_candidates (
    run_id        TEXT NOT NULL REFERENCES pmcc_runs(id),
    ticker        TEXT NOT NULL,
    rank          INTEGER NOT NULL,
    grade TEXT, sector TEXT, spot REAL,
    long_expiry TEXT NOT NULL, long_dte INTEGER, long_strike REAL NOT NULL,
    long_bid REAL, long_ask REAL, long_mid REAL, long_spread_pct REAL,
    long_delta REAL, long_gamma REAL, long_theta REAL, long_vega REAL, long_iv REAL,
    long_time_value REAL,                     -- a share, at mid
    long_decay_month REAL,                    -- time value / days x 30, a share
    short_expiry TEXT NOT NULL, short_dte INTEGER, short_strike REAL NOT NULL,
    short_bid REAL, short_ask REAL, short_mid REAL, short_spread_pct REAL,
    short_delta REAL, short_gamma REAL, short_theta REAL, short_vega REAL, short_iv REAL,
    rent_month REAL,                          -- short mid / days x 30, a share
    rent_cover REAL,                          -- rent_month / long_decay_month (NULL: no time value)
    ann_return REAL,                          -- short mid / long mid x 365 / short days
    net_debit REAL,                           -- (long mid - short mid) x 100, one contract
    width_floor REAL,                         -- the long's ask, a share
    width_room REAL,                          -- strike gap - floor, a share
    loss_limit REAL,                          -- dollars: -half the long's cost
    rest_price REAL,                          -- the short's 50% buy-back
    iv_rank REAL, iv_current REAL,
    earnings TEXT,
    trend_json TEXT, trend_text TEXT, downtrend INTEGER,
    flags TEXT,                               -- '; '-joined plain words
    size_n INTEGER, size_binding TEXT, size_note TEXT,
    PRIMARY KEY (run_id, ticker)
);
CREATE TABLE IF NOT EXISTS pmcc_entry_trend (
    position_id   TEXT PRIMARY KEY,           -- the PMCC position the import booked
    ticker        TEXT NOT NULL,
    as_of         TEXT NOT NULL,              -- the fill date the trend is measured to
    read_at       TEXT NOT NULL,              -- local time the bars were read
    close REAL, sma50 REAL, sma50_prior REAL, sma200 REAL, n_bars INTEGER,
    below_200 INTEGER, sma50_falling INTEGER, downtrend INTEGER,
    note          TEXT
);
"""


# ---------------------------------------------------------------- pure rules
def _in(x, band):
    return x is not None and band[0] - 1e-9 <= x <= band[1] + 1e-9


def leg_reason(c, side):
    """None if the contract meets every rule for its side ('long' | 'short'), else the first broken."""
    bd = LONG_DELTA if side == "long" else SHORT_DELTA
    bt = LONG_DTE if side == "long" else SHORT_DTE
    if c.get("source") not in ("ibkr", "ibkr-live"):
        return "no live IBKR quote"
    if not c.get("bid") or not c.get("ask") or c["bid"] <= 0 or c["ask"] <= 0:
        return "no two-sided market"
    if not _in(c.get("delta"), bd):
        return "delta outside %.2f-%.2f" % bd
    dte = c.get("dte")
    if dte is None or not (bt[0] <= dte <= bt[1]):
        return "outside %d-%d days" % bt
    sp = c.get("spread_pct")
    if sp is None or sp >= SPREAD_MAX_PCT:
        return "spread 10% of mid or wider"
    return None


def why_none(contracts, side):
    """The true reason no contract on this side passes (contracts is the wider net)."""
    word = "long call" if side == "long" else "short call"
    band = LONG_DELTA if side == "long" else SHORT_DELTA
    if not contracts:
        return "IBKR returned no two-sided %s quotes near %.2f-%.2f delta" % (word, band[0], band[1])
    live = [c for c in contracts if leg_reason(c, side) not in ("no live IBKR quote", "no two-sided market")]
    if not live:
        return "no %s passes: %s" % (word, "; ".join(sorted({leg_reason(c, side) for c in contracts})))
    inband = [c for c in live if leg_reason(c, side) is None or leg_reason(c, side).startswith("spread")]
    if not inband:
        ds = sorted(c["delta"] for c in live if c.get("delta") is not None)
        lo = max((d for d in ds if d < band[0]), default=None)
        hi = min((d for d in ds if d > band[1]), default=None)
        near = " / ".join("%.2f" % d for d in (lo, hi) if d is not None)
        return "no %s in the %.2f-%.2f delta band%s" % (word, band[0], band[1],
                                                         " (nearest %s)" % near if near else "")
    narrow = min(c["spread_pct"] for c in inband if c.get("spread_pct") is not None)
    return "%s spread 10%% of mid or wider (narrowest in the band %.0f%%)" % (word, narrow)


def time_value(lg, spot):
    return max(lg["mid"] - max(spot - lg["strike"], 0.0), 0.0)


def decay_month(lg, spot):
    return time_value(lg, spot) / lg["dte"] * 30.0


def rent_month(sh):
    return sh["mid"] / sh["dte"] * 30.0


def rent_cover(lg, sh, spot):
    d = decay_month(lg, spot)
    return None if d <= 0 else rent_month(sh) / d


def ann_return(lg, sh):
    return sh["mid"] / lg["mid"] * 365.0 / sh["dte"]


def width(lg, short_strike, price):
    """(passes, floor a share, long strike) -- THE width test, for one contract at `price`."""
    from helm.fillopens import width_test
    return width_test([{"direction": "LONG", "status": "OPEN", "multiplier": 100, "contracts": 1,
                        "open_price": price, "strike": lg["strike"]}], short_strike, 1)


def _pair_key(p, spot):
    rc = rent_cover(p[0], p[1], spot)
    return (-(rc if rc is not None else 1e9), -ann_return(*p), p[1]["dte"], -p[1]["strike"], p[0]["dte"],
            p[0]["strike"])


def best_pair(longs, shorts, spot):
    """((long, short), None) -- the passing pair with the highest rent cover -- or (None, why)."""
    S_ = [c for c in shorts if leg_reason(c, "short") is None]
    if not S_:
        return None, why_none(shorts, "short")
    L_ = [c for c in longs if leg_reason(c, "long") is None]
    if not L_:
        return None, why_none(longs, "long")
    pairs, mid_only, fail = [], [], []
    for lg in L_:
        for sh in S_:
            if P._d(sh["expiration"]) >= P._d(lg["expiration"]):
                continue
            ok_ask, floor_ask, _ = width(lg, sh["strike"], lg["ask"])
            if ok_ask:
                pairs.append((lg, sh))
                continue
            ok_mid, _, _ = width(lg, sh["strike"], lg["mid"])
            (mid_only if ok_mid else fail).append((lg, sh, floor_ask))
    if pairs:
        pairs.sort(key=lambda p: _pair_key(p, spot))
        return pairs[0], None
    if mid_only:
        lg, sh, fl = mid_only[0]
        return None, ("passes the width test only at the mid (the %g call over the %g long: gap %.2f, "
                      "long's ask %.2f)" % (sh["strike"], lg["strike"], sh["strike"] - lg["strike"], fl))
    if not fail:
        return None, "no short call expires before a long call"
    lg, sh, fl = min(fail, key=lambda x: x[2] - (x[1]["strike"] - x[0]["strike"]))
    return None, ("no short strike clears the width test (closest: the %g call over the %g long, gap %.2f, "
                  "needs more than the long's ask %.2f)" % (sh["strike"], lg["strike"],
                                                           sh["strike"] - lg["strike"], fl))


def expiries(exps, today, band):
    """[(expiry, dte)] inside the band, and a reason when there are none."""
    out = []
    for e in exps or ():
        d = (P._d(e) - today).days
        if band[0] <= d <= band[1]:
            out.append((str(e)[:10], d))
    if out:
        return out, None
    near = sorted(((P._d(e) - today).days, str(e)[:10]) for e in (exps or ()) if (P._d(e) - today).days > 0)
    before = [x for x in near if x[0] < band[0]][-1:]
    after = [x for x in near if x[0] > band[1]][:1]
    nb = ", ".join("%s is %d days" % (e, d) for d, e in before + after)
    return [], "no expiry between %d and %d days" % band + (" (%s)" % nb if nb else "")


def long_strike_range(spot, sigma, dte):
    """Strikes to read for the long: about 0.98 down to 0.60 delta (Black-Scholes with the
    stock's current IV; 50% when unknown) -- wider than the 0.75-0.85 rule. s148: the
    arithmetic lives in helm/strikewindow.py, shared with the put, call and roll tools."""
    from helm import strikewindow as SW
    return SW.z_range(spot, sigma, [dte], -2.0, -0.25)


def short_strike_range(spot, sigma, dtes):
    """Strikes to read for the short: about 0.44 down to 0.10 delta across the short expiries
    (Black-Scholes with the stock's current IV; 50% when unknown) -- wider than 0.20-0.30. The
    reader's own +/-3xATR window stops short of 0.20-0.30 calls on calm stocks (AAPL 11-02:
    the band was 6-7% above the price, the window ended at 0.33). s148: helm/strikewindow.py."""
    from helm import strikewindow as SW
    return SW.z_range(spot, sigma, dtes, 0.15, 1.3)


def trend(closes, k=TREND_SLOPE_BARS):
    """Pure. closes oldest first."""
    n = len(closes)
    t = {"n_bars": n, "close": closes[-1] if closes else None, "sma50": None, "sma50_prior": None,
         "sma200": None, "below_200": None, "sma50_falling": None, "downtrend": None}
    if n >= 50:
        t["sma50"] = sum(closes[-50:]) / 50.0
    if n >= 50 + k:
        t["sma50_prior"] = sum(closes[-50 - k:-k]) / 50.0
    if n >= 200:
        t["sma200"] = sum(closes[-200:]) / 200.0
        t["below_200"] = closes[-1] < t["sma200"]
    if t["sma50_prior"] is not None:
        t["sma50_falling"] = t["sma50"] < t["sma50_prior"]
    if t["below_200"] is not None and t["sma50_falling"] is not None:
        t["downtrend"] = bool(t["below_200"] and t["sma50_falling"])
    return t


def trend_text(t):
    if not t or t.get("close") is None:
        return "trend not read" + ((": " + t["error"]) if t and t.get("error") else "")
    if t.get("sma200") is None:
        return "trend: only %d days of prices" % t["n_bars"]
    pct = (t["close"] / t["sma200"] - 1) * 100
    where = "%.0f%% %s the 200-day" % (abs(pct), "below" if pct < 0 else "above")
    slope = "50-day falling" if t.get("sma50_falling") else "50-day rising"
    if t.get("downtrend"):
        return "DOWNTREND: %s, %s -- taking it is a conscious choice" % (where, slope)
    return "%s; %s" % (where, slope)


def rank(bests):
    def k(b):
        rc = b["rent_cover"]
        return (-(rc if rc is not None else 1e9), -b["ann_return"], b["ticker"])
    return sorted(bests, key=k)


# ---------------------------------------------------------------- network
def _bars(ib, ticker):
    from ib_insync import Stock
    from helm.ibkr import to_ibkr_symbol
    bars = ib.reqHistoricalData(Stock(to_ibkr_symbol(ticker), "SMART", "USD"), endDateTime="",
                                durationStr="1 Y", barSizeSetting="1 day", whatToShow="TRADES",
                                useRTH=True, formatDate=1)
    return [(str(b.date)[:10], float(b.close)) for b in (bars or [])]


def fetch_trends(tickers, as_of=None):
    """{ticker: trend dict}; a failure is a dict with 'error'. One IBKR connection, released."""
    out = {}
    if not tickers:
        return out
    try:
        from ib_insync import IB
        ib = IB()
        ib.connect("127.0.0.1", 4002, clientId=IBKR_CLIENT, readonly=True, timeout=15)
        ib.RequestTimeout = 30
    except Exception as e:
        return {t: {"close": None, "error": "IBKR not connected: %s" % str(e)[:60]} for t in tickers}
    try:
        for t in tickers:
            try:
                rows = _bars(ib, t)
                if as_of:
                    rows = [r for r in rows if r[0] <= str(as_of)[:10]]
                out[t] = trend([c for _, c in rows]) if rows else {"close": None, "error": "no daily bars"}
                if rows:
                    out[t]["last_bar"] = rows[-1][0]
            except Exception as e:
                out[t] = {"close": None, "error": str(e)[:60]}
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass
    return out


def fetch_pairs(ticker, today=None, sigma=None):
    """(longs, shorts, spot, err). Calls straight from IBKR; Yahoo gives only the spot,
    the 14-day range and the listed expiries (as helm puts). The longs are read only when
    a short passes its rules (a long can't make up for no short), one expiry a call."""
    from helm.cli import open_cmd as oc
    today = today or date.today()
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        spot = float(tk.fast_info.last_price)
        exps = tuple(tk.options)
    except Exception as e:
        return [], [], None, "price or expiries not read: %s" % str(e)[:80]
    sx, why = expiries(exps, today, SHORT_DTE)
    if not sx:
        return [], [], spot, why
    lx, why = expiries(exps, today, LONG_DTE)
    if not lx:
        return [], [], spot, why
    atr = spot * 0.05
    try:
        h = tk.history(period="30d", interval="1d")
        if not h.empty and len(h) >= 14:
            a = float((h["High"] - h["Low"]).rolling(14).mean().iloc[-1])
            if a == a and a > 0:
                atr = a
    except Exception:
        pass
    try:                                            # s149: a gateway failure says so
        shorts = oc.fetch_chain_from_ibkr(ticker, "CALL", sx, spot, atr, NET_SHORT_DELTA[0], NET_SHORT_DELTA[1],
                                          SHORT_DELTA, spread_threshold=P.NET_SPREAD,
                                          strike_range=short_strike_range(spot, sigma, [d for _, d in sx]),
                                          report_errors=True)
        if not [c for c in shorts if leg_reason(c, "short") is None]:
            return [], shorts, spot, None
        longs = []
        for e, d in lx:
            longs += oc.fetch_chain_from_ibkr(ticker, "CALL", [(e, d)], spot, atr, NET_LONG_DELTA[0],
                                              NET_LONG_DELTA[1], LONG_DELTA, spread_threshold=P.NET_SPREAD,
                                              strike_range=long_strike_range(spot, sigma, d), report_errors=True)
    except oc.ChainReadError as e:
        return [], [], spot, str(e)
    return longs, shorts, spot, None


# ---------------------------------------------------------------- the run
def ensure_tables(conn):
    conn.executescript(TABLES_SQL)


def screen(conn, today, tickers=None):
    """(checked, rejects, ivr_date, n_universe). No network."""
    from helm import advice as A
    from helm import sleeve as S
    ivr_date = P._latest_ivr_date(conn)
    names = [r[0] for r in conn.execute(
        "SELECT ticker FROM watchlist WHERE COALESCE(active,0)=1 ORDER BY ticker")]
    funds = {r[0] for r in conn.execute("SELECT ticker FROM approved_funds")}
    names = sorted(set(names) | funds)
    if tickers:
        want = {t.upper() for t in tickers}
        names = [t for t in names if t in want] + sorted(want - set(names))
    held = {}
    for r in conn.execute("SELECT ticker, strategy FROM positions WHERE book='REAL' AND status='OPEN' "
                          "AND strategy IN ('PMCC','DIAGONAL')"):
        held[S.name_key(r[0])] = (r[0], r[1])
    checked, rejects, nu = [], {}, 0
    for t in names:
        g = A._grade(conn, t)
        if g not in PASSING:
            rejects[t] = "grade %s -- not offered" % (g or "none")
            continue
        nu += 1
        k = S.name_key(t)
        if k in held:
            rejects[t] = "a %s is already open on %s -- one per name" % (
                "PMCC" if held[k][1] == "PMCC" else "diagonal", held[k][0])
            continue
        r = conn.execute("SELECT iv_rank, iv_current FROM iv_history WHERE ticker=? AND date=?",
                         (t, ivr_date)).fetchone() if ivr_date else None
        checked.append({"ticker": t, "grade": g, "iv_rank": r[0] if r else None,
                        "iv_current": r[1] if r else None,
                        "next_report": P._next_report(conn, t, today)})
    return checked, rejects, ivr_date, nu


CAND_COLS = (
    "ticker", "rank", "grade", "sector", "spot",
    "long_expiry", "long_dte", "long_strike", "long_bid", "long_ask", "long_mid", "long_spread_pct",
    "long_delta", "long_gamma", "long_theta", "long_vega", "long_iv", "long_time_value", "long_decay_month",
    "short_expiry", "short_dte", "short_strike", "short_bid", "short_ask", "short_mid", "short_spread_pct",
    "short_delta", "short_gamma", "short_theta", "short_vega", "short_iv",
    "rent_month", "rent_cover", "ann_return", "net_debit", "width_floor", "width_room", "loss_limit",
    "rest_price", "iv_rank", "iv_current", "earnings", "trend_json", "trend_text", "downtrend", "flags",
    "size_n", "size_binding", "size_note")


def build(conn, today, tickers=None, fetch=fetch_pairs, trends=None, book=None, db=None, progress=None):
    """Run the rules. Returns the run dict (not yet recorded)."""
    from helm import sleeve as S
    from helm import calltool as C
    from helm.bookview import _half_up
    t_start = time.time()
    checked, rejects, ivr_date, nu = screen(conn, today, tickers)
    b = book if book is not None else S.book()
    tr = trends if trends is not None else fetch_trends([c["ticker"] for c in checked])
    bests = []
    streak, stopped = [], None
    for i, ch in enumerate(checked):
        t = ch["ticker"]
        t0 = time.time()
        sigma = (ch["iv_current"] / 100.0) if ch.get("iv_current") else None
        longs, shorts, spot, err = fetch(t, today, sigma)
        streak = streak + [t] if P.gateway_down(err) else []      # s149
        if err:
            rejects[t] = err if err.startswith("no expiry") else "chain not read: %s" % err
            if len(streak) >= P.GATEWAY_STOP_AFTER:
                if progress:
                    progress(t, rejects.get(t), time.time() - t0)
                stopped = P.stop_rest(rejects, [c["ticker"] for c in checked[i + 1:]], streak)
                break
        else:
            pair, why = best_pair(longs, shorts, spot)
            if pair is None:
                rejects[t] = why
            else:
                lg, sh = pair
                tv, dm, rm = time_value(lg, spot), decay_month(lg, spot), rent_month(sh)
                rc = rent_cover(lg, sh, spot)
                _ok, floor, lk = width(lg, sh["strike"], lg["ask"])
                sec, _src = S.sector_of(conn, t)
                n, binding, note = S.size("PMCC", t, lg["mid"] * 100.0, db=db, b=b)
                if n:
                    n = min(n, 1)
                    note = "1 contract (one per name until there is a record); " + (note or "")
                earn = C.pmcc_earnings_line(ch["grade"] == "FUND", ch["next_report"], sh["expiration"], today)
                td = tr.get(t) or {"close": None}
                flags = []
                if rc is None:
                    flags.append("the long's mid is at or below its intrinsic value -- check the quote")
                elif rc < RENT_COVER_FLAG:
                    flags.append("rent cover %.1fx, under 2x" % rc)
                if td.get("downtrend"):
                    flags.append("downtrend")
                if "INSIDE" in earn:
                    flags.append("earnings inside the short")
                bests.append({
                    "ticker": t, "grade": ch["grade"], "sector": sec, "spot": spot,
                    "long_expiry": lg["expiration"], "long_dte": lg["dte"], "long_strike": lg["strike"],
                    "long_bid": lg["bid"], "long_ask": lg["ask"], "long_mid": lg["mid"],
                    "long_spread_pct": lg["spread_pct"], "long_delta": lg.get("delta"),
                    "long_gamma": lg.get("gamma"), "long_theta": lg.get("theta"), "long_vega": lg.get("vega"),
                    "long_iv": lg.get("iv"), "long_time_value": round(tv, 4), "long_decay_month": round(dm, 4),
                    "short_expiry": sh["expiration"], "short_dte": sh["dte"], "short_strike": sh["strike"],
                    "short_bid": sh["bid"], "short_ask": sh["ask"], "short_mid": sh["mid"],
                    "short_spread_pct": sh["spread_pct"], "short_delta": sh.get("delta"),
                    "short_gamma": sh.get("gamma"), "short_theta": sh.get("theta"), "short_vega": sh.get("vega"),
                    "short_iv": sh.get("iv"), "rent_month": round(rm, 4),
                    "rent_cover": round(rc, 4) if rc is not None else None,
                    "ann_return": ann_return(lg, sh),
                    "net_debit": round((lg["mid"] - sh["mid"]) * 100.0, 2),
                    "width_floor": floor, "width_room": round(sh["strike"] - lk - floor, 2),
                    "loss_limit": round(-LOSS_LIMIT * lg["mid"] * 100.0, 2),
                    "rest_price": _half_up(sh["mid"] * (1 - PROFIT_TAKE)),
                    "iv_rank": ch.get("iv_rank"), "iv_current": ch.get("iv_current"), "earnings": earn,
                    "trend_json": json.dumps(td, sort_keys=True), "trend_text": trend_text(td),
                    "downtrend": None if td.get("downtrend") is None else int(bool(td["downtrend"])),
                    "flags": "; ".join(flags), "size_n": n, "size_binding": binding, "size_note": note,
                })
        if progress:
            progress(t, rejects.get(t), time.time() - t0)
    ranked, firsts = [], {}
    for r in rank(bests):
        k = S.name_key(r["ticker"])
        if k in firsts:
            rejects[r["ticker"]] = "same company as %s, which ranks higher -- one PMCC per company" % firsts[k]
            continue
        firsts[k] = r["ticker"]
        ranked.append(r)
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    return {"id": "pmcc_" + uuid.uuid4().hex[:12],
            "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "rule_version": RULE_VERSION, "ivr_date": ivr_date, "market_open": P._market_open(),
            "n_universe": nu, "n_checked": len(checked), "n_found": len(ranked),
            "sleeve_free": b["share"] - b["used"], "elapsed_s": round(time.time() - t_start, 1),
            "rejects": rejects, "candidates": ranked, "stopped": stopped}


def record(conn, run, note=None):
    ensure_tables(conn)
    conn.execute("INSERT INTO pmcc_runs (id, run_at, rule_version, ivr_date, market_open, n_universe, n_checked, "
                 "n_found, sleeve_free, elapsed_s, rejects_json, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                 (run["id"], run["run_at"], run["rule_version"], run["ivr_date"], run["market_open"],
                  run["n_universe"], run["n_checked"], run["n_found"], run["sleeve_free"], run["elapsed_s"],
                  json.dumps(run["rejects"], sort_keys=True), note))
    for c in run["candidates"]:
        conn.execute("INSERT INTO pmcc_candidates (run_id, %s) VALUES (?, %s)" % (
            ", ".join(CAND_COLS), ", ".join("?" * len(CAND_COLS))),
            (run["id"],) + tuple(c.get(k) for k in CAND_COLS))
    conn.commit()


RUN_KEYS = ("id", "run_at", "rule_version", "ivr_date", "market_open", "n_universe", "n_checked", "n_found",
            "sleeve_free", "elapsed_s", "rejects_json", "note")


def latest(conn):
    """The newest recorded run with its candidates, or None. Read-only."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pmcc_runs'").fetchone():
        return None
    r = conn.execute("SELECT %s FROM pmcc_runs ORDER BY run_at DESC, rowid DESC LIMIT 1"
                     % ", ".join(RUN_KEYS)).fetchone()
    if not r:
        return None
    run = dict(zip(RUN_KEYS, tuple(r)))
    run["rejects"] = json.loads(run.pop("rejects_json") or "{}")
    run["candidates"] = [dict(zip(CAND_COLS, tuple(x))) for x in conn.execute(
        "SELECT %s FROM pmcc_candidates WHERE run_id=? ORDER BY rank" % ", ".join(CAND_COLS), (run["id"],))]
    return run


def decision_text(c):
    if c.get("size_n"):
        return "1 contract fits the limits"
    lab = {"sleeve_total": "sleeve full", "sleeve_name": "name over 20%",
           "sleeve_sector": "sector over 30%"}.get(c.get("size_binding"), "no room")
    return "would be declined: " + lab


# ---------------------------------------------------------------- trend at entry (the import)
def record_entry_trend(position_id, ticker, as_of, db_path=None, fetch=None):
    """Measure the trend to the fill date and store it for a PMCC the import booked.
    Never raises: a failure is stored as a note. Returns the plain-words line."""
    from helm.db import transaction
    try:
        tdict = (fetch or (lambda t, d: fetch_trends([t], as_of=d)[t]))(ticker, as_of)
    except Exception as e:
        tdict = {"close": None, "error": str(e)[:60]}
    line = trend_text(tdict)
    vals = (position_id, ticker, str(as_of)[:10], datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            tdict.get("close"), tdict.get("sma50"), tdict.get("sma50_prior"), tdict.get("sma200"),
            tdict.get("n_bars"),
            *[None if tdict.get(k) is None else int(bool(tdict[k])) for k in ("below_200", "sma50_falling", "downtrend")],
            ("last bar %s; " % tdict["last_bar"] if tdict.get("last_bar") else "") + line)
    kw = {"db_path": db_path} if db_path else {}
    with transaction(**kw) as c:
        for stmt in TABLES_SQL.split(";"):
            if "pmcc_entry_trend" in stmt:
                c.execute(stmt)
        c.execute("INSERT OR IGNORE INTO pmcc_entry_trend (position_id, ticker, as_of, read_at, close, sma50, "
                  "sma50_prior, sma200, n_bars, below_200, sma50_falling, downtrend, note) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", vals)
    return line


# ---------------------------------------------------------------- self-test
def _bs_call_delta(spot, k, sigma, dte):
    T = dte / 365.0
    d1 = (math.log(spot / k) + (RATE + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return 0.5 * (1 + math.erf(d1 / math.sqrt(2)))


def _selftest():
    import sqlite3, tempfile, os
    fails = []

    def ok(cond, msg):
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)

    T = date(2026, 11, 2)
    Lb = {"source": "ibkr", "expiration": "2027-12-17", "dte": 410, "strike": 160.0, "bid": 62.0, "ask": 63.0,
          "mid": 62.5, "spread_pct": 1.6, "delta": 0.80}
    Sb = {"source": "ibkr", "expiration": "2026-12-11", "dte": 39, "strike": 225.0, "bid": 3.3, "ask": 3.5,
          "mid": 3.4, "spread_pct": 5.9, "delta": 0.25}
    # leg rules
    ok(leg_reason(Lb, "long") is None and leg_reason(Sb, "short") is None, "legs inside every rule pass")
    ok(leg_reason({**Lb, "delta": 0.75}, "long") is None and leg_reason({**Lb, "delta": 0.85}, "long") is None,
       "0.75 and 0.85 long deltas are included")
    ok(leg_reason({**Lb, "delta": 0.749}, "long") and leg_reason({**Lb, "delta": 0.851}, "long"),
       "0.749 and 0.851 long deltas fail")
    ok(leg_reason({**Lb, "dte": 270}, "long") is None and leg_reason({**Lb, "dte": 730}, "long") is None
       and leg_reason({**Lb, "dte": 269}, "long") and leg_reason({**Lb, "dte": 731}, "long"),
       "long 270 and 730 days included, 269 and 731 fail")
    ok(leg_reason({**Sb, "delta": 0.20}, "short") is None and leg_reason({**Sb, "delta": 0.30}, "short") is None
       and leg_reason({**Sb, "delta": 0.31}, "short"), "short band 0.20-0.30 inclusive")
    ok(leg_reason({**Sb, "spread_pct": 10.0}, "short") and leg_reason({**Sb, "spread_pct": 9.9}, "short") is None,
       "a 10.0% spread fails, 9.9% passes")
    ok(leg_reason({**Lb, "source": "yfinance"}, "long") == "no live IBKR quote", "a Yahoo quote fails")
    # the NVDA arithmetic from the plan (10-30): long 160 @ 62.90, stock 204.64, 412 days; short 3.40, 35 days
    nl = {**Lb, "mid": 62.90, "dte": 412}
    ns = {**Sb, "mid": 3.40, "dte": 35}
    ok(abs(time_value(nl, 204.64) - 18.26) < 1e-9, "time value = mid - intrinsic (18.26)")
    ok(abs(decay_month(nl, 204.64) - 1.3296) < 1e-3 and abs(rent_month(ns) - 2.9143) < 1e-3,
       "monthly decay 1.33 and rent 2.91")
    ok(abs(rent_cover(nl, ns, 204.64) - 2.19) < 0.01, "rent cover 2.2x, as the plan worked it")
    ok(abs(ann_return(nl, ns) - 3.40 / 62.90 * 365 / 35) < 1e-12, "yearly return = short mid / long mid x 365 / days")
    # width test at the long's ask
    ok(width({**Lb, "ask": 62.90}, 222.90, 62.90)[0] is False, "a strike exactly at the floor fails (must exceed)")
    ok(width({**Lb, "ask": 62.90}, 222.91, 62.90)[0] is True, "a cent over the floor passes")
    lo = {**Lb, "bid": 60.0, "ask": 64.0, "mid": 62.0, "spread_pct": 6.5}
    p, why = best_pair([lo], [{**Sb, "strike": 223.0}], 204.0)
    ok(p is None and why.startswith("passes the width test only at the mid"), "a mid-only pass is left off: %s" % why)
    p, why = best_pair([lo], [{**Sb, "strike": 215.0}], 204.0)
    ok(p is None and why.startswith("no short strike clears the width test"), "a failed width test says so: %s" % why)
    # pair choice
    s_hi = {**Sb, "strike": 230.0, "mid": 4.0}            # more rent, passes
    s_lo = {**Sb, "strike": 240.0, "mid": 2.0}
    s_late = {**Sb, "strike": 240.0, "mid": 9.0, "expiration": "2028-01-21"}   # after the long
    p, _ = best_pair([Lb], [s_lo, s_hi, s_late], 204.0)
    ok(p is not None and p[1] is s_hi, "the highest rent cover wins; a short expiring after the long is never paired")
    l2 = {**Lb, "expiration": "2028-06-16", "dte": 592, "strike": 150.0, "mid": 72.0, "bid": 71.5, "ask": 72.5}
    p, _ = best_pair([Lb, l2], [s_hi], 204.0)
    rc1, rc2 = rent_cover(Lb, s_hi, 204.0), rent_cover(l2, s_hi, 204.0)
    ok(p[0] is (Lb if rc1 > rc2 else l2), "across longs, the pair with the higher rent cover wins (%.2f vs %.2f)" % (rc1, rc2))
    p, why = best_pair([Lb], [{**Sb, "delta": 0.18}, {**Sb, "delta": 0.33}], 204.0)
    ok(why == "no short call in the 0.20-0.30 delta band (nearest 0.18 / 0.33)", "no short in band names the nearest: %s" % why)
    p, why = best_pair([{**Lb, "spread_pct": 14.0}], [Sb], 204.0)
    ok(why == "long call spread 10% of mid or wider (narrowest in the band 14%)", "a wide long spread says so: %s" % why)
    ok(best_pair([], [Sb], 204.0)[1].startswith("IBKR returned no two-sided long call"), "no long quotes says so")
    # expiries and the strike range
    ex, why = expiries(["2026-12-04", "2027-06-17", "2027-12-17", "2029-01-19"], T, LONG_DTE)
    ok([e for e, _ in ex] == ["2027-12-17"], "long expiries inside 270-730 only")
    ex, why = expiries(["2026-11-20", "2027-01-15"], T, SHORT_DTE)
    ok(ex == [] and why == "no expiry between 30 and 60 days (2026-11-20 is 18 days, 2027-01-15 is 74 days)",
       "an empty window names the nearest expiries: %s" % why)
    ex, why = expiries(["2026-11-20", "2026-12-04", "2026-12-18", "2027-01-15"], T, SHORT_DTE)
    ok([e for e, _ in ex] == ["2026-12-04", "2026-12-18"],
       "s149: the 12-18 monthly (46 days on 11-02) is inside the short's window; 01-15 (74) is not: %s" % ex)
    good = True
    for sg in (0.15, 0.30, 0.60):
        for d in (270, 730):
            a, b = long_strike_range(100.0, sg, d)
            if not (_bs_call_delta(100.0, a, sg, d) > 0.95 and _bs_call_delta(100.0, b, sg, d) < 0.65):
                good = False
    ok(good, "the strike range reaches past 0.95 and below 0.65 delta at 15-60% IV, 270 and 730 days")
    good = True
    for sg in (0.15, 0.30, 0.60):
        a, b = short_strike_range(100.0, sg, [30, 60])
        for d in (30, 60):
            if not (_bs_call_delta(100.0, a, sg, d) > 0.40 and _bs_call_delta(100.0, b, sg, d) < 0.15):
                good = False
    ok(good, "the short's strike range reaches past 0.40 and below 0.15 delta at 15-60% IV, 30 and 60 days")
    a, b = short_strike_range(340.93, 0.26, [32, 39])
    ok(a < 355 and b > 375, "AAPL 11-02 (0.20-0.30 calls at 360-365): the range %.0f-%.0f covers them" % (a, b))
    # trend
    up = [100 + i * 0.1 for i in range(252)]
    dn = [200 - i * 0.2 for i in range(252)]
    tu, td = trend(up), trend(dn)
    ok(tu["downtrend"] is False and tu["below_200"] is False and tu["sma50_falling"] is False, "a rising stock: no downtrend")
    ok(td["downtrend"] is True and trend_text(td).startswith("DOWNTREND"), "a falling stock: DOWNTREND flagged")
    bounce = dn[:-1] + [dn[-2] + 2]              # a 2-point bounce: still below the 200-day, 50-day falling
    ok(trend(bounce)["downtrend"] is True, "a small bounce stays a downtrend")
    ok(trend(dn[:-1] + [dn[-2] + 60])["downtrend"] is False, "a close back above the 200-day is not a downtrend")
    ok(abs(tu["sma200"] - sum(up[-200:]) / 200) < 1e-9 and abs(tu["sma50_prior"] - sum(up[-60:-10]) / 50) < 1e-9,
       "200-day average and the 50-day ten bars back")
    ok(trend(up[:120])["sma200"] is None and trend_text(trend(up[:120])) == "trend: only 120 days of prices",
       "short history says so")
    ok(trend_text({"close": None, "error": "IBKR not connected: x"}) == "trend not read: IBKR not connected: x",
       "an unread trend says why")

    # end to end on a tiny database, no network
    tmp = os.path.join(tempfile.mkdtemp(), "t.db")
    conn = sqlite3.connect(tmp)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE watchlist (ticker TEXT, active INTEGER, next_earnings TEXT, sector TEXT, exposure_group TEXT);
      CREATE TABLE approved_funds (ticker TEXT, bucket TEXT);
      CREATE TABLE ownership_quality (ticker TEXT, date TEXT, grade TEXT, updated_at TEXT);
      CREATE TABLE iv_history (ticker TEXT, date TEXT, iv_current REAL, iv_rank REAL);
      CREATE TABLE earnings_history (ticker TEXT, earn_date TEXT);
      CREATE TABLE positions (id TEXT, ticker TEXT, strategy TEXT, status TEXT, book TEXT);
    """)
    for t, ne, g in [("AAA", "2026-12-01", "B"), ("BBB", "2027-01-20", "A"), ("CCC", "2027-01-20", "C"),
                     ("DDD", "2027-01-20", "D"), ("HHH", "2027-01-20", "B"), ("EEE", "2027-01-20", "B")]:
        conn.execute("INSERT INTO watchlist VALUES (?,1,?,'Tech',NULL)", (t, ne))
        conn.execute("INSERT INTO ownership_quality VALUES (?,'2026-10-24',?,'x')", (t, g))
        conn.execute("INSERT INTO iv_history VALUES (?,'2026-10-30',35.0,40.0)", (t,))
    conn.execute("INSERT INTO approved_funds VALUES ('XLV','Healthcare')")
    conn.execute("INSERT INTO iv_history VALUES ('XLV','2026-10-30',18.0,30.0)")
    conn.execute("INSERT INTO positions VALUES ('p1','HHH','PMCC','OPEN','REAL')")
    conn.commit()
    checked, rej, ivd, nu = screen(conn, T)
    names = sorted(c["ticker"] for c in checked)
    ok(names == ["AAA", "BBB", "CCC", "EEE", "XLV"], "screen: A-C/FUND, no PMCC open: %s" % names)
    ok(rej["DDD"].startswith("grade D") and rej["HHH"] == "a PMCC is already open on HHH -- one per name",
       "grade D and an open PMCC are left off with reasons")
    ok(nu == 6, "universe counts A-C and funds (6)")
    ok(checked[0]["iv_current"] == 35.0, "the stock's current IV is carried for the strike range")
    chains = {
        "AAA": ([Lb], [s_hi]),                                   # earnings 12-01 inside the 12-11 short
        "BBB": ([Lb], [s_lo]),                                   # less rent
        "CCC": ([Lb], [s_hi, {**Sb, "strike": 235.0, "mid": 3.9}]),   # same company as AAA (pretend)
        "EEE": ([Lb], [{**Sb, "spread_pct": 12.0}]),             # no short inside the rules
        "XLV": ([{**Lb, "strike": 120.0, "mid": 25.5, "bid": 25.3, "ask": 25.7}],   # little time value:
                [{**Sb, "strike": 160.0, "mid": 0.5}]),          # top by rent cover, last by yearly return
    }
    seen = []

    def fake(t, today, sigma):
        seen.append((t, sigma))
        lg, sh = chains[t]
        return lg, sh, 204.0 if t != "XLV" else 145.0, None
    trends = {"AAA": tu, "BBB": td, "CCC": tu, "EEE": tu, "XLV": tu}
    import helm.sleeve as S
    _nk, _so = S.name_key, S.sector_of
    S.name_key = lambda t: "AAA + CCC" if t in ("AAA", "CCC") else t
    S.sector_of = lambda c, t: ("Tech", "test")
    try:
        roomy = {"share": 300000.0, "used": 0.0, "name_cap": 60000.0, "sector_cap": 90000.0,
                 "by_name": {}, "by_sector": {}}
        run = build(conn, T, fetch=fake, trends=trends, book=roomy, db=tmp)
        full = {**roomy, "used": 299000.0}
        run_full = build(conn, T, fetch=fake, trends=trends, book=full, db=tmp)
    finally:
        S.name_key, S.sector_of = _nk, _so
    tick = [c["ticker"] for c in run["candidates"]]
    rcs = [c["rent_cover"] for c in run["candidates"]]
    ok(tick == ["XLV", "AAA", "BBB"] and rcs == sorted(rcs, reverse=True),
       "ranked by rent cover, not yearly return: %s %s" % (tick, rcs))
    yr = sorted(run["candidates"], key=lambda c: -c["ann_return"])
    ok([c["ticker"] for c in yr][0] == "AAA", "(yearly return alone would put AAA first -- the case can see the rule)")
    ok("CCC" not in tick and run["rejects"]["CCC"].startswith("same company as AAA"), "one PMCC per company")
    ok(run["rejects"]["EEE"].startswith("short call spread"), "a name with no passing short is left off with the reason")
    ok(dict(seen)["XLV"] == 0.18 and dict(seen)["AAA"] == 0.35, "each name's IV (as a fraction) goes to the reader")
    a = [c for c in run["candidates"] if c["ticker"] == "AAA"][0]
    b = [c for c in run["candidates"] if c["ticker"] == "BBB"][0]
    ok("earnings inside the short" in a["flags"] and "INSIDE" in a["earnings"], "earnings inside the short are flagged")
    ok("downtrend" in b["flags"] and b["downtrend"] == 1 and b["trend_text"].startswith("DOWNTREND"),
       "a downtrend is flagged, not a gate")
    ok(a["size_n"] == 1 and a["size_note"].startswith("1 contract"), "one contract per name even with room for more")
    ok(all(c["size_n"] == 0 and decision_text(c) == "would be declined: sleeve full" for c in run_full["candidates"])
       and len(run_full["candidates"]) == len(run["candidates"]), "a full sleeve: every idea shown, would be declined")
    ok(abs(a["net_debit"] - (62.5 - 4.0) * 100) < 1e-9 and a["loss_limit"] == -3125.0 and a["rest_price"] == 2.0,
       "net debit, loss limit and 50% buy-back in dollars")
    ok(abs(a["width_room"] - (230 - 160 - 63.0)) < 1e-9 and a["width_floor"] == 63.0, "width room is measured at the ask")
    record(conn, run, "test")
    back = latest(conn)
    ok(back["id"] == run["id"] and [c["ticker"] for c in back["candidates"]] == tick
       and back["rejects"] == run["rejects"] and back["candidates"][0]["rent_cover"] == run["candidates"][0]["rent_cover"],
       "recorded and read back unchanged")
    conn.close()
    # trend at entry (the import's hook)
    line = record_entry_trend("pos1", "NVDA", "2026-10-30", db_path=tmp, fetch=lambda t, d: td)
    line2 = record_entry_trend("pos1", "NVDA", "2026-10-30", db_path=tmp, fetch=lambda t, d: tu)
    c2 = sqlite3.connect(tmp)
    rows = c2.execute("SELECT position_id, downtrend, as_of, note FROM pmcc_entry_trend").fetchall()
    ok(len(rows) == 1 and rows[0][1] == 1 and rows[0][2] == "2026-10-30" and line.startswith("DOWNTREND"),
       "the entry trend is stored once, the first reading kept: %s" % rows)
    line3 = record_entry_trend("pos2", "XLE", "2026-10-30", db_path=tmp,
                               fetch=lambda t, d: (_ for _ in ()).throw(RuntimeError("gateway down")))
    r3 = c2.execute("SELECT close, note FROM pmcc_entry_trend WHERE position_id='pos2'").fetchone()
    ok(r3[0] is None and "gateway down" in r3[1], "a failed read is stored as a note, never raised: %s" % (r3,))
    c2.close()
    print("\n%d failed" % len(fails) if fails else "\nall passed")
    return not fails


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
