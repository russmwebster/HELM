"""helm/calltool.py -- the covered-call tool (s138, 2026-10-28): the settled
covered-call rules, run on demand (`helm calls`), recorded.

The rules (rulebook v0.2; Russ 2026-10-24, 10-28):
  * on shares HELM holds (stock_positions), 100 at a time, not already covered by
    an open covered call. `--if-assigned TICKER` treats an open put as assigned
    (shares at the strike, effective basis after its premium) -- labelled, so the
    calls can be seen before the assignment decision.
  * strike >= the EFFECTIVE BASIS (strike paid less put premium, per share).
  * 0.20-0.35 delta, 30-45 DTE, spread < 10% of mid (Russ 10-28: as for puts),
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

HELM informs, never acts. Writes only call_runs / call_candidates.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta

from helm import puttool as P

RULE_VERSION = "calls-v1 (rulebook v0.2; 0.20-0.35, strike >= effective basis; earnings fail closed)"

DELTA_MIN, DELTA_MAX = 0.20, 0.35
DTE_MIN, DTE_MAX = P.DTE_MIN, P.DTE_MAX
SPREAD_MAX_PCT = P.SPREAD_MAX_PCT
NEXT_REPORT_EARLIEST = 70        # days after the last report before the next can fall
CHOICES = 3
PROFIT_TAKE = 0.50

TABLES_SQL = """
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
CREATE TABLE IF NOT EXISTS call_candidates (
    run_id          TEXT NOT NULL REFERENCES call_runs(id),
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
    PRIMARY KEY (run_id, ticker, choice)
);
"""


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


def contract_reason(c, eff_basis, latest_expiry):
    d, dte, sp = c.get("delta"), c.get("dte"), c.get("spread_pct")
    if c.get("source") not in ("ibkr", "ibkr-live"):
        return "no live IBKR quote"
    if not c.get("bid") or not c.get("ask") or c["bid"] <= 0 or c["ask"] <= 0:
        return "no two-sided market"
    if d is None or not (DELTA_MIN - 1e-9 <= abs(d) <= DELTA_MAX + 1e-9):
        return "delta outside 0.20-0.35"
    if dte is None or not (DTE_MIN <= dte <= DTE_MAX):
        return "outside 30-45 days"
    if c["strike"] < eff_basis - 1e-9:
        return "strike below the effective basis"
    if latest_expiry is None or P._d(c["expiration"]) > latest_expiry:
        return "earnings inside the trade"
    if sp is None or sp >= SPREAD_MAX_PCT:
        return "spread 10% of mid or wider"
    return None


def ann_return(c, spot):
    return c["mid"] / spot * 365.0 / c["dte"]


def choose(contracts, eff_basis, latest_expiry, spot, n=CHOICES):
    """(top n passing contracts, why_none). Ranked by annualized premium on the
    shares' value; ties to the nearer expiry, then the HIGHER strike (keeps the shares)."""
    ok = [c for c in contracts if contract_reason(c, eff_basis, latest_expiry) is None]
    if ok:
        ok.sort(key=lambda c: (-ann_return(c, spot), c["dte"], -c["strike"]))
        return ok[:n], None
    if not contracts:
        return [], "IBKR returned no two-sided call quotes near 0.20-0.35 delta"
    order = ("strike below the effective basis", "earnings inside the trade", "delta outside 0.20-0.35",
             "spread 10% of mid or wider", "outside 30-45 days", "no two-sided market", "no live IBKR quote")
    why = {id(c): contract_reason(c, eff_basis, latest_expiry) for c in contracts}
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
        covered = conn.execute(
            "SELECT COALESCE(SUM(l.contracts * COALESCE(l.multiplier,100)),0) FROM legs l JOIN positions p "
            "ON p.id=l.position_id WHERE p.book='REAL' AND p.status='OPEN' AND p.ticker=? AND l.status='OPEN' "
            "AND l.direction='SHORT' AND l.option_type='CALL'", (r["ticker"],)).fetchone()[0]
        free = int(r["shares"] or 0) - int(covered or 0)
        eff = r["effective_basis"] if r["effective_basis"] is not None else r["cost_basis"]
        out.append({"ticker": r["ticker"], "shares": int(r["shares"] or 0), "free_shares": free,
                    "contracts": max(free, 0) // 100, "cost_basis": r["cost_basis"],
                    "effective_basis": eff, "hypothetical": 0,
                    "basis_note": None if r["effective_basis"] is not None else "no effective basis stored -- cost used"})
    return out


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
    rows = oc.fetch_chain_from_ibkr(ticker, "CALL", exps_in, spot, atr, 0.15, 0.40,
                                    (DELTA_MIN, DELTA_MAX), spread_threshold=P.NET_SPREAD)
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
    else:
        hold = holdings(conn, account_id)
        if tickers:
            want = {t.upper() for t in tickers}
            hold = [h for h in hold if h["ticker"] in want]
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
                "basis_note": h.get("basis_note"),
            })
    return {"id": "calls_" + uuid.uuid4().hex[:12],
            "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "rule_version": RULE_VERSION, "market_open": P._market_open(),
            "n_holdings": len(hold), "hypothetical": 1 if assigned else 0,
            "rejects": rejects, "candidates": cands}


CAND_COLS = ("ticker", "choice", "hypothetical", "shares", "contracts", "cost_basis", "effective_basis",
             "expiry", "dte", "strike", "spot", "bid", "ask", "mid", "spread_pct", "delta", "gamma", "theta",
             "vega", "iv", "ann_return", "if_called", "earnings", "exdiv", "rest_price")


def ensure_tables(conn):
    conn.executescript(TABLES_SQL)


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
    run["candidates"] = [dict(zip(CAND_COLS, tuple(x))) for x in conn.execute(
        "SELECT %s FROM call_candidates WHERE run_id=? ORDER BY ticker, choice" % ", ".join(CAND_COLS),
        (run["id"],))]
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
    print("\n%d failed" % len(fails) if fails else "\nall passed")
    return not fails


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
