#!/usr/bin/env python3
"""_s142/verify_retired_paths.py -- no double entry: the old booking paths are off (s142).

On fresh VACUUM INTO copies of helm.db (the live one is only read), through the
real CLI:
  R1 helm import / confirm-fills / activity refuse with the "use helm fills" line,
     exit 1, write nothing; HELM_RETIRED_ON=1 lets them load (old harnesses)
  R2 helm open TICKER CSP --confirm refuses before any prompt or chain fetch,
     exit 1, writes nothing; without --confirm it is not refused; the help line
     says it books nothing
  R3 helm reconcile on the newest Positions download writes NOTHING -- every
     table compared -- even with a matched position set PENDING (the old code
     promoted it) and the balances out of date (the old code refreshed them)
  control: the s142 .bak of reconcile_cmd.py, run the same way, DOES write
     (accounts + helm_events: the table check can see a write; its PENDING
     promotion never took effect -- UPDATE and commit on two connections);
     the open gate switched off is not refused

  cd ~/Projects/helm && python3 _s142/verify_retired_paths.py
"""
import glob, os, shutil, sqlite3, subprocess, sys, tempfile, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LIVE = Path(os.environ.get("VERIFY_LIVE_DB", REPO / "data" / "helm.db"))
DL = Path(os.environ.get("VERIFY_DOWNLOADS", Path.home() / "Downloads"))
TMP = Path(tempfile.mkdtemp(prefix="helm-s142r-"))
ok_all = True


def check(c, m):
    global ok_all
    print(("  PASS  " if c else "  FAIL  ") + m)
    ok_all &= bool(c)


def fresh(name):
    db = TMP / (name + ".db")
    s = sqlite3.connect("file:%s?mode=ro" % LIVE, uri=True); s.execute("VACUUM INTO ?", (str(db),)); s.close()
    return db


def home_for(db):
    """reconcile reads the active account from ~/.helm_profile; the device shell has
    none, so each run gets a temp home holding the copy's account id (only that file)."""
    h = TMP / ("home-" + Path(db).stem)
    if not h.exists():
        h.mkdir()
        acct = sqlite3.connect(db).execute("SELECT id FROM accounts ORDER BY created_at LIMIT 1").fetchone()[0]
        (h / ".helm_profile").write_text(acct)
    return h


def cli(db, args, root=REPO, extra=None, timeout=60):
    env = dict(os.environ, HELM_DB=str(db), HELM_ROOT=str(root), COLUMNS="300", HELM_DOWNLOADS=str(DL),
               HOME=str(home_for(db)), PYTHONUSERBASE=os.environ.get("PYTHONUSERBASE",
                                                                      str(Path.home() / ".local")))
    env.update(extra or {})
    try:
        r = subprocess.run([sys.executable, "-B", str(root / "helm.py")] + args, capture_output=True, text=True,
                           env=env, timeout=timeout, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired as e:
        return "timeout", (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")


def diff(a, b):
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


def snap(db, name):
    p = TMP / (name + "-pre.db"); shutil.copy(db, p); return p


def reconcile_case(root, name):
    db = fresh(name)
    c = sqlite3.connect(db)
    pid = c.execute("SELECT id FROM positions WHERE book='REAL' AND status='OPEN' AND ticker='XLE'").fetchone()[0]
    c.execute("UPDATE positions SET status='PENDING' WHERE id=?", (pid,))
    cols = {r[1] for r in c.execute("PRAGMA table_info(accounts)")}
    if "balances_as_of" in cols:
        c.execute("UPDATE accounts SET balances_as_of='2026-01-01'")
    c.commit(); c.close()
    pre = snap(db, name)
    pf = sorted(glob.glob(str(DL / "Portfolio_Positions*.csv")), key=os.path.getmtime)[-1]
    rc, out = cli(db, ["reconcile", pf], root=root)
    st = sqlite3.connect(db).execute("SELECT status FROM positions WHERE id=?", (pid,)).fetchone()[0]
    return rc, out, diff(pre, db), st


def main():
    print("code: %s   temp: %s\n" % (REPO, TMP))
    db = fresh("R1"); pre = snap(db, "R1")
    for cmd in ("import", "confirm-fills", "activity"):
        rc, out = cli(db, [cmd])
        check(rc == 1 and "retired" in out and "helm fills" in out, "R1 helm %s refuses, pointing to helm fills (exit %s)" % (cmd, rc))
        rc, out = cli(db, [cmd, "--help"], extra={"HELM_RETIRED_ON": "1"}, timeout=40)
        check("Trades enter HELM from Fidelity" not in out, "R1 HELM_RETIRED_ON=1 lets helm %s load (not refused)" % cmd)
    check(not diff(pre, db), "R1 nothing written: %s" % sorted(diff(pre, db)))

    db = fresh("R2"); pre = snap(db, "R2")
    t0 = time.time()
    rc, out = cli(db, ["open", "XLE", "CSP", "--confirm"])
    check(rc == 1 and "no longer books trades" in out and "Nothing was written" in out and time.time() - t0 < 20,
          "R2 helm open --confirm refuses at once (exit %s, %.1fs)" % (rc, time.time() - t0))
    rc, out = cli(db, ["open", "XLE", "PMCC", "--confirm", "--strike", "225", "--expiry", "2026-12-04"])
    check(rc == 1 and "no longer books trades" in out, "R2 ...also for a pinned PMCC")
    rc, out = cli(db, ["open", "--help"])
    check("no longer books" not in out, "R2 helm open without --confirm is not refused (help shown)")
    rc, out = cli(db, ["help"])
    check("books nothing" in out and "RETIRED" in out, "R2 helm help says open books nothing and marks the retired three")
    check(not diff(pre, db), "R2 nothing written: %s" % sorted(diff(pre, db)))

    rc, out, d, st = reconcile_case(REPO, "R3")
    check(rc == 0 and "MATCH" in out, "R3 reconcile runs and shows its comparison (exit %s)" % rc)
    check(not d and st == "PENDING", "R3 reconcile writes nothing -- every table equal; the PENDING position stays PENDING (%s, %s)"
          % (sorted(d), st))

    print("\ncontrols")
    pr = TMP / "old"
    shutil.copytree(REPO / "helm", pr / "helm", ignore=shutil.ignore_patterns("__pycache__", "*.bak*"))
    shutil.copy(REPO / "helm.py", pr / "helm.py"); (pr / "data").mkdir()
    shutil.copy(REPO / "helm" / "cli" / "reconcile_cmd.py.bak-20261031-s142", pr / "helm" / "cli" / "reconcile_cmd.py")
    f = pr / "helm" / "cli" / "open_cmd.py"
    f.write_text(f.read_text().replace("OPEN_BOOKING_RETIRED = True", "OPEN_BOOKING_RETIRED = False", 1))
    rc, out, d, st = reconcile_case(pr, "C3")
    # the old PENDING promotion never took effect: it ran the UPDATE on one get_conn() and
    # committed a second, new one. Its real writes were the balance refresh and RECONCILE_RUN.
    check({"accounts", "helm_events"} <= d, "control: the old reconcile DOES write (%s) -- the table check sees writes"
          % sorted(d))
    db = fresh("C2")
    rc, out = cli(db, ["open", "XLE", "CSP", "--confirm"], root=pr, timeout=25)
    check("no longer books trades" not in out, "control: with the gate off, --confirm is not refused (it goes on to the chain)")
    print("\nALL PASS" if ok_all else "\nNOT PASSED -- send me the output")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
