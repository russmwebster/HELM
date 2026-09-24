"""tools/verify_w180_candidates_pg.py -- the board's re-sell candidates route.
Run FROM ~/Projects/helm-pg, clean env, as launchd starts PG:
  env -i HOME=<home with .helm_profile> PATH=$PATH HELM_ROOT=~/Projects/helm \
    PYTHONPATH=~/Projects/helm/tools/fixtures/fakeyf HELM_DB=<copy.db> \
    python3 ~/Projects/helm/tools/verify_w180_candidates_pg.py
Reads the copy only; the CLI subprocess reads the stand-in chain.
"""
import os, sys
root = os.environ["HELM_ROOT"]; assert root not in sys.path
sys.path.insert(0, os.getcwd())
import app as A, helm_engine as HE
print("LOADED", HE.__file__, "| engine", root)
HE.helm_python = lambda: sys.executable
cl = A.app.test_client(); F = []
def case(n, got, want):
    ok = got == want; print(("PASS" if ok else "FAIL"), n, repr(got)[:150], "" if ok else "want %r" % (want,))
    if not ok: F.append(n)
import sqlite3
c = sqlite3.connect(os.environ["HELM_DB"])
pid = c.execute("select id from positions where ticker='AA' and book='REAL' and status='OPEN'").fetchone()[0]
j = cl.post("/api/diagonal/candidates", json={"ticker": "AA", "pos_id": pid}).get_json()
case("ok", j.get("ok"), True)
case("never_inside", all(x["strike"] >= 47 for x in j["candidates"]), True)
case("by_21dte", all(x["expiration"] <= "2026-11-27" for x in j["candidates"]), True)
case("nonempty", len(j["candidates"]) > 0, True)
j2 = cl.post("/api/diagonal/candidates", json={"ticker": "AA", "pos_id": "nope"}).get_json()
case("bad_id_not_ok", j2.get("ok"), False)
page = cl.get("/positions?book=REAL").get_data(as_text=True)
case("page_flow", "/api/diagonal/candidates" in page and "function sellFlow" in page, True)
print("ALL PASS" if not F else "%d FAIL" % len(F))
