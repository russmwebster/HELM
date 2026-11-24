"""helm/spreadtool.py -- the bull put spread list (s165, 2026-11-23): Russ's spread rules,
run on demand (`helm spreads`), ranked, recorded. Display only: nothing is booked from it.

The rules (Russ, 2026-11-23):
  * universe, premium bar and earnings -- exactly the put list's (puttool.screen): names graded
    A-C or approved funds, IV rank >= 50, no entry within 10 days of a report (fails closed),
    a report later in the trade allowed and shown.
  * the sold put -- 0.25-0.30 delta (0.30 included), 30-60 days, a live two-sided IBKR quote.
  * the bought put -- the listed strike nearest WIDTH_PCT (5%) of the stock's price below the sold
    put, same expiry, a live two-sided quote (ties go to the lower strike).
  * the market -- judged on the whole spread: the gap between selling it at the worst fill
    (sold bid - bought ask) and at the best (sold ask - bought bid) must be under 10% of the mid
    credit (10.0% is refused, as everywhere else).
  * the spread per name -- the passing spread with the highest yearly return on the most it can
    lose (credit / (width - credit) x 365 / days). One per company, ranked, top 5 shown, and the
    best 5 that fit the limits.
  * sizing -- by the most it can lose, through helm.sleeve.size, against the sleeve, the 20% name
    and the 30% sector limits. A single put and a spread on one name count together against the
    name limit (the sleeve already sums a name's collateral and maximum losses).
  * management, shown with every line -- the 50% buy-back of the credit (a resting order Russ
    places), out at 21 days left (the date shown), the stock a contract would bring in if the sold
    put were assigned.
  * the broad index funds -- read on every run whatever their IV rank, at 2%, 3% and 5% widths, shown
    beside the list for comparison. Display only: they enter the ranked list only by passing every
    rule at the 5% width like any other name.

HELM informs, never acts. This module reads the database and IBKR, and writes only its own three
tables (spread_runs, spread_candidates, spread_index). The Book of Books may read them, read-only
(Russ, 2026-11-23).
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import date, datetime, timedelta

from helm import puttool as P

RULE_VERSION = ("spreads-v1 (Russ 2026-11-23: sold put 0.25-0.30, bought put 5% of price below, "
                "whole-spread market under 10% of the credit, 50% / 21 days)")
STRATEGY = "BULL_PUT_SPREAD"

WIDTH_PCT = 0.05                 # the bought put: this share of the stock's price below the sold put
INDEX_WIDTHS = (0.02, 0.03, 0.05)   # the index funds are also shown at these, for comparison
GAP_MAX_PCT = 10.0               # worst-to-best fill, % of the mid credit; strictly below
CREDIT_MIN_FRAC = None           # a floor on credit / width -- none unless Russ sets one (s165: 0.20 empties the list)
PROFIT_TAKE = 0.50               # buy back at half the credit
EXIT_DTE = 21                    # out at 21 days left
TOP_N, FIT_N = 5, 5
CEILING = 20                     # contracts, the sizing sanity limit

TABLES_SQL = """
CREATE TABLE IF NOT EXISTS spread_runs (
    id            TEXT PRIMARY KEY,
    run_at        TEXT NOT NULL,              -- local time
    rule_version  TEXT NOT NULL,
    ivr_date      TEXT,
    market_open   INTEGER,                    -- 0 = frozen (last) quotes
    n_universe    INTEGER,                    -- graded A-C or FUND
    n_checked     INTEGER,                    -- passed grade, IV rank and earnings
    n_found       INTEGER,                    -- had a spread inside every rule
    sleeve_free   REAL,
    rejects_json  TEXT,                       -- {ticker: reason} for every name left off
    note          TEXT
);
CREATE TABLE IF NOT EXISTS spread_candidates (
    run_id        TEXT NOT NULL REFERENCES spread_runs(id),
    ticker        TEXT NOT NULL,
    rank          INTEGER NOT NULL,
    grade TEXT, sector TEXT,
    expiry        TEXT NOT NULL,
    dte           INTEGER,
    spot          REAL,
    width_pct     REAL,                       -- the rule's width, as a fraction of the price
    short_strike  REAL NOT NULL, short_bid REAL, short_ask REAL, short_mid REAL,
    short_delta REAL, short_gamma REAL, short_theta REAL, short_vega REAL, short_iv REAL,
    long_strike   REAL NOT NULL, long_bid REAL, long_ask REAL, long_mid REAL,
    long_delta REAL, long_iv REAL,
    width         REAL,                       -- strike gap, dollars per share
    credit        REAL,                       -- mid credit per share
    max_loss      REAL,                       -- dollars per contract: (width - credit) x 100
    gap           REAL,                       -- worst-to-best fill, per share
    gap_pct       REAL,                       -- gap as % of the credit
    credit_frac   REAL,                       -- credit / width
    ann_return    REAL,                       -- fraction per year on the most it can lose
    iv_rank       REAL,
    earnings      TEXT,
    rest_price    REAL,                       -- the 50% buy-back, per share (a debit)
    exit_date     TEXT,                       -- expiry minus 21 days
    stock_if_assigned REAL,                   -- sold strike x 100, one contract
    size_n        INTEGER,
    size_binding  TEXT,
    size_note     TEXT,
    PRIMARY KEY (run_id, ticker)
);
CREATE TABLE IF NOT EXISTS spread_index (
    run_id        TEXT NOT NULL REFERENCES spread_runs(id),
    ticker        TEXT NOT NULL,
    width_pct     REAL NOT NULL,
    bar_met       INTEGER,                    -- 1 = IV rank at or above the bar; 0 = below; NULL = no reading
    note          TEXT,                       -- why no spread is stored, when none is
    rank          INTEGER,                    -- its rank on the list, when it is there at this width
    grade TEXT, sector TEXT, expiry TEXT, dte INTEGER, spot REAL,
    short_strike REAL, short_bid REAL, short_ask REAL, short_mid REAL,
    short_delta REAL, short_gamma REAL, short_theta REAL, short_vega REAL, short_iv REAL,
    long_strike REAL, long_bid REAL, long_ask REAL, long_mid REAL, long_delta REAL, long_iv REAL,
    width REAL, credit REAL, max_loss REAL, gap REAL, gap_pct REAL, credit_frac REAL, ann_return REAL,
    iv_rank REAL, earnings TEXT, rest_price REAL, exit_date TEXT, stock_if_assigned REAL,
    size_n INTEGER, size_binding TEXT, size_note TEXT,
    PRIMARY KEY (run_id, ticker, width_pct)
);
"""


# ---------------------------------------------------------------- pure rules
def _two_sided(c):
    return (c.get("source") in ("ibkr", "ibkr-live") and c.get("bid") and c.get("ask")
            and c["bid"] > 0 and c["ask"] > 0)


def short_reason(c):
    """None if the contract may be the sold put, else the first rule it breaks (the put list's own)."""
    return P.contract_reason(dict(c, spread_pct=0.0))     # the leg's own spread is judged on the whole spread


def pair_long(short, puts, spot, width_pct):
    """The bought put for `short`: the two-sided put on the same expiry, below the sold strike, whose
    strike is nearest short strike - width_pct x spot (ties: the lower strike). None if there is none."""
    target = short["strike"] - width_pct * spot
    below = [c for c in puts if c["expiration"] == short["expiration"] and c["strike"] < short["strike"] - 1e-9
             and _two_sided(c)]
    if not below:
        return None
    return min(below, key=lambda c: (abs(c["strike"] - target), c["strike"]))


def spread_of(short, long, spot, width_pct):
    """The spread's numbers, per share except max_loss (per contract)."""
    # From the quotes themselves, not the legs' rounded mids (two roundings drift a cent).
    raw_credit = ((short["bid"] + short["ask"]) - (long["bid"] + long["ask"])) / 2.0
    raw_gap = (short["ask"] - short["bid"]) + (long["ask"] - long["bid"])
    credit = round(raw_credit + 1e-9, 2)
    width = round(short["strike"] - long["strike"], 2)
    s = {"expiry": short["expiration"], "dte": short["dte"], "spot": spot, "width_pct": width_pct,
         "short": short, "long": long, "width": width, "credit": credit, "gap": round(raw_gap + 1e-9, 2),
         "max_loss": round((width - credit) * 100.0, 2)}
    s["gap_pct"] = round(raw_gap / raw_credit * 100.0, 4) if raw_credit > 0 else None
    s["credit_frac"] = round(credit / width, 4) if width > 0 else None
    s["ann_return"] = (credit / (width - credit) * 365.0 / short["dte"]
                       if credit > 0 and width - credit > 0 and short["dte"] else None)
    return s


def _pct(x):
    """Whole percent, unless rounding would hide which side of the 10% line it is on (10.4% printed "10%")."""
    w = "%.0f" % x
    return w if (float(w) >= GAP_MAX_PCT) == (x >= GAP_MAX_PCT) and float(w) != GAP_MAX_PCT or x == GAP_MAX_PCT else "%.1f" % x


def spread_reason(s, credit_min=None):
    """None if the spread passes every spread rule, else the first it breaks."""
    if s["credit"] <= 0:
        return "no credit at mid"
    if s["width"] - s["credit"] <= 0:
        return "credit at or above the width"
    if s["gap_pct"] is None or s["gap_pct"] >= GAP_MAX_PCT:
        return "market on the spread %s%% of the credit, 10%% or wider" % _pct(s["gap_pct"] if s["gap_pct"] is not None else 999)
    cm = CREDIT_MIN_FRAC if credit_min is None else credit_min
    if cm is not None and s["credit_frac"] < cm:
        return "credit %.0f%% of the width, under %.0f%%" % (100 * s["credit_frac"], 100 * cm)
    return None


def best_spread(puts, spot, width_pct=WIDTH_PCT, credit_min=None):
    """(best, why_none). Every sold put in the band is paired and judged; the passing spread with the
    highest yearly return wins (ties: nearer expiry, then the higher sold strike)."""
    if not puts:
        return None, "IBKR returned no two-sided put quotes"
    shorts = [c for c in puts if short_reason(c) is None]
    if not shorts:
        live = [c for c in puts if _two_sided(c)]
        if not live:
            return None, "no two-sided market on any put"
        inwin = [c for c in live if c.get("dte") is not None and P.DTE_MIN <= c["dte"] <= P.DTE_MAX]
        if not inwin:
            return None, P.DTE_REASON
        ds = sorted(abs(c["delta"]) for c in inwin if c.get("delta") is not None)
        lo = max((d for d in ds if d < P.DELTA_MIN), default=None)
        hi = min((d for d in ds if d > P.DELTA_MAX), default=None)
        near = " / ".join("%.2f" % d for d in (lo, hi) if d is not None)
        return None, "no put in the 0.25-0.30 delta band" + (" (nearest %s)" % near if near else "")
    spreads, whys = [], []
    for sh in shorts:
        lg = pair_long(sh, puts, spot, width_pct)
        if lg is None:
            whys.append("no bought put listed below the sold put")
            continue
        s = spread_of(sh, lg, spot, width_pct)
        w = spread_reason(s, credit_min)
        if w is None:
            spreads.append(s)
        else:
            whys.append(w)
            s["why"] = w
    if spreads:
        spreads.sort(key=lambda s: (-s["ann_return"], s["dte"], -s["short"]["strike"]))
        return spreads[0], None
    gaps = [w for w in whys if w.startswith("market on the spread")]
    if gaps:
        narrow = min(float(w.split("spread ")[1].split("%")[0]) for w in gaps)
        return None, "market on the spread 10%% of the credit or wider (narrowest %s%%)" % _pct(narrow)
    return None, sorted(set(whys))[0]


def rank(rows):
    return sorted(rows, key=lambda r: (-r["ann_return"], r["ticker"]))


def rest_price(credit):
    """The 50% buy-back of the credit, per share, half a cent rounding up (the put list's rounding)."""
    from helm.bookview import _half_up
    return _half_up(credit * (1 - PROFIT_TAKE))


def exit_date(expiry):
    return (P._d(expiry) - timedelta(days=EXIT_DTE)).isoformat()


# ---------------------------------------------------------------- the chain
NET_DELTA = (0.0, 0.35)          # the sold band and everything below it, so the bought put is read
NET_SPREAD = 10.0                # keep every quote; the spread rule judges


def fetch_puts(ticker, today=None):
    """Live puts from IBKR for the sold band and the strikes below it, down past the widest width
    (Yahoo gives only the price, the 14-day range and the listed expiries, as for the put list).
    (contracts, spot, err)."""
    from helm.cli import open_cmd as oc
    from helm import strikewindow as SW
    today = today or date.today()
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        spot = float(tk.fast_info.last_price)
        exps = tuple(tk.options)
    except Exception as e:
        return [], None, "price or expiries not read: %s" % str(e)[:80]
    exps_in, why = P.target_expiries(exps, today)
    if not exps_in:
        return [], spot, why
    atr = spot * 0.05
    lo, hi = SW.delta_range(spot, SW.latest_iv(ticker, today), [d for _, d in exps_in], "PUT", SW.PUT_NET)
    lo = lo - 1.25 * max(INDEX_WIDTHS + (WIDTH_PCT,)) * spot      # room below for the bought put
    try:
        rows = oc.fetch_chain_from_ibkr(ticker, "PUT", exps_in, spot, atr, NET_DELTA[0], NET_DELTA[1],
                                        (P.DELTA_MIN, P.DELTA_MAX), spread_threshold=NET_SPREAD,
                                        strike_range=(max(lo, 0.01), hi), report_errors=True)
    except oc.ChainReadError as e:
        return [], spot, str(e)
    return rows, spot, None


# ---------------------------------------------------------------- one line
def _row(conn, ch, s, b, db):
    from helm import sleeve as S
    t = ch["ticker"]
    sec, _ = S.sector_of(conn, t)
    n, binding, note = S.size(STRATEGY, t, s["max_loss"], db=db, b=b, ceiling=CEILING)
    earn = ch["earn_line"]
    if P.earnings_in_trade(ch["upcoming"], s["expiry"]):
        earn += " -- inside the trade"
    sh, lg = s["short"], s["long"]
    return {
        "ticker": t, "grade": ch["grade"], "sector": sec, "expiry": s["expiry"], "dte": s["dte"],
        "spot": s["spot"], "width_pct": s["width_pct"],
        "short_strike": sh["strike"], "short_bid": sh["bid"], "short_ask": sh["ask"], "short_mid": sh["mid"],
        "short_delta": sh.get("delta"), "short_gamma": sh.get("gamma"), "short_theta": sh.get("theta"),
        "short_vega": sh.get("vega"), "short_iv": sh.get("iv"),
        "long_strike": lg["strike"], "long_bid": lg["bid"], "long_ask": lg["ask"], "long_mid": lg["mid"],
        "long_delta": lg.get("delta"), "long_iv": lg.get("iv"),
        "width": s["width"], "credit": s["credit"], "max_loss": s["max_loss"], "gap": s["gap"],
        "gap_pct": s["gap_pct"], "credit_frac": s["credit_frac"], "ann_return": s["ann_return"],
        "iv_rank": ch["iv_rank"], "earnings": earn, "rest_price": rest_price(s["credit"]),
        "exit_date": exit_date(s["expiry"]), "stock_if_assigned": sh["strike"] * 100.0,
        "size_n": n, "size_binding": binding, "size_note": note,
    }


# ---------------------------------------------------------------- the index funds
def index_rows(conn, today, rejects, chains, fetch, b, db, ivr_date, tickers=None, stopped=None):
    """One entry per broad index fund per width. A fund the run already read reuses that chain (never
    read twice); one the IV-rank bar left off is read now; any other reason it was left off is its note.
    Reads `rejects` and `chains`, changes neither."""
    from helm import advice as A
    want = {t.upper() for t in tickers} if tickers else None
    out = []
    for t in P.index_names(conn):
        if want is not None and t not in want:
            continue
        r = conn.execute("SELECT iv_rank FROM iv_history WHERE ticker=? AND date=?",
                         (t, ivr_date)).fetchone() if ivr_date else None
        ivr = r[0] if r else None
        base = {"ticker": t, "iv_rank": ivr, "bar_met": None if ivr is None else int(ivr >= P.IVR_MIN)}
        if t in chains:
            rows, spot, err = chains[t]
        else:
            why = rejects.get(t) or "not on the run"
            if not (why.startswith("IV rank ") and ", below " in why):
                for w in INDEX_WIDTHS:
                    out.append(dict(base, width_pct=w, note=why))
                continue
            if stopped:
                for w in INDEX_WIDTHS:
                    out.append(dict(base, width_pct=w, note="not read: run stopped -- IB Gateway unreachable"))
                continue
            rows, spot, err = fetch(t)
        if err:
            for w in INDEX_WIDTHS:
                out.append(dict(base, width_pct=w, note=err if err.startswith("no expiry") else "chain not read: %s" % err))
            continue
        _ok, line, upcoming = P.earnings_check(True, None, None, today)
        ch = {"ticker": t, "grade": A._grade(conn, t), "iv_rank": ivr, "earn_line": line, "upcoming": upcoming}
        for w in INDEX_WIDTHS:
            s, why = best_spread(rows, spot, w)
            if s is None:
                out.append(dict(base, width_pct=w, note=why))
            else:
                out.append(dict(_row(conn, ch, s, b, db), bar_met=base["bar_met"], note=None))
    return out


# ---------------------------------------------------------------- the run
def build(conn, today, tickers=None, fetch=fetch_puts, book=None, db=None):
    """Run the rules. Returns the run dict (not yet recorded)."""
    from helm import sleeve as S
    checked, rejects, ivr_date, n_universe = P.screen(conn, today, tickers)
    b = book if book is not None else S.book(db=db)
    found, chains = [], {}
    streak, stopped = [], None
    for i, ch in enumerate(checked):
        t = ch["ticker"]
        rows, spot, err = fetch(t)
        chains[t] = (rows, spot, err)
        if err:
            rejects[t] = err if err.startswith("no expiry") else "chain not read: %s" % err
            streak = streak + [t] if P.gateway_down(err) else []
            if len(streak) >= P.GATEWAY_STOP_AFTER:
                stopped = P.stop_rest(rejects, [c["ticker"] for c in checked[i + 1:]], streak)
                break
            continue
        streak = []
        s, why = best_spread(rows, spot, WIDTH_PCT)
        if s is None:
            rejects[t] = why
            continue
        found.append(_row(conn, ch, s, b, db))
    ranked, firsts = [], {}
    for r in rank(found):                       # one per company, as on the put list
        k = S.name_key(r["ticker"])
        if k in firsts:
            rejects[r["ticker"]] = "same company as %s, which ranks higher -- one spread per company" % firsts[k]
            continue
        firsts[k] = r["ticker"]
        ranked.append(r)
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    try:                                        # display only -- never costs the list
        index = index_rows(conn, today, rejects, chains, fetch, b, db, ivr_date, tickers, stopped)
        rk = {r["ticker"]: r["rank"] for r in ranked}
        for x in index:
            x["rank"] = rk.get(x["ticker"]) if x.get("width_pct") == WIDTH_PCT and x.get("short_strike") is not None else None
    except Exception as e:
        index = [{"ticker": "INDEX", "width_pct": 0.0, "bar_met": None, "note": "index funds not read: %s" % str(e)[:80]}]
    return {"id": "spreads_" + uuid.uuid4().hex[:12],
            "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "rule_version": RULE_VERSION, "ivr_date": ivr_date,
            "market_open": P._market_open(), "n_universe": n_universe,
            "n_checked": len(checked), "n_found": len(ranked),
            "sleeve_free": b["share"] - b["used"], "rejects": rejects,
            "candidates": ranked, "stopped": stopped, "index": index}


CAND_COLS = ("ticker", "rank", "grade", "sector", "expiry", "dte", "spot", "width_pct",
             "short_strike", "short_bid", "short_ask", "short_mid", "short_delta", "short_gamma",
             "short_theta", "short_vega", "short_iv",
             "long_strike", "long_bid", "long_ask", "long_mid", "long_delta", "long_iv",
             "width", "credit", "max_loss", "gap", "gap_pct", "credit_frac", "ann_return", "iv_rank",
             "earnings", "rest_price", "exit_date", "stock_if_assigned", "size_n", "size_binding", "size_note")
INDEX_COLS = ("ticker", "width_pct", "bar_met", "note") + tuple(c for c in CAND_COLS if c not in ("ticker", "width_pct"))


def ensure_tables(conn):
    conn.executescript(TABLES_SQL)


def record(conn, run, note=None):
    ensure_tables(conn)
    conn.execute("INSERT INTO spread_runs (id, run_at, rule_version, ivr_date, market_open, n_universe, "
                 "n_checked, n_found, sleeve_free, rejects_json, note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (run["id"], run["run_at"], run["rule_version"], run["ivr_date"], run["market_open"],
                  run["n_universe"], run["n_checked"], run["n_found"], run["sleeve_free"],
                  json.dumps(run["rejects"], sort_keys=True), note))
    for c in run["candidates"]:
        conn.execute("INSERT INTO spread_candidates (run_id, %s) VALUES (?, %s)" % (
            ", ".join(CAND_COLS), ", ".join("?" * len(CAND_COLS))),
            (run["id"],) + tuple(c.get(k) for k in CAND_COLS))
    for x in run.get("index") or []:
        conn.execute("INSERT INTO spread_index (run_id, %s) VALUES (?, %s)" % (
            ", ".join(INDEX_COLS), ", ".join("?" * len(INDEX_COLS))),
            (run["id"],) + tuple(x.get(k) for k in INDEX_COLS))
    conn.commit()


def latest(conn):
    """The newest recorded run with its candidates and index rows, or None. Read-only."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='spread_runs'").fetchone():
        return None
    keys = ("id", "run_at", "rule_version", "ivr_date", "market_open", "n_universe", "n_checked",
            "n_found", "sleeve_free", "rejects_json", "note")
    r = conn.execute("SELECT %s FROM spread_runs ORDER BY run_at DESC, rowid DESC LIMIT 1" % ", ".join(keys)).fetchone()
    if not r:
        return None
    run = dict(zip(keys, tuple(r)))
    run["rejects"] = json.loads(run.pop("rejects_json") or "{}")
    run["candidates"] = [dict(zip(CAND_COLS, tuple(x))) for x in conn.execute(
        "SELECT %s FROM spread_candidates WHERE run_id=? ORDER BY rank" % ", ".join(CAND_COLS), (run["id"],))]
    run["index"] = [dict(zip(INDEX_COLS, tuple(x))) for x in conn.execute(
        "SELECT %s FROM spread_index WHERE run_id=? ORDER BY ticker, width_pct" % ", ".join(INDEX_COLS), (run["id"],))]
    return run


decision_text = P.decision_text


def fitting(cands, n=FIT_N):
    return P.fitting(cands, n)


declined_summary = P.declined_summary


# ---------------------------------------------------------------- self-test
def _put(exp, dte, k, bid, ask, delta):
    return {"expiration": exp, "dte": dte, "strike": k, "bid": bid, "ask": ask,
            "mid": round((bid + ask) / 2, 2), "delta": delta, "source": "ibkr",
            "spread_pct": round((ask - bid) / ((bid + ask) / 2) * 100, 1)}


def _spy_chain():
    """The S&P fund's Dec 24 puts as IB showed them at 13:19 on 2026-11-23 (delayed), spot 724.27."""
    E = "2026-12-24"
    q = [(645, 3.46, 3.56, .1033), (650, 3.81, 3.91, .1142), (655, 4.19, 4.30, .1255), (660, 4.63, 4.74, .139),
         (665, 5.10, 5.23, .1546), (670, 5.67, 5.78, .1704), (675, 6.27, 6.40, .185), (680, 6.97, 7.09, .2051),
         (685, 7.72, 7.86, .2297), (690, 8.60, 8.73, .2534), (695, 9.54, 9.69, .2808), (700, 10.62, 10.77, .3094)]
    return [_put(E, 31, k, b, a, d) for k, b, a, d in q], 724.27


def _selftest():
    import sqlite3
    fails, n = [], [0]

    def ok(cond, msg):
        n[0] += 1
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)

    puts, spot = _spy_chain()
    # the bought put at 5%, 3%, 2% of the price below the sold put
    sh = next(c for c in puts if c["strike"] == 695)
    ok(pair_long(sh, puts, spot, 0.05)["strike"] == 660, "5%: 695 - 36.21 = 658.79 -> the 660 put")
    ok(pair_long(sh, puts, spot, 0.03)["strike"] == 675, "3%: 695 - 21.73 = 673.27 -> the 675 put")
    ok(pair_long(sh, puts, spot, 0.02)["strike"] == 680, "2%: 695 - 14.49 = 680.51 -> the 680 put")
    ok(pair_long({"strike": 645, "expiration": "2026-12-24"}, puts, spot, 0.05) is None, "nothing listed below -> no bought put")
    tie = [_put("E", 31, 100, 1, 1.1, .28), _put("E", 31, 96, .4, .5, .1), _put("E", 31, 94, .3, .4, .08)]
    ok(pair_long(tie[0], tie, 100.0, 0.05)["strike"] == 94, "a tie between two strikes goes to the lower")
    other = [_put("A", 31, 100, 1, 1.1, .28), _put("B", 45, 95, .4, .5, .1)]
    ok(pair_long(other[0], other, 100.0, 0.05) is None, "the bought put must be on the same expiry")
    # the numbers, against the hand-worked table sent to Russ at 13:19
    s = spread_of(sh, pair_long(sh, puts, spot, 0.05), spot, 0.05)
    ok(abs(s["credit"] - 4.93) < 1e-9 and abs(s["max_loss"] - 3007.0) < 0.5, "695/660: credit $4.93, most it can lose $3,007")
    ok(abs(s["gap"] - 0.26) < 1e-9 and abs(s["gap_pct"] - 5.3) < 0.05, "695/660: worst-to-best 0.26 = 5.3% of the credit")
    ok(spread_reason(s) is None, "695/660 passes")
    s3 = spread_of(sh, pair_long(sh, puts, spot, 0.03), spot, 0.03)
    ok(spread_reason(s3) is None and abs(s3["gap_pct"] - 8.5) < 0.1, "695/675 passes at 8.5%")
    s2 = spread_of(sh, pair_long(sh, puts, spot, 0.02), spot, 0.02)
    ok(spread_reason(s2) is not None and s2["gap_pct"] >= 10.0, "695/680 is refused at %.1f%%" % s2["gap_pct"])
    # the 10% line itself is refused
    edge = spread_of(_put("E", 31, 100, 2.00, 2.05, .28), _put("E", 31, 95, 1.00, 1.05, .1), 100.0, 0.05)
    ok(abs(edge["gap_pct"] - 10.0) < 1e-9 and spread_reason(edge) is not None, "exactly 10% of the credit is refused")
    under = spread_of(_put("E", 31, 100, 2.00, 2.05, .28), _put("E", 31, 95, 1.00, 1.0499, .1), 100.0, 0.05)
    ok(spread_reason(under) is None, "just under 10% passes")
    # the credit floor: none unless set; 0.20 would refuse every SPY spread in the table
    ok(spread_reason(s, credit_min=0.20) is not None and s["credit_frac"] < 0.20, "a 20% floor would refuse 695/660 (14%)")
    ok(spread_reason(dict(s, credit=0.0)) == "no credit at mid", "no credit is refused")
    ok(_pct(10.4) == "10.4" and _pct(9.6) == "9.6" and _pct(10.0) == "10" and _pct(41.2) == "41" and _pct(5.3) == "5",
       "a market near the 10% line is shown to a decimal (10.4, not 10)")
    # the best per name
    best, why = best_spread(puts, spot)
    ok(best is not None and best["short"]["strike"] in (690, 695), "the best SPY spread sells the 690 or 695 put (%s)" %
       (best and best["short"]["strike"]))
    ok(best is not None and all(best["ann_return"] >= x["ann_return"] for x in
       [spread_of(c, pair_long(c, puts, spot, .05), spot, .05) for c in puts if short_reason(c) is None]),
       "the chosen spread has the highest yearly return on what it can lose")
    ok(best_spread([], spot)[1] == "IBKR returned no two-sided put quotes", "an empty chain says so")
    nob = [c for c in puts if c["delta"] < .24 or c["delta"] > .31]
    ok("delta band" in best_spread(nob, spot)[1], "no sold put in the band says so, with the nearest deltas")
    wide = [dict(c, ask=c["bid"] + 1.0, mid=round(c["bid"] + .5, 2)) for c in puts]
    w = best_spread(wide, spot)[1]
    ok(w.startswith("market on the spread"), "every spread too wide says so (%s)" % w)
    yahoo = [dict(c, source="yahoo") for c in puts]
    ok(best_spread(yahoo, spot)[0] is None, "a Yahoo quote is never used")
    # management numbers
    ok(abs(rest_price(4.93) - 2.47) < 1e-9, "the 50% buy-back of a $4.93 credit is $2.47 (half a cent rounds up)")
    ok(exit_date("2026-12-24") == "2026-12-03", "out at 21 days left: Dec 24 expiry -> Dec 3")
    ok(TOP_N == 5 and PROFIT_TAKE == 0.5 and EXIT_DTE == 21 and GAP_MAX_PCT == 10.0 and WIDTH_PCT == 0.05,
       "the constants are Russ's: top 5, 50%, 21 days, 10%, 5%")
    ok(CREDIT_MIN_FRAC is None, "no credit floor unless Russ sets one")
    # tables
    c = sqlite3.connect(":memory:")
    ensure_tables(c)
    cols = {r[1] for r in c.execute("PRAGMA table_info(spread_candidates)")}
    ok(set(CAND_COLS) <= cols, "every recorded column exists in spread_candidates")
    icols = {r[1] for r in c.execute("PRAGMA table_info(spread_index)")}
    ok(set(INDEX_COLS) <= icols, "every recorded column exists in spread_index")
    ok({"short_bid", "short_ask", "long_bid", "long_ask", "max_loss", "rest_price", "exit_date"} <= cols,
       "both legs' bid and ask, the maximum loss, the target and the time exit are recorded")
    print("spreadtool self-test: %d checks, %d failed" % (n[0], len(fails)))
    return not fails


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
