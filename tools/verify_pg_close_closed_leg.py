"""tools/verify_pg_close_closed_leg.py -- 2026-09-24. The board's whole-position
close on a position with a leg already closed (a harvested diagonal).

Run FROM ~/Projects/helm-pg, clean env, HELM_ROOT in the environment only,
HOME with a .helm_profile, HELM_DB a VACUUM INTO copy. WRITES to the copy.
Perturbation used: the price-count guard disabled -> 4 FAIL, AA long booked
at 0.17, realized -3,325. Control: ALL PASS, realized -1,810.
"""
import os, sys, sqlite3
root = os.environ["HELM_ROOT"]; assert root not in sys.path
sys.path.insert(0, os.getcwd())
import app as A, helm_engine as HE
print("LOADED", HE.__file__)
HE.helm_python = lambda: sys.executable
cl = A.app.test_client(); db = os.environ["HELM_DB"]; c = sqlite3.connect(db)
F = []
def case(n, got, want):
    ok = got == want; print(("PASS" if ok else "FAIL"), n, repr(got), "" if ok else "want %r" % (want,))
    if not ok: F.append(n)
pid = c.execute("select id from positions where ticker='AA' and book='REAL' and status='OPEN'").fetchone()[0]
legs = [dict(zip(("status","close_price"), r)) for r in c.execute("select status,close_price from legs where position_id=? order by created_at",(pid,))]
case("leg_done", [HE._leg_done(l) for l in legs], [True, False])
case("open_count", HE._open_leg_count(pid), 1)
r = cl.post("/api/close", json={"ticker":"AA","prices":[0.17,3.20],"pos_id":pid,"reason":"STOP"}).get_json()
case("stale_form_refused", (r["ok"], "Not closed" in r["output"]), (False, True))
case("nothing_written", c.execute("select status from positions where id=?",(pid,)).fetchone()[0], "OPEN")
r = cl.post("/api/close", json={"ticker":"AA","prices":[3.20],"pos_id":pid,"reason":"STOP"}).get_json()
c2 = sqlite3.connect(db)
case("long_close_price", c2.execute("select close_price from legs where position_id=? and direction='LONG'",(pid,)).fetchone()[0], 3.2)
case("realized", c2.execute("select status, realized_pnl from positions where id=?",(pid,)).fetchone(), ("CLOSED", -1810.0))
case("short_untouched", c2.execute("select close_price from legs where position_id=? and direction='SHORT'",(pid,)).fetchone()[0], 0.17)
print("ALL PASS" if not F else "%d FAIL" % len(F))
