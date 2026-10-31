import sys, os, sqlite3
PG="/Users/russmacbookpro/Projects/helm-pg"; H="/Users/russmacbookpro/Projects/helm"
sys.path.insert(0, PG); sys.path.insert(0, H); os.chdir(PG)
import app as A
c = sqlite3.connect("file:%s/data/helm.db?mode=ro" % H, uri=True)
pid = c.execute("select id from positions where book='REAL' and status='OPEN' and ticker='MA'").fetchone()[0]
h = A.app.test_client().get("/thesis/" + pid).get_data(as_text=True)
css = open(PG + "/static/style.css").read()
h = h.replace('<link rel="stylesheet" href="/static/style.css', '<link rel="stylesheet" href="/static/_x_')
h = h.replace("</head>", "<style>%s</style></head>" % css, 1)
open(H + "/_s115/ma_card.html", "w").write(h)
print(len(h), "written")
