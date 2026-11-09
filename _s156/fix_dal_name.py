"""s156 -- give DAL its company name (Delta Air Lines, Inc.; Russ, 2026-11-08).

DAL is the only one of the watchlist's 135 names with no company name, so the Watchlist showed it blank.
Changes ONE column of ONE row: watchlist.company_name for DAL, NULL -> 'Delta Air Lines, Inc.'. Nothing else.

Usage (on the Mac):   cd ~/Projects/helm && python3 _s156/fix_dal_name.py            (dry run)
                      cd ~/Projects/helm && python3 _s156/fix_dal_name.py --apply    (backs up, then writes)
HELM_DB=COPY.db points it at a copy. Refuses unless exactly one active DAL row with no name.
"""
import os, sqlite3, sys
from datetime import datetime

DB = os.environ.get("HELM_DB") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "helm.db")
NAME = "Delta Air Lines, Inc."
apply = "--apply" in sys.argv

c = sqlite3.connect(DB, timeout=30)
rows = c.execute("SELECT id, ticker, active, company_name FROM watchlist WHERE ticker='DAL'").fetchall()
print("database:", DB)
print("DAL rows:", rows)
if len(rows) != 1 or rows[0][2] != 1 or rows[0][3] not in (None, ""):
    print("REFUSED: expected exactly one active DAL row with no company name -- nothing written.")
    sys.exit(1)
print("would set watchlist.company_name = %r for %s (%s)" % (NAME, rows[0][1], rows[0][0]))
if not apply:
    print("dry run -- nothing written. Add --apply to write.")
    sys.exit(0)
bak = DB + ".bak-s156-dal-" + datetime.now().strftime("%Y%m%d-%H%M%S")
b = sqlite3.connect(bak)
c.backup(b)
b.close()
print("backup:", bak)
with c:
    n = c.execute("UPDATE watchlist SET company_name=? WHERE id=? AND ticker='DAL' AND (company_name IS NULL OR company_name='')",
                  (NAME, rows[0][0])).rowcount
if n != 1:
    print("REFUSED at write: %d rows changed (expected 1); the backup is above." % n)
    sys.exit(1)
print("done: DAL's company name is now", c.execute("SELECT company_name FROM watchlist WHERE id=?", (rows[0][0],)).fetchone()[0])
