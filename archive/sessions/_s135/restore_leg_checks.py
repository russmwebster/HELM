#!/usr/bin/env python3
"""s135 (Russ, 2026-10-25): put back the leg readings the paper freeze deleted.

The freeze (_s135/freeze_paper.py) closed 158 paper legs through Leg.save, which
was INSERT OR REPLACE; leg_checks references legs(id) ON DELETE CASCADE, so every
closed leg's readings were deleted -- 5,612 rows. (Leg.save is fixed in s135.)
The freeze's own backup, taken just before it wrote, still has them.

  python3 _s135/restore_leg_checks.py            # dry run: counts what it would put back
  python3 _s135/restore_leg_checks.py --apply    # backs up helm.db, then re-inserts them

Re-inserts only rows the live leg_checks lacks, copied unchanged from the backup.
Refuses unless every such row belongs to a paper position closed RUN_OFF, its leg,
position and check still exist, and the count is --expect (default 5612). Checks
afterwards that leg_checks grew by exactly that and no other table changed. A
second run finds nothing missing and does nothing.
"""
import os, sys, sqlite3, argparse
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = os.environ.get("HELM_DB") or str(ROOT / "data" / "helm.db")
SRC = str(ROOT / "data" / "helm.db.bak-s135-freeze-20261024-184809")


def counts(c):
    return {t: c.execute('SELECT COUNT(*) FROM main."%s"' % t).fetchone()[0]
            for (t,) in c.execute("SELECT name FROM main.sqlite_master WHERE type='table' "
                                  "AND name NOT LIKE 'sqlite_%' ORDER BY name")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--from", dest="src", default=SRC)
    ap.add_argument("--expect", type=int, default=5612)
    a = ap.parse_args()
    if not os.path.exists(a.src):
        print("REFUSED: backup not found:", a.src); return 2
    # uri=True on the main connection: without it, macOS/Anaconda builds refuse the
    # read-only file: URI in ATTACH below ("unable to open database").
    c = sqlite3.connect("file:%s" % os.path.abspath(DB), uri=True)
    print("database:", DB, "\nrestoring from:", a.src)
    c.execute("ATTACH DATABASE ? AS b", ("file:%s?mode=ro" % a.src,))
    missing = "SELECT k.* FROM b.leg_checks k WHERE k.id NOT IN (SELECT id FROM main.leg_checks)"
    n = c.execute("SELECT COUNT(*) FROM (%s)" % missing).fetchone()[0]
    if n == 0:
        print("nothing missing -- already restored"); return 0
    bad = c.execute("""SELECT COUNT(*) FROM (%s) m
        LEFT JOIN main.positions p ON p.id = m.position_id
        LEFT JOIN main.legs l ON l.id = m.leg_id
        WHERE p.id IS NULL OR l.id IS NULL OR p.book != 'PAPER' OR coalesce(p.exit_reason,'') != 'RUN_OFF'
           OR (m.check_id IS NOT NULL AND m.check_id NOT IN (SELECT id FROM main.checks))""" % missing).fetchone()[0]
    legs = c.execute("SELECT COUNT(DISTINCT leg_id), COUNT(DISTINCT position_id) FROM (%s)" % missing).fetchone()
    print("missing from live: %d readings on %d legs of %d positions" % (n, legs[0], legs[1]))
    if bad:
        print("REFUSED: %d of them are not on a RUN_OFF paper position with its leg, position and check present" % bad)
        return 2
    if n != a.expect:
        print("REFUSED: %d missing, expected %d" % (n, a.expect)); return 2
    if not a.apply:
        print("DRY RUN -- nothing written. Re-run with --apply."); return 0
    bak = "%s.bak-s135-legchecks-%s" % (DB, datetime.now().strftime("%Y%m%d-%H%M%S"))
    c.execute("VACUUM INTO ?", (bak,))
    print("backup:", bak)
    before = counts(c)
    cols = [r[1] for r in c.execute("PRAGMA main.table_info(leg_checks)")]
    cl = ", ".join(cols)
    c.execute("INSERT INTO main.leg_checks (%s) SELECT %s FROM b.leg_checks WHERE id NOT IN "
              "(SELECT id FROM main.leg_checks)" % (cl, cl))
    after = counts(c)
    grew = after["leg_checks"] - before["leg_checks"]
    others = {t for t in before if t != "leg_checks" and before[t] != after.get(t)}
    same = c.execute("""SELECT COUNT(*) FROM main.leg_checks m JOIN b.leg_checks k ON k.id = m.id
                        WHERE %s""" % " OR ".join("m.%s IS NOT k.%s" % (x, x) for x in cols)).fetchone()[0]
    if grew != n or others or same:
        c.rollback()
        print("REFUSED after insert (rolled back): grew %d, other tables changed %s, rows differing from backup %d"
              % (grew, sorted(others), same)); return 2
    c.commit()
    print("restored %d readings. leg_checks %d -> %d; no other table changed: YES"
          % (grew, before["leg_checks"], after["leg_checks"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
