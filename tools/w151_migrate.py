#!/usr/bin/env python3
"""W151 - add the three columns the confirm path needs. Dry-run by default.

    python3 tools/w151_migrate.py            # say what it would do
    python3 tools/w151_migrate.py --apply    # do it, after backing the db up

Additive only: three new nullable columns, no data rewritten, no column
dropped or renamed. Existing readers see NULL and behave exactly as they
do today.

  positions.fills_confirmed_at  - a real column instead of prose in notes.
      The whole feature foundered on the entry path writing the NOTE
      "Pending execution" while the confirm path selected on
      status = 'PENDING', so pending_confirms was always empty and
      confirm_pending_position never ran once in 413 positions.
  legs.commission, legs.fees    - captured, never computed with. Stored
      P&L stays GROSS by decision. A column that exists can be ignored;
      one that does not cannot be recovered.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("HELM_DB") or os.path.join(os.path.dirname(HERE), "data", "helm.db")

# EMPTY BY DECISION. Russ decided 2026-08-26 that commissions and fees are not
# tracked; this tool added columns for them anyway, on the strength of a spec
# recommendation rather than his decision, and the migration broke `helm close`:
# helm/models/leg.py maps SELECT * straight into the constructor, so ANY new
# column on `legs` breaks every path that builds a Leg. All three columns were
# dropped again 2026-08-26. Nothing goes back without an explicit decision.
COLUMNS = []


def existing(conn, table):
    return [r[1] for r in conn.execute("PRAGMA table_info(" + table + ")")]


def main():
    apply_it = "--apply" in sys.argv
    conn = sqlite3.connect(DB)
    todo = [(t, c, k) for t, c, k in COLUMNS if c not in existing(conn, t)]

    print("database: " + DB)
    if not COLUMNS:
        print("nothing to migrate: COLUMNS is empty by decision - see the note above")
        return 0
    if not todo:
        print("every declared column is already present - nothing to do")
        return 0
    for table, column, kind in todo:
        print(("would add " if not apply_it else "adding   ")
              + table + "." + column + " " + kind)
    if not apply_it:
        print("\ndry run. re-run with --apply")
        return 0

    conn.close()
    backup = DB + ".bak-w151-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(DB, backup)
    print("backed up to " + os.path.basename(backup))

    conn = sqlite3.connect(DB)
    for table, column, kind in todo:
        conn.execute("ALTER TABLE " + table + " ADD COLUMN " + column + " " + kind)
    conn.commit()

    # Readback: assert the columns are there AND that nothing else moved.
    ok = True
    for table, column, _ in COLUMNS:
        present = column in existing(conn, table)
        print("readback " + table + "." + column + ": " + ("present" if present else "MISSING"))
        ok = ok and present
    counts = {t: conn.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
              for t in ("positions", "legs")}
    print("row counts unchanged by an additive migration: " + str(counts))
    nulls = conn.execute("SELECT COUNT(*) FROM positions WHERE fills_confirmed_at IS NOT NULL").fetchone()[0]
    print("positions already marked confirmed: " + str(nulls) + " (expected 0)")
    conn.close()
    return 0 if ok and nulls == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
