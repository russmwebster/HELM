"""
s134 -- read-only check after `helm quality --watchlist` (the ownership-grade refresh).
    python3 _s134/grade_refresh_check.py [DB] [YYYY-MM-DD]
DB defaults to data/helm.db (opened ?mode=ro); the date defaults to the newest grade date.
Lists active names with no grade on that date (the refresh skipped them -- record_grade
swallows yfinance failures silently) and the A-C / D / F counts on each name's latest grade.
"""
import sqlite3, sys, os
from collections import Counter
db = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "data", "helm.db")
c = sqlite3.connect("file:" + os.path.abspath(db) + "?mode=ro", uri=True)
day = sys.argv[2] if len(sys.argv) > 2 else c.execute("select max(date) from ownership_quality").fetchone()[0]
active = [r[0] for r in c.execute("select ticker from watchlist where active=1 order by ticker")]
latest = {}
for t, d, g in c.execute("select ticker,date,grade from ownership_quality order by date"):
    latest[t] = (d, g)
missed = [t for t in active if latest.get(t, ("", ""))[0] != day]
print("refresh date:", day, "| active names:", len(active), "| graded that day:", len(active) - len(missed))
print("not graded that day (%d):" % len(missed), ", ".join("%s(%s)" % (t, latest.get(t, ("never", ""))[0]) for t in missed) or "none")
cnt = Counter(latest[t][1] if t in latest else "none" for t in active)
print("latest grade, active names:", dict(sorted(cnt.items())), "| A-C:", sum(cnt[g] for g in "ABC"))
