"""s143: verify advice.json / advice-status.json (helm/advice.py write_files, the
snapshot post-pass in helm/cli/check_cmd.py). Runs on a VACUUM INTO copy only.

    HELM_ROOT=... python3 -B _s143/verify_advice_json.py LIVE_DB WORKDIR
Exit 0 = all pass. HELM_CODE=DIR loads the code from a (perturbed) copy. Never touches the live database or the live Claude outputs."""
import hashlib, json, os, shutil, sqlite3, subprocess, sys
from datetime import date

LIVE, WORK = sys.argv[1], sys.argv[2]
ROOT = os.environ["HELM_ROOT"]
fails = []
def ok(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c: fails.append(m)

def fresh(name):
    p = os.path.join(WORK, name)
    if os.path.exists(p): os.remove(p)
    c = sqlite3.connect("file:%s?mode=ro" % LIVE, uri=True); c.execute("VACUUM INTO ?", (p,)); c.close()
    return p

def tables(db):
    c = sqlite3.connect(db); out = {}
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        h = hashlib.sha256()
        cols = [r[1] for r in c.execute('PRAGMA table_info("%s")' % t)]
        h.update(repr(cols).encode())
        for r in c.execute('SELECT * FROM "%s" ORDER BY rowid' % t): h.update(repr(r).encode())
        out[t] = h.hexdigest()
    c.close(); return out

sys.path.insert(0, os.environ.get("HELM_CODE", ROOT))   # HELM_CODE: a perturbed copy of the code
import helm.advice as A
print("loaded helm.advice from", A.__file__)
import helm.cli.check_cmd as CC
print("loaded check_cmd from", CC.__file__)

# 1. the file matches advise() line for line; every table unchanged
db = fresh("t1.db"); out = os.path.join(WORK, "o1"); shutil.rmtree(out, ignore_errors=True)
os.environ["HELM_ADVICE_DIR"] = out
before = tables(db)
st = A.write_files(db=db)
after = tables(db)
ok(st["status"] == "ok", "write_files ok (%s)" % st.get("reason"))
ok(before == after, "every table unchanged (%d tables)" % len(before))
doc = json.load(open(os.path.join(out, "advice.json")))
recs = A.advise(db=db)
pos = [r for r in recs if r["position_id"]]
ents = {k: e for k, e in doc["entries"].items() if e["kind"] == "position"}
ok(set(ents) == {r["position_id"] for r in pos}, "one entry per open real position, keyed by id (%d)" % len(pos))
c = sqlite3.connect(db)
open_ids = {r[0] for r in c.execute("SELECT id FROM positions WHERE book='REAL' AND status='OPEN'")}
ok(set(ents) == open_ids, "keys are exactly the open real position ids")
ok(all(ents[r["position_id"]]["recommendation"][k] == r[k] for r in pos
       for k in ("verb", "headline", "reason", "act", "lean")), "every line word for word as helm advise")
ok(doc["schema_version"] == 1 and doc["generated_at"] and "limits" in doc, "schema_version, timestamp, limits")
L = doc["limits"]
ok(set(L) >= {"sleeve", "name", "sector", "beta_weighted_delta", "theta"}, "limits: sleeve, name, sector, delta, theta")
s1 = json.load(open(os.path.join(out, "advice-status.json")))
ok(s1["ok"] is True and s1["advice"]["generated_at"] == doc["generated_at"], "status ok, points at this advice")

# 2. shares, and flags matched by company (GOOG + GOOGL); a name over the limit attached to its entries
db = fresh("t2.db")
c = sqlite3.connect(db)
acct = c.execute("SELECT account_id FROM positions WHERE book='REAL' AND status='OPEN' LIMIT 1").fetchone()[0]
c.execute("INSERT INTO stock_positions (id, account_id, ticker, shares, cost_basis, effective_basis) "
          "VALUES ('SP-GOOG','%s','GOOG',100,335.0,326.62)" % acct)
c.execute("INSERT INTO stock_positions (id, account_id, ticker, shares, cost_basis, effective_basis) "
          "VALUES ('SP-XLE','%s','XLE',850,67.0,65.78)" % acct)
c.commit(); c.close()
import helm.sleeve as S
cap = S.NAME_PCT
S.NAME_PCT = 0.10          # force GOOG + GOOGL over the name limit
try:
    doc2 = A.build(db=db)
finally:
    S.NAME_PCT = cap
E = doc2["entries"]
g = E.get("SP-GOOG")
ok(g and g["kind"] == "shares" and g["shares"] == 100 and g["effective_basis"] == 326.62 and g["call_on_them"] is False
   and g["recommendation"]["verb"] == "SELL_CALL", "GOOG shares: kind shares, basis, no call, sell a call")
x = E.get("SP-XLE")
ok(x and x["shares_without_call"] == 850 and x["recommendation"]["verb"] == "SELL_CALL" and x["covered_by"] == []
   and "850 of 850 shares" in x["recommendation"]["reason"], "XLE 850 shares: none covered, sell a call")
gp = [e for e in E.values() if e["kind"] == "position" and e["ticker"] == "GOOGL"][0]
ok(any(f["key"] == "GOOG + GOOGL" for f in g["rule_flags"]) and any(f["key"] == "GOOG + GOOGL" for f in gp["rule_flags"]),
   "the company's name-limit flag is on both the GOOG shares and the GOOGL put")
ok("GOOG + GOOGL" in doc2["limits"]["name"]["over"], "limits.name.over names the company")
ok(not any(f["key"] == "GOOG + GOOGL" for f in doc2["book_flags"]), "a name flag is not left in book_flags")

# 3. failure keeps the last good advice.json, status says failed with the reason
good = open(os.path.join(out, "advice.json"), "rb").read()
real = A.advise
A.advise = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("perturbed advise"))
try:
    st3 = A.write_files(db=fresh("t3.db"), snapshot={"started": "2026-11-02T12:30:05"}, source="helm snapshot")
finally:
    A.advise = real
ok(open(os.path.join(out, "advice.json"), "rb").read() == good, "failed run: advice.json byte-identical")
s3 = json.load(open(os.path.join(out, "advice-status.json")))
ok(s3["status"] == "failed" and "perturbed advise" in s3["reason"] and s3["slot"] == "12:30"
   and s3["advice"]["generated_at"] == doc["generated_at"], "status: 12:30 failed, reason, last good advice named")

# 4. the snapshot post-pass: stood down / raised / normal (cmd_snapshot faked -- no IBKR)
os.environ["HELM_DB"] = fresh("t4.db")
import importlib, helm.config as HC
real_cmd = CC.cmd_snapshot
def fake_down(args): CC.SNAPSHOT_INFO["stood_down"] = "market closed (holiday)"
def fake_raise(args): raise ValueError("boom")
def fake_ok(args): CC.SNAPSHOT_INFO.update(started="2026-11-02T10:00:04", finished="2026-11-02T10:03:10", attempted=7, journaled=7)
HC.DB_PATH = __import__("pathlib").Path(os.environ["HELM_DB"])
A.S.__dict__  # sleeve reads helm.config.DB_PATH lazily
CC.get_active_account = lambda: "x"
for fake, want in ((fake_down, "skipped"), (fake_raise, "failed"), (fake_ok, "ok")):
    CC.cmd_snapshot = fake
    try:
        CC.run_snapshot()
    except ValueError:
        pass
    s = json.load(open(os.path.join(out, "advice-status.json")))
    ok(s["status"] == want, "snapshot %s -> status %s (%s)" % (fake.__name__, s["status"], s.get("reason")))
CC.cmd_snapshot = real_cmd
d = json.load(open(os.path.join(out, "advice.json")))
ok(d["slot"] == "10:00" and d["snapshot"]["journaled"] == 7 and d["source"] == "helm snapshot", "after the ok run: slot 10:00, snapshot counts")

# 5. a copy never writes the Book's live file
del os.environ["HELM_ADVICE_DIR"]
live_out = os.path.join(ROOT, "Claude outputs")
had = os.path.exists(os.path.join(live_out, "advice.json"))
st5 = A.write_files(db=os.environ["HELM_DB"])
ok(st5["status"] == "not written" and os.path.exists(os.path.join(live_out, "advice.json")) == had,
   "HELM_DB copy without HELM_ADVICE_DIR: nothing written to the live folder")

print("\n%d FAIL" % len(fails) if fails else "\nALL PASS")
sys.exit(1 if fails else 0)
