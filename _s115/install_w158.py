"""W158 install: back the live DB up, then seed the exit_flags decision log.

ADDITIVE ONLY. Creates one new table and one row per open decision point.
Touches no existing table -- W155: a new column on positions/legs/checks
breaks every model that maps SELECT * into a constructor.

Undo, if it is ever wanted: DROP TABLE exit_flags.
Run: /opt/anaconda3/envs/helm/bin/python3 _s115/install_w158.py --apply
"""
import os, sqlite3, sys, datetime
H = "/Users/russmacbookpro/Projects/helm"
sys.path.insert(0, H)
LIVE = H + "/data/helm.db"
APPLY = "--apply" in sys.argv

c0 = sqlite3.connect("file:%s?mode=ro" % LIVE, uri=True)
counts_before = {t: c0.execute("select count(*) from %s" % t).fetchone()[0]
                 for t in ("positions", "legs", "checks", "lifecycle_events")}
has = c0.execute("select count(*) from sqlite_master where name='exit_flags'").fetchone()[0]
c0.close()
print("before: exit_flags table present = %d" % has)
print("before: %s" % counts_before)
if not APPLY:
    print("\nDRY RUN. Re-run with --apply.")
    sys.exit(0)

stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
bak = "%s/data/helm.db.bak-s115-w158-%s" % (H, stamp)
c0 = sqlite3.connect("file:%s?mode=ro" % LIVE, uri=True)
c0.execute("VACUUM INTO '%s'" % bak); c0.close()
print("backup: %s (%d bytes)" % (bak, os.path.getsize(bak)))

from helm import exit_flags as F
c = sqlite3.connect(LIVE)
try:
    F.settle_closed(c)
    w = F.scan(c, seed=True)
    print("seeded %d decision point(s)" % len(w))
    for x in sorted(w, key=lambda y: y["flag_date"]):
        print("   %-6s %-7s %s" % (x["ticker"], x["kind"], x["flag_date"]))
finally:
    c.close()

# readback
c1 = sqlite3.connect("file:%s?mode=ro" % LIVE, uri=True)
after = {t: c1.execute("select count(*) from %s" % t).fetchone()[0]
         for t in ("positions", "legs", "checks", "lifecycle_events")}
n = c1.execute("select count(*) from exit_flags").fetchone()[0]
pend = c1.execute("select count(*) from exit_flags where disposition is null").fetchone()[0]
cols = {t: [r[1] for r in c1.execute("pragma table_info(%s)" % t)]
        for t in ("positions", "legs", "checks")}
c1.close()
ok = True
if after != counts_before:
    print("**FAIL** existing row counts moved: %s -> %s" % (counts_before, after)); ok = False
else:
    print("readback: existing tables untouched %s" % after)
for t, cl in cols.items():
    if "disposition" in cl or "flag_date" in cl:
        print("**FAIL** %s gained a column" % t); ok = False
print("readback: exit_flags %d rows, %d pending" % (n, pend))
print("readback: no existing table gained a column")
print("\n%s" % ("INSTALLED." if ok else "PROBLEM — restore from the backup above."))
sys.exit(0 if ok else 1)
