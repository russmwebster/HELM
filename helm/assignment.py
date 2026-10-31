"""helm/assignment.py -- book a short put's assignment (s138, 2026-10-28).

One writer, two callers: the Fidelity import (`helm fills`, from the ASSIGNED
option row and its "YOU BOUGHT ASSIGNED PUTS" share row) and `helm assign` (by
hand, the fallback).

The accounting (Russ, 2026-10-28):
  * the put leg closes at $0.00 on the assignment date -- the whole premium is
    kept, so the leg's realized result is its credit and the sleeve counts it
    like any expired put. (Before s138 `helm assign` left the price blank and
    the sleeve left the premium out.)
  * the position closes: status CLOSED, exit reason ASSIGNED (as the one earlier
    real assignment was booked), realized = the leg's credit.
  * the shares go into stock_positions AT THE STRIKE (cost_basis, per share --
    the cash that left). The premium is NOT folded into cost_basis (it is
    already realized on the put; counting it again would double it).
  * effective_basis = strike - premium per share, stored as a NUMBER (the
    covered-call strike rule reads it). Lots average by shares for both.
  * premium = the leg's open_price x multiplier x contracts -- the leg, not
    positions.total_contracts, which disagrees with the leg on 7 old CSPs.

s142 -- a covered call ASSIGNED (shares called away), the mirror image:
  * the call leg closes at $0.00 on the assignment date (premium kept);
  * the shares leave at the call strike. Their result -- (call strike - the
    cost basis HELM paid, i.e. the put strike, lots averaged) x shares -- is
    booked as a CLOSED stock leg on the covered-call position (LONG_STOCK,
    contracts = shares, multiplier 1, opened at the cost basis on the day the
    shares came in, closed at the strike), so the sleeve's realized total counts
    it like any closed leg;
  * the position closes ASSIGNED; realized = premium + share result (the notes
    give the two parts);
  * stock_positions loses the shares; the row goes when none are left. Its
    cost and effective basis stay for the shares that remain.
  * the whole covered call only: a partial call-away is refused (reported).
"""
from __future__ import annotations

from datetime import datetime


def ensure_columns(conn):
    """stock_positions.effective_basis (s138). Idempotent; a plain ADD COLUMN --
    nothing maps SELECT * from stock_positions into a constructor."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(stock_positions)")}
    if "effective_basis" not in cols:
        conn.execute("ALTER TABLE stock_positions ADD COLUMN effective_basis REAL")


def preview(conn, position_id):
    """Everything the booking would do, or raises ValueError with the reason. Read-only."""
    p = conn.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
    if p is None:
        raise ValueError("no position %s" % position_id)
    if p["strategy"] != "CSP" or p["status"] != "OPEN" or p["book"] != "REAL":
        raise ValueError("%s is %s %s %s -- only an open real CSP can be assigned"
                         % (position_id, p["book"], p["strategy"], p["status"]))
    legs = conn.execute("SELECT * FROM legs WHERE position_id=? AND status='OPEN'", (position_id,)).fetchall()
    puts = [l for l in legs if l["direction"] == "SHORT" and l["option_type"] == "PUT"]
    if len(puts) != 1 or len(legs) != 1:
        raise ValueError("%s: expected one open short put and nothing else, found %d open legs"
                         % (position_id, len(legs)))
    l = puts[0]
    n, mult, k = int(l["contracts"]), int(l["multiplier"] or 100), float(l["strike"])
    shares = n * mult
    premium = round(float(l["open_price"]) * mult * n, 2)
    prem_ps = premium / shares
    prior = conn.execute("SELECT * FROM stock_positions WHERE ticker=? AND account_id=?",
                         (p["ticker"], p["account_id"])).fetchone()
    psh = int(prior["shares"] or 0) if prior else 0
    if psh:
        pcb = float(prior["cost_basis"] or 0.0)
        peb = prior["effective_basis"] if "effective_basis" in prior.keys() and prior["effective_basis"] is not None else pcb
        tot = psh + shares
        cost = round((pcb * psh + k * shares) / tot, 4)
        eff = round((float(peb) * psh + (k - prem_ps) * shares) / tot, 4)
    else:
        tot, cost, eff = shares, round(k, 4), round(k - prem_ps, 4)
    return {"position": p, "leg": l, "ticker": p["ticker"], "contracts": n, "shares": shares,
            "strike": k, "premium": premium, "premium_ps": prem_ps, "cash": round(k * shares, 2),
            "prior_shares": psh, "new_shares": tot, "cost_basis": cost, "effective_basis": eff,
            "expiry": str(l["expiration"])[:10]}


def book(conn, position_id, when, note):
    """Write the assignment. `when` is an ISO timestamp (the import: the as-of date
    at 16:00). Re-checks everything in preview(); commits; returns the preview."""
    ensure_columns(conn)
    v = preview(conn, position_id)
    p, l = v["position"], v["leg"]
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute("UPDATE legs SET status='CLOSED', close_price=0.0, close_date=?, "
                       "notes=COALESCE(notes || ' | ', '') || ? WHERE id=? AND status='OPEN'",
                       (when, "ASSIGNED %s at %g: closed at 0.00, premium kept. %s" % (when[:10], v["strike"], note),
                        l["id"]))
    if cur.rowcount != 1:
        conn.rollback()
        raise RuntimeError("%s: the put leg changed under the booking -- nothing written" % v["ticker"])
    conn.execute("UPDATE positions SET status='CLOSED', closed_at=?, exit_reason='ASSIGNED', realized_pnl=?, "
                 "notes=COALESCE(notes || ' | ', '') || ?, updated_at=? WHERE id=? AND status='OPEN'",
                 (when, v["premium"], "assigned %s: %d shares at %g; %s" % (when[:10], v["shares"], v["strike"], note),
                  now, p["id"]))
    snote = "assigned from %s on %s at %g; premium $%.2f ($%.4f/sh); %s" % (
        p["id"], when[:10], v["strike"], v["premium"], v["premium_ps"], note)
    prior = conn.execute("SELECT id, acquired_at, notes FROM stock_positions WHERE ticker=? AND account_id=?",
                         (v["ticker"], p["account_id"])).fetchone()
    if prior:
        conn.execute("UPDATE stock_positions SET shares=?, cost_basis=?, effective_basis=?, "
                     "notes=COALESCE(notes || ' | ', '') || ?, updated_at=? WHERE id=?",
                     (v["new_shares"], v["cost_basis"], v["effective_basis"], snote, now, prior["id"]))
    else:
        conn.execute("INSERT INTO stock_positions (id, account_id, ticker, shares, cost_basis, effective_basis, "
                     "acquired_at, notes, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     ("SP-" + v["ticker"], p["account_id"], v["ticker"], v["new_shares"], v["cost_basis"],
                      v["effective_basis"], when, snote, now))
    conn.commit()
    return v


def preview_call(conn, position_id):
    """Everything a covered call's assignment would do, or ValueError. Read-only."""
    p = conn.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
    if p is None:
        raise ValueError("no position %s" % position_id)
    if p["strategy"] != "COVERED_CALL" or p["status"] != "OPEN" or p["book"] != "REAL":
        raise ValueError("%s is %s %s %s -- only an open real covered call can be called away"
                         % (position_id, p["book"], p["strategy"], p["status"]))
    legs = conn.execute("SELECT * FROM legs WHERE position_id=? AND status='OPEN'", (position_id,)).fetchall()
    calls = [l for l in legs if l["direction"] == "SHORT" and l["option_type"] == "CALL"]
    if len(calls) != 1 or len(legs) != 1:
        raise ValueError("%s: expected one open short call and nothing else, found %d open legs"
                         % (position_id, len(legs)))
    l = calls[0]
    n, mult, k = int(l["contracts"]), int(l["multiplier"] or 100), float(l["strike"])
    shares = n * mult
    sp = conn.execute("SELECT * FROM stock_positions WHERE ticker=? AND account_id=?",
                      (p["ticker"], p["account_id"])).fetchone()
    held = int(sp["shares"] or 0) if sp else 0
    if held < shares:
        raise ValueError("%s: HELM holds %d %s shares, the call takes %d" % (position_id, held, p["ticker"], shares))
    if sp["cost_basis"] is None:
        raise ValueError("%s: HELM's %s shares have no cost basis" % (position_id, p["ticker"]))
    cb = float(sp["cost_basis"])
    premium = round(float(l["open_price"]) * mult * n, 2)
    share_result = round((k - cb) * shares, 2)
    return {"position": p, "leg": l, "stock": sp, "ticker": p["ticker"], "contracts": n, "shares": shares,
            "strike": k, "cost_basis": cb, "premium": premium, "share_result": share_result,
            "realized": round(premium + share_result, 2), "shares_left": held - shares,
            "cash": round(k * shares, 2), "acquired_at": sp["acquired_at"]}


def book_call(conn, position_id, when, note):
    """Write a covered call's assignment (shares called away). Re-checks everything
    in preview_call(); one transaction; commits; returns the preview."""
    v = preview_call(conn, position_id)
    p, l, sp = v["position"], v["leg"], v["stock"]
    now = datetime.now().isoformat(timespec="seconds")
    try:
        cur = conn.execute("UPDATE legs SET status='CLOSED', close_price=0.0, close_date=?, "
                           "notes=COALESCE(notes || ' | ', '') || ? WHERE id=? AND status='OPEN'",
                           (when, "ASSIGNED %s at %g: shares called away; closed at 0.00, premium kept. %s"
                            % (when[:10], v["strike"], note), l["id"]))
        if cur.rowcount != 1:
            raise RuntimeError("%s: the call leg changed under the booking -- nothing written" % v["ticker"])
        import uuid
        sid = "%s-LO-%s" % (p["id"], uuid.uuid4().hex[:4].upper())
        conn.execute(
            "INSERT INTO legs (id, position_id, leg_role, option_type, direction, strike, expiration, contracts, "
            "multiplier, open_price, close_price, open_date, close_date, status, notes) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, p["id"], "LONG_STOCK", "STOCK", "LONG", None, None, v["shares"], 1, v["cost_basis"], v["strike"],
             v["acquired_at"] or when, when, "CLOSED",
             "%d %s shares called away %s at %g; cost basis %.4f (what HELM paid); result $%.2f. %s"
             % (v["shares"], v["ticker"], when[:10], v["strike"], v["cost_basis"], v["share_result"], note)))
        conn.execute("UPDATE positions SET status='CLOSED', closed_at=?, exit_reason='ASSIGNED', realized_pnl=?, "
                     "notes=COALESCE(notes || ' | ', '') || ?, updated_at=? WHERE id=? AND status='OPEN'",
                     (when, v["realized"], "called away %s: %d shares at %g; call premium $%.2f + shares $%.2f = $%.2f; %s"
                      % (when[:10], v["shares"], v["strike"], v["premium"], v["share_result"], v["realized"], note),
                      now, p["id"]))
        if v["shares_left"] == 0:
            conn.execute("DELETE FROM stock_positions WHERE id=?", (sp["id"],))
        else:
            conn.execute("UPDATE stock_positions SET shares=?, notes=COALESCE(notes || ' | ', '') || ?, updated_at=? "
                         "WHERE id=?", (v["shares_left"], "%d called away %s at %g by %s"
                                        % (v["shares"], when[:10], v["strike"], p["id"]), now, sp["id"]))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return v


def _selftest():
    import sqlite3
    fails = []

    def ok(c, m):
        print(("  ok   " if c else "  FAIL ") + m)
        if not c:
            fails.append(m)

    def fresh():
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.executescript("""
          CREATE TABLE positions (id TEXT PRIMARY KEY, account_id TEXT, strategy TEXT, ticker TEXT, status TEXT,
            book TEXT, closed_at TEXT, exit_reason TEXT, realized_pnl REAL, notes TEXT, updated_at TEXT,
            total_contracts INT, net_premium REAL);
          CREATE TABLE legs (id TEXT PRIMARY KEY, position_id TEXT, option_type TEXT, direction TEXT, strike REAL,
            expiration TEXT, contracts INT, multiplier INT, open_price REAL, close_price REAL, close_date TEXT,
            status TEXT, notes TEXT);
          CREATE TABLE stock_positions (id TEXT PRIMARY KEY, account_id TEXT, ticker TEXT, shares INT,
            cost_basis REAL, acquired_at TEXT, notes TEXT, updated_at TEXT, UNIQUE(account_id, ticker));
          INSERT INTO positions VALUES ('G','a','CSP','GOOGL','OPEN','REAL',NULL,NULL,NULL,NULL,NULL,9,1.0);
          INSERT INTO legs VALUES ('GL','G','PUT','SHORT',335,'2026-11-20',1,100,8.38,NULL,NULL,'OPEN',NULL);
          INSERT INTO positions VALUES ('J','a','CSP','JPM','OPEN','REAL',NULL,NULL,NULL,NULL,NULL,2,1562);
          INSERT INTO legs VALUES ('JL','J','PUT','SHORT',335,'2026-11-20',2,100,7.81,NULL,NULL,'OPEN',NULL);
          INSERT INTO positions VALUES ('J2','a','CSP','JPM','OPEN','REAL',NULL,NULL,NULL,NULL,NULL,1,500);
          INSERT INTO legs VALUES ('J2L','J2','PUT','SHORT',300,'2026-12-18',1,100,5.00,NULL,NULL,'OPEN',NULL);
          INSERT INTO positions VALUES ('P','a','CSP','XX','OPEN','PAPER',NULL,NULL,NULL,NULL,NULL,1,1);
          INSERT INTO legs VALUES ('PL','P','PUT','SHORT',10,'2026-11-20',1,100,1,NULL,NULL,'OPEN',NULL);
        """)
        return c

    c = fresh()
    v = book(c, "G", "2026-11-20T16:00:00", "test")
    leg = c.execute("SELECT * FROM legs WHERE id='GL'").fetchone()
    pos = c.execute("SELECT * FROM positions WHERE id='G'").fetchone()
    sp = c.execute("SELECT * FROM stock_positions WHERE ticker='GOOGL'").fetchone()
    ok(leg["status"] == "CLOSED" and leg["close_price"] == 0.0 and leg["close_date"] == "2026-11-20T16:00:00",
       "the put leg closes at 0.00 on the assignment date")
    ok(pos["status"] == "CLOSED" and pos["exit_reason"] == "ASSIGNED" and pos["realized_pnl"] == 838.0,
       "the position closes ASSIGNED with the premium realized (from the leg, not total_contracts)")
    ok(sp["shares"] == 100 and sp["cost_basis"] == 335.0 and abs(sp["effective_basis"] - 326.62) < 1e-9,
       "100 shares at the 335 strike, effective basis 326.62")
    ok(sp["acquired_at"] == "2026-11-20T16:00:00", "acquired on the assignment date, not today")
    try:
        book(c, "G", "2026-11-20T16:00:00", "again")
        ok(False, "a second booking of the same put refuses")
    except ValueError:
        ok(True, "a second booking of the same put refuses")
    ok(c.execute("SELECT shares FROM stock_positions WHERE ticker='GOOGL'").fetchone()[0] == 100,
       "...and the shares are not added twice")
    # two lots average by shares
    book(c, "J", "2026-11-20T16:00:00", "t")
    book(c, "J2", "2026-12-18T16:00:00", "t")
    sp = c.execute("SELECT * FROM stock_positions WHERE ticker='JPM'").fetchone()
    ok(sp["shares"] == 300 and abs(sp["cost_basis"] - (335 * 200 + 300 * 100) / 300) < 1e-3,
       "a second lot averages the cost basis by shares")
    ok(abs(sp["effective_basis"] - ((335 - 7.81) * 200 + (300 - 5.0) * 100) / 300) < 1e-3,
       "...and the effective basis the same way")
    ok(sp["acquired_at"] == "2026-11-20T16:00:00", "the first lot's date stays")
    try:
        book(c, "P", "2026-11-20T16:00:00", "t")
        ok(False, "a paper put refuses")
    except ValueError:
        ok(True, "a paper put refuses")

    # s142: covered calls called away
    def fresh_cc():
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.executescript("""
          CREATE TABLE positions (id TEXT PRIMARY KEY, account_id TEXT, strategy TEXT, ticker TEXT, status TEXT,
            book TEXT, closed_at TEXT, exit_reason TEXT, realized_pnl REAL, notes TEXT, updated_at TEXT,
            total_contracts INT, net_premium REAL);
          CREATE TABLE legs (id TEXT PRIMARY KEY, position_id TEXT, leg_role TEXT NOT NULL, option_type TEXT,
            direction TEXT, strike REAL, expiration TEXT, contracts INT, multiplier INT, open_price REAL,
            close_price REAL, open_date TEXT NOT NULL, close_date TEXT, status TEXT, notes TEXT);
          CREATE TABLE stock_positions (id TEXT PRIMARY KEY, account_id TEXT, ticker TEXT, shares INT,
            cost_basis REAL, effective_basis REAL, acquired_at TEXT, notes TEXT, updated_at TEXT,
            UNIQUE(account_id, ticker));
          INSERT INTO stock_positions VALUES ('SP-GOOGL','a','GOOGL',100,335,326.62,'2026-11-20T16:00:00',NULL,NULL);
          INSERT INTO positions VALUES ('C','a','COVERED_CALL','GOOGL','OPEN','REAL',NULL,NULL,NULL,NULL,NULL,1,450);
          INSERT INTO legs VALUES ('CL','C','SHORT_CALL','CALL','SHORT',340,'2026-12-31',1,100,4.50,NULL,
            '2026-11-23T16:00:00',NULL,'OPEN',NULL);
          INSERT INTO stock_positions VALUES ('SP-JPM','a','JPM',300,325,317.4,'2026-11-20T16:00:00',NULL,NULL);
          INSERT INTO positions VALUES ('K','a','COVERED_CALL','JPM','OPEN','REAL',NULL,NULL,NULL,NULL,NULL,2,600);
          INSERT INTO legs VALUES ('KL','K','SHORT_CALL','CALL','SHORT',330,'2026-12-31',2,100,3.00,NULL,
            '2026-11-23T16:00:00',NULL,'OPEN',NULL);
          INSERT INTO stock_positions VALUES ('SP-AMZN','a','AMZN',100,245,240,'2026-11-20T16:00:00',NULL,NULL);
          INSERT INTO positions VALUES ('A','a','COVERED_CALL','AMZN','OPEN','REAL',NULL,NULL,NULL,NULL,NULL,2,600);
          INSERT INTO legs VALUES ('AL','A','SHORT_CALL','CALL','SHORT',250,'2026-12-31',2,100,3.00,NULL,
            '2026-11-23T16:00:00',NULL,'OPEN',NULL);
        """)
        return c

    c = fresh_cc()
    v = book_call(c, "C", "2026-12-31T16:00:00", "test")
    cl = c.execute("SELECT * FROM legs WHERE id='CL'").fetchone()
    st = c.execute("SELECT * FROM legs WHERE position_id='C' AND leg_role='LONG_STOCK'").fetchone()
    pos = c.execute("SELECT * FROM positions WHERE id='C'").fetchone()
    ok(cl["status"] == "CLOSED" and cl["close_price"] == 0.0, "called away: the call closes at 0.00, premium kept")
    ok(st is not None and st["status"] == "CLOSED" and st["contracts"] == 100 and st["multiplier"] == 1
       and st["open_price"] == 335 and st["close_price"] == 340 and st["open_date"] == "2026-11-20T16:00:00"
       and st["close_date"] == "2026-12-31T16:00:00",
       "the shares leave as a closed stock leg: 100 at 335 (opened the day they came in) -> 340")
    ok(pos["status"] == "CLOSED" and pos["exit_reason"] == "ASSIGNED" and abs(pos["realized_pnl"] - 950.0) < 1e-9,
       "the position closes ASSIGNED: premium $450 + shares $500 = $950")
    sign = 1.0
    leg_sum = (0 - 4.50) * -1 * 100 * 1 + sign * (st["close_price"] - st["open_price"]) * st["multiplier"] * st["contracts"]
    ok(abs(leg_sum - pos["realized_pnl"]) < 1e-9, "the legs add up to the position's realized result")
    ok(c.execute("SELECT COUNT(*) FROM stock_positions WHERE ticker='GOOGL'").fetchone()[0] == 0,
       "no GOOGL shares left: the stock row goes")
    try:
        book_call(c, "C", "2026-12-31T16:00:00", "again")
        ok(False, "a second booking refuses")
    except ValueError:
        ok(True, "a second booking refuses")
    v = book_call(c, "K", "2026-12-31T16:00:00", "t")
    sp = c.execute("SELECT * FROM stock_positions WHERE ticker='JPM'").fetchone()
    ok(sp["shares"] == 100 and sp["cost_basis"] == 325 and sp["effective_basis"] == 317.4
       and abs(v["realized"] - (600 + 5 * 200)) < 1e-9,
       "two of three hundred JPM called away: 100 stay at the same basis; realized $600 + $1,000")
    try:
        book_call(c, "A", "2026-12-31T16:00:00", "t")
        ok(False, "a call on more shares than HELM holds refuses")
    except ValueError:
        ok(True, "a call on more shares than HELM holds refuses")
    ok(c.execute("SELECT status FROM legs WHERE id='AL'").fetchone()[0] == "OPEN"
       and c.execute("SELECT shares FROM stock_positions WHERE ticker='AMZN'").fetchone()[0] == 100,
       "...and writes nothing")
    print("\n%d failed" % len(fails) if fails else "\nall passed")
    return not fails


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
