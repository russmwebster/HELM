"""helm/bookview.py -- the book on one screen (Russ, 2026-10-24; rulebook v0.2 section 7).

READ-ONLY. Built from the sleeve (helm/sleeve.py) and the readings the snapshot
already stored (leg_checks, then checks for a single-leg position with no leg
row). Nothing is fetched live, so every Greek is "as of the last snapshot".

  greeks    per position and summed, signed by position (a short put is long
            the stock): delta in shares and dollars, beta-weighted dollar delta
            ("moves like $X of the S&P 500"), theta in $/day, vega in $/vol point
  stress    approximate book P&L for a market move of -5% / -10% (each stock
            moves beta x the market; delta + gamma) and for implied vol +10 points
  expiries  what expires on each date and how much of the sleeve it frees
  orders    the 50% profit-take buy-to-close price for each open CSP short
            (covered-call and PMCC shorts too, when there are any); the run-off
            diagonals keep their own exit flags and are left out

A fund (or any name) with no beta on the watchlist is counted at beta 1.0 and
listed in `beta_assumed`.

    python3 -m helm.bookview --selftest     helm book
"""
import os
import sqlite3

from helm import sleeve as S
from helm import advice as A   # s136: the grade, read the one way advice reads it

STRESS_MARKET = (-0.05, -0.10)
STRESS_VOL_POINTS = 10.0
ORDER_STRATEGIES = ("CSP", "COVERED_CALL", "PMCC")
PROFIT_TAKE = 0.50


def _latest_leg_reading(conn, leg_id):
    r = conn.execute("SELECT checked_at, delta, gamma, theta, vega, current_price, data_quality "
                     "FROM leg_checks WHERE leg_id=? ORDER BY checked_at DESC LIMIT 1", (leg_id,)).fetchone()
    return dict(r) if r else None


def _latest_check(conn, position_id):
    r = conn.execute("SELECT checked_at, spot_price, delta, gamma, theta, vega, current_price, data_quality "
                     "FROM checks WHERE position_id=? ORDER BY checked_at DESC LIMIT 1", (position_id,)).fetchone()
    return dict(r) if r else None


def _beta(conn, ticker):
    r = conn.execute("SELECT beta FROM watchlist WHERE ticker=?", (ticker,)).fetchone()
    return (float(r["beta"]), False) if r and r["beta"] is not None else (1.0, True)


def position_greeks(conn, p):
    """Signed position Greeks from each open leg's latest reading."""
    legs = conn.execute("SELECT * FROM legs WHERE position_id=? AND status='OPEN'", (p["id"],)).fetchall()
    chk = _latest_check(conn, p["id"])
    out = {"delta_sh": 0.0, "gamma_sh": 0.0, "theta": 0.0, "vega": 0.0,
           "spot": chk["spot_price"] if chk else None, "as_of": None, "missing_legs": []}
    times = []
    for l in legs:
        if l["leg_role"] in ("LONG_STOCK", "SHORT_STOCK"):
            sign = 1.0 if l["direction"] == "LONG" else -1.0
            out["delta_sh"] += sign * (l["contracts"] or 0)      # stock legs: contracts = shares
            continue
        rd = _latest_leg_reading(conn, l["id"])
        if (rd is None or rd["delta"] is None) and len(legs) == 1 and chk and chk["delta"] is not None:
            rd = chk                                            # single-leg: the check is the leg
        if rd is None or rd["delta"] is None:
            out["missing_legs"].append(l["id"])
            continue
        sign = 1.0 if l["direction"] == "LONG" else -1.0
        m = sign * (l["multiplier"] or 100) * (l["contracts"] or 0)
        out["delta_sh"] += m * rd["delta"]
        out["gamma_sh"] += m * (rd["gamma"] or 0.0)
        out["theta"] += m * (rd["theta"] or 0.0)
        out["vega"] += m * (rd["vega"] or 0.0)
        times.append(rd["checked_at"])
    out["as_of"] = min(times) if times else None
    return out


def _half_up(x):
    """To the cent, halves rounded up (2.175 -> 2.18): plain round() on a float
    turns 2.175 into 2.17. Exchange tick sizes (0.05 above $3 on some names) are
    Fidelity's to enforce."""
    from decimal import Decimal, ROUND_HALF_UP
    return float(Decimal(repr(float(x))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def stress_pnl(delta_sh, gamma_sh, spot, beta, market_move):
    """Delta + gamma estimate of P&L if the market moves `market_move` (e.g. -0.05)."""
    if spot is None:
        return None
    ds = spot * market_move * beta
    return delta_sh * ds + 0.5 * gamma_sh * ds * ds


def view(account_id=None, db=None):
    b = S.book(account_id, db)
    conn = S._conn(db)
    try:
        rows, beta_assumed = [], []
        q = "SELECT * FROM positions WHERE book='REAL' AND status='OPEN'" + (" AND account_id=?" if account_id else "")
        for p in conn.execute(q, ((account_id,) if account_id else ())).fetchall():
            g = position_greeks(conn, p)
            beta, assumed = _beta(conn, p["ticker"])
            if assumed:
                beta_assumed.append(p["ticker"])
            spot = g["spot"]
            usd_delta = g["delta_sh"] * spot if spot is not None else None
            rows.append({
                "position_id": p["id"], "ticker": p["ticker"], "strategy": p["strategy"],
                "grade": A._grade(conn, p["ticker"]),   # s136: latest ownership grade, or FUND
                "spot": spot, "beta": beta, "beta_assumed": assumed,
                "delta_sh": g["delta_sh"], "usd_delta": usd_delta,
                "bw_delta": usd_delta * beta if usd_delta is not None else None,
                "theta": g["theta"], "vega": g["vega"], "gamma_sh": g["gamma_sh"],
                "stress": {str(m): stress_pnl(g["delta_sh"], g["gamma_sh"], spot, beta, m) for m in STRESS_MARKET},
                "vol_up": g["vega"] * STRESS_VOL_POINTS,
                "as_of": g["as_of"], "missing_legs": g["missing_legs"],
            })
        tot = lambda k: sum(r[k] for r in rows if r[k] is not None)
        totals = {k: tot(k) for k in ("delta_sh", "usd_delta", "bw_delta", "theta", "vega")}
        stress = {str(m): sum(r["stress"][str(m)] for r in rows if r["stress"][str(m)] is not None)
                  for m in STRESS_MARKET}
        stress["vol+%d" % STRESS_VOL_POINTS] = tot("vol_up")
        as_ofs = [r["as_of"] for r in rows if r["as_of"]]

        # expiries: what expires on each date, and the sleeve dollars it frees
        exp = {}
        for p in conn.execute(q, ((account_id,) if account_id else ())).fetchall():
            for l in conn.execute("SELECT * FROM legs WHERE position_id=? AND status='OPEN' AND expiration IS NOT NULL",
                                  (p["id"],)).fetchall():
                d = exp.setdefault(l["expiration"], {"date": l["expiration"], "legs": [], "frees": 0.0})
                frees = 0.0
                if p["strategy"] == "CSP" and l["leg_role"] == "SHORT_PUT":
                    frees = (l["strike"] or 0) * 100 * (l["contracts"] or 0)
                elif l["direction"] == "LONG" and p["strategy"] in S.DIAG_FAMILY:
                    frees = (l["open_price"] or 0) * (l["multiplier"] or 100) * (l["contracts"] or 0)
                d["frees"] += frees
                d["legs"].append({"ticker": p["ticker"], "strategy": p["strategy"], "role": l["leg_role"],
                                  "strike": l["strike"], "contracts": l["contracts"], "frees": frees})
        expiries = [exp[k] for k in sorted(exp)]

        # resting profit-take orders
        orders = []
        for p in conn.execute(q, ((account_id,) if account_id else ())).fetchall():
            if p["strategy"] not in ORDER_STRATEGIES:
                continue
            for l in conn.execute("SELECT * FROM legs WHERE position_id=? AND status='OPEN' AND direction='SHORT'",
                                  (p["id"],)).fetchall():
                orders.append({"ticker": p["ticker"], "strategy": p["strategy"], "role": l["leg_role"],
                               "strike": l["strike"], "expiration": l["expiration"], "contracts": l["contracts"],
                               "credit": l["open_price"],
                               "buy_to_close": _half_up((l["open_price"] or 0) * (1 - PROFIT_TAKE))})
        return {"sleeve": b, "positions": rows,
                "longs": [it for it in b["items"] if "long at cost" in it["what"]], "totals": totals, "stress": stress,
                "stress_note": "approximate: delta + gamma for market moves (each stock moves beta x the market); vega for vol",
                "as_of": (min(as_ofs), max(as_ofs)) if as_ofs else None,
                "beta_assumed": sorted(set(beta_assumed)), "expiries": expiries, "orders": orders}
    finally:
        conn.close()


def _money(x, sign=False):
    if x is None:
        return "—"
    s = format(int(round(abs(float(x)))), ",d")
    return ("-" if x < 0 else ("+" if sign else "")) + "$" + s


def _report(account_id=None, db=None):
    v = view(account_id, db)
    S._report(account_id, db)
    t = v["totals"]
    print("\nbook Greeks (as of %s)" % (" to ".join(x[:16] for x in v["as_of"]) if v["as_of"] else "no readings"))
    print("  delta %+.0f shares (%s); moves like %s of the S&P 500 (beta-weighted)" % (
        t["delta_sh"], _money(t["usd_delta"], True), _money(t["bw_delta"], True)))
    print("  theta %s a day   vega %s per vol point" % (_money(t["theta"], True), _money(t["vega"], True)))
    print("  if the market moves -5%%: %s   -10%%: %s   if vol +10 points: %s   (approximate)" % (
        _money(v["stress"]["-0.05"], True), _money(v["stress"]["-0.1"], True), _money(v["stress"]["vol+10"], True)))
    if v["beta_assumed"]:
        print("  beta assumed 1.0 (none on the watchlist):", ", ".join(v["beta_assumed"]))
    print("\n  %-6s %-9s %8s %10s %10s %8s %8s" % ("name", "strategy", "delta sh", "$ delta", "beta-wtd", "theta", "vega"))
    for r in sorted(v["positions"], key=lambda r: r["ticker"]):
        print("  %-6s %-9s %+8.0f %10s %10s %8s %8s%s" % (
            r["ticker"], r["strategy"][:9], r["delta_sh"], _money(r["usd_delta"], True), _money(r["bw_delta"], True),
            _money(r["theta"], True), _money(r["vega"], True), "  (legs without a reading: %d)" % len(r["missing_legs"]) if r["missing_legs"] else ""))
    print("\nexpiries")
    for e in v["expiries"]:
        print("  %s  frees %-9s %s" % (e["date"], _money(e["frees"]), ", ".join(
            "%s %s %g x%d" % (l["ticker"], l["role"].lower().replace("_", " "), l["strike"] or 0, l["contracts"] or 0) for l in e["legs"])))
    if v["orders"]:
        print("\nresting 50% profit-take orders (buy to close)")
        for o in v["orders"]:
            print("  %-6s %s $%g %s x%d   sold %.2f -> buy back at %.2f" % (
                o["ticker"], o["role"].lower().replace("_", " "), o["strike"], o["expiration"], o["contracts"], o["credit"], o["buy_to_close"]))
    return v


def run():
    import sys
    if "--selftest" in sys.argv:
        return _selftest()
    from helm.config import get_active_account
    _report(get_active_account())


def _selftest():
    import tempfile
    fails = []

    def ok(c, m):
        if not c:
            fails.append(m); print("FAIL", m)

    d = tempfile.mkdtemp(); db = os.path.join(d, "t.db")
    c = sqlite3.connect(db)
    c.executescript("""
      CREATE TABLE positions (id TEXT, account_id TEXT, strategy TEXT, ticker TEXT, status TEXT, book TEXT, max_loss REAL);
      CREATE TABLE legs (id TEXT, position_id TEXT, leg_role TEXT, direction TEXT, strike REAL, contracts INT, multiplier INT,
                         open_price REAL, close_price REAL, close_date TEXT, status TEXT, expiration TEXT);
      CREATE TABLE leg_checks (leg_id TEXT, checked_at TEXT, delta REAL, gamma REAL, theta REAL, vega REAL, current_price REAL, data_quality TEXT);
      CREATE TABLE checks (position_id TEXT, checked_at TEXT, spot_price REAL, delta REAL, gamma REAL, theta REAL, vega REAL, current_price REAL, data_quality TEXT);
      CREATE TABLE watchlist (ticker TEXT, sector TEXT, beta REAL);
      CREATE TABLE stock_positions (account_id TEXT, ticker TEXT, shares INT, cost_basis REAL);
      CREATE TABLE ownership_quality (ticker TEXT, date TEXT, updated_at TEXT, grade TEXT);
      CREATE TABLE approved_funds (ticker TEXT, bucket TEXT);
      INSERT INTO ownership_quality VALUES ('AMZN','2026-09-01','x','C'),('AMZN','2026-10-24','x','B');
      INSERT INTO approved_funds VALUES ('SPY','US equity index');
      INSERT INTO watchlist VALUES ('AMZN','Consumer Cyclical',1.46),('JPM','Financial Services',0.98),('SPY',NULL,NULL);
      INSERT INTO positions VALUES ('p1','a','CSP','AMZN','OPEN','REAL',NULL),('p2','a','DIAGONAL','JPM','OPEN','REAL',NULL),
                                   ('p3','a','CSP','SPY','OPEN','REAL',NULL),('pp','a','CSP','AMZN','OPEN','PAPER',NULL);
      INSERT INTO legs VALUES ('a1','p1','SHORT_PUT','SHORT',245,2,100,6.5,NULL,NULL,'OPEN','2026-11-20'),
                              ('j1','p2','LONG_CALL','LONG',325,2,100,24.695,NULL,NULL,'OPEN','2026-12-18'),
                              ('j2','p2','SHORT_CALL','SHORT',340,2,100,4.82,NULL,NULL,'OPEN','2026-11-20'),
                              ('s1','p3','SHORT_PUT','SHORT',600,1,100,8.0,NULL,NULL,'OPEN','2026-11-20'),
                              ('x1','pp','SHORT_PUT','SHORT',245,9,100,6.5,NULL,NULL,'OPEN','2026-11-20');
      INSERT INTO leg_checks VALUES ('a1','2026-10-23T15:15',-0.421,0.0128,-0.21,0.27,10.28,'GOOD'),
                                    ('j1','2026-10-23T15:19',0.645,0.0124,-0.12,0.48,16.68,'GOOD'),
                                    ('j2','2026-10-23T15:19',0.386,0.0196,-0.15,0.36,5.12,'GOOD');
      INSERT INTO checks VALUES ('p1','2026-10-23T15:15',248.53,-0.421,0.0128,-0.21,0.27,10.28,'GOOD'),
                                ('p2','2026-10-23T15:19',332.57,0.386,0.0196,-0.15,0.36,5.12,'GOOD'),
                                ('p3','2026-10-23T15:20',660.0,-0.30,0.004,-0.30,0.80,8.0,'GOOD');
    """)
    c.commit(); c.close()
    v = view(db=db)
    P = {r["ticker"]: r for r in v["positions"]}
    # AMZN short put x2, by hand: delta = -(-0.421)*200 = +84.2 sh; theta = -(-0.21)*200 = +42; vega = -0.27*200 = -54
    a = P["AMZN"]
    ok(abs(a["delta_sh"] - 84.2) < 1e-9 and abs(a["theta"] - 42.0) < 1e-9 and abs(a["vega"] + 54.0) < 1e-9,
       "short put signed Greeks: %s %s %s" % (a["delta_sh"], a["theta"], a["vega"]))
    ok(abs(a["bw_delta"] - 84.2 * 248.53 * 1.46) < 1e-6, "beta-weighted dollar delta")
    # -5%: ds = 248.53*-0.05*1.46 = -18.14269; pnl = 84.2*ds + 0.5*(-0.0128*200)*ds^2
    ds = 248.53 * -0.05 * 1.46
    ok(abs(a["stress"]["-0.05"] - (84.2 * ds + 0.5 * (-2.56) * ds * ds)) < 1e-6, "stress -5% by hand")
    # JPM diagonal: delta = 0.645*200 - 0.386*200 = 51.8; theta = -0.12*200 + 0.15*200 = +6; vega = 0.48*200 - 0.36*200 = 24
    j = P["JPM"]
    ok(abs(j["delta_sh"] - 51.8) < 1e-9 and abs(j["theta"] - 6.0) < 1e-9 and abs(j["vega"] - 24.0) < 1e-9,
       "diagonal nets its legs: %s %s %s" % (j["delta_sh"], j["theta"], j["vega"]))
    # SPY: no leg_check -> single-leg falls back to the check; no beta -> 1.0, listed
    s = P["SPY"]
    ok(abs(s["delta_sh"] - 30.0) < 1e-9 and s["beta_assumed"] and v["beta_assumed"] == ["SPY"], "single-leg fallback and assumed beta")
    ok("AMZN" in P and len(v["positions"]) == 3, "paper excluded")
    ok(abs(v["totals"]["theta"] - (42 + 6 + 30)) < 1e-9, "theta totals")
    e = {x["date"]: x for x in v["expiries"]}
    ok(e["2026-11-20"]["frees"] == 49000 + 60000 and abs(e["2026-12-18"]["frees"] - 4939) < 1e-6, "expiries free collateral and long cost")
    o = {x["ticker"]: x for x in v["orders"]}
    ok(set(o) == {"AMZN", "SPY"} and o["AMZN"]["buy_to_close"] == 3.25, "resting orders: CSP shorts at half the credit; diagonal left out")
    ok(P["AMZN"]["grade"] == "B" and P["SPY"]["grade"] == "FUND" and P["JPM"]["grade"] is None,
       "grade: latest wins, fund reads FUND, ungraded is None: %s %s %s" % (P["AMZN"]["grade"], P["SPY"]["grade"], P["JPM"]["grade"]))
    ok(_half_up(4.35 * 0.5) == 2.18 and _half_up(2.27 * 0.5) == 1.14, "half-cent rounds up")
    print("bookview selftest:", "PASS (11 checks)" if not fails else "%d FAIL" % len(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest() if "--selftest" in sys.argv else (_report() and 0))
