"""helm/calltool.py -- the covered-call tool (s138, 2026-10-28): the settled
covered-call rules, run on demand (`helm calls`), recorded.

The rules (rulebook v0.2; Russ 2026-10-24, 10-28):
  * on shares HELM holds (stock_positions), 100 at a time, not already covered by
    an open covered call. `--if-assigned TICKER` treats an open put as assigned
    (shares at the strike, effective basis after its premium) -- labelled, so the
    calls can be seen before the assignment decision.
  * strike >= the EFFECTIVE BASIS (strike paid less put premium, per share).
  * 0.20-0.35 delta, 30-60 DTE, spread < 10% of mid (Russ 10-28: as for puts),
    a live IBKR two-sided quote -- no Yahoo quotes.
  * NO EARNINGS INSIDE THE TRADE, failing closed: a known report on or before the
    expiry rules the contract out; with no upcoming date, a stock that reported
    within the last 60 days may sell calls expiring before last report + 70 days
    (the earliest a quarterly reporter is due again); otherwise none. Funds have
    no earnings.
  * ex-dividend inside the trade is SHOWN (a call in the money before it can be
    assigned early) -- the date is read from Yahoo at run time, labelled.
  * three choices per holding (Russ 10-28), ranked by annualized premium on the
    shares' value (mid / spot x 365 / DTE). The highest return is the highest
    delta, the likeliest to be called away -- hence three to choose from.
  * the 50% buy-back price for the resting order; 21 days is the manage line
    (helm advise carries both once the call is booked).

PMCC longs (s146, Russ 2026-11-01 -- the PMCC plan, step 1): an open PMCC with no
short is a holding too. Its next short: 0.20-0.30 delta (the PMCC band), 30-60 DTE,
spread < 10%, live IBKR quote; the strike must pass THE WIDTH TEST (fillopens.width_test:
strike - long strike > what the long cost net of rent banked, per share); earnings
inside the short are ALLOWED and flagged (not failed closed); the yearly return is on
the long's cost; "if called" is the whole trade's result with the long sold at no more
than its intrinsic value (so "at least"). A PMCC short never covers shares.

HELM informs, never acts. Writes only call_runs / call_candidates.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta

from helm import puttool as P

RULE_VERSION = ("calls-v3 (rulebook v0.2; shares 0.20-0.35, strike >= effective basis, earnings fail closed; "
                "PMCC 0.20-0.30, strike passes the width test, earnings flagged; 30-60 days, Russ 2026-11-03)")

DELTA_MIN, DELTA_MAX = 0.20, 0.35
PMCC_DELTA_MIN, PMCC_DELTA_MAX = 0.20, 0.30
DTE_MIN, DTE_MAX = P.DTE_MIN, P.DTE_MAX
SPREAD_MAX_PCT = P.SPREAD_MAX_PCT
NEXT_REPORT_EARLIEST = 70        # days after the last report before the next can fall
CHOICES = 3
PROFIT_TAKE = 0.50

RUNS_SQL = """
CREATE TABLE IF NOT EXISTS call_runs (
    id            TEXT PRIMARY KEY,
    run_at        TEXT NOT NULL,
    rule_version  TEXT NOT NULL,
    market_open   INTEGER,
    n_holdings    INTEGER,                    -- holdings looked at (real or if-assigned)
    hypothetical  INTEGER NOT NULL DEFAULT 0, -- 1 = an --if-assigned run
    rejects_json  TEXT,                       -- {ticker: reason} for a holding with no call
    note          TEXT
);
"""
CANDS_SQL = """
CREATE TABLE IF NOT EXISTS call_candidates (
    run_id          TEXT NOT NULL REFERENCES call_runs(id),
    kind            TEXT NOT NULL DEFAULT 'shares',  -- s146: 'shares' or 'pmcc'
    ticker          TEXT NOT NULL,
    choice          INTEGER NOT NULL,         -- 1..3 within the holding
    hypothetical    INTEGER NOT NULL DEFAULT 0,
    shares          INTEGER, contracts INTEGER,
    cost_basis      REAL, effective_basis REAL,
    expiry          TEXT NOT NULL, dte INTEGER, strike REAL NOT NULL, spot REAL,
    bid REAL, ask REAL, mid REAL, spread_pct REAL,
    delta REAL, gamma REAL, theta REAL, vega REAL, iv REAL,
    ann_return      REAL,                     -- fraction per year on the shares' value
    if_called       REAL,                     -- dollars if called: (strike - effective basis) x shares + premium
    earnings        TEXT, exdiv TEXT,
    rest_price      REAL,
    position_id     TEXT,                     -- s146: the PMCC position (NULL for shares)
    long_strike     REAL,                     -- s146: the PMCC's long strike
    floor_strike    REAL,                     -- s146: long strike + the width-test floor
    PRIMARY KEY (run_id, kind, ticker, choice)
);
"""
TABLES_SQL = RUNS_SQL + CANDS_SQL


# ---------------------------------------------------------------- pure rules
def earnings_cutoff(is_fund, next_date, last_report, today):
    """(latest allowed expiry or None for 'none allowed', plain line). Pure.
    Calls must expire BEFORE the next report."""
    if is_fund:
        return date.max, "fund -- no earnings"
    nd, lr = P._d(next_date), P._d(last_report)
    if nd is not None and nd >= today:
        return nd - timedelta(days=1), "earnings %s -- calls must expire before it" % nd
    if lr is not None and lr <= today and (today - lr).days <= P.LAST_REPORT_OK_DAYS:
        lim = lr + timedelta(days=NEXT_REPORT_EARLIEST)
        return lim - timedelta(days=1), "last reported %s; next not known -- calls must expire before %s" % (lr, lim)
    if lr is not None:
        return None, "no reliable earnings date (last known %s, %d days ago)" % (lr, (today - lr).days)
    return None, "no reliable earnings date"


def contract_reason(c, eff_basis, latest_expiry, kind="shares", strike_ok=None):
    """kind 'shares': strike >= eff_basis. kind 'pmcc': strike_ok(strike) is the width
    test (eff_basis unused); the earnings limit is passed as date.max (allowed)."""
    d, dte, sp = c.get("delta"), c.get("dte"), c.get("spread_pct")
    lo, hi = (PMCC_DELTA_MIN, PMCC_DELTA_MAX) if kind == "pmcc" else (DELTA_MIN, DELTA_MAX)
    if c.get("source") not in ("ibkr", "ibkr-live"):
        return "no live IBKR quote"
    if not c.get("bid") or not c.get("ask") or c["bid"] <= 0 or c["ask"] <= 0:
        return "no two-sided market"
    if d is None or not (lo - 1e-9 <= abs(d) <= hi + 1e-9):
        return "delta outside %.2f-%.2f" % (lo, hi)
    if dte is None or not (DTE_MIN <= dte <= DTE_MAX):
        return P.DTE_REASON
    if kind == "pmcc":
        if not strike_ok(c["strike"]):
            return "strike not above the width-test floor"
    elif c["strike"] < eff_basis - 1e-9:
        return "strike below the effective basis"
    if latest_expiry is None or P._d(c["expiration"]) > latest_expiry:
        return "earnings inside the trade"
    if sp is None or sp >= SPREAD_MAX_PCT:
        return "spread 10% of mid or wider"
    return None


def ann_return(c, spot):
    """Yearly return on `spot` -- the shares' value, or for a PMCC the long's cost a share."""
    return c["mid"] / spot * 365.0 / c["dte"]


def choose(contracts, eff_basis, latest_expiry, spot, n=CHOICES, kind="shares", strike_ok=None):
    """(top n passing contracts, why_none). Ranked by annualized premium on `spot`
    (the shares' value; a PMCC passes the long's cost); ties to the nearer expiry,
    then the HIGHER strike (keeps the shares / the long)."""
    lo, hi = (PMCC_DELTA_MIN, PMCC_DELTA_MAX) if kind == "pmcc" else (DELTA_MIN, DELTA_MAX)
    reason = lambda c: contract_reason(c, eff_basis, latest_expiry, kind, strike_ok)  # noqa: E731
    ok = [c for c in contracts if reason(c) is None]
    if ok:
        ok.sort(key=lambda c: (-ann_return(c, spot), c["dte"], -c["strike"]))
        return ok[:n], None
    if not contracts:
        return [], "IBKR returned no two-sided call quotes near %.2f-%.2f delta" % (lo, hi)
    order = ("strike below the effective basis", "strike not above the width-test floor",
             "earnings inside the trade", "delta outside %.2f-%.2f" % (lo, hi),
             "spread 10% of mid or wider", P.DTE_REASON, "no two-sided market", "no live IBKR quote")
    why = {id(c): reason(c) for c in contracts}
    # spread is checked last, so a contract failing only on spread met every other rule
    wide = [c for c in contracts if why[id(c)] == "spread 10% of mid or wider" and c.get("spread_pct") is not None]
    if wide:
        return [], "spread 10%% of mid or wider (narrowest in the band %.0f%%)" % min(c["spread_pct"] for c in wide)
    seen = set(why.values())
    return [], "no call passes: " + "; ".join(r for r in order if r in seen)


# ---------------------------------------------------------------- holdings
def holdings(conn, account_id=None):
    """[{ticker, shares, free_shares, contracts, cost_basis, effective_basis, hypothetical:0}]."""
    from helm import assignment as A
    A.ensure_columns(conn)
    out = []
    q = "SELECT * FROM stock_positions" + (" WHERE account_id=?" if account_id else "") + " ORDER BY ticker"
    for r in conn.execute(q, ((account_id,) if account_id else ())):
        covered = conn.execute(       # s146: only a covered call covers shares, never a PMCC's short
            "SELECT COALESCE(SUM(l.contracts * COALESCE(l.multiplier,100)),0) FROM legs l JOIN positions p "
            "ON p.id=l.position_id WHERE p.book='REAL' AND p.status='OPEN' AND p.strategy='COVERED_CALL' "
            "AND p.ticker=? AND l.status='OPEN' AND l.direction='SHORT' AND l.option_type='CALL'",
            (r["ticker"],)).fetchone()[0]
        free = int(r["shares"] or 0) - int(covered or 0)
        eff = r["effective_basis"] if r["effective_basis"] is not None else r["cost_basis"]
        out.append({"ticker": r["ticker"], "shares": int(r["shares"] or 0), "free_shares": free,
                    "contracts": max(free, 0) // 100, "cost_basis": r["cost_basis"],
                    "effective_basis": eff, "hypothetical": 0,
                    "basis_note": None if r["effective_basis"] is not None else "no effective basis stored -- cost used"})
    return out


def pmcc_holdings(conn, account_id=None):
    """Every open real PMCC, as a holding for its next short (s146).
    [{kind:'pmcc', ticker, position_id, contracts, long_strike, long_expiry, long_price,
      floor, floor_strike, rent_banked, legs, short_open, problem, hypothetical:0}].
    contracts is 0 when a short is open (short_open names it) or the legs can't be read."""
    from helm.fillopens import width_floor
    out = []
    q = ("SELECT * FROM positions WHERE book='REAL' AND status='OPEN' AND strategy='PMCC'"
         + (" AND account_id=?" if account_id else "") + " ORDER BY ticker, id")
    for p in conn.execute(q, ((account_id,) if account_id else ())).fetchall():
        legs = [dict(l) for l in conn.execute("SELECT * FROM legs WHERE position_id=? ORDER BY id", (p["id"],))]
        opts = [l for l in legs if l["option_type"] in ("CALL", "PUT")]
        longs = [l for l in opts if l["status"] == "OPEN" and l["direction"] == "LONG"]
        shorts = [l for l in opts if l["status"] == "OPEN" and l["direction"] == "SHORT"]
        h = {"kind": "pmcc", "ticker": p["ticker"], "position_id": p["id"], "contracts": 0, "hypothetical": 0,
             "legs": opts, "short_open": None, "problem": None, "shares": None, "free_shares": None,
             "cost_basis": None, "effective_basis": None, "basis_note": None}
        if len(longs) != 1 or longs[0]["option_type"] != "CALL":
            h["problem"] = "can't read this PMCC: %d open long legs" % len(longs)
            out.append(h)
            continue
        L = longs[0]
        floor, lk = width_floor(opts)
        h.update({"long_strike": lk, "long_expiry": str(L["expiration"])[:10], "long_price": L["open_price"],
                  "floor": floor, "floor_strike": lk + floor,
                  "rent_banked": ((L["open_price"] or 0) - floor) * (L["multiplier"] or 100) * (L["contracts"] or 0),
                  "cost_basis": L["open_price"]})
        if shorts:
            h["short_open"] = "; ".join("the %g call %s" % (x["strike"], str(x["expiration"])[:10]) for x in shorts)
        else:
            h["contracts"] = int(L["contracts"] or 0)
        out.append(h)
    return out


def pmcc_earnings_line(is_fund, next_date, expiry, today):
    """Earnings for a PMCC short: allowed, flagged when inside the trade. Pure."""
    if is_fund:
        return "fund -- no earnings"
    nd = P._d(next_date)
    if nd is None or nd < today:
        return "next earnings date not known"
    if nd <= P._d(expiry):
        return "EARNINGS %s INSIDE THE TRADE -- allowed for a PMCC, flagged" % nd
    return "earnings %s, after the expiry" % nd


def if_assigned(conn, ticker):
    """The holding an open put would hand over, from assignment.preview. Raises ValueError."""
    from helm import assignment as A
    A.ensure_columns(conn)
    rows = conn.execute("SELECT id FROM positions WHERE book='REAL' AND status='OPEN' AND strategy='CSP' "
                        "AND ticker=?", (ticker.upper(),)).fetchall()
    if len(rows) != 1:
        raise ValueError("%d open real puts on %s -- need exactly one" % (len(rows), ticker.upper()))
    v = A.preview(conn, rows[0]["id"])
    return {"ticker": v["ticker"], "shares": v["new_shares"], "free_shares": v["new_shares"],
            "contracts": v["new_shares"] // 100, "cost_basis": v["cost_basis"],
            "effective_basis": v["effective_basis"], "hypothetical": 1,
            "basis_note": "if the %s %g put is assigned on %s" % (v["ticker"], v["strike"], v["expiry"])}


def fetch_calls(ticker, today=None):
    """Live calls straight from IBKR (Yahoo gives only spot, range, expiries, ex-dividend).
    (contracts, spot, exdiv_date_or_None, err)."""
    from helm.cli import open_cmd as oc
    today = today or date.today()
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        spot = float(tk.fast_info.last_price)
        exps = tuple(tk.options)
    except Exception as e:
        return [], None, None, "price or expiries not read: %s" % str(e)[:80]
    exdiv = None
    try:
        v = (tk.info or {}).get("exDividendDate")
        if v:
            exdiv = datetime.utcfromtimestamp(int(v)).date() if not isinstance(v, str) else P._d(v)
    except Exception:
        exdiv = None
    exps_in, why = P.target_expiries(exps, today)
    if not exps_in:
        return [], spot, exdiv, why
    atr = spot * 0.05
    try:
        h = tk.history(period="30d", interval="1d")
        if not h.empty and len(h) >= 14:
            a = float((h["High"] - h["Low"]).rolling(14).mean().iloc[-1])
            if a == a and a > 0:
                atr = a
    except Exception:
        pass
    from helm import strikewindow as SW   # s148: strikes from the stock's IV, not 3 x ATR
    rng = SW.delta_range(spot, SW.latest_iv(ticker, today), [d for _, d in exps_in], "CALL", SW.CALL_NET)
    try:
        rows = oc.fetch_chain_from_ibkr(ticker, "CALL", exps_in, spot, atr, 0.15, 0.40,
                                        (DELTA_MIN, DELTA_MAX), spread_threshold=P.NET_SPREAD, strike_range=rng,
                                        report_errors=True)
    except oc.ChainReadError as e:                 # s149: say so, never "no quotes"
        return [], spot, exdiv, str(e)
    return rows, spot, exdiv, None


def build(conn, today, tickers=None, assigned=None, fetch=fetch_calls, account_id=None):
    """assigned: [ticker] for --if-assigned. Returns the run dict (not recorded)."""
    from helm import advice as Adv
    from helm.bookview import _half_up
    rejects, hold = {}, []
    if assigned:
        for t in assigned:
            try:
                hold.append(if_assigned(conn, t))
            except ValueError as e:
                rejects[t.upper()] = str(e)
    pm = []
    if not assigned:
        hold = holdings(conn, account_id)
        pm = pmcc_holdings(conn, account_id)
        if tickers:
            want = {t.upper() for t in tickers}
            hold = [h for h in hold if h["ticker"] in want]
            pm = [h for h in pm if h["ticker"] in want]
    cands = []
    for h in hold:
        t = h["ticker"]
        if h["contracts"] < 1:
            rejects[t] = ("%d shares, all covered by open calls" % h["shares"]) if h["free_shares"] < 100 and \
                h["shares"] >= 100 else "fewer than 100 shares"
            continue
        is_fund = Adv._grade(conn, t) == "FUND"
        latest, eline = earnings_cutoff(is_fund, P._next_report(conn, t, today), P._last_report(conn, t, today), today)
        if latest is None:
            rejects[t] = eline
            continue
        rows, spot, exdiv, err = fetch(t)
        if err:
            rejects[t] = err if err.startswith("no expiry") else "chain not read: %s" % err
            continue
        picks, why = choose(rows, h["effective_basis"], latest, spot)
        if not picks:
            rejects[t] = why
            continue
        for i, c in enumerate(picks, 1):
            n = h["contracts"]
            ex = None
            if exdiv and today <= exdiv <= P._d(c["expiration"]):
                ex = "ex-dividend %s inside the trade (Yahoo)" % exdiv
            cands.append({
                "ticker": t, "choice": i, "hypothetical": h["hypothetical"], "shares": h["shares"],
                "contracts": n, "cost_basis": h["cost_basis"], "effective_basis": h["effective_basis"],
                "expiry": c["expiration"], "dte": c["dte"], "strike": c["strike"], "spot": spot,
                "bid": c["bid"], "ask": c["ask"], "mid": c["mid"], "spread_pct": c["spread_pct"],
                "delta": c.get("delta"), "gamma": c.get("gamma"), "theta": c.get("theta"),
                "vega": c.get("vega"), "iv": c.get("iv"), "ann_return": ann_return(c, spot),
                "if_called": (c["strike"] - h["effective_basis"]) * 100 * n + c["mid"] * 100 * n,
                "earnings": eline, "exdiv": ex, "rest_price": _half_up(c["mid"] * (1 - PROFIT_TAKE)),
                "basis_note": h.get("basis_note"), "kind": "shares",
            })
    from helm.fillopens import width_test
    for h in pm:
        t, key = h["ticker"], h["ticker"] + " (PMCC)"
        if h["problem"]:
            rejects[key] = h["problem"]
            continue
        if h["short_open"]:
            rejects[key] = "a short is already open (%s) -- nothing to sell until it closes" % h["short_open"]
            continue
        if h["contracts"] < 1:
            rejects[key] = "no contracts on the long"
            continue
        rows, spot, exdiv, err = fetch(t)
        if err:
            rejects[key] = err if err.startswith("no expiry") else "chain not read: %s" % err
            continue
        legs, n = h["legs"], h["contracts"]
        picks, why = choose(rows, None, date.max, h["long_price"], kind="pmcc",
                            strike_ok=lambda k, legs=legs, n=n: bool(width_test(legs, k, n)[0]))
        if not picks:
            rejects[key] = why
            continue
        is_fund = Adv._grade(conn, t) == "FUND"
        nxt = None if is_fund else P._next_report(conn, t, today)
        for i, c in enumerate(picks, 1):
            ex = None
            if exdiv and today <= exdiv <= P._d(c["expiration"]):
                ex = "ex-dividend %s inside the trade (Yahoo)" % exdiv
            cands.append({
                "ticker": t, "choice": i, "hypothetical": 0, "shares": None, "contracts": n,
                "cost_basis": h["long_price"], "effective_basis": None,
                "expiry": c["expiration"], "dte": c["dte"], "strike": c["strike"], "spot": spot,
                "bid": c["bid"], "ask": c["ask"], "mid": c["mid"], "spread_pct": c["spread_pct"],
                "delta": c.get("delta"), "gamma": c.get("gamma"), "theta": c.get("theta"),
                "vega": c.get("vega"), "iv": c.get("iv"), "ann_return": ann_return(c, h["long_price"]),
                "if_called": (c["strike"] - h["floor_strike"]) * 100 * n + c["mid"] * 100 * n,
                "earnings": pmcc_earnings_line(is_fund, nxt, c["expiration"], today), "exdiv": ex,
                "rest_price": _half_up(c["mid"] * (1 - PROFIT_TAKE)), "basis_note": None,
                "kind": "pmcc", "position_id": h["position_id"], "long_strike": h["long_strike"],
                "floor_strike": h["floor_strike"], "long_expiry": h["long_expiry"],
                "rent_banked": h["rent_banked"],
            })
    return {"id": "calls_" + uuid.uuid4().hex[:12],
            "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "rule_version": RULE_VERSION, "market_open": P._market_open(),
            "n_holdings": len(hold) + len(pm), "hypothetical": 1 if assigned else 0,
            "rejects": rejects, "candidates": cands}


CAND_COLS = ("ticker", "choice", "hypothetical", "shares", "contracts", "cost_basis", "effective_basis",
             "expiry", "dte", "strike", "spot", "bid", "ask", "mid", "spread_pct", "delta", "gamma", "theta",
             "vega", "iv", "ann_return", "if_called", "earnings", "exdiv", "rest_price",
             "kind", "position_id", "long_strike", "floor_strike")


def ensure_tables(conn):
    conn.executescript(TABLES_SQL)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(call_candidates)")]
    if "kind" not in cols:          # s146: a calls-v1 table -- rebuild with kind in the key, rows kept
        old = ", ".join(cols)
        conn.executescript("BEGIN; ALTER TABLE call_candidates RENAME TO call_candidates_v1; " + CANDS_SQL
                           + "; INSERT INTO call_candidates (%s) SELECT %s FROM call_candidates_v1; "
                           "DROP TABLE call_candidates_v1; COMMIT;" % (old, old))


def record(conn, run, note=None):
    ensure_tables(conn)
    conn.execute("INSERT INTO call_runs (id, run_at, rule_version, market_open, n_holdings, hypothetical, "
                 "rejects_json, note) VALUES (?,?,?,?,?,?,?,?)",
                 (run["id"], run["run_at"], run["rule_version"], run["market_open"], run["n_holdings"],
                  run["hypothetical"], json.dumps(run["rejects"], sort_keys=True), note))
    for c in run["candidates"]:
        conn.execute("INSERT INTO call_candidates (run_id, %s) VALUES (?, %s)" % (
            ", ".join(CAND_COLS), ", ".join("?" * len(CAND_COLS))),
            (run["id"],) + tuple(c.get(k) for k in CAND_COLS))
    conn.commit()


def latest(conn, hypothetical=0):
    """Newest recorded run of the given kind, or None. Read-only (the board's reader)."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='call_runs'").fetchone():
        return None
    r = conn.execute("SELECT id, run_at, rule_version, market_open, n_holdings, hypothetical, rejects_json, note "
                     "FROM call_runs WHERE hypothetical=? ORDER BY run_at DESC, rowid DESC LIMIT 1",
                     (hypothetical,)).fetchone()
    if not r:
        return None
    run = dict(zip(("id", "run_at", "rule_version", "market_open", "n_holdings", "hypothetical",
                    "rejects_json", "note"), tuple(r)))
    run["rejects"] = json.loads(run.pop("rejects_json") or "{}")
    have = {r[1] for r in conn.execute("PRAGMA table_info(call_candidates)")}
    cols = [c for c in CAND_COLS if c in have]          # a calls-v1 table has no kind etc. (read-only readers)
    run["candidates"] = []
    for x in conn.execute("SELECT %s FROM call_candidates WHERE run_id=? ORDER BY %s ticker, choice" % (
            ", ".join(cols), "kind DESC," if "kind" in have else ""), (run["id"],)):
        d = {c: None for c in CAND_COLS}
        d.update(zip(cols, tuple(x)))
        d["kind"] = d["kind"] or "shares"
        run["candidates"].append(d)
    return run


# ---------------------------------------------------------------- self-test
def _selftest():
    import sqlite3
    fails = []

    def ok(c, m):
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            fails.append(m)

    T = date(2026, 10, 28)
    # earnings cutoff
    ok(earnings_cutoff(True, None, None, T)[0] == date.max, "a fund has no earnings limit")
    ok(earnings_cutoff(False, "2026-12-01", None, T)[0] == date(2026, 11, 30), "calls must expire the day before a known report")
    ok(earnings_cutoff(False, None, "2026-10-21", T)[0] == date(2026, 12, 29), "reported 10-21: calls may expire through 12-29 (10-21 + 70 - 1)")
    ok(earnings_cutoff(False, None, "2026-08-28", T)[0] is None, "reported 61 days ago fails closed")
    ok(earnings_cutoff(False, None, None, T)[0] is None, "no date at all fails closed")
    # contract rules
    base = {"source": "ibkr", "bid": 2.0, "ask": 2.1, "delta": 0.30, "dte": 35, "spread_pct": 4.9,
            "mid": 2.05, "strike": 350.0, "expiration": "2026-12-02"}
    L = date(2026, 12, 31)
    ok(contract_reason(base, 326.62, L) is None, "a call inside every rule passes")
    ok(contract_reason({**base, "strike": 326.0}, 326.62, L) == "strike below the effective basis",
       "a strike below the effective basis fails")
    ok(contract_reason({**base, "strike": 326.62}, 326.62, L) is None, "a strike AT the effective basis passes")
    ok(contract_reason(base, 326.62, date(2026, 12, 1)) == "earnings inside the trade",
       "an expiry after the earnings cutoff fails")
    ok(contract_reason(base, 326.62, date(2026, 12, 2)) is None, "an expiry ON the cutoff day passes")
    ok(contract_reason(base, 326.62, None) == "earnings inside the trade", "no allowed expiry fails closed")
    ok(contract_reason({**base, "delta": 0.20}, 1, L) is None and contract_reason({**base, "delta": 0.35}, 1, L) is None,
       "0.20 and 0.35 delta are included")
    ok(contract_reason({**base, "delta": 0.36}, 1, L) is not None and contract_reason({**base, "delta": 0.19}, 1, L) is not None,
       "0.19 and 0.36 fail")
    ok(contract_reason({**base, "spread_pct": 10.0}, 1, L) is not None, "a 10.0% spread fails")
    ok(contract_reason({**base, "source": "yfinance"}, 1, L) is not None, "a Yahoo quote fails")
    ok(contract_reason({**base, "dte": 60}, 1, L) is None and contract_reason({**base, "dte": 61}, 1, L) == "outside 30-60 days"
       and contract_reason({**base, "dte": 29}, 1, L) == "outside 30-60 days", "s149: 60 days passes; 29 and 61 fail")
    # choosing
    a = {**base, "strike": 340.0, "mid": 3.0, "delta": 0.35}
    b = {**base, "strike": 350.0, "mid": 2.0, "delta": 0.28}
    c = {**base, "strike": 360.0, "mid": 1.2, "delta": 0.21}
    d = {**base, "strike": 370.0, "mid": 0.8, "delta": 0.15}
    e = {**base, "strike": 320.0, "mid": 9.0, "delta": 0.34}
    f = {**base, "strike": 355.0, "mid": 1.6, "delta": 0.25}
    picks, _ = choose([d, c, b, a, e, f], 326.62, L, 345.0)
    ok([x["strike"] for x in picks] == [340.0, 350.0, 355.0],
       "three choices by return; out-of-band and below-basis strikes skipped: %s" % [x["strike"] for x in picks])
    ok(abs(ann_return(a, 345.0) - 3.0 / 345 * 365 / 35) < 1e-12, "annualized on the shares' value")
    _, why = choose([{**b, "spread_pct": 14.1}, {**c, "spread_pct": 18.0}, {**d, "spread_pct": 5.0}, e], 326.62, L, 345.0)
    ok(why == "spread 10% of mid or wider (narrowest in the band 14%)",
       "too-wide holdings name the narrowest spread among calls meeting every other rule: %s" % why)
    _, why = choose([e], 326.62, L, 345.0)
    ok(why == "no call passes: strike below the effective basis", "a holding with no call says why: %s" % why)

    # end to end
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE positions (id TEXT PRIMARY KEY, account_id TEXT, strategy TEXT, ticker TEXT, status TEXT,
        book TEXT, closed_at TEXT, exit_reason TEXT, realized_pnl REAL, notes TEXT, updated_at TEXT,
        total_contracts INT, net_premium REAL);
      CREATE TABLE legs (id TEXT PRIMARY KEY, position_id TEXT, option_type TEXT, direction TEXT, strike REAL,
        expiration TEXT, contracts INT, multiplier INT, open_price REAL, close_price REAL, close_date TEXT,
        status TEXT, notes TEXT);
      CREATE TABLE stock_positions (id TEXT PRIMARY KEY, account_id TEXT, ticker TEXT, shares INT,
        cost_basis REAL, acquired_at TEXT, notes TEXT, updated_at TEXT);
      CREATE TABLE watchlist (ticker TEXT, next_earnings TEXT);
      CREATE TABLE earnings_history (ticker TEXT, earn_date TEXT);
      CREATE TABLE approved_funds (ticker TEXT, bucket TEXT);
      CREATE TABLE ownership_quality (ticker TEXT, date TEXT, grade TEXT, updated_at TEXT);
      INSERT INTO positions VALUES ('G','a','CSP','GOOGL','OPEN','REAL',NULL,NULL,NULL,NULL,NULL,1,838);
      INSERT INTO legs VALUES ('GL','G','PUT','SHORT',335,'2026-11-20',1,100,8.38,NULL,NULL,'OPEN',NULL);
      INSERT INTO watchlist VALUES ('GOOGL','2027-02-03');
      INSERT INTO stock_positions VALUES ('S1','a','AAA',250,100.0,NULL,NULL,NULL);
      INSERT INTO watchlist VALUES ('AAA','2027-01-20');
      INSERT INTO stock_positions VALUES ('S2','a','BBB',100,50.0,NULL,NULL,NULL);
      INSERT INTO watchlist VALUES ('BBB',NULL);
      INSERT INTO positions VALUES ('C','a','COVERED_CALL','AAA','OPEN','REAL',NULL,NULL,NULL,NULL,NULL,1,100);
      INSERT INTO legs VALUES ('CL','C','CALL','SHORT',110,'2026-11-20',1,100,1.0,NULL,NULL,'OPEN',NULL);
    """)
    hs = {h["ticker"]: h for h in holdings(conn)}
    ok(hs["AAA"]["free_shares"] == 150 and hs["AAA"]["contracts"] == 1,
       "250 shares with one call open leaves one contract to sell")
    ok(hs["AAA"]["effective_basis"] == 100.0 and hs["AAA"]["basis_note"], "no stored effective basis falls back to cost, and says so")
    h = if_assigned(conn, "GOOGL")
    ok(h["shares"] == 100 and abs(h["effective_basis"] - 326.62) < 1e-9 and h["hypothetical"] == 1,
       "--if-assigned GOOGL: 100 shares at effective 326.62, marked hypothetical")
    ok(conn.execute("SELECT status FROM legs WHERE id='GL'").fetchone()[0] == "OPEN", "...and writes nothing")
    chain = [{**b, "strike": 340.0}, {**b, "strike": 350.0, "mid": 1.5}]
    run = build(conn, T, assigned=["GOOGL"], fetch=lambda t: (chain, 345.0, date(2026, 11, 30), None))
    ok([c["strike"] for c in run["candidates"]] == [340.0, 350.0] and run["hypothetical"] == 1,
       "an if-assigned run ranks GOOGL's calls")
    c1 = run["candidates"][0]
    ok(abs(c1["if_called"] - ((340 - 326.62) * 100 + 2.0 * 100)) < 1e-6, "if called = (strike - effective basis) x shares + premium")
    ok(c1["exdiv"] and "2026-11-30" in c1["exdiv"], "an ex-dividend date inside the trade is shown")
    run2 = build(conn, T, fetch=lambda t: (chain, 345.0, None, None))
    ok("BBB" in run2["rejects"] and "reliable" in run2["rejects"]["BBB"], "a holding with no earnings date fails closed")
    record(conn, run)
    record(conn, run2)
    ok(latest(conn, 1)["id"] == run["id"] and latest(conn, 0)["id"] == run2["id"],
       "real and if-assigned runs are read back separately")
    # ---- s146: PMCC longs
    T2 = date(2026, 11, 1)
    ok(contract_reason({**base, "delta": 0.31}, None, date.max, "pmcc", lambda k: True) == "delta outside 0.20-0.30",
       "a PMCC short at 0.31 delta fails (its band is 0.20-0.30)")
    ok(contract_reason({**base, "delta": 0.30}, None, date.max, "pmcc", lambda k: True) is None,
       "a PMCC short at 0.30 passes")
    ok(contract_reason(base, None, date.max, "pmcc", lambda k: False) == "strike not above the width-test floor",
       "a PMCC short failing the width test fails with that reason")
    c2 = sqlite3.connect(":memory:")
    c2.row_factory = sqlite3.Row
    c2.executescript("""
      CREATE TABLE positions (id TEXT PRIMARY KEY, account_id TEXT, strategy TEXT, ticker TEXT, status TEXT,
        book TEXT, closed_at TEXT, exit_reason TEXT, realized_pnl REAL, notes TEXT, updated_at TEXT,
        total_contracts INT, net_premium REAL);
      CREATE TABLE legs (id TEXT PRIMARY KEY, position_id TEXT, option_type TEXT, direction TEXT, strike REAL,
        expiration TEXT, contracts INT, multiplier INT, open_price REAL, close_price REAL, close_date TEXT,
        status TEXT, notes TEXT);
      CREATE TABLE stock_positions (id TEXT PRIMARY KEY, account_id TEXT, ticker TEXT, shares INT,
        cost_basis REAL, acquired_at TEXT, notes TEXT, updated_at TEXT);
      CREATE TABLE watchlist (ticker TEXT, next_earnings TEXT);
      CREATE TABLE earnings_history (ticker TEXT, earn_date TEXT);
      CREATE TABLE approved_funds (ticker TEXT, bucket TEXT);
      CREATE TABLE ownership_quality (ticker TEXT, date TEXT, grade TEXT, updated_at TEXT);
      INSERT INTO watchlist VALUES ('NVDA','2026-11-17');
      INSERT INTO positions VALUES ('N','a','PMCC','NVDA','OPEN','REAL',NULL,NULL,NULL,NULL,NULL,1,-5950);
      INSERT INTO legs VALUES ('NL','N','CALL','LONG',160,'2027-12-17',1,100,62.90,NULL,NULL,'OPEN',NULL);
      INSERT INTO legs VALUES ('NS','N','CALL','SHORT',225,'2026-12-04',1,100,3.40,NULL,NULL,'OPEN',NULL);
      INSERT INTO stock_positions VALUES ('S9','a','NVDA',100,200.0,NULL,NULL,NULL);
    """)
    ok(holdings(c2)[0]["free_shares"] == 100, "a PMCC's open short does not cover HELM-held shares of the same stock")
    ph = pmcc_holdings(c2)
    ok(len(ph) == 1 and ph[0]["contracts"] == 0 and "225" in ph[0]["short_open"],
       "a PMCC with its short open is listed with nothing to sell")
    pchain = [{**base, "strike": 222.90, "mid": 4.0, "delta": 0.30, "dte": 33, "expiration": "2026-12-04"},
              {**base, "strike": 222.91, "mid": 3.9, "delta": 0.29, "dte": 33, "expiration": "2026-12-04"},
              {**base, "strike": 225.0, "mid": 3.4, "delta": 0.27, "dte": 33, "expiration": "2026-12-04"},
              {**base, "strike": 230.0, "mid": 2.5, "delta": 0.22, "dte": 33, "expiration": "2026-12-04"},
              {**base, "strike": 215.0, "mid": 6.0, "delta": 0.34, "dte": 33, "expiration": "2026-12-04"}]
    fetch2 = lambda t: (pchain, 204.64, None, None)  # noqa: E731
    r = build(c2, T2, tickers=["NVDA"], fetch=fetch2)
    ok("already open" in r["rejects"].get("NVDA (PMCC)", ""), "...and a run says so: %s" % r["rejects"])
    c2.execute("UPDATE legs SET status='CLOSED', close_price=NULL WHERE id='NS'")
    c2.execute("DELETE FROM legs WHERE id='NS'")
    h = pmcc_holdings(c2)[0]
    ok(h["contracts"] == 1 and abs(h["floor_strike"] - 222.90) < 1e-9, "no short: floor strike = 160 + 62.90")
    r = build(c2, T2, tickers=["NVDA"], fetch=fetch2)
    pc = [c for c in r["candidates"] if c["kind"] == "pmcc"]
    ok([c["strike"] for c in pc] == [222.91, 225.0, 230.0],
       "PMCC choices: 222.90 (not above the floor) and 215 (0.34 delta) left off: %s" % [c["strike"] for c in pc])
    ok(abs(pc[0]["ann_return"] - 3.9 / 62.90 * 365 / 33) < 1e-12, "the PMCC's yearly return is on the long's cost")
    ok(abs(pc[1]["if_called"] - ((225 - 222.90) * 100 + 3.4 * 100)) < 1e-6,
       "PMCC if called = (strike - floor strike) x 100 + premium")
    ok(all("INSIDE THE TRADE" in c["earnings"] for c in pc), "earnings 11-17 inside a 12-04 short: offered and flagged")
    ok(pc[0]["position_id"] == "N" and pc[0]["shares"] is None, "PMCC rows carry the position, no shares")
    c2.execute("INSERT INTO legs VALUES ('NS0','N','CALL','SHORT',225,'2026-10-30',1,100,3.40,1.70,NULL,'CLOSED',NULL)")
    h = pmcc_holdings(c2)[0]
    ok(abs(h["floor_strike"] - 221.20) < 1e-9 and abs(h["rent_banked"] - 170) < 1e-9,
       "a closed short's net rent ($1.70) lowers the floor strike to 221.20")
    pchain[0]["strike"], pchain[1]["strike"] = 221.20, 221.21
    r = build(c2, T2, tickers=["NVDA"], fetch=fetch2)
    ok([c["strike"] for c in r["candidates"] if c["kind"] == "pmcc"][0] == 221.21,
       "after rent: 221.20 left off, 221.21 offered")
    pchain[:] = [x for x in pchain if x["strike"] <= 221.20 or x["delta"] > 0.30]
    r = build(c2, T2, tickers=["NVDA"], fetch=fetch2)
    ok(r["rejects"].get("NVDA (PMCC)") == "no call passes: strike not above the width-test floor; delta outside 0.20-0.30",
       "a PMCC with no call says why: %s" % r["rejects"].get("NVDA (PMCC)"))
    # shares and a PMCC on one ticker record side by side; read back
    c2.execute("INSERT INTO watchlist VALUES ('NVDA', '2027-02-25')")
    pchain[:] = [{**base, "strike": 230.0, "mid": 2.5, "delta": 0.22, "dte": 33, "expiration": "2026-12-04"}]
    c2.execute("UPDATE watchlist SET next_earnings='2027-02-25'")
    r = build(c2, T2, tickers=["NVDA"], fetch=fetch2)
    ok(sorted(c["kind"] for c in r["candidates"]) == ["pmcc", "shares"], "shares and a PMCC on NVDA both get a call")
    record(c2, r)
    back = latest(c2, 0)
    ok([(c["kind"], c["choice"]) for c in back["candidates"]] == [("shares", 1), ("pmcc", 1)]
       and back["candidates"][1]["floor_strike"] == r["candidates"][1]["floor_strike"],
       "both recorded under one run and read back, shares first")
    # an old (calls-v1) table: read as is, then rebuilt with its rows kept
    c3 = sqlite3.connect(":memory:")
    c3.row_factory = sqlite3.Row
    c3.executescript(RUNS_SQL + """CREATE TABLE call_candidates (run_id TEXT NOT NULL, ticker TEXT NOT NULL,
        choice INTEGER NOT NULL, hypothetical INTEGER NOT NULL DEFAULT 0, shares INTEGER, contracts INTEGER,
        cost_basis REAL, effective_basis REAL, expiry TEXT NOT NULL, dte INTEGER, strike REAL NOT NULL,
        spot REAL, bid REAL, ask REAL, mid REAL, spread_pct REAL, delta REAL, gamma REAL, theta REAL,
        vega REAL, iv REAL, ann_return REAL, if_called REAL, earnings TEXT, exdiv TEXT, rest_price REAL,
        PRIMARY KEY (run_id, ticker, choice));
      INSERT INTO call_runs VALUES ('r1','2026-10-28 13:00:00','calls-v1',1,1,0,'{}',NULL);
      INSERT INTO call_candidates (run_id, ticker, choice, expiry, strike, mid) VALUES ('r1','JPM',1,'2026-11-27',350,4.3);""")
    v1 = latest(c3, 0)
    ok(v1 and v1["candidates"][0]["kind"] == "shares" and v1["candidates"][0]["strike"] == 350,
       "a calls-v1 table reads back without being changed")
    ensure_tables(c3)
    cols = [x[1] for x in c3.execute("PRAGMA table_info(call_candidates)")]
    ok("kind" in cols and c3.execute("SELECT kind, strike FROM call_candidates").fetchall()[0][:] == ("shares", 350.0)
       and not c3.execute("SELECT 1 FROM sqlite_master WHERE name='call_candidates_v1'").fetchone(),
       "a calls-v1 table is rebuilt with kind, its row kept as shares")
    ensure_tables(c3)
    ok(c3.execute("SELECT COUNT(*) FROM call_candidates").fetchone()[0] == 1, "a second ensure changes nothing")
    print("\n%d failed" % len(fails) if fails else "\nall passed")
    return not fails


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
