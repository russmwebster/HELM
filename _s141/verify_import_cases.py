#!/usr/bin/env python3
"""_s141/verify_import_cases.py -- the shapes the import must recognise (stage 2, s141).

Cases the downloads on file don't contain, built on a fresh VACUUM INTO copy
of helm.db and fed to helm.fillopens.classify / book directly:

  PMCC's next short (width test passes) -> PMCC_SHORT, booked on the PMCC
  PMCC's next short (width test fails)  -> PMCC_SHORT with a width flag
  covered call on HELM-held shares      -> COVERED_CALL, booked
  covered call with too few shares      -> REPORT
  shares AND a PMCC waiting             -> REPORT (ambiguous)
  a put spread                          -> REPORT
  a short-dated long call               -> REPORT
  a roll (close + sell same day)        -> CSP linked to the old position
  perturbation: the width test turned off -> the failing short carries no flag

  cd ~/Projects/helm && python3 _s141/verify_import_cases.py
"""
import os, sqlite3, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIVE = Path(os.environ.get("VERIFY_LIVE_DB", REPO / "data" / "helm.db"))
ok_all = True
D = "2026-11-02"


def check(cond, msg):
    global ok_all
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    ok_all &= bool(cond)


def grp(tk, exp, otype, strike, direction, q, px):
    return (("OPEN", D, tk, exp, otype, float(strike), direction), {"contracts": q, "price": px}, [])


def setup():
    tmp = tempfile.mkdtemp(prefix="helm-s141-cases-")
    db = Path(tmp) / "cases.db"
    s = sqlite3.connect("file:%s?mode=ro" % LIVE, uri=True); s.execute("VACUUM INTO ?", (str(db),)); s.close()
    c = sqlite3.connect(db)
    acct = c.execute("SELECT id FROM accounts").fetchone()[0]
    pm = c.execute("SELECT id FROM positions WHERE ticker='NVDA' AND strategy='PMCC' AND status='OPEN'").fetchone()[0]
    c.execute("UPDATE legs SET status='CLOSED', close_price=1.70, close_date='2026-11-01T16:00:00' "
              "WHERE position_id=? AND direction='SHORT'", (pm,))
    c.execute("INSERT INTO stock_positions (id, account_id, ticker, shares, cost_basis, effective_basis, acquired_at) "
              "VALUES ('SP-AMZN-T', ?, 'AMZN', 200, 245, 238.5, '2026-11-01')", (acct,))
    csco = c.execute("SELECT id FROM positions WHERE ticker='CSCO' AND status='OPEN'").fetchone()[0]
    c.commit(); c.close()
    return tmp, db, pm, csco


def load(db):
    os.environ["HELM_DB"] = str(db)
    for m in [m for m in list(sys.modules) if m == "helm" or m.startswith("helm.")]:
        del sys.modules[m]
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    import helm.fillopens as FO
    return FO


def main():
    tmp, db, pm, csco = setup()
    print("copy in %s\n" % tmp)
    FO = load(db)
    conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
    opens = [grp("NVDA", "2026-12-31", "CALL", 240, "SHORT", 1, 2.10),       # PMCC next short, passes
             grp("AMZN", "2026-12-18", "CALL", 255, "SHORT", 2, 3.00),       # covered call, 200 shares
             grp("XYZ", "2026-12-18", "PUT", 50, "SHORT", 1, 1.00),          # put spread
             grp("XYZ", "2026-12-18", "PUT", 45, "LONG", 1, 0.40),
             grp("AAPL", "2026-12-18", "CALL", 300, "LONG", 1, 5.00),         # short-dated long call
             grp("CSCO", "2026-12-18", "PUT", 100, "SHORT", 3, 2.00)]         # roll of the CSCO put
    ctx = {"closed": {(D, "CSCO", "PUT"): csco}, "closing_legs": set(), "assign_shares": {}}
    items = FO.classify(conn, opens, ctx)
    by = {}
    for it in items:
        by.setdefault(it.ticker, []).append(it)
        print("    %-6s %s" % (it.kind, it.text))
    nv = by.get("NVDA", [None])[0]
    check(nv and nv.kind == "OPEN" and nv.open["strategy"] == "PMCC_SHORT" and nv.position_id == pm
          and not any("width" in f for f in nv.open["flags"]), "NVDA 240 call -> the PMCC's next short, width test passes")
    am = by.get("AMZN", [None])[0]
    check(am and am.kind == "OPEN" and am.open["strategy"] == "COVERED_CALL", "AMZN 255 call x2 on 200 shares -> covered call")
    check(all(i.kind == "REPORT" for i in by.get("XYZ", [])) and len(by.get("XYZ", [])) == 2, "XYZ put spread -> reported")
    check(all(i.kind == "REPORT" for i in by.get("AAPL", [])), "AAPL short-dated long call -> reported")
    cs = by.get("CSCO", [None])[0]
    check(cs and cs.kind == "OPEN" and cs.open["strategy"] == "CSP" and cs.open["parent"] == csco,
          "CSCO put sold the day the old one closed -> CSP linked as a roll")

    items2 = FO.classify(conn, [grp("NVDA", "2026-12-31", "CALL", 210, "SHORT", 1, 9.0),
                                grp("AMZN", "2026-12-18", "CALL", 255, "SHORT", 3, 3.0)], ctx)
    nv2 = [i for i in items2 if i.ticker == "NVDA"][0]
    check(nv2.kind == "OPEN" and any("width test failed" in f for f in nv2.open["flags"]),
          "NVDA 210 call -> next short with a width-test flag: " + nv2.text[-110:])
    am2 = [i for i in items2 if i.ticker == "AMZN"][0]
    check(am2.kind == "REPORT", "AMZN call x3 on 200 shares -> reported")

    conn.execute("INSERT INTO stock_positions (id, account_id, ticker, shares, cost_basis, acquired_at) "
                 "SELECT 'SP-NVDA-T', id, 'NVDA', 100, 200, '2026-11-01' FROM accounts")
    conn.commit()
    items3 = FO.classify(conn, [grp("NVDA", "2026-12-31", "CALL", 240, "SHORT", 1, 2.10)], ctx)
    check(items3[0].kind == "REPORT" and "could be" in items3[0].text, "NVDA shares and a waiting PMCC -> reported as ambiguous")
    conn.execute("DELETE FROM stock_positions WHERE id='SP-NVDA-T'"); conn.commit()

    # perturbation: width test switched off
    real = FO.width_test
    FO.width_test = lambda legs, k, q: (True, 0.0, 160.0)
    items4 = FO.classify(conn, [grp("NVDA", "2026-12-31", "CALL", 210, "SHORT", 1, 9.0)], ctx)
    FO.width_test = real
    check(not any("width" in f for f in items4[0].open["flags"]),
          "perturbation: with the width test off the failing short carries no flag (the check above can fail)")
    conn.close()

    # bookings
    from helm.fillimport import FILL_TIME
    for it in (nv, am, cs):
        it.as_of = D
        pid, lids, lab = FO.book(it, "2026-11-02", FILL_TIME)
        it._pid, it._lids = pid, lids
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    l = c.execute("SELECT * FROM legs WHERE id=?", (nv._lids[0],)).fetchone()
    check(nv._pid == pm and l["direction"] == "SHORT" and l["strike"] == 240 and l["open_date"] == D
          and l["status"] == "OPEN", "booked: the NVDA short is a new leg on the PMCC, dated the fill day")
    p = c.execute("SELECT * FROM positions WHERE id=?", (am._pid,)).fetchone()
    check(p["strategy"] == "COVERED_CALL" and p["opened_at"] == D + "T16:00:00" and p["net_premium"] == 600.0,
          "booked: AMZN covered call, opened 16:00 on the fill day, premium $600")
    p = c.execute("SELECT * FROM positions WHERE id=?", (cs._pid,)).fetchone()
    check(p["parent_position_id"] == csco and p["strategy"] == "CSP", "booked: the CSCO roll links to the old put")
    c.close()
    print("\nALL PASS" if ok_all else "\nNOT PASSED -- send me the output")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
