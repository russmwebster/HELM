"""s155 step 1: dump advise(), advice.json's build() and decide() for every open CSP from whichever code tree is first
on sys.path (HELM_CODE). Read-only on HELM_DB (a copy). Usage: HELM_CODE=DIR HELM_DB=COPY python3 -B dump_step1.py OUT"""
import json, os, sys
sys.path.insert(0, os.environ["HELM_CODE"])
from datetime import date
import helm, helm.advice as A, helm.decide as D
print("code:", os.path.dirname(helm.__file__), file=sys.stderr)
db = os.environ["HELM_DB"]
T = date(2026, 11, 8)
recs = A.advise(db=db, today=T)
built = A.build(db=db, today=T)
built.pop("generated_at", None)
import sqlite3
c = sqlite3.connect(db)
ids = [r[0] for r in c.execute("SELECT id FROM positions WHERE book='REAL' AND status='OPEN' AND strategy='CSP' ORDER BY id")]
dec = {i: D.decide(i, db=db, today=T) for i in ids}
json.dump({"recs": recs, "built": built, "decide": dec}, open(sys.argv[1], "w"), indent=1, sort_keys=True, default=str)
