"""tools/verify_w180_step5_pg.py -- W180 step 5, the PG half: the buy-back route.

Run FROM the helm-pg directory, in a clean environment, exactly as launchd
starts PG: HELM_ROOT in the environment and NOT on sys.path, HOME pointing at a
home whose .helm_profile names the account, HELM_DB a VACUUM INTO copy:

    cd ~/Projects/helm-pg && env -i HOME=<home> PATH=$PATH HELM_ROOT=~/Projects/helm \
        HELM_DB=<copy.db> python3 ~/Projects/helm/tools/verify_w180_step5_pg.py

It WRITES to that copy (closes B's short at 0.47). Never point it at the live DB.
Perturbation used 2026-09-23: engine_store.close_leg trusting the return code
alone -> both refusals read ok=True (W154). Control ok=False/True/False.
"""
import os, sys, sqlite3
import os, sys, sqlite3
root = os.environ["HELM_ROOT"]
assert root not in sys.path, "harness would hide the lazy-import defect"
sys.path.insert(0, os.getcwd())
import app as A
print("LOADED", A.__file__, "| REAL", A.REAL, "| helm on path at start:", root in sys.path)
import helm_engine as HE
HE.helm_python = lambda: sys.executable     # the VM has no anaconda; subprocess path only
cl = A.app.test_client()
r = cl.get("/positions?book=REAL"); b = r.get_data(as_text=True)
print("positions", r.status_code, "harvlnk" in b, "buy back short" in b)
p = cl.get("/api/pending").get_json()
print("pending", len(p), [(x["ticker"], x.get("label")) for x in p][:6])
db = os.environ["HELM_DB"]
c = sqlite3.connect(db)
pid, lid = c.execute("select l.position_id,l.id from legs l join positions p on p.id=l.position_id "
    "where p.ticker='B' and p.book='REAL' and p.status='OPEN' and l.direction='SHORT' and l.status='OPEN'").fetchone()
c.close()
bad = cl.post("/api/close/leg", json={"ticker":"B","pos_id":pid,"leg_id":lid,"price":0.47,"reason":"NONSENSE"}).get_json()
print("bad reason ok=", bad["ok"], "|", bad["output"][-90:].replace("\n"," "))
good = cl.post("/api/close/leg", json={"ticker":"B","pos_id":pid,"leg_id":lid,"price":0.47,"reason":"harvest"}).get_json()
print("good ok=", good["ok"], "|", good["output"][-160:].replace("\n"," "))
again = cl.post("/api/close/leg", json={"ticker":"B","pos_id":pid,"leg_id":lid,"price":0.47}).get_json()
print("again ok=", again["ok"], "|", again["output"][-90:].replace("\n"," "))
c = sqlite3.connect(db)
print("leg:", c.execute("select status,close_price from legs where id=?", (lid,)).fetchone(),
      "pos:", c.execute("select status from positions where id=?", (pid,)).fetchone())
