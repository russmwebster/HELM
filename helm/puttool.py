"""helm/puttool.py -- the put tool (s138, 2026-10-28): the settled cash-secured-put
rules, run on demand (`helm puts`), ranked, recorded.

The rules (rulebook v0.2; Russ 2026-10-24, 10-27, 10-28):
  * universe -- the name's latest ownership grade is A, B or C, or it is an approved
    fund (grade FUND). Ungraded, D, F and N are not offered.
  * IV rank >= 50, from today's (or the latest, at most IVR_STALE_DAYS old) iv_history.
  * earnings -- no entry within 10 days of a report; a report later in the trade is
    allowed and shown. FAILS CLOSED (Russ, 2026-10-28): a stock with no upcoming
    date is offered only when it reported within the last LAST_REPORT_OK_DAYS days
    (so it cannot report again inside 10); otherwise it is left off with the reason.
    Funds have no earnings.
  * contract -- 0.25-0.30 delta (0.30 included), 30-45 DTE, bid-ask spread < 10% of
    mid, a live IBKR two-sided quote. The contract per name is the one with the
    highest annualized return on collateral (mid / strike x 365 / DTE).
  * the list -- one contract per name, ranked by that return, top 5 shown. Every
    name's best contract is recorded, so the record is wider than the list.
  * sizing -- helm.sleeve.size: the room left under the sleeve, the 20% name and the
    30% sector limits. 0 = "would be declined", with the limit named. Shown, never
    acted on: Russ places every trade.

HELM informs, never acts. This module reads the database and IBKR, and writes only
its own two tables (put_runs, put_candidates). Nothing else reads them with SELECT *.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta

RULE_VERSION = "puts-v1 (rulebook v0.2; band 0.25-0.30 incl.; earnings fail closed)"

PASSING = ("A", "B", "C", "FUND")
IVR_MIN = 50.0
IVR_STALE_DAYS = 3
DELTA_MIN, DELTA_MAX = 0.25, 0.30
DTE_MIN, DTE_MAX = 30, 45
SPREAD_MAX_PCT = 10.0            # strictly below; 10.0% is refused, as everywhere else
EARN_VETO_DAYS = 10
LAST_REPORT_OK_DAYS = 60         # a quarterly reporter that printed <= 60 days ago
QUARTER_DAYS = 91                # is not due again inside 10 (next ~ last + 91)
TOP_N = 5
PROFIT_TAKE = 0.50

TABLES_SQL = """
CREATE TABLE IF NOT EXISTS put_runs (
    id            TEXT PRIMARY KEY,
    run_at        TEXT NOT NULL,              -- local time
    rule_version  TEXT NOT NULL,
    ivr_date      TEXT,                       -- the iv_history date the run read
    market_open   INTEGER,                    -- 0 = frozen (last) quotes
    n_universe    INTEGER,                    -- graded A-C or FUND
    n_checked     INTEGER,                    -- passed grade, IV rank and earnings
    n_found       INTEGER,                    -- had a contract inside every rule
    sleeve_free   REAL,
    rejects_json  TEXT,                       -- {ticker: reason} for every name left off
    note          TEXT
);
CREATE TABLE IF NOT EXISTS put_candidates (
    run_id        TEXT NOT NULL REFERENCES put_runs(id),
    ticker        TEXT NOT NULL,
    rank          INTEGER NOT NULL,           -- 1 = best; the list shows the top 5
    grade         TEXT,
    sector        TEXT,
    expiry        TEXT NOT NULL,
    dte           INTEGER,
    strike        REAL NOT NULL,
    spot          REAL,
    bid REAL, ask REAL, mid REAL,
    spread_pct    REAL,
    delta REAL, gamma REAL, theta REAL, vega REAL,
    iv            REAL,
    iv_rank       REAL,
    ann_return    REAL,                       -- fraction per year on collateral
    collateral    REAL,                       -- strike x 100, one contract
    earnings      TEXT,                       -- plain-words earnings line
    rest_price    REAL,                       -- the 50% buy-to-close price
    size_n        INTEGER,                    -- contracts the limits allow (0 = declined)
    size_binding  TEXT,
    size_note     TEXT,
    PRIMARY KEY (run_id, ticker)
);
"""


# ---------------------------------------------------------------- pure rules
def _d(x):
    return date.fromisoformat(str(x)[:10]) if x else None


def earnings_check(is_fund, next_date, last_report, today):
    """(ok, line, upcoming_date_or_None). Pure. Fails closed for stocks."""
    if is_fund:
        return True, "fund -- no earnings", None
    nd, lr = _d(next_date), _d(last_report)
    if nd is not None and nd >= today:
        days = (nd - today).days
        if days <= EARN_VETO_DAYS:
            return False, "earnings %s, in %d day%s (no entry within %d)" % (
                nd, days, "" if days == 1 else "s", EARN_VETO_DAYS), nd
        return True, "earnings %s (%d days)" % (nd, days), nd
    if lr is not None and lr <= today and (today - lr).days <= LAST_REPORT_OK_DAYS:
        return True, "last reported %s; next not known (about %s)" % (
            lr, lr + timedelta(days=QUARTER_DAYS)), None
    if lr is not None:
        return False, "no reliable earnings date (last known %s, %d days ago)" % (lr, (today - lr).days), None
    return False, "no reliable earnings date", None


def contract_reason(c):
    """None if the contract meets every contract rule, else the first one it breaks."""
    d, dte, sp = c.get("delta"), c.get("dte"), c.get("spread_pct")
    if c.get("source") not in ("ibkr", "ibkr-live"):
        return "no live IBKR quote"
    if not c.get("bid") or not c.get("ask") or c["bid"] <= 0 or c["ask"] <= 0:
        return "no two-sided market"
    if d is None or not (DELTA_MIN - 1e-9 <= abs(d) <= DELTA_MAX + 1e-9):
        return "delta outside 0.25-0.30"
    if dte is None or not (DTE_MIN <= dte <= DTE_MAX):
        return "outside 30-45 days"
    if sp is None or sp >= SPREAD_MAX_PCT:
        return "spread 10% of mid or wider"
    return None


def ann_return(c):
    return c["mid"] / c["strike"] * 365.0 / c["dte"]


def best_contract(contracts):
    """(best, why_none). The passing contract with the highest annualized return;
    ties go to the nearer expiry, then the lower strike (less collateral).
    `contracts` may be a wider net than the rules (s138: 0.20-0.35 delta, any
    spread), so the reason for an empty result is the true one."""
    ok = [c for c in contracts if contract_reason(c) is None]
    if ok:
        ok.sort(key=lambda c: (-ann_return(c), c["dte"], c["strike"]))
        return ok[0], None
    if not contracts:
        return None, "IBKR returned no two-sided put quotes near 0.25-0.30 delta"
    live = [c for c in contracts if contract_reason(c) not in ("no live IBKR quote", "no two-sided market")]
    if not live:
        return None, "no contract passes: " + "; ".join(sorted({contract_reason(c) for c in contracts}))
    inband = [c for c in live if contract_reason(c) not in ("delta outside 0.25-0.30", "outside 30-45 days")]
    if not inband:
        ds = sorted(abs(c["delta"]) for c in live if c.get("delta") is not None)
        lo = max((d for d in ds if d < DELTA_MIN), default=None)
        hi = min((d for d in ds if d > DELTA_MAX), default=None)
        near = " / ".join("%.2f" % d for d in (lo, hi) if d is not None)
        return None, "no put in the 0.25-0.30 delta band" + (" (nearest %s)" % near if near else "")
    narrow = min(c["spread_pct"] for c in inband if c.get("spread_pct") is not None)
    return None, "spread 10%% of mid or wider (narrowest in the band %.0f%%)" % narrow


def target_expiries(expirations, today):
    """[(expiry, dte)] inside 30-45 days, and a reason when there are none."""
    out = []
    for e in expirations or ():
        d = (_d(e) - today).days
        if DTE_MIN <= d <= DTE_MAX:
            out.append((str(e)[:10], d))
    if out:
        return out, None
    near = sorted(((_d(e) - today).days, str(e)[:10]) for e in (expirations or ()) if (_d(e) - today).days > 0)
    before = [x for x in near if x[0] < DTE_MIN][-1:]
    after = [x for x in near if x[0] > DTE_MAX][:1]
    nb = ", ".join("%s is %d days" % (e[5:], d) for d, e in before + after)
    return [], "no expiry between 30 and 45 days" + (" (%s)" % nb if nb else "")


def rank(bests):
    """One per name, highest annualized return first."""
    return sorted(bests, key=lambda b: (-b["ann_return"], b["ticker"]))


def earnings_in_trade(upcoming, expiry):
    e = _d(expiry)
    return bool(upcoming and e and upcoming <= e)


# ---------------------------------------------------------------- database
def ensure_tables(conn):
    conn.executescript(TABLES_SQL)


def _latest_ivr_date(conn):
    r = conn.execute("SELECT MAX(date) FROM iv_history").fetchone()
    return r[0] if r else None


def _last_report(conn, ticker, today):
    """Latest known report date on or before today, from both stores."""
    cands = []
    r = conn.execute("SELECT next_earnings FROM watchlist WHERE ticker=?", (ticker,)).fetchone()
    if r and r[0] and _d(r[0]) and _d(r[0]) <= today:
        cands.append(_d(r[0]))
    r = conn.execute("SELECT MAX(earn_date) FROM earnings_history WHERE ticker=? AND earn_date<=?",
                     (ticker, today.isoformat())).fetchone()
    if r and r[0]:
        cands.append(_d(r[0]))
    return max(cands) if cands else None


def _next_report(conn, ticker, today):
    cands = []
    r = conn.execute("SELECT next_earnings FROM watchlist WHERE ticker=?", (ticker,)).fetchone()
    if r and r[0] and _d(r[0]) and _d(r[0]) >= today:
        cands.append(_d(r[0]))
    r = conn.execute("SELECT MIN(earn_date) FROM earnings_history WHERE ticker=? AND earn_date>=?",
                     (ticker, today.isoformat())).fetchone()
    if r and r[0]:
        cands.append(_d(r[0]))
    return min(cands) if cands else None      # the nearer of two disagreeing dates


def screen(conn, today, tickers=None):
    """(checked, rejects, ivr_date, n_universe). No network.
    checked: [{ticker, grade, iv_rank, earn_line, upcoming}]."""
    from helm import advice as A
    ivr_date = _latest_ivr_date(conn)
    stale = (ivr_date is None) or ((today - _d(ivr_date)).days > IVR_STALE_DAYS)
    names = [r[0] for r in conn.execute(
        "SELECT ticker FROM watchlist WHERE COALESCE(active,0)=1 ORDER BY ticker")]
    funds = {r[0] for r in conn.execute("SELECT ticker FROM approved_funds")}
    names = sorted(set(names) | funds)
    if tickers:
        want = {t.upper() for t in tickers}
        names = [t for t in names if t in want] + sorted(want - set(names))
    checked, rejects, n_universe = [], {}, 0
    for t in names:
        g = A._grade(conn, t)
        if g not in PASSING:
            rejects[t] = "grade %s -- not offered" % (g or "none")
            continue
        n_universe += 1
        r = conn.execute("SELECT iv_rank FROM iv_history WHERE ticker=? AND date=?",
                         (t, ivr_date)).fetchone() if ivr_date else None
        ivr = r[0] if r else None
        if stale:
            rejects[t] = "IV rank is stale (latest %s)" % ivr_date
            continue
        if ivr is None:
            rejects[t] = "no IV rank on %s" % ivr_date
            continue
        if ivr < IVR_MIN:
            rejects[t] = "IV rank %.0f, below 50" % ivr
            continue
        ok, line, upcoming = earnings_check(g == "FUND", _next_report(conn, t, today),
                                            _last_report(conn, t, today), today)
        if not ok:
            rejects[t] = line
            continue
        checked.append({"ticker": t, "grade": g, "iv_rank": ivr, "earn_line": line, "upcoming": upcoming})
    return checked, rejects, ivr_date, n_universe


NET_DELTA = (0.20, 0.35)          # the net cast before the rules, so reasons are true
NET_SPREAD = 10.0                 # fetch_chain_from_ibkr's threshold x100 = 1000%: keep every spread


def fetch_contracts(ticker, today=None):
    """Live puts straight from IBKR -- no Yahoo quotes (s138). Yahoo gives only
    the spot, the 14-day range and the listed expiries. (contracts, spot, err)."""
    from helm.cli import open_cmd as oc
    today = today or date.today()
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        spot = float(tk.fast_info.last_price)
        exps = tuple(tk.options)
    except Exception as e:
        return [], None, "price or expiries not read: %s" % str(e)[:80]
    exps_in, why = target_expiries(exps, today)
    if not exps_in:
        return [], spot, why
    atr = spot * 0.05
    try:
        h = tk.history(period="30d", interval="1d")
        if not h.empty and len(h) >= 14:
            a = float((h["High"] - h["Low"]).rolling(14).mean().iloc[-1])
            if a == a and a > 0:
                atr = a
    except Exception:
        pass
    rows = oc.fetch_chain_from_ibkr(ticker, "PUT", exps_in, spot, atr, NET_DELTA[0], NET_DELTA[1],
                                    (DELTA_MIN, DELTA_MAX), spread_threshold=NET_SPREAD)
    return rows, spot, None


def build(conn, today, tickers=None, fetch=fetch_contracts, book=None, db=None):
    """Run the rules. Returns the run dict (not yet recorded)."""
    from helm import sleeve as S
    from helm.bookview import _half_up
    checked, rejects, ivr_date, n_universe = screen(conn, today, tickers)
    b = book if book is not None else S.book()
    bests = []
    for ch in checked:
        t = ch["ticker"]
        rows, spot, err = fetch(t)
        if err:
            rejects[t] = err if err.startswith("no expiry") else "chain not read: %s" % err
            continue
        best, why = best_contract(rows)
        if best is None:
            rejects[t] = why
            continue
        sec, _ = S.sector_of(conn, t)
        n, binding, note = S.size("CSP", t, best["strike"] * 100.0, db=db, b=b)
        earn = ch["earn_line"]
        if earnings_in_trade(ch["upcoming"], best["expiration"]):
            earn += " -- inside the trade"
        bests.append({
            "ticker": t, "grade": ch["grade"], "sector": sec,
            "expiry": best["expiration"], "dte": best["dte"], "strike": best["strike"],
            "spot": spot, "bid": best["bid"], "ask": best["ask"], "mid": best["mid"],
            "spread_pct": best["spread_pct"], "delta": best.get("delta"),
            "gamma": best.get("gamma"), "theta": best.get("theta"), "vega": best.get("vega"),
            "iv": best.get("iv"), "iv_rank": ch["iv_rank"], "ann_return": ann_return(best),
            "collateral": best["strike"] * 100.0, "earnings": earn,
            "rest_price": _half_up(best["mid"] * (1 - PROFIT_TAKE)),
            "size_n": n, "size_binding": binding, "size_note": note,
        })
    ranked = rank(bests)
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    return {"id": "puts_" + uuid.uuid4().hex[:12],
            "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "rule_version": RULE_VERSION, "ivr_date": ivr_date,
            "market_open": _market_open(), "n_universe": n_universe,
            "n_checked": len(checked), "n_found": len(ranked),
            "sleeve_free": b["share"] - b["used"], "rejects": rejects,
            "candidates": ranked}


def _market_open():
    try:
        from helm.cli.check_cmd import is_market_open
        return 1 if is_market_open() else 0
    except Exception:
        return None


CAND_COLS = ("ticker", "rank", "grade", "sector", "expiry", "dte", "strike", "spot", "bid", "ask",
             "mid", "spread_pct", "delta", "gamma", "theta", "vega", "iv", "iv_rank", "ann_return",
             "collateral", "earnings", "rest_price", "size_n", "size_binding", "size_note")


def record(conn, run, note=None):
    ensure_tables(conn)
    conn.execute("INSERT INTO put_runs (id, run_at, rule_version, ivr_date, market_open, n_universe, "
                 "n_checked, n_found, sleeve_free, rejects_json, note) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (run["id"], run["run_at"], run["rule_version"], run["ivr_date"], run["market_open"],
                  run["n_universe"], run["n_checked"], run["n_found"], run["sleeve_free"],
                  json.dumps(run["rejects"], sort_keys=True), note))
    for c in run["candidates"]:
        conn.execute("INSERT INTO put_candidates (run_id, %s) VALUES (?, %s)" % (
            ", ".join(CAND_COLS), ", ".join("?" * len(CAND_COLS))),
            (run["id"],) + tuple(c.get(k) for k in CAND_COLS))
    conn.commit()


def latest(conn):
    """The newest recorded run with its candidates, or None. Read-only (the board's reader)."""
    has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='put_runs'").fetchone()
    if not has:
        return None
    r = conn.execute("SELECT id, run_at, rule_version, ivr_date, market_open, n_universe, n_checked, "
                     "n_found, sleeve_free, rejects_json, note FROM put_runs "
                     "ORDER BY run_at DESC, rowid DESC LIMIT 1").fetchone()
    if not r:
        return None
    keys = ("id", "run_at", "rule_version", "ivr_date", "market_open", "n_universe", "n_checked",
            "n_found", "sleeve_free", "rejects_json", "note")
    run = dict(zip(keys, tuple(r)))
    run["rejects"] = json.loads(run.pop("rejects_json") or "{}")
    run["candidates"] = [dict(zip(CAND_COLS, tuple(x))) for x in conn.execute(
        "SELECT %s FROM put_candidates WHERE run_id=? ORDER BY rank" % ", ".join(CAND_COLS), (run["id"],))]
    return run


def earnings_short(line):
    """The table's compact form of the stored earnings line."""
    import re
    if not line:
        return ""
    if line.startswith("fund"):
        return "fund"
    inside = line.endswith("inside the trade")
    m = re.match(r"earnings (\d{4}-(\d\d-\d\d)) \((\d+) days\)", line)
    if m:
        return "%s (%sd)%s" % (m.group(2), m.group(3), " in trade" if inside else "")
    m = re.match(r"last reported \d{4}-(\d\d-\d\d)", line)
    if m:
        return "reported %s" % m.group(1)
    return line


def decision_text(c):
    if c.get("size_n"):
        return "%d contract%s fit the limits" % (c["size_n"], "" if c["size_n"] == 1 else "s")
    lab = {"sleeve_total": "sleeve full", "sleeve_name": "name over 20%",
           "sleeve_sector": "sector over 30%"}.get(c.get("size_binding"), "no room")
    return "would be declined: " + lab


# ---------------------------------------------------------------- self-test
def _selftest():
    import sqlite3, tempfile, os
    fails = []

    def ok(cond, msg):
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)

    T = date(2026, 10, 28)
    # earnings
    ok(earnings_check(True, None, None, T)[0], "a fund passes with no earnings date")
    ok(not earnings_check(False, "2026-11-07", None, T)[0], "earnings in 10 days vetoes (10 counts)")
    ok(earnings_check(False, "2026-11-08", None, T)[0], "earnings in 11 days passes")
    ok(not earnings_check(False, None, None, T)[0], "no date at all fails closed")
    ok(earnings_check(False, None, "2026-08-29", T)[0], "reported 60 days ago passes")
    ok(not earnings_check(False, None, "2026-08-28", T)[0], "reported 61 days ago fails closed")
    ok(earnings_check(False, "2026-10-21", "2026-10-21", T)[0] is True, "a passed 'next' date reads as last reported")
    ok("reliable" in earnings_check(False, None, "2026-07-01", T)[1], "the fail-closed reason names the problem")
    # contract rules
    base = {"source": "ibkr", "bid": 1.0, "ask": 1.05, "delta": 0.27, "dte": 35, "spread_pct": 4.9,
            "mid": 1.03, "strike": 100.0, "expiration": "2026-12-04"}
    ok(contract_reason(base) is None, "a contract inside every rule passes")
    ok(contract_reason({**base, "delta": 0.30}) is None, "0.30 delta is included")
    ok(contract_reason({**base, "delta": 0.25}) is None, "0.25 delta is included")
    ok(contract_reason({**base, "delta": 0.301}) is not None, "0.301 delta fails")
    ok(contract_reason({**base, "delta": 0.249}) is not None, "0.249 delta fails")
    ok(contract_reason({**base, "spread_pct": 10.0}) is not None, "a 10.0% spread fails")
    ok(contract_reason({**base, "spread_pct": 9.9}) is None, "a 9.9% spread passes")
    ok(contract_reason({**base, "dte": 29}) is not None and contract_reason({**base, "dte": 46}) is not None,
       "29 and 46 days fail")
    ok(contract_reason({**base, "source": "yfinance"}) is not None, "a Yahoo-only quote fails")
    ok(contract_reason({**base, "delta": None}) is not None, "no delta fails closed")
    # choice and ranking
    a = {**base, "mid": 1.00, "strike": 100.0, "dte": 35}
    b2 = {**base, "mid": 1.30, "strike": 100.0, "dte": 42}
    bad = {**base, "mid": 5.0, "spread_pct": 15.0}
    best, _ = best_contract([a, b2, bad])
    ok(best is b2, "the highest annualized return wins; a wider-spread richer one is skipped")
    ok(abs(ann_return(b2) - 1.30 / 100 * 365 / 42) < 1e-12, "annualized return = mid/strike x 365/DTE")
    ok(best_contract([bad])[0] is None and "spread" in best_contract([bad])[1], "no passing contract says why")
    ok("narrowest in the band 15%" in best_contract([bad, {**bad, "spread_pct": 22.0}])[1],
       "too-wide names give the narrowest spread in the band")
    why = best_contract([{**base, "delta": 0.20}, {**base, "delta": 0.22}, {**base, "delta": 0.33}, {**base, "delta": 0.35}])[1]
    ok(why == "no put in the 0.25-0.30 delta band (nearest 0.22 / 0.33)", "an empty band names the nearest deltas: %s" % why)
    ok(best_contract([])[1].startswith("IBKR returned no"), "an empty chain says IBKR returned nothing")
    ok(best_contract([{**base, "source": "yfinance"}])[1] == "no contract passes: no live IBKR quote",
       "a Yahoo-only chain still fails closed")
    ex, why = target_expiries(["2026-11-20", "2026-12-18"], T)
    ok(ex == [] and why == "no expiry between 30 and 45 days (11-20 is 23 days, 12-18 is 51 days)",
       "monthly-only names say which expiries miss: %s" % why)
    ex, _ = target_expiries(["2026-11-20", "2026-11-27", "2026-12-04", "2026-12-12", "2026-12-18"], T)
    ok([d for _, d in ex] == [30, 37, 45], "30 and 45 days are both inside the window")
    r = rank([{"ticker": "X", "ann_return": .1}, {"ticker": "Y", "ann_return": .3}, {"ticker": "Z", "ann_return": .2}])
    ok([x["ticker"] for x in r] == ["Y", "Z", "X"], "ranked by annualized return")
    ok(earnings_in_trade(date(2026, 12, 1), "2026-12-04") and not earnings_in_trade(date(2026, 12, 5), "2026-12-04"),
       "earnings inside the trade is spotted")

    # end to end on a tiny database, no network
    tmp = os.path.join(tempfile.mkdtemp(), "t.db")
    conn = sqlite3.connect(tmp)
    conn.executescript("""
      CREATE TABLE watchlist (ticker TEXT, active INTEGER, next_earnings TEXT, sector TEXT, exposure_group TEXT);
      CREATE TABLE approved_funds (ticker TEXT, bucket TEXT);
      CREATE TABLE ownership_quality (ticker TEXT, date TEXT, grade TEXT, updated_at TEXT);
      CREATE TABLE iv_history (ticker TEXT, date TEXT, iv_rank REAL);
      CREATE TABLE earnings_history (ticker TEXT, earn_date TEXT);
    """)
    for t, ne, g, ivr in [("AAA", "2026-12-20", "B", 60), ("BBB", "2026-11-03", "A", 70),
                          ("CCC", None, "C", 80), ("DDD", "2026-12-20", "D", 90),
                          ("EEE", "2026-12-20", "B", 40), ("FFF", "2026-10-21", "B", 55),
                          ("GGG", "2026-12-20", "A", 65)]:
        conn.execute("INSERT INTO watchlist VALUES (?,1,?,'Tech',NULL)", (t, ne))
        conn.execute("INSERT INTO ownership_quality VALUES (?,'2026-10-24',?,'x')", (t, g))
        conn.execute("INSERT INTO iv_history VALUES (?,'2026-10-28',?)", (t, ivr))
    conn.execute("INSERT INTO approved_funds VALUES ('XLV','Healthcare')")
    conn.execute("INSERT INTO iv_history VALUES ('XLV','2026-10-28',63)")
    checked, rej, ivd, nu = screen(conn, T)
    names = sorted(c["ticker"] for c in checked)
    ok(names == ["AAA", "FFF", "GGG", "XLV"], "screen keeps A-C/FUND, IVR>=50, earnings ok: %s" % names)
    ok("in 6 days" in rej["BBB"] and "reliable" in rej["CCC"] and "grade D" in rej["DDD"] and "below 50" in rej["EEE"],
       "each left-off name carries its reason")
    ok(nu == 7, "universe counts graded A-C and funds (7)")

    chains = {"AAA": [{**base, "mid": 1.0}], "FFF": [{**base, "mid": 2.0}], "GGG": [{**base, "spread_pct": 12.0}],
              "XLV": [{**base, "mid": 1.5, "strike": 140.0}]}
    fake_book = {"share": 300000.0, "used": 298000.0, "name_cap": 60000.0, "sector_cap": 90000.0,
                 "by_name": {}, "by_sector": {}}
    import helm.sleeve as S
    _so = S.sector_of
    S.sector_of = lambda c, t: ("Tech", "test")
    try:
        run = build(conn, T, fetch=lambda t: (chains.get(t, []), 100.0, None), book=fake_book, db=tmp)
    finally:
        S.sector_of = _so
    tick = [c["ticker"] for c in run["candidates"]]
    ok(tick == ["FFF", "XLV", "AAA"], "end to end: ranked one per name: %s" % tick)
    ok("spread" in run["rejects"]["GGG"], "a name with no passing contract is left off with the reason")
    ok(all(c["size_n"] == 0 and decision_text(c) == "would be declined: sleeve full" for c in run["candidates"]),
       "a full sleeve marks every line 'would be declined: sleeve full'")
    ok(run["candidates"][0]["rest_price"] == 1.0, "the 50% resting price is half the mid")
    record(conn, run)
    back = latest(conn)
    ok(back["id"] == run["id"] and [c["ticker"] for c in back["candidates"]] == tick
       and back["rejects"] == run["rejects"], "recorded and read back unchanged")
    ok(earnings_short("earnings 2026-12-02 (35 days) -- inside the trade") == "12-02 (35d) in trade"
       and earnings_short("last reported 2026-10-27; next not known (about 2027-01-26)") == "reported 10-27"
       and earnings_short("fund -- no earnings") == "fund", "the compact earnings column")
    print("\n%d failed" % len(fails) if fails else "\nall passed")
    return not fails


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
