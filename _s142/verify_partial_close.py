#!/usr/bin/env python3
"""_s142/verify_partial_close.py -- partial closes in the Fidelity import (s142).

Every case runs the real CLI (`helm.py fills --file ... --apply --yes`) on a
fresh VACUUM INTO copy of helm.db, with Fidelity rows added to a copy of the
newest real download (written to a temp folder; the account fields are copied
from the real rows and never printed). The live database is opened read-only.

  A  XLE x8: 5 closed on 11-02, then (a second download) the other 3 on 11-03
  B  the same two days in ONE download
  C  control: one close of all 8 at the contract-weighted price
  D  same-day pieces (5 + 3 on 11-02) -> one close of 8, as before s142
  E  CSCO x3: 1 closed -> the open 2 keep every reading and the entry snapshot
  F  a close of 9 against HELM's 8 -> reported, nothing written
  then: A, B and C realize the same total and leave the same sleeve; a rerun
  proposes nothing; after the split the open part reads as an ordinary put to
  the snapshot's assess_position, helm advise and the sleeve; every table is
  compared before and after and only the expected ones change.
  perturbation: the split's cut of the original switched off (python3 -B, a
  patched copy of the code) -> the checks fail.

  cd ~/Projects/helm && python3 _s142/verify_partial_close.py [main|perturb]
"""
import csv, glob, io, os, shutil, sqlite3, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIVE = Path(os.environ.get("VERIFY_LIVE_DB", REPO / "data" / "helm.db"))
DL = Path(os.environ.get("VERIFY_DOWNLOADS", Path.home() / "Downloads"))
TMP = Path(tempfile.mkdtemp(prefix="helm-s142-"))
ok_all = True
EXPECTED_TABLES = {"positions", "legs", "broker_fills", "helm_events", "close_snapshots", "lifecycle_events"}


def check(cond, msg):
    global ok_all
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    ok_all &= bool(cond)


def fresh(name):
    db = TMP / (name + ".db")
    s = sqlite3.connect("file:%s?mode=ro" % LIVE, uri=True)
    s.execute("VACUUM INTO ?", (str(db),)); s.close()
    return db


def base_download():
    files = sorted(glob.glob(str(DL / "Accounts_History*.csv")), key=os.path.getmtime)
    return Path(files[-1])


def read_rows(path):
    raw = path.read_text(encoding="utf-8-sig")
    lines = raw.splitlines()
    hi = next(i for i, l in enumerate(lines) if l.startswith("Run Date,"))
    return lines, hi


def template(lines, hi, symbol):
    for l in lines[hi + 1:]:
        r = next(csv.reader([l]))
        if len(r) > 5 and r[4].strip() == symbol:
            return r
    raise SystemExit("no row for %s in the download" % symbol)


def close_row(t, day, qty, px, text):
    r = list(t)
    r[0] = day
    r[3] = "YOU BOUGHT CLOSING TRANSACTION %s (Cash)" % text
    r[7], r[8], r[9], r[10] = "%g" % px, str(qty), "0.65", "0.02"
    r[12] = "%.2f" % -(px * qty * 100 + 0.65 + 0.02)
    r[13] = day
    return r


def write_download(name, extra):
    lines, hi = read_rows(base_download())
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    body = lines[:hi + 1]
    out.write("\n".join(body) + "\n")
    for r in extra:
        w.writerow(r)
    out.write("\n".join(lines[hi + 1:]) + "\n")
    p = TMP / ("Accounts_History-s142-" + name + ".csv")
    p.write_text(out.getvalue(), encoding="utf-8")
    return p


def cli(db, *args, root=REPO):
    # the backup name and the IMPORT_RUN event id are stamped to the second: two
    # runs inside one second collide (a harness artifact; a hand-run can't)
    import time; time.sleep(1.1)
    env = dict(os.environ, HELM_DB=str(db), HELM_ROOT=str(root))
    r = subprocess.run([sys.executable, "-B", str(root / "helm.py"), "fills"] + list(args),
                       capture_output=True, text=True, env=env, timeout=170)
    return r.returncode, r.stdout + r.stderr


def q(db, sql, a=()):
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    try:
        return [dict(x) for x in c.execute(sql, a)]
    finally:
        c.close()


def realized_total(db, ticker):
    return round(sum(r["realized_pnl"] or 0 for r in q(db, "SELECT realized_pnl FROM positions WHERE book='REAL' "
                                                       "AND ticker=? AND closed_at >= '2026-11-01'", (ticker,))), 2)


def sleeve(db, root=REPO):
    code = ("import json,helm.sleeve as S; b=S.book(); "
            "print(json.dumps({'share':round(b['share'],2),'used':round(b['used'],2),"
            "'xle':round(b['by_name'].get('XLE',0),2),'csco':round(b['by_name'].get('CSCO',0),2)}))")
    env = dict(os.environ, HELM_DB=str(db), HELM_ROOT=str(root), PYTHONPATH=str(root))
    r = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, env=env, cwd=str(root))
    import json
    return json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 else {"err": r.stderr[-300:]}


def table_diff(a, b):
    c = sqlite3.connect(b)
    c.execute("ATTACH ? AS o", (str(a),))
    changed = set()
    for (t,) in c.execute("SELECT name FROM o.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        try:
            n1 = c.execute('SELECT COUNT(*) FROM (SELECT * FROM main."%s" EXCEPT SELECT * FROM o."%s")' % (t, t)).fetchone()[0]
            n2 = c.execute('SELECT COUNT(*) FROM (SELECT * FROM o."%s" EXCEPT SELECT * FROM main."%s")' % (t, t)).fetchone()[0]
        except sqlite3.OperationalError:
            changed.add(t + "(schema)"); continue
        if n1 or n2:
            changed.add(t)
    c.close()
    return changed


def assess(db, pid, mid, root=REPO):
    code = ("import json,sqlite3,helm.cli.check_cmd as K; c=sqlite3.connect(%r); c.row_factory=sqlite3.Row; "
            "p=dict(c.execute('SELECT * FROM positions WHERE id=?',(%r,)).fetchone()); "
            "L=[dict(r) for r in c.execute('SELECT * FROM legs WHERE position_id=?',(%r,))]; "
            "a=K.assess_position(p,L,66.0,{'mid':%r},{}); print(json.dumps({'pnl':a.get('pnl_mtm'),'pct':a.get('pnl_pct'),'legs':len(L)}))"
            % (str(db), pid, pid, mid))
    env = dict(os.environ, HELM_DB=str(db), HELM_ROOT=str(root), PYTHONPATH=str(root))
    r = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, env=env, cwd=str(root))
    import json
    return json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 else {"err": r.stderr[-400:]}


def advice_line(db, ticker, root=REPO):
    code = ("import helm.advice as A\nfor r in A.advise(book_limits=False):\n"
            "    if r.get('ticker')==%r: print(r.get('verb'),'|',r.get('headline'))" % ticker)
    env = dict(os.environ, HELM_DB=str(db), HELM_ROOT=str(root), PYTHONPATH=str(root))
    r = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, env=env, cwd=str(root))
    return (r.stdout + r.stderr).strip()


def scenario(root=REPO, label=""):
    """Runs A-F against `root`'s code. Returns a dict of results for the checks."""
    lines, hi = read_rows(base_download())
    tx = template(lines, hi, "-XLE261204P67")
    tc = template(lines, hi, "-CSCO261120P105") if any("CSCO261120P105" in l for l in lines) else None
    xtxt = "PUT (XLE) SELECT SECTOR SPDR DEC 04 26 $67 (100 SHS)"
    a1 = close_row(tx, "11/02/2026", 5, 0.61, xtxt)
    a2 = close_row(tx, "11/03/2026", 3, 0.50, xtxt)
    res = {}
    xpid = q(fresh("probe" + label), "SELECT id FROM positions WHERE ticker='XLE' AND status='OPEN'")[0]["id"]
    res["xpid"] = xpid

    # A: two downloads
    db = fresh("A" + label); pre = fresh("A0" + label)
    f1 = write_download("A1" + label, [a1])
    rc, out = cli(db, "--file", str(f1), "--apply", "--yes", root=root); res["A1_out"] = out; res["A1_rc"] = rc
    res["A_mid"] = {"open": q(db, "SELECT p.total_contracts, p.net_premium, l.contracts, l.status FROM positions p "
                              "JOIN legs l ON l.position_id=p.id WHERE p.id=?", (xpid,)),
                    "pieces": q(db, "SELECT p.id, p.status, p.total_contracts, p.net_premium, p.realized_pnl, p.exit_reason, "
                                "p.opened_at, p.closed_at, p.tags, l.contracts, l.open_price, l.close_price, l.status lstatus, "
                                "l.close_date FROM positions p JOIN legs l ON l.position_id=p.id "
                                "WHERE p.tags LIKE ?", ("%split_from:" + xpid + "%",)),
                    "assess": assess(db, xpid, 0.80, root), "advice": advice_line(db, "XLE", root),
                    "sleeve": sleeve(db, root)}
    rc, out = cli(db, "--file", str(f1), root=root); res["A1_rerun"] = out
    f2 = write_download("A2" + label, [a1, a2])
    rc, out = cli(db, "--file", str(f2), "--apply", "--yes", root=root); res["A2_out"] = out
    rc, out = cli(db, "--file", str(f2), root=root); res["A2_rerun"] = out
    res["A_end"] = {"real": realized_total(db, "XLE"), "sleeve": sleeve(db, root),
                    "open": q(db, "SELECT status, exit_reason, realized_pnl, total_contracts FROM positions WHERE id=?", (xpid,))}
    res["A_tables"] = table_diff(pre, db)

    # B: one download, both days
    db = fresh("B" + label)
    rc, out = cli(db, "--file", str(f2), "--apply", "--yes", root=root); res["B_out"] = out
    res["B_end"] = {"real": realized_total(db, "XLE"), "sleeve": sleeve(db, root)}

    # C: control, one close of 8 at the weighted price
    db = fresh("C" + label)
    wpx = (5 * 0.61 + 3 * 0.50) / 8
    fc = write_download("C" + label, [close_row(tx, "11/03/2026", 8, wpx, xtxt)])
    rc, out = cli(db, "--file", str(fc), "--apply", "--yes", root=root); res["C_out"] = out
    res["C_end"] = {"real": realized_total(db, "XLE"), "sleeve": sleeve(db, root)}

    # D: same-day pieces
    db = fresh("D" + label)
    fd = write_download("D" + label, [a1, close_row(tx, "11/02/2026", 3, 0.50, xtxt)])
    rc, out = cli(db, "--file", str(fd), "--apply", "--yes", root=root); res["D_out"] = out
    res["D_end"] = {"real": realized_total(db, "XLE"),
                    "pieces": len(q(db, "SELECT id FROM positions WHERE tags LIKE '%split_from:%'"))}

    # E: CSCO 1 of 3 -- readings stay
    if tc is not None:
        db = fresh("E" + label)
        cpid = q(db, "SELECT id FROM positions WHERE ticker='CSCO' AND status='OPEN'")[0]["id"]
        clid = q(db, "SELECT id FROM legs WHERE position_id=?", (cpid,))[0]["id"]
        before = {"lc": q(db, "SELECT COUNT(*) n FROM leg_checks WHERE leg_id=?", (clid,))[0]["n"],
                  "es": q(db, "SELECT COUNT(*) n FROM entry_snapshots WHERE position_id=?", (cpid,))[0]["n"],
                  "ck": q(db, "SELECT COUNT(*) n FROM checks WHERE position_id=?", (cpid,))[0]["n"]}
        fe = write_download("E" + label, [close_row(tc, "11/02/2026", 1, 1.14,
                                                     "PUT (CSCO) CISCO SYSTEMS INC NOV 20 26 $105 (100 SHS)")])
        rc, out = cli(db, "--file", str(fe), "--apply", "--yes", root=root); res["E_out"] = out
        after = {"lc": q(db, "SELECT COUNT(*) n FROM leg_checks WHERE leg_id=?", (clid,))[0]["n"],
                 "es": q(db, "SELECT COUNT(*) n FROM entry_snapshots WHERE position_id=?", (cpid,))[0]["n"],
                 "ck": q(db, "SELECT COUNT(*) n FROM checks WHERE position_id=?", (cpid,))[0]["n"]}
        res["E"] = {"before": before, "after": after,
                    "open": q(db, "SELECT p.total_contracts, p.net_premium, l.contracts, l.open_price FROM positions p "
                              "JOIN legs l ON l.position_id=p.id WHERE p.id=?", (cpid,)),
                    "piece": q(db, "SELECT p.exit_reason, p.realized_pnl, p.net_premium, l.contracts FROM positions p "
                               "JOIN legs l ON l.position_id=p.id WHERE p.tags LIKE ?", ("%split_from:" + cpid + "%",)),
                    "advice": advice_line(db, "CSCO", root)}

    # F: too many
    db = fresh("F" + label); pre = fresh("F0" + label)
    ff = write_download("F" + label, [close_row(tx, "11/02/2026", 9, 0.61, xtxt)])
    rc, out = cli(db, "--file", str(ff), "--apply", "--yes", root=root); res["F_out"] = out
    res["F_tables"] = table_diff(pre, db)
    return res


def judge(r, tag=""):
    m = r["A_mid"]
    o = m["open"][0] if m["open"] else {}
    pc = m["pieces"][0] if m["pieces"] else {}
    check("PARTLY CLOSED: 5 of 8" in r["A1_out"], tag + "A day 1: the plan shows XLE partly closed, 5 of 8")
    check(o.get("contracts") == 3 and o.get("total_contracts") == 3 and abs((o.get("net_premium") or 0) - 366.0) < 0.01
          and o.get("status") == "OPEN", tag + "A day 1: the open XLE keeps 3 contracts, premium $366 (%s)" % o)
    check(len(m["pieces"]) == 1 and pc.get("status") == "CLOSED" and pc.get("contracts") == 5 and pc.get("total_contracts") == 5
          and abs((pc.get("net_premium") or 0) - 610.0) < 0.01 and abs((pc.get("realized_pnl") or 0) - 305.0) < 0.01
          and pc.get("exit_reason") == "PROFIT_TARGET" and pc.get("close_price") == 0.61
          and (pc.get("closed_at") or "").startswith("2026-11-02T16:00") and pc.get("opened_at", "").startswith("2026-10-30"),
          tag + "A day 1: the 5 closed are their own CLOSED position: premium $610, realized +$305, PROFIT_TARGET, "
          "11-02 16:00, opened 10-30 (%s)" % {k: pc.get(k) for k in ("realized_pnl", "exit_reason", "closed_at", "opened_at")})
    a = m["assess"]
    check(a.get("legs") == 1 and abs((a.get("pnl") or 0) - 126.0) < 0.01 and abs((a.get("pct") or 0) - 34.4) < 0.06,
          tag + "A day 1: the snapshot's assess_position reads the open part as a one-leg put: mid 0.80 -> +$126, "
          "34.4%% of $366 (%s)" % a)
    check("XLE" in m["advice"] and "Traceback" not in m["advice"], tag + "A day 1: helm advise reads XLE: " + m["advice"][:120])
    s = m["sleeve"]
    check(abs(s.get("xle", 0) - 20100.0) < 0.01, tag + "A day 1: sleeve counts XLE collateral 67 x 100 x 3 = $20,100 (%s)" % s.get("xle"))
    check("Nothing to write" in r["A1_rerun"], tag + "A: a rerun of day 1's download proposes nothing")
    check("XLE csp CLOSED:" in r["A2_out"] and r["A_end"]["open"][0]["status"] == "CLOSED",
          tag + "A day 2: the other 3 close the original position whole (%s)" % r["A_end"]["open"])
    check("Nothing to write" in r["A2_rerun"], tag + "A: a rerun of day 2's download proposes nothing")
    check(abs(r["A_end"]["real"] - r["C_end"]["real"]) < 0.01 and abs(r["A_end"]["real"] - 521.0) < 0.01,
          tag + "A (two runs) and C (one close of 8) realize the same: $%s vs $%s" % (r["A_end"]["real"], r["C_end"]["real"]))
    check(abs(r["B_end"]["real"] - r["C_end"]["real"]) < 0.01, tag + "B (both days, one run) realizes the same: $%s" % r["B_end"]["real"])
    check(r["A_end"]["sleeve"] == r["C_end"]["sleeve"] == r["B_end"]["sleeve"],
          tag + "A, B and C leave the same sleeve: %s" % r["C_end"]["sleeve"])
    check(r["D_end"]["pieces"] == 0 and abs(r["D_end"]["real"] - 521.0) < 0.01,
          tag + "D same-day 5 + 3 -> one close of 8, no split (%s)" % r["D_end"])
    check(r["A_tables"] <= EXPECTED_TABLES, tag + "A: only the expected tables changed: %s" % sorted(r["A_tables"]))
    e = r.get("E")
    if e:
        check(e["before"] == e["after"] and e["before"]["lc"] > 0,
              tag + "E CSCO 1 of 3: the open part keeps every reading and its entry snapshot %s" % e["after"])
        eo = e["open"][0]; ep = e["piece"][0] if e["piece"] else {}
        check(eo["contracts"] == 2 and eo["total_contracts"] == 2 and abs(eo["net_premium"] - 456.0) < 0.01
              and ep.get("contracts") == 1 and abs((ep.get("realized_pnl") or 0) - 114.0) < 0.01
              and ep.get("exit_reason") == "PROFIT_TARGET",
              tag + "E: CSCO open x2 premium $456; piece x1 realized +$114 PROFIT_TARGET (%s / %s)" % (eo, ep))
        check("Traceback" not in e["advice"] and "CSCO" in e["advice"], tag + "E: helm advise reads CSCO: " + e["advice"][:120])
    check("Fidelity closed 9" in r["F_out"] and not r["F_tables"] - {"helm_events"} - {"broker_fills"}
          and "positions" not in r["F_tables"] and "legs" not in r["F_tables"],
          tag + "F a close of 9 against 8 is reported, no position or leg written (%s)" % sorted(r["F_tables"]))


def main():
    """Part `main` (the cases), `perturb` (the perturbation), or both (default).
    The device shell's 3-minute limit needs them run separately there."""
    global ok_all
    part = sys.argv[1] if len(sys.argv) > 1 else "both"
    print("code: %s   live db (read-only): %s   temp: %s\n" % (REPO, LIVE, TMP))
    good = caught = True
    if part in ("main", "both"):
        judge(scenario())
        good = ok_all
    if part in ("perturb", "both"):
        print("\nperturbation: split_off leaves the original's contracts uncut")
        pr = TMP / "perturbed"
        shutil.copytree(REPO / "helm", pr / "helm", ignore=shutil.ignore_patterns("__pycache__", "*.bak*"))
        shutil.copy(REPO / "helm.py", pr / "helm.py")
        if (REPO / "paper_exit_agent.py").exists():
            shutil.copy(REPO / "paper_exit_agent.py", pr / "paper_exit_agent.py")
        (pr / "data").mkdir()
        f = pr / "helm" / "fillimport.py"
        s = f.read_text()
        old = '(have - k, "Fidelity import %s: %d of %d closed early, split off to %s"'
        assert s.count(old) == 1
        f.write_text(s.replace(old, '(have, "Fidelity import %s: %d of %d closed early, split off to %s"'))
        saved, ok_all = ok_all, True
        judge(scenario(root=pr, label="p"), tag="[perturbed] ")
        caught = not ok_all
        ok_all = saved
        print("\n  %s  perturbation caught (some checks above must FAIL)" % ("PASS" if caught else "FAIL"))
    ok = good and caught
    print("\nALL PASS" if ok else "\nNOT PASSED -- send me the output")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
