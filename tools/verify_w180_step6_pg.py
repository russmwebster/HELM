"""tools/verify_w180_step6_pg.py -- W180 step 6, PG half: the "sell short" route.

Run FROM ~/Projects/helm-pg, clean env, as launchd starts PG:
  env -i HOME=<home with .helm_profile> PATH=$PATH HELM_ROOT=~/Projects/helm \
    PYTHONPATH=~/Projects/helm/tools/fixtures/fakeyf HELM_DB=<copy.db> \
    python3 ~/Projects/helm/tools/verify_w180_step6_pg.py
The CLI subprocess reads a STAND-IN chain from tools/fixtures/fakeyf. WRITES to the copy.
Perturbation used 2026-09-24: engine_store.resell_short trusting the return code ->
off-chain and second-short refusals both read ok=True (W154).
"""
import os, sys, sqlite3
root = os.environ["HELM_ROOT"]; assert root not in sys.path
sys.path.insert(0, os.getcwd())
import app as A, helm_engine as HE
print("LOADED", HE.__file__, "| engine", root)
HE.helm_python = lambda: sys.executable
cl = A.app.test_client(); db = os.environ["HELM_DB"]
F = []
def case(n, got, want):
    ok = got == want; print(("PASS" if ok else "FAIL"), n, repr(got)[:160], "" if ok else "want %r" % (want,))
    if not ok: F.append(n)
c = sqlite3.connect(db)
pid = c.execute("select id from positions where ticker='AA' and book='REAL' and status='OPEN'").fetchone()[0]
n0 = c.execute("select count(*) from legs where position_id=?", (pid,)).fetchone()[0]
page = cl.get("/positions?book=REAL").get_data(as_text=True)
case("page_has_link", "reselllnk" in page and "sell short" in page, True)
r = cl.post("/api/diagonal/resell", json={"ticker":"AA","pos_id":pid,"strike":"55","expiry":"2026-11-20","dry_run":True}).get_json()
case("dry_ok", r["ok"], True)
case("dry_panel", ("re-sell panel" in r["output"], "nothing written" in r["output"]), (True, True))
case("dry_wrote_nothing", c.execute("select count(*) from legs where position_id=?", (pid,)).fetchone()[0], n0)
r = cl.post("/api/diagonal/resell", json={"ticker":"AA","pos_id":pid,"strike":"52.5","expiry":"2026-11-20","dry_run":True}).get_json()
case("off_chain_not_ok", (r["ok"], "no $52.5 CALL" in r["output"]), (False, True))
r = cl.post("/api/diagonal/resell", json={"ticker":"AA","pos_id":pid,"strike":"55","expiry":"2026-11-20","price":0.48}).get_json()
case("record_ok", r["ok"], True)
c2 = sqlite3.connect(db)
case("leg_written", c2.execute("select count(*) from legs where position_id=? and status='OPEN' and direction='SHORT' and strike=55 and expiration='2026-11-20' and open_price=0.48", (pid,)).fetchone()[0], 1)
r = cl.post("/api/diagonal/resell", json={"ticker":"AA","pos_id":pid,"strike":"50","expiry":"2026-11-20","price":0.9}).get_json()
case("second_refused_not_ok", (r["ok"], "a short is already on" in r["output"]), (False, True))
case("still_one_open_short", c2.execute("select count(*) from legs where position_id=? and status='OPEN' and direction='SHORT'", (pid,)).fetchone()[0], 1)
print("ALL PASS" if not F else "%d FAIL" % len(F))
