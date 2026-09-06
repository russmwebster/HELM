"""W158 -- prove the post-snapshot hook body runs on the Mac's interpreter,
against a VACUUM INTO copy. The live DB is opened read-only, once, to copy.
Run: /opt/anaconda3/envs/helm/bin/python3 _s115/verify_hook_mac.py
"""
import os, sqlite3, sys, hashlib
H = "/Users/russmacbookpro/Projects/helm"
sys.path.insert(0, H)
LIVE = H + "/data/helm.db"
COPY = "/tmp/helm_s115_hook.db"
before = hashlib.sha256(open(LIVE, "rb").read()).hexdigest()
if os.path.exists(COPY):
    os.remove(COPY)
c = sqlite3.connect("file:%s?mode=ro" % LIVE, uri=True)
c.execute("VACUUM INTO '%s'" % COPY); c.close()
os.environ["HELM_DB"] = COPY

P = F = 0
def ck(n, ok):
    global P, F
    P, F = P + (1 if ok else 0), F + (0 if ok else 1)
    print(("PASS " if ok else "**FAIL** ") + n)

# --- the hook body, verbatim from check_cmd.py -------------------------
def hook():
    import sqlite3 as _s3
    from helm import exit_flags as _xf
    from helm.config import DB_PATH as _dbp
    _c = _s3.connect(str(_dbp))
    try:
        _xf.settle_closed(_c)
        return _xf.scan(_c)
    finally:
        _c.close()

from helm import exit_flags as X
conn = sqlite3.connect(COPY)

n0 = hook()
ck("hook runs clean on a virgin DB (no exit_flags table yet)", isinstance(n0, list))
# The daily scan reads the whole latest journal DAY, not the latest check
# row -- three slots. A kind that fired at 10:00 and had cleared by 15:15
# still fired, and that is precisely the case W158 exists to make visible.
# So expect MORE than pending()'s "still firing" count, and assert the
# invariant that actually matters instead: never two rows for one decision.
print("   (daily scan opened %d: %s)" % (len(n0), [x["ticker"] + "/" + x["kind"] for x in n0]))
ck("every row is a distinct (position, kind) - no slot opens a duplicate",
   len({(x["position_id"], x["kind"]) for x in n0}) == len(n0))
ck("all of them are dated the latest journal day",
   len({x["flag_date"] for x in n0}) <= 1)
ck("none is marked seeded - these were observed, not reconstructed",
   not any(x["seeded"] for x in n0))

X.scan(conn, seed=True)
tot = conn.execute("select count(*) from exit_flags").fetchone()[0]
ck("after an explicit --seed the book has its 11 decision points", tot == 11)

n1 = hook()
ck("hook re-run opens nothing new", len(n1) == 0)

ck("DB_PATH honoured HELM_DB (live untouched)",
   hashlib.sha256(open(LIVE, "rb").read()).hexdigest() == before)
live = sqlite3.connect("file:%s?mode=ro" % LIVE, uri=True)
ck("live still has no exit_flags table",
   live.execute("select count(*) from sqlite_master where name='exit_flags'").fetchone()[0] == 0)

print("PASS %d FAIL %d" % (P, F))
sys.exit(1 if F else 0)
