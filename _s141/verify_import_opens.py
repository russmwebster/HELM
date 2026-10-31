#!/usr/bin/env python3
"""_s141/verify_import_opens.py -- the import books new opens (stage 2, s141).

On a fresh VACUUM INTO copy of helm.db:
  1. delete the three positions opened at Fidelity since 10/24 that were booked
     by hand (DELL CSP 10-29, XLE CSP 10-30, NVDA PMCC 10-30) and their fill
     records -- as if they had never been booked;
  2. let the import plan and write them from the Fidelity download;
  3. compare what it booked with what was booked by hand (after the s140 repairs);
  4. compare EVERY table before and after: only the expected ones may change;
  5. run the plan again: it must propose nothing;
  6. perturbations: (a) the open path switched off -> nothing planned to book,
     so check 2 can fail; (b) XLE left in HELM but dated a day later -> the
     import must refuse to book it a second time.

Live database: read only (VACUUM INTO).  IBKR: read for the entry readings if
it answers; if not, the opens are booked labelled "readings missing".

  cd ~/Projects/helm && python3 _s141/verify_import_opens.py [--file DOWNLOAD]
"""
import glob, hashlib, os, sqlite3, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIVE = Path(os.environ.get("VERIFY_LIVE_DB", REPO / "data" / "helm.db"))
EXPECT = {"DELL-CSP-20261029-82ACB3": ("DELL", "CSP"), "XLE-CSP-20261031-5354C5": ("XLE", "CSP"),
          "NVDA-PMCC-20261031-CC173B": ("NVDA", "PMCC")}
ok_all = True


def check(cond, msg):
    global ok_all
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    ok_all &= bool(cond)


def copy_db(tmp, name):
    dst = Path(tmp) / (name + ".db")
    s = sqlite3.connect("file:%s?mode=ro" % LIVE, uri=True)
    s.execute("VACUUM INTO ?", (str(dst),)); s.close()
    return dst


def table_digest(db):
    c = sqlite3.connect(db)
    out = {}
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY 1"):
        rows = c.execute("SELECT * FROM '%s'" % t).fetchall()
        out[t] = (len(rows), hashlib.sha256(repr(sorted(map(repr, rows))).encode()).hexdigest()[:12])
    c.close()
    return out


def originals(db):
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    out = {}
    for pid in EXPECT:
        p = c.execute("SELECT * FROM positions WHERE id=?", (pid,)).fetchone()
        legs = c.execute("SELECT direction, option_type, strike, expiration, open_price, contracts, open_date "
                         "FROM legs WHERE position_id=? ORDER BY direction", (pid,)).fetchall()
        out[pid] = (p and dict(p), [tuple(l) for l in legs])
    c.close()
    return out


def prune(db, pids, shift=None):
    c = sqlite3.connect(db); c.execute("PRAGMA foreign_keys=ON")
    for pid in pids:
        c.execute("DELETE FROM broker_fills WHERE position_id=?", (pid,))
        for t in ("entry_snapshots", "lifecycle_events", "checks", "entry_thesis", "exit_flags", "exit_considered"):
            try:
                c.execute("DELETE FROM %s WHERE position_id=?" % t, (pid,))
            except sqlite3.OperationalError:
                pass
        c.execute("DELETE FROM leg_checks WHERE leg_id IN (SELECT id FROM legs WHERE position_id=?)", (pid,))
        c.execute("DELETE FROM legs WHERE position_id=?", (pid,))
        c.execute("DELETE FROM positions WHERE id=?", (pid,))
    if shift:
        c.execute("DELETE FROM broker_fills WHERE position_id=?", (shift,))
        c.execute("UPDATE legs SET open_date='2026-10-31' WHERE position_id=?", (shift,))
    c.commit(); c.close()


def run_plan(db, path, apply=False, switch_off=False):
    os.environ["HELM_DB"] = str(db)
    for m in [m for m in list(sys.modules) if m == "helm" or m.startswith("helm.")]:
        del sys.modules[m]
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from helm import brokerfills as B, fillimport as FI
    if switch_off:
        import helm.fillopens as FO
        from helm.fillimport import Item
        FO.classify = lambda conn, opens, ctx: [Item("REPORT", "switched off", ticker=g[0][2]) for g in opens]
    conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True); conn.row_factory = sqlite3.Row
    ira = FI.stored_ira_hash(conn)
    exp = B.read_export(path, ira)
    p = FI.plan(conn, [exp])
    conn.close()
    done = FI.apply(p, db_path=None, ira_hash=ira) if apply else None
    return p, done


def booked(db):
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    out = {}
    for p in c.execute("SELECT * FROM positions WHERE book='REAL' AND origin_screen='FIDELITY_IMPORT'"):
        legs = c.execute("SELECT direction, option_type, strike, expiration, open_price, contracts, open_date "
                         "FROM legs WHERE position_id=? ORDER BY direction", (p["id"],)).fetchall()
        snap = c.execute("SELECT delta, gamma, vega, iv_current, bid, ask, dte, snapshot_at FROM entry_snapshots "
                         "WHERE position_id=?", (p["id"],)).fetchone()
        out[(p["ticker"], p["strategy"])] = (dict(p), [tuple(l) for l in legs], snap and dict(snap))
    c.close()
    return out


def main():
    a = sys.argv[1:]
    path = a[a.index("--file") + 1] if "--file" in a else None
    if not path:
        dl = os.path.expanduser("~/Downloads")
        c = sorted(glob.glob(dl + "/Accounts_History*.csv") + glob.glob(dl + "/History_for_Account*.csv"),
                   key=os.path.getmtime)
        path = c[-1] if c else None
    if not path:
        sys.exit("no Fidelity activity download found")
    tmp = tempfile.mkdtemp(prefix="helm-s141-opens-")
    print("download: %s\ncopies in %s\n" % (os.path.basename(path), tmp))

    db = copy_db(tmp, "main")
    orig = originals(db)
    check(all(orig[p][0] and (orig[p][0]["ticker"], orig[p][0]["strategy"]) == EXPECT[p] for p in EXPECT),
          "the three hand-booked opens are in the copy as expected")
    prune(db, list(EXPECT))
    before = table_digest(db)
    p, _ = run_plan(db, path)
    opens = [i for i in p.items if i.kind == "OPEN"]
    print("  planned opens:")
    for i in opens:
        print("    " + i.text)
    for i in [i for i in p.items if i.kind == "REPORT"]:
        print("    report: " + i.text)
    check(sorted((i.ticker, i.open["strategy"]) for i in opens) == sorted(EXPECT.values()),
          "plan: exactly DELL CSP, XLE CSP, NVDA PMCC to book")
    p, done = run_plan(db, path, apply=True)
    b = booked(db)
    for pid, (op, ol) in orig.items():
        k = EXPECT[pid]
        got = b.get(k)
        if not got:
            check(False, "%s %s booked" % k); continue
        gp, gl, snap = got
        check(gl == ol, "%s %s legs match the hand booking (side, type, strike, expiry, price, contracts, date)" % k)
        if gl != ol:
            print("         import: %s\n         hand:   %s" % (gl, ol))
        check(abs((gp["net_premium"] or 0) - (op["net_premium"] or 0)) < 0.005
              and gp["total_contracts"] == op["total_contracts"],
              "%s %s premium %.2f and contracts %d match" % (k + (gp["net_premium"], gp["total_contracts"])))
        check(gp["opened_at"] == ol[0][6] + "T16:00:00",
              "%s %s opened_at %s (16:00 on the trade date; the hand booking had %s)"
              % (k + (gp["opened_at"], op["opened_at"])))
        check("Fidelity import" in (gp["notes"] or "") and "after the fill" in (gp["notes"] or ""),
              "%s %s notes carry the import label" % k)
        print("         readings: %s" % (snap,))
        if snap and snap["delta"] is not None and k[1] == "CSP":
            check(snap["delta"] > 0,
                  "%s %s entry delta stored positive for a put (%s), as HELM always has" % (k + (snap["delta"],)))
    after = table_digest(db)
    changed = sorted(t for t in after if after[t] != before.get(t))
    allowed = {"positions", "legs", "entry_snapshots", "lifecycle_events", "broker_fills", "helm_events",
               "entry_thesis", "signals", "stop_arm_events"}
    print("  tables changed: " + ", ".join("%s %+d" % (t, after[t][0] - before.get(t, (0,))[0]) for t in changed))
    check(set(changed) <= allowed, "only the expected tables changed")
    p2, _ = run_plan(db, path)
    check(not p2.writes, "second run proposes nothing (%d writes)" % len(p2.writes))

    print("\n  perturbation (a): open path switched off")
    db2 = copy_db(tmp, "pert_a"); prune(db2, list(EXPECT))
    pa, _ = run_plan(db2, path, switch_off=True)
    check(not [i for i in pa.items if i.kind == "OPEN"],
          "with the open path off, nothing is planned to book (so the plan check above can fail)")

    print("\n  perturbation (b): XLE still in HELM, dated a day later")
    db3 = copy_db(tmp, "pert_b")
    prune(db3, ["DELL-CSP-20261029-82ACB3", "NVDA-PMCC-20261031-CC173B"], shift="XLE-CSP-20261031-5354C5")
    pb, _ = run_plan(db3, path)
    xle = [i for i in pb.items if i.ticker == "XLE" and i.kind in ("OPEN", "REPORT")]
    check(xle and all(i.kind == "REPORT" for i in xle),
          "XLE is reported, not booked a second time: " + (xle[0].text if xle else "nothing"))
    print("\nALL PASS" if ok_all else "\nNOT PASSED -- send me the output")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
