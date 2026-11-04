"""s151: save the NVDA/NKE cards and Today, rendered by the new code on a DB copy, for a visual check."""
import os, re, sys
sys.argv = ["x", "/tmp/s151/eng-new", "/tmp/s151/pg-new"]
src = open(os.path.dirname(os.path.abspath(__file__)) + "/verify_glance.py").read().split("# ---------------------------------------------------------------- the live copy")[0]
exec(src)
db = copy("render"); c = sqlite3.connect(db)
ids = [r[0] for r in c.execute("select id from positions where status='OPEN' and strategy='PMCC' and book='REAL'")]
out, log = render(db, NEW_ENG, NEW_PG, ["/today"] + ["/thesis/" + i for i in ids])
css = "".join(open(NEW_PG + "/static/" + f).read() for f in sorted(os.listdir(NEW_PG + "/static")) if f.endswith(".css"))
os.makedirs(ROOT + "/_s151/render", exist_ok=True)
for pth, (st, body) in out.items():
    body = re.sub(r'<link[^>]+stylesheet[^>]*>', "<style>%s</style>" % css.replace("\\", "\\\\"), body)
    open(ROOT + "/_s151/render/%s.html" % (pth.strip("/").replace("/", "_") or "x"), "w").write(body)
    print(pth, st, len(body))
