"""W158 verification. Fresh VACUUM INTO copy per case; control LAST."""
import sqlite3, sys, os, tempfile

# Path-independent: runs from the repo, on the Mac or in the mounted VM.
# s107's rule -- a study meant to be repeated needs a fixture, not a memory.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
LIVE = os.environ.get("HELM_DB") or os.path.join(ROOT, "data", "helm.db")
W = tempfile.mkdtemp(prefix="w158_verify_")
P = F = 0
def check(name, ok):
    global P, F
    P, F = P + (1 if ok else 0), F + (0 if ok else 1)
    print("  %s %s" % ("PASS" if ok else "**FAIL**", name))
def fresh(tag):
    p = os.path.join(W, "c_%s.db" % tag)
    if os.path.exists(p): os.remove(p)
    c = sqlite3.connect("file:%s?mode=ro" % LIVE, uri=True)
    c.execute("VACUUM INTO '%s'" % p); c.close()
    c = sqlite3.connect(p)
    # W158 is now INSTALLED on live, so a copy of live arrives with the log
    # already in it. Every case below asserts what a first install does, so
    # each copy is returned to the pre-install state deliberately.
    c.execute("DROP TABLE IF EXISTS exit_flags"); c.commit()
    return c
import helm.exit_flags as F_

# WHICH CODE IS LOADED -- asserted from the loaded objects, not from disk.
# The VM cannot delete __pycache__, so a stale .pyc is a live hazard (s109).
_c = {"checked_at": "2026-01-05T10:00:00", "pnl_pct": 99.0, "dte_now": 99,
      "thesis_broken": 0, "pnl_unrealized": 1.0}
_gA = not F_.new_flags("LONG_CALL", [_c], {"target"}, {})
_gB = not F_.new_flags("LONG_CALL", [_c], set(), {"target": "2026-06-01"})
print("LOADED CODE: already_open guard %s | dispositioned_after cut %s"
      % ("intact" if _gA else "BLUNTED", "intact" if _gB else "BLUNTED"))

print("\n[1] pure classify()")
check("thesis wins over target", F_.classify("LONG_CALL", 90.0, 5, 1) == "thesis")
check("target wins over dte", F_.classify("LONG_CALL", 30.0, 5, 0) == "target")
check("dte alone", F_.classify("LONG_CALL", 1.0, 21, 0) == "dte")
check("dte boundary 22 does not fire", F_.classify("LONG_CALL", 1.0, 22, 0) is None)
check("CSP target is 50 not 25", F_.classify("CSP", 30.0, 99, 0) is None)
check("CSP fires at 50", F_.classify("CSP", 50.0, 99, 0) == "target")
check("default target 25", F_.classify("IRON_CONDOR", 25.0, 99, 0) == "target")
check("nothing fires", F_.classify("LONG_CALL", 0.0, 99, 0) is None)
check("None pct/dte safe", F_.classify("LONG_CALL", None, None, None) is None)

print("\n[2] seed reproduces the hand count")
c = fresh("seed"); w = F_.scan(c, seed=True, now="2026-09-06T10:00:00")
check("11 flags seeded", len(w) == 11)
check("all marked seeded=1", all(x["seeded"] for x in w))
check("MA has two kinds", sorted(x["kind"] for x in w if x["ticker"] == "MA") == ["target", "thesis"])
check("re-seed writes nothing", len(F_.scan(c, seed=True, now="2026-09-06T10:05:00")) == 0)
n_pending = len(F_.pending(c)); check("11 pending", n_pending == 11)

print("\n[3] THE DEFECT MOST FEARED: a still-firing kind opens a second row")
before = c.execute("select count(*) from exit_flags").fetchone()[0]
w2 = F_.scan(c, seed=False, now="2026-09-07T10:00:00")
after = c.execute("select count(*) from exit_flags").fetchone()[0]
check("daily scan opens nothing while decisions are open (GM/MA/JNJ still firing)",
      len(w2) == 0 and after == before)

print("\n[4] keep() -- the one disposition nobody infers")
pend = [d for d in F_.pending(c) if d["ticker"] == "GM"][0]
n = F_.keep(c, pend["position_id"], "thesis", "earnings 10-28, thesis is the tape not the company",
            note="reviewed on the card", now="2026-09-06T11:00:00")
check("keep updated exactly 1 row", n == 1)
row = c.execute("select disposition, decided_by, reason from exit_flags where id=?", (pend["id"],)).fetchone()
check("disposition KEEP by russ with a reason", row[0] == "KEEP" and row[1] == "russ" and row[2])
check("GM no longer pending", not any(d["ticker"] == "GM" for d in F_.pending(c)))
check("keep on an already-settled flag updates nothing",
      F_.keep(c, pend["position_id"], "thesis", "again", now="2026-09-06T11:05:00") == 0)

print("\n[5] a settled flag must not re-open the same day (GM is STILL firing)")
w3 = F_.scan(c, seed=False, now="2026-09-06T12:00:00")
check("same-day re-fire does not re-open", not any(x["ticker"] == "GM" for x in w3))
print("    ... but a LATER re-fire is a new decision")
c.execute("update exit_flags set decided_date='2026-09-01' where id=?", (pend["id"],)); c.commit()
w4 = F_.scan(c, seed=True, now="2026-09-06T12:30:00")
check("re-fire after the decision date opens a NEW decision point",
      any(x["ticker"] == "GM" and x["kind"] == "thesis" for x in w4))

print("\n[6] settle_closed() infers ACTED and says it inferred")
c2 = fresh("settle"); F_.scan(c2, seed=True, now="2026-09-06T10:00:00")
pid = c2.execute("select id from positions where book='REAL' and status='OPEN' "
                 "and id in (select position_id from exit_flags) limit 1").fetchone()[0]
c2.execute("update positions set status='CLOSED', closed_at='2026-09-05T15:00:00', "
           "exit_reason='THESIS_BREAK' where id=?", (pid,)); c2.commit()
n = F_.settle_closed(c2, now="2026-09-06T16:00:00")
check("settled at least one", n >= 1)
r = c2.execute("select disposition, decided_by, decided_date, reason from exit_flags "
               "where position_id=?", (pid,)).fetchone()
check("ACTED / inferred-close / dated the close / carries exit_reason",
      r[0] == "ACTED" and r[1] == "inferred-close" and r[2] == "2026-09-05" and r[3] == "THESIS_BREAK")
check("closed position drops out of pending", not any(d["position_id"] == pid for d in F_.pending(c2)))
d = F_.dispositions_for(c2, [pid])
check("dispositions_for reports it for M6", d.get(pid, {}) and "ACTED" in d[pid].values())

print("\n[7] THE LIVE FILE WAS NEVER TOUCHED")
live = sqlite3.connect("file:%s?mode=ro" % LIVE, uri=True)
# Post-install invariant: the live log holds only what the install seeded.
# Every case above ran on a copy, so nothing here may have moved it.
_n = live.execute("select count(*) from exit_flags").fetchone()[0]
check("live exit_flags untouched by this harness (11 seeded rows)", _n == 11)
check("no synthetic/test disposition reached live",
      live.execute("select count(*) from exit_flags where decided_by "
                   "not in ('russ','inferred-close')").fetchone()[0] == 0)
for t in ("positions", "legs", "checks"):
    cols = [r[1] for r in live.execute("pragma table_info(%s)" % t)]
    check("%s unchanged (no new column)" % t, "disposition" not in cols and "flag_date" not in cols)

print("\n[8] CONTROL, unperturbed, LAST")
c3 = fresh("control"); w = F_.scan(c3, seed=True, now="2026-09-06T10:00:00")
check("control still seeds 11", len(w) == 11)
check("control pending still 11", len(F_.pending(c3)) == 11)

print("\nPASS %d · FAIL %d" % (P, F))
sys.exit(1 if F else 0)
