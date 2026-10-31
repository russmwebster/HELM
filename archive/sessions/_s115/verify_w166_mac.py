"""W166 on the Mac's interpreter: compile, then render the real thesis page
for MA and BMY through Flask's test client in THIS process (the running PG
server is untouched; it still holds the old engine until `helm restart pg`)."""
import sys, os, py_compile, sqlite3
H="/Users/russmacbookpro/Projects/helm"; PG="/Users/russmacbookpro/Projects/helm-pg"
for f in [H+"/helm/rule_read.py", PG+"/helm_engine.py", PG+"/engine_store.py"]:
    py_compile.compile(f, doraise=True)
print("py_compile 3.12 OK")
sys.path.insert(0, PG); sys.path.insert(0, H); os.chdir(PG)
import app as A
c = sqlite3.connect("file:%s/data/helm.db?mode=ro" % H, uri=True)
ids = {t: c.execute("select id from positions where book='REAL' and status='OPEN' and ticker=?", (t,)).fetchone()[0] for t in ("MA","BMY","JNJ")}
tc = A.app.test_client()
P=F=0
def ck(n, ok):
    global P,F; P,F = P+(1 if ok else 0), F+(0 if ok else 1); print(("PASS " if ok else "**FAIL** ")+n)
for t, want in (("MA", "would CLOSE this now"), ("BMY", "would hold this"), ("JNJ", "would CLOSE this now")):
    r = tc.get("/thesis/%s" % ids[t]); html = r.get_data(as_text=True)
    ck("%s thesis page 200" % t, r.status_code == 200)
    ck("%s card carries the rule block (%s)" % (t, want), ('<div class="rulebox' in html) and (want in html))
    if t == "MA":
        ck("MA explains the trail with its own numbers", "Peak +42%" in html and "+22%" in html)
        ck("MA carries the measured record", "regained its peak in 5" in html)
        ck("MA carries the doctrine line", "informs and never acts" in html)
ck("no rendering error text", "Rule read unavailable" not in html)
# a closed position must NOT show the block
cid = c.execute("select id from positions where book='REAL' and status='CLOSED' and strategy='LONG_CALL' order by closed_at desc limit 1").fetchone()[0]
r = tc.get("/thesis/%s" % cid); ck("closed position shows no rule block", r.status_code == 200 and '<div class="rulebox' not in r.get_data(as_text=True))
# board mapping under 3.12
import helm_engine as E
lbl, tone, _ = E._verdict_from({"core_reason": "GIVE_BACK", "reasons": ["x"], "flag": "YELLOW"})
ck("board verdict map now speaks GIVE_BACK (%s)" % lbl, lbl.startswith("Rule:"))
print("PASS %d FAIL %d" % (P, F)); sys.exit(1 if F else 0)
