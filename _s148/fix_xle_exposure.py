"""s148 -- give XLE its exposure group (ENERGY), as XOM and CVX have.

The 11-02 audit failed one check: XLE, held since 10-30, has no exposure group on the
watchlist (nothing writes that field for a new name -- W143). Changes ONE column of ONE row:
watchlist.exposure_group for XLE, NULL -> 'ENERGY'. Nothing else.

Usage (on the Mac):   cd ~/Projects/helm && python3 _s148/fix_xle_exposure.py            (dry run)
                      cd ~/Projects/helm && python3 _s148/fix_xle_exposure.py --apply    (backs up, then writes)
HELM_DB=COPY.db points it at a copy. Refuses unless exactly one active XLE row with no group.
"""
import os, sqlite3, sys
from datetime import datetime

DB = os.environ.get("HELM_DB") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "helm.db")
GROUP = "ENERGY"
apply = "--apply" in sys.argv

c = sqlite3.connect(DB, timeout=30)
rows = c.execute("SELECT id, ticker, active, exposure_group FROM watchlist WHERE ticker='XLE'").fetchall()
print("database:", DB)
print("XLE rows:", rows)
if len(rows) != 1 or rows[0][2] != 1 or rows[0][3] not in (None, ""):
    print("REFUSED: expected exactly one active XLE row with no exposure group -- nothing written.")
    sys.exit(1)
if c.execute("SELECT COUNT(*) FROM watchlist WHERE exposure_group=?", (GROUP,)).fetchone()[0] == 0:
    print("REFUSED: no existing row uses the group %s -- nothing written." % GROUP)
    sys.exit(1)
print("would set watchlist.exposure_group = %r for %s (%s)" % (GROUP, rows[0][1], rows[0][0]))
if not apply:
    print("dry run -- nothing written. Add --apply to write.")
    sys.exit(0)
bak = DB + ".bak-s148-xle-" + datetime.now().strftime("%Y%m%d-%H%M%S")
b = sqlite3.connect(bak)
c.backup(b)
b.close()
print("backup:", bak)
with c:
    n = c.execute("UPDATE watchlist SET exposure_group=? WHERE id=? AND ticker='XLE' AND exposure_group IS NULL",
                  (GROUP, rows[0][0])).rowcount
if n != 1:
    print("REFUSED at write: %d rows changed (expected 1); the backup is above." % n)
    sys.exit(1)
print("done: XLE's exposure group is now", c.execute("SELECT exposure_group FROM watchlist WHERE id=?", (rows[0][0],)).fetchone()[0])
