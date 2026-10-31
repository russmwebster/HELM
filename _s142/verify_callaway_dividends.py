#!/usr/bin/env python3
"""_s142/verify_callaway_dividends.py -- called-away shares and dividends in the
Fidelity import (s142).

Runs the real CLI (`helm.py fills --file ... --apply --yes`) on fresh VACUUM INTO
copies of helm.db, with Fidelity rows added to a copy of the newest real
download and test Positions downloads in a temp folder (HELM_DOWNLOADS). The
account fields are copied from the real rows and never printed. The live
database is only read.

The chain, GOOGL: the 11/20 335 put assigned (100 shares in at 335) -> a 12/31
340 covered call (put on the copy directly; booking an open needs IB Gateway)
-> called away on 12/31.
  G1 the call-away is booked: call at 0.00, the shares out as a closed stock
     leg 335 -> 340, the position ASSIGNED with $450 + $500; the stock row goes;
     the sleeve counts it; a rerun proposes nothing; only the expected tables change
  G2 no share row -> reported, nothing written
  G3 share row at the wrong price -> reported
  G4 the NVDA PMCC's short assigned early -> reported
  D1 a 12/15 GOOGL dividend of $42 with the account at 200 shares (Positions of
     12/12) -> HELM's $21 booked and counted in the sleeve; rerun proposes nothing
  D2 no Positions download within 7 days -> reported (asks for one)
  D3 the Positions download shows fewer shares than HELM's -> reported
  D4 the real download's dividends on names HELM doesn't hold -> a note, nothing booked
  D5 a dividend after the shares were called away -> reported
  perturbations (python3 -B, a patched copy of the code): the share result left
  out of the call-away; the dividend not split -> checks fail.

  cd ~/Projects/helm && python3 _s142/verify_callaway_dividends.py [main|perturb]
"""
import csv, glob, io, json, os, shutil, sqlite3, subprocess, sys, tempfile, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIVE = Path(os.environ.get("VERIFY_LIVE_DB", REPO / "data" / "helm.db"))
DL = Path(os.environ.get("VERIFY_DOWNLOADS", Path.home() / "Downloads"))
TMP = Path(tempfile.mkdtemp(prefix="helm-s142cd-"))
EXPECTED = {"positions", "legs", "broker_fills", "helm_events", "stock_positions", "helm_dividends", "helm_meta"}
ok_all = True


def check(cond, msg):
    global ok_all
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    ok_all &= bool(cond)


def fresh(name):
    db = TMP / (name + ".db")
    s = sqlite3.connect("file:%s?mode=ro" % LIVE, uri=True)
    s.execute("VACUUM INTO ?", (str(db),)); s.close()
    return db


def q(db, sql, a=()):
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    try:
        return [dict(x) for x in c.execute(sql, a)]
    finally:
        c.close()


def base():
    f = sorted(glob.glob(str(DL / "Accounts_History*.csv")), key=os.path.getmtime)[-1]
    lines = Path(f).read_text(encoding="utf-8-sig").splitlines()
    hi = next(i for i, l in enumerate(lines) if l.startswith("Run Date,"))
    t = next(next(csv.reader([l])) for l in lines[hi + 1:] if "-XLE261204P67" in l)
    return lines, hi, t


def row(t, run, action, symbol, desc, price, qty, amount):
    r = list(t)
    r[0], r[3], r[4], r[5] = run, action, symbol, desc
    r[7], r[8], r[9], r[10], r[12], r[13] = price, qty, "", "", amount, run
    return r


G_DESC = "ALPHABET INC CAP STK CL A"
PUT_ASSIGNED = lambda t: row(t, "11/23/2026", "ASSIGNED as of 2026-11-20 PUT (GOOGL) %s NOV 20 26 $335 (100 SHS) (Margin)" % G_DESC,
                             " -GOOGL261120P335", "PUT (GOOGL) %s NOV 20 26 $335 (100 SHS)" % G_DESC, "", "1", "")
PUT_SHARES = lambda t: row(t, "11/23/2026", "YOU BOUGHT ASSIGNED PUTS AS OF 11-20-26 %s (GOOGL) (Margin)" % G_DESC,
                           "GOOGL", G_DESC, "335", "100", "-33500")
CALL_ASSIGNED = lambda t: row(t, "01/04/2027", "ASSIGNED as of 2026-12-31 CALL (GOOGL) %s DEC 31 26 $340 (100 SHS) (Margin)" % G_DESC,
                              " -GOOGL261231C340", "CALL (GOOGL) %s DEC 31 26 $340 (100 SHS)" % G_DESC, "", "1", "")
CALL_SHARES = lambda t, px="340": row(t, "01/04/2027", "YOU SOLD ASSIGNED CALLS AS OF 12-31-26 %s (GOOGL) (Margin)" % G_DESC,
                                      "GOOGL", G_DESC, px, "-100", "%.2f" % (float(px) * 100))
DIV = lambda t, run, amt: row(t, run, "DIVIDEND RECEIVED %s (GOOGL) (Margin)" % G_DESC, "GOOGL", G_DESC, "", "0", "%.2f" % amt)
NVDA_ASSIGNED = lambda t: row(t, "12/07/2026", "ASSIGNED as of 2026-12-04 CALL (NVDA) NVIDIA CORPORATION COM DEC 04 26 $225 (100 SHS) (Margin)",
                              " -NVDA261204C225", "CALL (NVDA) NVIDIA CORPORATION COM DEC 04 26 $225 (100 SHS)", "", "1", "")
NVDA_SHARES = lambda t: row(t, "12/07/2026", "YOU SOLD ASSIGNED CALLS AS OF 12-04-26 NVIDIA CORPORATION COM (NVDA) (Margin)",
                            "NVDA", "NVIDIA CORPORATION COM", "225", "-100", "22500")


def download(name, extra):
    lines, hi, _ = base()
    out = io.StringIO()
    out.write("\n".join(lines[:hi + 1]) + "\n")
    w = csv.writer(out, lineterminator="\n")
    for r in extra:
        w.writerow(r)
    out.write("\n".join(lines[hi + 1:]) + "\n")
    p = TMP / ("Accounts_History-s142-%s.csv" % name)
    p.write_text(out.getvalue(), encoding="utf-8")
    return p


def positions_dir(name, files):
    """files: [(Mon-DD-YYYY, {ticker: shares})] -> a downloads folder holding them."""
    d = TMP / ("dl-" + name)
    d.mkdir()
    src = sorted(glob.glob(str(DL / "Portfolio_Positions*.csv")), key=os.path.getmtime)[-1]
    raw = Path(src).read_text(encoding="utf-8-sig")
    lines = raw.splitlines()
    tmpl = next(next(csv.reader([l])) for l in lines if ",Rollover IRA,AAPL," in l)
    for stamp, shares in files:
        body = []
        for l in lines:
            if l.startswith('"Date downloaded'):
                l = '"Date downloaded %s at 8:21 a.m ET"' % stamp
            body.append(l)
            if l.startswith("Account number,"):
                for tk, n in shares.items():
                    r = list(tmpl); r[2], r[4] = tk, str(n)
                    o = io.StringIO(); csv.writer(o, lineterminator="").writerow(r); body.append(o.getvalue())
        (d / ("Portfolio_Positions_%s.csv" % stamp)).write_text("\n".join(body) + "\n", encoding="utf-8")
    return d


def cli(db, dl, *args, root=REPO):
    time.sleep(1.1)   # the backup name and the run's event id are stamped to the second
    env = dict(os.environ, HELM_DB=str(db), HELM_ROOT=str(root), HELM_DOWNLOADS=str(dl), COLUMNS="300")
    r = subprocess.run([sys.executable, "-B", str(root / "helm.py"), "fills"] + list(args),
                       capture_output=True, text=True, env=env, timeout=170)
    return r.stdout + r.stderr


def py(db, code, root=REPO):
    env = dict(os.environ, HELM_DB=str(db), HELM_ROOT=str(root), PYTHONPATH=str(root))
    r = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, env=env, cwd=str(root))
    return r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else "ERR " + r.stderr[-400:]


def sleeve(db, root=REPO):
    return json.loads(py(db, "import json,helm.sleeve as S;b=S.book();print(json.dumps({'share':round(b['share'],2),"
                             "'used':round(b['used'],2),'div':round(b['dividends'],2)}))", root))


def add_covered_call(db, root=REPO):
    code = ("from helm.models.position import Position; from helm.models.leg import Leg; import sqlite3,os\n"
            "c=sqlite3.connect(os.environ['HELM_DB']); a=c.execute('SELECT id FROM accounts').fetchone()[0]; c.close()\n"
            "p=Position.create(a,'COVERED_CALL','GOOGL',opened_at='2026-11-23T16:00:00',total_contracts=1,net_premium=450.0,book='REAL')\n"
            "Leg.create(p.id,'SHORT_CALL','SHORT',4.50,'2026-11-23T16:00:00',option_type='CALL',strike=340.0,expiration='2026-12-31',contracts=1)\n"
            "print(p.id)")
    return py(db, code, root)


def table_diff(a, b):
    c = sqlite3.connect(b); c.execute("ATTACH ? AS o", (str(a),))
    out = set()
    for (t,) in c.execute("SELECT name FROM main.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        if not c.execute("SELECT 1 FROM o.sqlite_master WHERE name=?", (t,)).fetchone():
            out.add(t); continue
        n = c.execute('SELECT (SELECT COUNT(*) FROM (SELECT * FROM main."%s" EXCEPT SELECT * FROM o."%s")) + '
                      '(SELECT COUNT(*) FROM (SELECT * FROM o."%s" EXCEPT SELECT * FROM main."%s"))' % (t, t, t, t)).fetchone()[0]
        if n:
            out.add(t)
    c.close()
    return out


def setup(name, root=REPO):
    """A copy with GOOGL assigned through the import and a covered call on its shares."""
    _, _, t = base()
    db = fresh(name)
    dl = positions_dir(name, [])
    out = cli(db, dl, "--file", str(download(name + "-a", [PUT_ASSIGNED(t), PUT_SHARES(t)])), "--apply", "--yes", root=root)
    cc = add_covered_call(db, root)
    return db, dl, t, out, cc


def scenario(root=REPO, tag=""):
    r = {}
    db, dl, t, out, cc = setup("G1" + tag, root)
    r["assign_out"], r["cc"] = out, cc
    r["sp_before"] = q(db, "SELECT shares, cost_basis, effective_basis FROM stock_positions WHERE ticker='GOOGL'")
    r["sleeve0"] = sleeve(db, root)
    pre = TMP / ("G1pre%s.db" % tag); shutil.copy(db, pre)
    f = download("G1" + tag, [PUT_ASSIGNED(t), PUT_SHARES(t), CALL_ASSIGNED(t), CALL_SHARES(t)])
    r["g1_out"] = cli(db, dl, "--file", str(f), "--apply", "--yes", root=root)
    r["g1_pos"] = q(db, "SELECT status, exit_reason, realized_pnl FROM positions WHERE id=?", (cc,))
    r["g1_legs"] = q(db, "SELECT leg_role, status, contracts, multiplier, open_price, close_price, open_date, close_date "
                         "FROM legs WHERE position_id=? ORDER BY leg_role", (cc,))
    r["g1_sp"] = q(db, "SELECT * FROM stock_positions WHERE ticker='GOOGL'")
    r["sleeve1"] = sleeve(db, root)
    r["g1_rerun"] = cli(db, dl, "--file", str(f), root=root)
    r["g1_tables"] = table_diff(pre, db)

    # D5: a dividend after the call-away
    dl5 = positions_dir("D5" + tag, [("Jan-08-2027", {"GOOGL": 100})])
    f5 = download("D5" + tag, [PUT_ASSIGNED(t), PUT_SHARES(t), CALL_ASSIGNED(t), CALL_SHARES(t), DIV(t, "01/10/2027", 21.0)])
    r["d5_out"] = cli(db, dl5, "--file", str(f5), "--apply", "--yes", root=root)
    r["d5_div"] = q(db, "SELECT COUNT(*) n FROM helm_dividends")[0]["n"]

    # G2 / G3 / G4
    db, dl, t, _, cc = setup("G2" + tag, root)
    pre = TMP / ("G2pre%s.db" % tag); shutil.copy(db, pre)
    r["g2_out"] = cli(db, dl, "--file", str(download("G2" + tag, [PUT_ASSIGNED(t), PUT_SHARES(t), CALL_ASSIGNED(t)])),
                      "--apply", "--yes", root=root)
    r["g3_out"] = cli(db, dl, "--file", str(download("G3" + tag, [PUT_ASSIGNED(t), PUT_SHARES(t), CALL_ASSIGNED(t),
                                                                   CALL_SHARES(t, "345")])), "--apply", "--yes", root=root)
    r["g4_out"] = cli(db, dl, "--file", str(download("G4" + tag, [PUT_ASSIGNED(t), PUT_SHARES(t), NVDA_ASSIGNED(t),
                                                                   NVDA_SHARES(t)])), "--apply", "--yes", root=root)
    r["g234_tables"] = table_diff(pre, db) - {"broker_fills", "helm_events", "helm_meta"}

    # D1 / D2 / D3 on a copy still holding the shares
    db, _, t, _, cc = setup("D1" + tag, root)
    s0 = sleeve(db, root)
    dl1 = positions_dir("D1x" + tag, [("Dec-12-2026", {"GOOGL": 200}), ("Nov-30-2026", {"GOOGL": 300})])
    f1 = download("D1" + tag, [PUT_ASSIGNED(t), PUT_SHARES(t), DIV(t, "12/15/2026", 42.0)])
    r["d1_out"] = cli(db, dl1, "--file", str(f1), "--apply", "--yes", root=root)
    r["d1_rows"] = q(db, "SELECT ticker, paid_on, gross, helm_shares, account_shares, helm_amount, positions_date "
                         "FROM helm_dividends")
    r["d1_sleeve"] = (s0, sleeve(db, root))
    r["d1_rerun"] = cli(db, dl1, "--file", str(f1), root=root)
    dl2 = positions_dir("D2" + tag, [("Dec-01-2026", {"GOOGL": 200})])
    r["d2_out"] = cli(db, dl2, "--file", str(download("D2" + tag, [PUT_ASSIGNED(t), PUT_SHARES(t), DIV(t, "12/16/2026", 10.0)])),
                      root=root)
    dl3 = positions_dir("D3" + tag, [("Dec-15-2026", {"GOOGL": 50})])
    r["d3_out"] = cli(db, dl3, "--file", str(download("D3" + tag, [PUT_ASSIGNED(t), PUT_SHARES(t), DIV(t, "12/16/2026", 10.0)])),
                      root=root)
    return r


def judge(r, tag=""):
    check("ASSIGNED" in r["assign_out"] and r["sp_before"] and r["sp_before"][0]["shares"] == 100
          and r["cc"].startswith("GOOGL-COVERED_CALL"),
          tag + "setup: the GOOGL put assigned through the import (100 shares at %s), a covered call on them"
          % (r["sp_before"][0]["cost_basis"] if r["sp_before"] else "?"))
    check("CALLED AWAY" in r["g1_out"], tag + "G1 the plan shows the covered call called away")
    pos = r["g1_pos"][0] if r["g1_pos"] else {}
    check(pos.get("status") == "CLOSED" and pos.get("exit_reason") == "ASSIGNED"
          and abs((pos.get("realized_pnl") or 0) - 950.0) < 0.01,
          tag + "G1 the position closes ASSIGNED: $450 premium + $500 shares = $%s" % pos.get("realized_pnl"))
    legs = {l["leg_role"]: l for l in r["g1_legs"]}
    sc, st = legs.get("SHORT_CALL", {}), legs.get("LONG_STOCK", {})
    check(sc.get("status") == "CLOSED" and sc.get("close_price") == 0.0
          and (sc.get("close_date") or "").startswith("2026-12-31T16:00"), tag + "G1 the call closes at 0.00 on 12-31")
    check(st.get("status") == "CLOSED" and st.get("contracts") == 100 and st.get("multiplier") == 1
          and st.get("open_price") == 335.0 and st.get("close_price") == 340.0
          and (st.get("open_date") or "").startswith("2026-11-20"),
          tag + "G1 the shares leave as a closed stock leg: 100 x 335 (in 11-20) -> 340 (%s)" % st)
    check(r["g1_sp"] == [], tag + "G1 no GOOGL shares left in HELM: the stock row goes")
    s0, s1 = r["sleeve0"], r["sleeve1"]
    check(abs(s1["share"] - s0["share"] - 950.0) < 0.01 and abs(s0["used"] - s1["used"] - 33500.0) < 0.01,
          tag + "G1 the sleeve: realized +$950, in use -$33,500 (%s -> %s)" % (s0, s1))
    check("Nothing to write" in r["g1_rerun"], tag + "G1 a rerun proposes nothing")
    check(r["g1_tables"] <= EXPECTED, tag + "G1 only the expected tables changed: %s" % sorted(r["g1_tables"]))
    check("share row (100 shares expected, 0 found)" in r["g2_out"], tag + "G2 no share row -> reported")
    check("not the 340 strike" in r["g3_out"], tag + "G3 shares at the wrong price -> reported")
    check("assigned early" in r["g4_out"], tag + "G4 the PMCC's short assigned early -> reported")
    check(not r["g234_tables"], tag + "G2-G4 no position, leg or share row written: %s" % sorted(r["g234_tables"]))
    d = r["d1_rows"][0] if r["d1_rows"] else {}
    check(len(r["d1_rows"]) == 1 and d.get("helm_shares") == 100 and d.get("account_shares") == 200
          and abs(d.get("helm_amount", 0) - 21.0) < 0.001 and d.get("positions_date") == "2026-12-12",
          tag + "D1 GOOGL $42 dividend: HELM's 100 of 200 shares (Positions of 12-12, the nearest) -> $21 (%s)" % d)
    a, b = r["d1_sleeve"]
    check(abs(b["share"] - a["share"] - 21.0) < 0.01 and abs(b["div"] - 21.0) < 0.01,
          tag + "D1 the sleeve counts it: +$21 (%s -> %s)" % (a["share"], b["share"]))
    check("Nothing to write" in r["d1_rerun"], tag + "D1 a rerun proposes nothing")
    check("no Positions download within 7 days" in r["d2_out"], tag + "D2 no Positions download near it -> asks for one")
    check("fewer than HELM's 100" in r["d3_out"], tag + "D3 Positions shows fewer shares than HELM's -> reported")
    check("on names HELM holds no shares in" in r["d1_out"], tag + "D4 the real dividends on other names -> a note only")
    check("were called away on 2026-12-31" in r["d5_out"] and r["d5_div"] == 0,
          tag + "D5 a dividend after the call-away -> reported, not booked")


PERTURB = [("helm/assignment.py", '(when, v["realized"], "called away %s', '(when, v["premium"], "called away %s'),
           ("helm/fillimport.py", 'amt = round(float(f.amount or 0) * held / acct, 2)', 'amt = round(float(f.amount or 0), 2)')]


def main():
    global ok_all
    part = sys.argv[1] if len(sys.argv) > 1 else "both"
    print("code: %s   live db (read-only)   temp: %s\n" % (REPO, TMP))
    good = caught = True
    if part in ("main", "both"):
        judge(scenario())
        good = ok_all
    if part in ("perturb", "both"):
        print("\nperturbation: the share result left out of a call-away; a dividend not split")
        pr = TMP / "perturbed"
        shutil.copytree(REPO / "helm", pr / "helm", ignore=shutil.ignore_patterns("__pycache__", "*.bak*"))
        shutil.copy(REPO / "helm.py", pr / "helm.py")
        (pr / "data").mkdir()
        for rel, old, new in PERTURB:
            f = pr / rel; s = f.read_text(); assert s.count(old) == 1, rel; f.write_text(s.replace(old, new))
        saved, ok_all = ok_all, True
        judge(scenario(root=pr, tag="p"), tag="[perturbed] ")
        caught = not ok_all
        ok_all = saved
        print("\n  %s  perturbation caught (some checks above must FAIL)" % ("PASS" if caught else "FAIL"))
    ok = good and caught
    print("\nALL PASS" if ok else "\nNOT PASSED -- send me the output")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
